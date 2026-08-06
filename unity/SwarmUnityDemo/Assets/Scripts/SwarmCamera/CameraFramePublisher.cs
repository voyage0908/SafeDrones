using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using SwarmTelemetry;
using uPLibrary.Networking.M2Mqtt;
using uPLibrary.Networking.M2Mqtt.Messages;

namespace SwarmCamera
{
    /// <summary>
    /// Manages per-drone camera rigs and publishes JPEG frames + metadata over MQTT.
    /// Supports mono FPV (center) and stereo (left/right) camera modes.
    /// Uses round-robin staggered publish to avoid GPU ReadPixels stalls.
    /// </summary>
    public sealed class CameraFramePublisher : MonoBehaviour
    {
        [Header("MQTT")]
        [SerializeField] private string brokerHost = "127.0.0.1";
        [SerializeField] private int brokerPort = 1883;

        [Header("Frame Publishing")]
        [SerializeField] [Range(1f, 30f)] private float targetFrameRate = 10f;
        [SerializeField] [Range(10, 100)] private int jpegQuality = 70;
        [SerializeField] private bool publishEnabled = true;

        [Header("Debug")]
        [SerializeField] private bool logPublishStats;

        private MqttClient client;
        private readonly Dictionary<int, DroneCameraRig> rigs = new Dictionary<int, DroneCameraRig>();
        private readonly Dictionary<int, long> frameCounters = new Dictionary<int, long>();
        private readonly Dictionary<int, long> leftFrameCounters = new Dictionary<int, long>();
        private readonly Dictionary<int, long> rightFrameCounters = new Dictionary<int, long>();
        private readonly Dictionary<int, string> lastPublishedMetaJson = new Dictionary<int, string>();
        private float publishTimer;
        private float metaTimer;
        private bool isQuitting;

        // Round-robin state: tracks which (drone, eye) to publish next
        private int roundRobinSlotIndex;
        private readonly List<(int droneId, int eye)> publishSlots = new List<(int, int)>();
        // eye: 0 = center/mono, 1 = left, 2 = right

        private const float MetaIntervalSec = 1f;
        private const byte QosAtMostOnce = MqttMsgBase.QOS_LEVEL_AT_MOST_ONCE;

        private void Start()
        {
            ConnectMQTT();
        }

        private void Update()
        {
            if (!publishEnabled || client == null || !client.IsConnected)
            {
                return;
            }

            float dt = Time.deltaTime;
            int slotCount = Mathf.Max(publishSlots.Count, 1);
            float frameInterval = 1f / Mathf.Max(targetFrameRate, 1f);

            publishTimer += dt;
            metaTimer += dt;

            // Round-robin: publish one slot per interval/N to avoid GPU readback stalls
            if (publishTimer >= frameInterval / slotCount)
            {
                publishTimer -= frameInterval / slotCount;
                PublishNextSlot();
            }

            // Publish meta at ~1 Hz
            if (metaTimer >= MetaIntervalSec)
            {
                metaTimer -= MetaIntervalSec;
                PublishAllMetas();
            }
        }

        private void RebuildPublishSlots()
        {
            publishSlots.Clear();
            foreach (int droneId in rigs.Keys)
            {
                DroneCameraRig rig = rigs[droneId];
                if (rig == null) continue;

                if (rig.StereoEnabled)
                {
                    // Stereo: publish left and right (skip center to save bandwidth)
                    publishSlots.Add((droneId, 1)); // left
                    publishSlots.Add((droneId, 2)); // right
                }
                else
                {
                    // Mono: publish center camera only
                    publishSlots.Add((droneId, 0)); // center
                }
            }
        }

