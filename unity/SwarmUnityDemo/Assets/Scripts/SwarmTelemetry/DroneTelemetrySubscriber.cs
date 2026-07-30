using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using uPLibrary.Networking.M2Mqtt;
using uPLibrary.Networking.M2Mqtt.Messages;

namespace SwarmTelemetry
{
    public sealed class DroneTelemetrySubscriber : MonoBehaviour
    {
        [Header("MQTT")]
        [SerializeField] private string brokerHost = "127.0.0.1";
        [SerializeField] private int brokerPort = 1883;
        [SerializeField] private string topicFilter = "swarm/drone/+/telemetry";
        [SerializeField] private string statusTopicFilter = "swarm/commander/status";
        [SerializeField] private string overrideTopicFilter = "swarm/commander/override";
        [SerializeField] private byte qos = MqttMsgBase.QOS_LEVEL_AT_MOST_ONCE;

        [Header("Scene")]
        [SerializeField] private DroneTelemetryView dronePrefab;
        [SerializeField] private Vector3 fallbackDroneScale = new Vector3(0.35f, 0.18f, 0.35f);
        [SerializeField] private float positionScale = 1.0f;
        [SerializeField] private bool useUnityYAsAltitude = true;
        [SerializeField] private int maxMessagesPerFrame = 64;

        private readonly object queueLock = new object();
        private readonly Queue<TelemetryEnvelope> pendingMessages = new Queue<TelemetryEnvelope>();
        private readonly Dictionary<int, DroneTelemetryView> drones = new Dictionary<int, DroneTelemetryView>();

        private MqttClient client;
        private bool isQuitting;

        private void Start()
        {
            Connect();
        }

        private void Update()
        {
            int processed = 0;
            while (processed < maxMessagesPerFrame && TryDequeue(out TelemetryEnvelope envelope))
            {
                processed += 1;
                if (envelope.Topic.StartsWith("swarm/drone/"))
                {
                    HandleTelemetry(envelope);
                }
                else
                {
                    HandleSafetyEvent(envelope);
                }
            }
        }

        private void OnApplicationQuit()
        {
            isQuitting = true;
            Disconnect();
        }

        private void OnDestroy()
        {
            if (!isQuitting)
            {
                Disconnect();
            }
        }

        public void Connect()
        {
            if (client != null && client.IsConnected)
            {
                return;
            }

            client = new MqttClient(brokerHost, brokerPort, false, null, null, MqttSslProtocols.None);
            client.MqttMsgPublishReceived += OnMqttMessageReceived;

            string clientId = "unity-telemetry-" + Guid.NewGuid().ToString("N");
            client.Connect(clientId);
            client.Subscribe(
                new[] { topicFilter, statusTopicFilter, overrideTopicFilter },
                new[] { qos, qos, qos });

            Debug.Log("Subscribed to MQTT telemetry topic: " + topicFilter);
        }

        public void Disconnect()
        {
            if (client == null)
            {
                return;
            }

            client.MqttMsgPublishReceived -= OnMqttMessageReceived;
            if (client.IsConnected)
            {
                client.Disconnect();
            }
            client = null;
        }

        private void OnMqttMessageReceived(object sender, MqttMsgPublishEventArgs args)
        {
            string payload = Encoding.UTF8.GetString(args.Message);
            lock (queueLock)
            {
                pendingMessages.Enqueue(new TelemetryEnvelope(args.Topic, payload));
            }
        }

        private bool TryDequeue(out TelemetryEnvelope envelope)
        {
            lock (queueLock)
            {
                if (pendingMessages.Count == 0)
                {
                    envelope = default(TelemetryEnvelope);
                    return false;
                }

                envelope = pendingMessages.Dequeue();
                return true;
            }
        }

