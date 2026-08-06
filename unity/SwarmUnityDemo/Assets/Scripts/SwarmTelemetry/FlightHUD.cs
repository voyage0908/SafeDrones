using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

namespace SwarmTelemetry
{
    /// <summary>
    /// Simple runtime HUD showing drone telemetry and flight controls.
    /// Uses Unity's immediate GUI for zero-setup display.
    /// </summary>
    public sealed class FlightHUD : MonoBehaviour
    {
        [Header("Display")]
        [SerializeField] private bool showHUD = true;
        [SerializeField] private int fontSize = 14;
        [SerializeField] private Color panelColor = new Color(0f, 0f, 0f, 0.7f);
        [SerializeField] private Color textColor = Color.white;
        [SerializeField] private Color highlightColor = new Color(0.2f, 0.8f, 0.3f);
        [SerializeField] private Color warningColor = new Color(0.95f, 0.72f, 0.18f);
        [SerializeField] private Color dangerColor = new Color(0.95f, 0.2f, 0.16f);

        [Header("Quick Commands")]
        [SerializeField] private string gatewayUrl = "http://127.0.0.1:8000";

        private DroneTelemetrySubscriber subscriber;
        private readonly List<DroneInfo> droneList = new List<DroneInfo>();
        private string statusMessage = "";
        private float statusMessageTime;
        private int selectedDroneId = 1;

        private Rect panelRect;
        private GUIStyle labelStyle;
        private GUIStyle boldStyle;
        private GUIStyle buttonStyle;
        private bool stylesBuilt;

        private class DroneInfo
        {
            public int id;
            public Vector3 position;
            public string status;
            public string lastCommandId;
            public long timestampMs;
            public string safetyMode;
            public float riskLevel;
        }

        private void Awake()
        {
            subscriber = FindObjectOfType<DroneTelemetrySubscriber>();
        }

        private void Update()
        {
            RefreshDroneList();
        }

        private void RefreshDroneList()
        {
            droneList.Clear();
            foreach (var view in FindObjectsOfType<DroneTelemetryView>())
            {
                if (!view.gameObject.activeInHierarchy) continue;

                droneList.Add(new DroneInfo
                {
                    id = view.DroneId,
                    position = view.transform.position,
                    status = view.Status,
                    lastCommandId = view.LastCommandId,
                    timestampMs = view.TimestampMs
                });
            }

            // Sort by drone id
            droneList.Sort((a, b) => a.id.CompareTo(b.id));
        }