        private void PublishNextSlot()
        {
            if (publishSlots.Count == 0) return;

            int idx = roundRobinSlotIndex % publishSlots.Count;
            (int droneId, int eye) = publishSlots[idx];
            roundRobinSlotIndex = (roundRobinSlotIndex + 1) % publishSlots.Count;

            if (!rigs.TryGetValue(droneId, out DroneCameraRig rig) || rig == null) return;

            Texture2D frame;
            string suffix;
            long counter;

            switch (eye)
            {
                case 1: // left
                    frame = rig.CaptureFrameLeft();
                    suffix = "/left";
                    break;
                case 2: // right
                    frame = rig.CaptureFrameRight();
                    suffix = "/right";
                    break;
                default: // center (mono)
                    frame = rig.CaptureFrame();
                    suffix = "";
                    break;
            }

            if (frame == null) return;

            byte[] jpegBytes = frame.EncodeToJPG(jpegQuality);
            if (jpegBytes == null || jpegBytes.Length == 0) return;

            PublishFrame(droneId, suffix, jpegBytes);

            // Increment appropriate counter
            if (!frameCounters.ContainsKey(droneId))
                frameCounters[droneId] = 0;
            frameCounters[droneId] = frameCounters[droneId] + 1;
        }

        private void OnApplicationQuit()
        {
            isQuitting = true;
            DisconnectMQTT();
        }

        private void OnDestroy()
        {
            if (!isQuitting)
            {
                DisconnectMQTT();
            }
        }

        /// <summary>
        /// Register a drone for camera frame publishing.
        /// Called from DroneTelemetrySubscriber after drone creation.
        /// </summary>
        public void RegisterDrone(int droneId, DroneTelemetryView droneView)
        {
            if (rigs.ContainsKey(droneId))
            {
                return;
            }

            if (droneView == null)
            {
                Debug.LogWarning("[CameraFramePublisher] Cannot register drone " + droneId + ": null DroneTelemetryView");
                return;
            }

            // Add DroneCameraRig to the drone's GameObject if not already present
            DroneCameraRig rig = droneView.GetComponent<DroneCameraRig>();
            if (rig == null)
            {
                rig = droneView.gameObject.AddComponent<DroneCameraRig>();
            }

            // Auto-wire target tracking so FPV cameras can see the target
            if (rig.LookAtTarget == null)
            {
                SimTarget target = FindObjectOfType<SimTarget>();
                if (target != null)
                {
                    rig.LookAtTarget = target.transform;
                }
            }

            rigs[droneId] = rig;
            frameCounters[droneId] = 0;
            leftFrameCounters[droneId] = 0;
            rightFrameCounters[droneId] = 0;
            lastPublishedMetaJson[droneId] = "";

            RebuildPublishSlots();

            string mode = rig.StereoEnabled ? "STEREO" : "MONO";
            Debug.Log("[CameraFramePublisher] Registered drone " + droneId
                + " (" + mode + " " + rig.RenderWidth + "x" + rig.RenderHeight
                + (rig.StereoEnabled ? " baseline=" + rig.StereoBaselineM + "m" : "")
                + ")");
        }

        /// <summary>
        /// Remove a drone from publishing (e.g. on despawn).
        /// </summary>
        public void UnregisterDrone(int droneId)
        {
            if (!rigs.TryGetValue(droneId, out DroneCameraRig rig))
            {
                return;
            }

            if (rig != null)
            {
                Destroy(rig);
            }

            rigs.Remove(droneId);
            frameCounters.Remove(droneId);
            leftFrameCounters.Remove(droneId);
            rightFrameCounters.Remove(droneId);
            lastPublishedMetaJson.Remove(droneId);

            RebuildPublishSlots();

            Debug.Log("[CameraFramePublisher] Unregistered drone " + droneId);
        }

        /// <summary>
        /// Returns the number of currently registered drones.
        /// </summary>
        public int RegisteredCount => rigs.Count;

        private void ConnectMQTT()
        {
            if (client != null && client.IsConnected)
            {
                return;
            }

            string clientId = "unity-camera-pub-" + Guid.NewGuid().ToString("N").Substring(0, 8);
            client = new MqttClient(brokerHost, brokerPort, false, null, null, MqttSslProtocols.None);
            client.Connect(clientId);

            Debug.Log("[CameraFramePublisher] MQTT connected as " + clientId);
        }