        private void HandleTelemetry(TelemetryEnvelope envelope)
        {
            TelemetryPayload payload;
            try
            {
                payload = JsonUtility.FromJson<TelemetryPayload>(envelope.Payload);
            }
            catch (Exception exc)
            {
                Debug.LogWarning("Failed to parse telemetry JSON: " + exc.Message);
                return;
            }

            if (payload == null)
            {
                return;
            }

            int droneId = payload.drone > 0 ? payload.drone : DroneIdFromTopic(envelope.Topic);
            if (droneId <= 0)
            {
                Debug.LogWarning("Telemetry did not include a drone id: " + envelope.Topic);
                return;
            }

            Vector3 projectPosition = ProjectPosition(payload);
            Vector3 unityPosition = ToUnityPosition(projectPosition);

            DroneTelemetryView drone = GetOrCreateDrone(droneId);
            drone.ApplyTelemetry(
                droneId,
                unityPosition,
                payload.status,
                payload.last_command_id,
                payload.timestamp_ms);
        }

        private void HandleSafetyEvent(TelemetryEnvelope envelope)
        {
            SafetyPayload payload;
            try
            {
                payload = JsonUtility.FromJson<SafetyPayload>(envelope.Payload);
            }
            catch (Exception exc)
            {
                Debug.LogWarning("Failed to parse safety JSON: " + exc.Message);
                return;
            }

            if (payload == null || payload.drone <= 0)
            {
                return;
            }

            DroneTelemetryView drone = GetOrCreateDrone(payload.drone);
            string mode = string.IsNullOrEmpty(payload.mode) ? payload.status : payload.mode;
            if (string.IsNullOrEmpty(mode) && payload.event_name == "safety_override")
            {
                mode = "override";
            }
            drone.ApplySafetyState(mode, payload.risk_level);
        }

        private Vector3 ProjectPosition(TelemetryPayload payload)
        {
            if (payload.position != null && payload.position.Length >= 3)
            {
                return new Vector3(payload.position[0], payload.position[1], payload.position[2]);
            }

            return new Vector3(payload.x, payload.y, payload.z);
        }

        private Vector3 ToUnityPosition(Vector3 projectPosition)
        {
            if (useUnityYAsAltitude)
            {
                return new Vector3(
                    projectPosition.x * positionScale,
                    projectPosition.z * positionScale,
                    projectPosition.y * positionScale);
            }

            return projectPosition * positionScale;
        }

        private DroneTelemetryView GetOrCreateDrone(int droneId)
        {
            if (drones.TryGetValue(droneId, out DroneTelemetryView existing))
            {
                return existing;
            }

            DroneTelemetryView drone;
            if (dronePrefab != null)
            {
                drone = Instantiate(dronePrefab);
            }
            else
            {
                GameObject fallback = GameObject.CreatePrimitive(PrimitiveType.Capsule);
                fallback.transform.localScale = fallbackDroneScale;
                drone = fallback.AddComponent<DroneTelemetryView>();
            }

            drone.name = "Drone " + droneId;
            drone.SetBaseColor(FallbackColor(droneId));
            drones.Add(droneId, drone);
            return drone;
        }

        private static int DroneIdFromTopic(string topic)
        {
            string[] parts = topic.Split('/');
            for (int i = 0; i < parts.Length - 1; i += 1)
            {
                if (parts[i] == "drone" && int.TryParse(parts[i + 1], out int droneId))
                {
                    return droneId;
                }
            }

            return -1;
        }

        private static Color FallbackColor(int droneId)
        {
            Color[] palette =
            {
                new Color(0.1f, 0.45f, 0.95f),
                new Color(0.95f, 0.2f, 0.16f),
                new Color(0.13f, 0.62f, 0.35f),
                new Color(0.95f, 0.72f, 0.18f)
            };
            return palette[Mathf.Abs(droneId - 1) % palette.Length];
        }

        private struct TelemetryEnvelope
        {
            public readonly string Topic;
            public readonly string Payload;

            public TelemetryEnvelope(string topic, string payload)
            {
                Topic = topic;
                Payload = payload;
            }
        }

        [Serializable]
        private sealed class TelemetryPayload
        {
            public int drone;
            public float x;
            public float y;
            public float z;
            public float[] position;
            public string status;
            public string last_command_id;
            public long timestamp_ms;
        }

        [Serializable]
        private sealed class SafetyPayload
        {
            public int drone;
            public string event_name;
            public string mode;
            public string status;
            public float risk_level;
            public int target_drone;
            public long timestamp_ms;
        }
    }
}
