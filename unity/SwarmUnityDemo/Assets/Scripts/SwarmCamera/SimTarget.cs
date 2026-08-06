using System;
using System.Text;
using UnityEngine;
using uPLibrary.Networking.M2Mqtt;
using uPLibrary.Networking.M2Mqtt.Messages;

namespace SwarmCamera
{
    /// <summary>
    /// Represents a ground target in the simulation scene.
    /// Publishes ground-truth position over MQTT for perception validation.
    /// </summary>
    public sealed class SimTarget : MonoBehaviour
    {
        [Header("Identity")]
        [SerializeField] private int targetId = 1;

        [Header("MQTT")]
        [SerializeField] private string brokerHost = "127.0.0.1";
        [SerializeField] private int brokerPort = 1883;

        [Header("Movement")]
        [SerializeField] private bool enableMovement = true;
        [SerializeField] private float moveSpeed = 1f;
        [SerializeField] private bool loopWaypoints = true;
        [SerializeField] private Vector3[] waypoints = new Vector3[]
        {
            new Vector3(5f, 0f, 0.15f),
            new Vector3(5f, 5f, 0.15f),
            new Vector3(0f, 5f, 0.15f),
            new Vector3(0f, 0f, 0.15f),
            new Vector3(-5f, 0f, 0.15f),
            new Vector3(-5f, -5f, 0.15f),
        };

        [Header("Publishing")]
        [SerializeField] [Range(1f, 30f)] private float publishRateHz = 5f;

        private MqttClient client;
        private float publishTimer;
        private int currentWaypointIndex;
        private bool isQuitting;
        private Vector3 spawnPosition;

        public int TargetId => targetId;
        public bool IsMoving => enableMovement && waypoints != null && waypoints.Length > 0;

        private void Start()
        {
            // Make the target visually prominent for FPV cameras
            transform.localScale = new Vector3(0.8f, 0.8f, 0.8f);
            Renderer r = GetComponent<Renderer>();
            if (r != null)
            {
                Material mat = new Material(Shader.Find("Unlit/Color"));
                if (mat == null) mat = new Material(Shader.Find("Standard"));
                mat.color = Color.red;
                r.sharedMaterial = mat;
            }

            // Apply defaults if not configured in Inspector
            if (waypoints == null || waypoints.Length == 0)
            {
                enableMovement = true;
                transform.position = new Vector3(0f, 0.5f, 0f);
                waypoints = new Vector3[]
                {
                    new Vector3(0f, 0f, 0.5f),
                    new Vector3(3f, 3f, 0.5f),
                    new Vector3(-3f, 3f, 0.5f),
                    new Vector3(-3f, -3f, 0.5f),
                    new Vector3(3f, -3f, 0.5f),
                };
            }

            spawnPosition = transform.position;
            ConnectMQTT();
            publishTimer = 0f;
            currentWaypointIndex = 0;

            if (enableMovement && waypoints.Length > 0)
            {
                // Start at first waypoint
                transform.position = DroneCameraRig.ProjectToUnity(waypoints[0]);
                currentWaypointIndex = 1;
            }
        }

        private void Update()
        {
            float dt = Time.deltaTime;

            // Movement
            if (enableMovement && waypoints.Length > 0)
            {
                MoveAlongWaypoints(dt);
            }

            // Publish loop
            float publishInterval = 1f / Mathf.Max(publishRateHz, 0.1f);
            publishTimer += dt;
            if (publishTimer >= publishInterval)
            {
                publishTimer -= publishInterval;
                PublishGroundTruth();
            }
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

        private void ConnectMQTT()
        {
            if (client != null && client.IsConnected)
            {
                return;
            }

            string clientId = "unity-sim-target-" + Guid.NewGuid().ToString("N").Substring(0, 8);
            client = new MqttClient(brokerHost, brokerPort, false, null, null, MqttSslProtocols.None);
            client.Connect(clientId);

            Debug.Log("[SimTarget] MQTT connected as " + clientId + " | target " + targetId);
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
        }

        /// <summary>
        /// Returns the target's current position in project coordinates [x, y, z].
        /// </summary>
        public Vector3 GetProjectPosition()
        {
            return DroneCameraRig.UnityToProject(transform.position);
        }

        private void PublishGroundTruth()
        {
            if (client == null || !client.IsConnected)
            {
                return;
            }

            Vector3 pos = GetProjectPosition();
            GroundTruthPayload payload = new GroundTruthPayload
            {
                target = targetId,
                position = new float[] { pos.x, pos.y, pos.z },
                timestamp_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
            };

            string json = JsonUtility.ToJson(payload);
            string topic = "swarm/target/" + targetId + "/groundtruth";
            byte[] data = Encoding.UTF8.GetBytes(json);

            client.Publish(topic, data, MqttMsgBase.QOS_LEVEL_AT_MOST_ONCE, retain: false);
        }

        private void MoveAlongWaypoints(float dt)
        {
            if (waypoints.Length == 0)
            {
                return;
            }

            // Clamp index
            if (currentWaypointIndex >= waypoints.Length)
            {
                currentWaypointIndex = loopWaypoints ? 0 : waypoints.Length - 1;
            }
            if (currentWaypointIndex < 0)
            {
                currentWaypointIndex = 0;
            }

            Vector3 targetUnity = DroneCameraRig.ProjectToUnity(waypoints[currentWaypointIndex]);
            Vector3 toTarget = targetUnity - transform.position;
            float distance = toTarget.magnitude;

            if (distance < 0.05f)
            {
                // Snap and advance
                transform.position = targetUnity;
                if (loopWaypoints)
                {
                    currentWaypointIndex = (currentWaypointIndex + 1) % waypoints.Length;
                }
                else if (currentWaypointIndex < waypoints.Length - 1)
                {
                    currentWaypointIndex += 1;
                }
            }
            else
            {
                // Move toward waypoint
                float step = moveSpeed * dt;
                if (step > distance)
                {
                    step = distance;
                }
                transform.position += toTarget.normalized * step;
            }
        }

        private void OnDrawGizmosSelected()
        {
            Gizmos.color = Color.red;

            // Draw waypoint path (waypoints are in project coordinates)
            if (waypoints != null && waypoints.Length > 1)
            {
                for (int i = 0; i < waypoints.Length; i++)
                {
                    Vector3 wp = DroneCameraRig.ProjectToUnity(waypoints[i]);
                    Gizmos.DrawWireSphere(wp, 0.15f);

                    int next = (i + 1) % waypoints.Length;
                    if (loopWaypoints || i < waypoints.Length - 1)
                    {
                        Vector3 nextWp = DroneCameraRig.ProjectToUnity(waypoints[next]);
                        Gizmos.DrawLine(wp, nextWp);
                    }
                }
            }

            // Draw target position
            Gizmos.DrawWireSphere(transform.position, 0.2f);
        }

        [Serializable]
        private sealed class GroundTruthPayload
        {
            public int target;
            public float[] position;
            public long timestamp_ms;
        }
    }
}