        private void DisconnectMQTT()
        {
            if (client == null)
            {
                return;
            }

            if (client.IsConnected)
            {
                client.Disconnect();
            }
            client = null;
            Debug.Log("[CameraFramePublisher] MQTT disconnected");
        }

        private void PublishFrame(int droneId, string suffix, byte[] jpegBytes)
        {
            if (client == null || !client.IsConnected)
            {
                return;
            }

            string topic = "swarm/cam/drone/" + droneId + suffix + "/frame";
            client.Publish(topic, jpegBytes, QosAtMostOnce, retain: false);

            if (logPublishStats)
            {
                Debug.Log("[CameraFramePublisher] Frame " + topic + " | " + jpegBytes.Length + " bytes");
            }
        }

        private void PublishMeta(int droneId, string metaJson)
        {
            if (client == null || !client.IsConnected)
            {
                return;
            }

            string topic = "swarm/cam/drone/" + droneId + "/meta";
            byte[] payload = Encoding.UTF8.GetBytes(metaJson);
            client.Publish(topic, payload, QosAtMostOnce, retain: false);

            if (logPublishStats)
            {
                Debug.Log("[CameraFramePublisher] Meta " + topic + " | " + metaJson);
            }
        }

        /// <summary>
        /// Build the meta JSON payload for a drone camera.
        /// All position/direction values are in project coordinates.
        /// </summary>
        private string BuildMetaJson(int droneId, DroneCameraRig rig, long frameId, long timestampMs)
        {
            Vector3 pos = rig.GetProjectPosition();
            Vector3 forward = rig.GetProjectForward();
            Vector3 up = rig.GetProjectUp();

            CameraMetaPayload meta = new CameraMetaPayload
            {
                drone_id = droneId,
                frame_id = frameId,
                timestamp_ms = timestampMs,
                width = rig.RenderWidth,
                height = rig.RenderHeight,
                fov_deg = rig.FovDeg,
                pitch_deg = rig.PitchDownDeg,
                cam_pos_xyz = new float[] { pos.x, pos.y, pos.z },
                cam_forward_xyz = new float[] { forward.x, forward.y, forward.z },
                cam_up_xyz = new float[] { up.x, up.y, up.z },
                is_stereo = rig.StereoEnabled,
                baseline_m = rig.StereoEnabled ? rig.StereoBaselineM : 0f
            };

            // Include left/right camera positions when stereo is enabled
            if (rig.StereoEnabled)
            {
                Vector3 leftPos = rig.GetStereoLeftProjectPosition();
                Vector3 rightPos = rig.GetStereoRightProjectPosition();
                meta.left_cam_pos_xyz = new float[] { leftPos.x, leftPos.y, leftPos.z };
                meta.right_cam_pos_xyz = new float[] { rightPos.x, rightPos.y, rightPos.z };
            }

            return JsonUtility.ToJson(meta);
        }

        private void PublishAllMetas()
        {
            long now = CurrentUnixMs();

            foreach (KeyValuePair<int, DroneCameraRig> kv in rigs)
            {
                int droneId = kv.Key;
                DroneCameraRig rig = kv.Value;

                if (rig == null)
                {
                    continue;
                }

                long frameId = frameCounters.ContainsKey(droneId) ? frameCounters[droneId] : 0;
                string metaJson = BuildMetaJson(droneId, rig, frameId, now);

                // Publish on every interval, even if unchanged — group 2 may have just subscribed
                PublishMeta(droneId, metaJson);
                lastPublishedMetaJson[droneId] = metaJson;
            }
        }

        private static long CurrentUnixMs()
        {
            return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        }

        [Serializable]
        private sealed class CameraMetaPayload
        {
            public int drone_id;
            public long frame_id;
            public long timestamp_ms;
            public int width;
            public int height;
            public float fov_deg;
            public float pitch_deg;
            public bool is_stereo;
            public float baseline_m;
            public float[] cam_pos_xyz;
            public float[] cam_forward_xyz;
            public float[] cam_up_xyz;
            public float[] left_cam_pos_xyz;
            public float[] right_cam_pos_xyz;
        }
    }
}