        // Toggle with H key
        private void OnGUI()
        {
            if (!showHUD) return;

            BuildStyles();

            float panelWidth = 280f;
            float panelHeight = Screen.height * 0.45f;
            panelRect = new Rect(10f, 10f, panelWidth, panelHeight);

            // Background panel
            Texture2D bg = new Texture2D(1, 1);
            bg.SetPixel(0, 0, panelColor);
            bg.Apply();
            GUI.DrawTexture(panelRect, bg);

            GUILayout.BeginArea(panelRect);
            GUILayout.Space(6f);

            // Title
            GUILayout.BeginHorizontal();
            GUILayout.Space(8f);
            GUILayout.Label("SWARM TELEMETRY", boldStyle);
            GUILayout.FlexibleSpace();
            string connectedStr = subscriber != null ? "● LIVE" : "○ IDLE";
            Color oldColor = GUI.color;
            GUI.color = subscriber != null ? highlightColor : warningColor;
            GUILayout.Label(connectedStr, labelStyle);
            GUI.color = oldColor;
            GUILayout.Space(8f);
            GUILayout.EndHorizontal();

            // Divider
            GUILayout.Label("".PadRight(40, '─'), labelStyle);

            // Drone list
            GUILayout.BeginHorizontal();
            GUILayout.Space(8f);
            GUILayout.Label("DRONE", boldStyle, GUILayout.Width(60f));
            GUILayout.Label("X", boldStyle, GUILayout.Width(50f));
            GUILayout.Label("Y", boldStyle, GUILayout.Width(50f));
            GUILayout.Label("Z", boldStyle, GUILayout.Width(50f));
            GUILayout.Label("STATUS", boldStyle);
            GUILayout.EndHorizontal();

            GUILayout.Space(4f);

            foreach (var drone in droneList)
            {
                GUILayout.BeginHorizontal();
                GUILayout.Space(8f);

                Color rowColor = GetDroneColor(drone.id);
                GUI.color = rowColor;
                GUILayout.Label("#" + drone.id, labelStyle, GUILayout.Width(56f));
                GUI.color = textColor;
                GUILayout.Label(drone.position.x.ToString("F1"), labelStyle, GUILayout.Width(50f));
                GUILayout.Label(drone.position.y.ToString("F1"), labelStyle, GUILayout.Width(50f));
                GUILayout.Label(drone.position.z.ToString("F1"), labelStyle, GUILayout.Width(50f));

                string safetyIcon = "●";
                if (drone.safetyMode == "override") { safetyIcon = "⚠"; GUI.color = dangerColor; }
                else if (drone.safetyMode == "warning") { safetyIcon = "▲"; GUI.color = warningColor; }
                else { GUI.color = highlightColor; }
                GUILayout.Label(safetyIcon + " " + drone.status, labelStyle);
                GUI.color = textColor;
                GUILayout.EndHorizontal();
            }

            if (droneList.Count == 0)
            {
                GUILayout.BeginHorizontal();
                GUILayout.Space(8f);
                GUILayout.Label("(waiting for drones...)", labelStyle);
                GUILayout.EndHorizontal();
            }

            // Divider
            GUILayout.Label("".PadRight(40, '─'), labelStyle);

            // Quick command section
            GUILayout.BeginHorizontal();
            GUILayout.Space(8f);
            GUILayout.Label("QUICK COMMAND", boldStyle);
            GUILayout.EndHorizontal();

            GUILayout.BeginHorizontal();
            GUILayout.Space(8f);
            GUILayout.Label("Drone:", labelStyle, GUILayout.Width(45f));
            string idText = GUILayout.TextField(selectedDroneId.ToString(), GUILayout.Width(40f));
            if (int.TryParse(idText, out int parsedId) && parsedId > 0)
            {
                selectedDroneId = parsedId;
            }
            GUILayout.EndHorizontal();

            GUILayout.BeginHorizontal();
            GUILayout.Space(8f);
            if (GUILayout.Button("Hover [0,0,1]", buttonStyle))
            {
                SendCommand(selectedDroneId, 0, 0, 1);
            }
            if (GUILayout.Button("Scout L", buttonStyle))
            {
                SendCommand(selectedDroneId, -5, 0, 1);
            }
            if (GUILayout.Button("Scout R", buttonStyle))
            {
                SendCommand(selectedDroneId, 5, 0, 1);
            }
            GUILayout.EndHorizontal();

            GUILayout.BeginHorizontal();
            GUILayout.Space(8f);
            if (GUILayout.Button("Forward [0,5,1]", buttonStyle))
            {
                SendCommand(selectedDroneId, 0, 5, 1);
            }
            if (GUILayout.Button("Back [0,-5,1]", buttonStyle))
            {
                SendCommand(selectedDroneId, 0, -5, 1);
            }
            if (GUILayout.Button("Up [0,0,3]", buttonStyle))
            {
                SendCommand(selectedDroneId, 0, 0, 3);
            }
            GUILayout.EndHorizontal();

            // Status message
            if (!string.IsNullOrEmpty(statusMessage) && Time.time < statusMessageTime)
            {
                GUILayout.Space(4f);
                GUILayout.BeginHorizontal();
                GUILayout.Space(8f);
                GUI.color = highlightColor;
                GUILayout.Label(statusMessage, labelStyle);
                GUI.color = textColor;
                GUILayout.EndHorizontal();
            }

            GUILayout.EndArea();

            // Keyboard hint
            Rect hintRect = new Rect(10f, Screen.height - 25f, 280f, 20f);
            GUI.Label(hintRect, "H: toggle HUD  |  M: toggle minimap", labelStyle);
        }

        private void SendCommand(int droneId, float x, float y, float z)
        {
            string json = $"{{\"drone\":{droneId},\"waypoint\":[{x:F1},{y:F1},{z:F1}]}}";
            statusMessage = $"→ Drone {droneId} → ({x:F1}, {y:F1}, {z:F1})";
            statusMessageTime = Time.time + 3f;

            // Non-blocking HTTP POST
            StartCoroutine(SendCommandCoroutine(json));
        }

        private System.Collections.IEnumerator SendCommandCoroutine(string json)
        {
            byte[] body = System.Text.Encoding.UTF8.GetBytes(json);
            var request = new UnityEngine.Networking.UnityWebRequest(
                gatewayUrl + "/api/direct-command", "POST");
            request.uploadHandler = new UnityEngine.Networking.UploadHandlerRaw(body);
            request.downloadHandler = new UnityEngine.Networking.DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json");

            yield return request.SendWebRequest();

            if (request.result == UnityEngine.Networking.UnityWebRequest.Result.Success)
            {
                Debug.Log("Command sent: " + json);
            }
            else
            {
                statusMessage = "✗ Send failed";
                statusMessageTime = Time.time + 2f;
                Debug.LogWarning("Command failed: " + request.error);
            }
        }

        private Color GetDroneColor(int droneId)
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

        private void BuildStyles()
        {
            if (stylesBuilt) return;
            stylesBuilt = true;

            labelStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = fontSize,
                normal = { textColor = textColor },
                alignment = TextAnchor.MiddleLeft
            };

            boldStyle = new GUIStyle(labelStyle)
            {
                fontStyle = FontStyle.Bold
            };

            buttonStyle = new GUIStyle(GUI.skin.button)
            {
                fontSize = fontSize - 2,
                padding = new RectOffset(6, 6, 4, 4)
            };
        }

        private void UpdateDroneSafety(int droneId, string mode, float risk)
        {
            foreach (var d in droneList)
            {
                if (d.id == droneId)
                {
                    d.safetyMode = mode;
                    d.riskLevel = risk;
                    break;
                }
            }
        }
    }
}
