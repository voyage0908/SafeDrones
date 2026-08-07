using System.Collections;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace SwarmTelemetry
{
    /// <summary>
    /// Fetches Google Street View static images and displays them as a full-screen
    /// overlay panel. Useful for getting a ground-level view of the drone's position.
    ///
    /// Press "S" to toggle street view.
    /// Use arrow keys or mouse drag to rotate the view (changes heading/pitch).
    /// </summary>
    public sealed class StreetViewPanel : MonoBehaviour
    {
        [Header("Google API")]
        [SerializeField] private string googleApiKey = "";
        [SerializeField] private int imageWidth = 640;
        [SerializeField] private int imageHeight = 480;

        [Header("View Settings")]
        [SerializeField] private float heading = 0f;       // 0-360, 0=North, 90=East
        [SerializeField] private float pitch = 0f;          // -90 to 90, 0=level
        [SerializeField] private int fov = 90;              // 10-120
        [SerializeField] private float rotateSpeed = 60f;   // degrees/sec for arrow keys

        [Header("Follow Drone")]
        [SerializeField] private bool followSelectedDrone = true;
        [SerializeField] private int followDroneId = 1;

        [Header("UI")]
        [SerializeField] private bool showOnStart = false;
        [SerializeField] private KeyCode toggleKey = KeyCode.S;
        [SerializeField] private Color loadingColor = new Color(0.1f, 0.1f, 0.12f, 1f);

        private RawImage displayImage;
        private GameObject panelRoot;
        private Text infoText;
        private bool isVisible;
        private string lastQueryUrl;
        private bool isLoading;

        // Faux GPS origin (arbitrary anchor for local coords -> lat/lng)
        private const double OriginLat = 31.2304;   // Suzhou area
        private const double OriginLng = 120.6311;
        private const double MetersPerDegLat = 111320.0;
        private double MetersPerDegLng => 111320.0 * System.Math.Cos(OriginLat * System.Math.PI / 180.0);

        private void Awake()
        {
            CreateUI();
            panelRoot.SetActive(showOnStart);
            isVisible = showOnStart;

            if (string.IsNullOrEmpty(googleApiKey))
            {
                Debug.LogWarning("StreetViewPanel: Google API Key not set. " +
                    "Set it in the Inspector or paste from drone-navigation client/config.json.");
            }
        }

        private void CreateUI()
        {
            // Root panel
            panelRoot = new GameObject("StreetViewPanel");
            panelRoot.transform.SetParent(transform, false);
            Canvas canvas = panelRoot.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 100;

            CanvasScaler scaler = panelRoot.AddComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1920, 1080);

            panelRoot.AddComponent<GraphicRaycaster>();

            // Background image
            GameObject imgGo = new GameObject("StreetViewImage");
            imgGo.transform.SetParent(panelRoot.transform, false);
            displayImage = imgGo.AddComponent<RawImage>();
            displayImage.color = loadingColor;
            RectTransform imgRt = displayImage.GetComponent<RectTransform>();
            imgRt.anchorMin = Vector2.zero;
            imgRt.anchorMax = Vector2.one;
            imgRt.offsetMin = Vector2.zero;
            imgRt.offsetMax = Vector2.zero;

            // Loading text
            GameObject textGo = new GameObject("InfoText");
            textGo.transform.SetParent(panelRoot.transform, false);
            infoText = textGo.AddComponent<Text>();
            infoText.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            infoText.fontSize = 20;
            infoText.color = Color.white;
            infoText.alignment = TextAnchor.LowerLeft;
            RectTransform textRt = infoText.GetComponent<RectTransform>();
            textRt.anchorMin = new Vector2(0f, 0f);
            textRt.anchorMax = new Vector2(0.5f, 0.1f);
            textRt.offsetMin = new Vector2(15f, 15f);
            textRt.offsetMax = new Vector2(0f, 0f);

            // Close button
            GameObject closeGo = new GameObject("CloseButton");
            closeGo.transform.SetParent(panelRoot.transform, false);
            Image closeBg = closeGo.AddComponent<Image>();
            closeBg.color = new Color(0f, 0f, 0f, 0.6f);
            Button closeBtn = closeGo.AddComponent<Button>();
            closeBtn.onClick.AddListener(() => ToggleVisibility(false));
            RectTransform closeRt = closeGo.GetComponent<RectTransform>();
            closeRt.anchorMin = new Vector2(1f, 1f);
            closeRt.anchorMax = new Vector2(1f, 1f);
            closeRt.pivot = new Vector2(1f, 1f);
            closeRt.sizeDelta = new Vector2(40f, 40f);
            closeRt.anchoredPosition = new Vector2(-10f, -10f);

            Text closeLabel = new GameObject("X").AddComponent<Text>();
            closeLabel.transform.SetParent(closeGo.transform, false);
            closeLabel.text = "✕";
            closeLabel.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            closeLabel.fontSize = 22;
            closeLabel.color = Color.white;
            closeLabel.alignment = TextAnchor.MiddleCenter;
            closeLabel.GetComponent<RectTransform>().sizeDelta = new Vector2(40f, 40f);
        }

        private void Update()
        {
            if (Input.GetKeyDown(toggleKey))
            {
                ToggleVisibility(!isVisible);
            }

            if (!isVisible) return;

            // Rotation controls
            if (Input.GetKey(KeyCode.LeftArrow))  { heading -= rotateSpeed * Time.deltaTime; }
            if (Input.GetKey(KeyCode.RightArrow)) { heading += rotateSpeed * Time.deltaTime; }
            if (Input.GetKey(KeyCode.UpArrow))    { pitch = Mathf.Min(pitch + rotateSpeed * 0.5f * Time.deltaTime, 90f); }
            if (Input.GetKey(KeyCode.DownArrow))  { pitch = Mathf.Max(pitch - rotateSpeed * 0.5f * Time.deltaTime, -90f); }

            // Normalize heading
            heading = (heading + 360f) % 360f;

            // Mouse drag to rotate
            if (Input.GetMouseButton(0))
            {
                heading += Input.GetAxis("Mouse X") * 2f;
                pitch   -= Input.GetAxis("Mouse Y") * 1.5f;
                pitch    = Mathf.Clamp(pitch, -90f, 90f);
                heading  = (heading + 360f) % 360f;
                UpdateInfoText();
                RequestStreetView();
            }

            // Follow drone position
            if (followSelectedDrone && !isLoading)
            {
                var drone = FindDrone(followDroneId);
                if (drone != null && Time.frameCount % 60 == 0) // update every 60 frames
                {
                    RequestStreetView();
                }
            }
        }

        private DroneTelemetryView FindDrone(int droneId)
        {
            foreach (var view in FindObjectsOfType<DroneTelemetryView>())
            {
                if (view.DroneId == droneId && view.gameObject.activeInHierarchy)
                {
                    return view;
                }
            }
            return null;
        }

        private void ToggleVisibility(bool show)
        {
            isVisible = show;
            panelRoot.SetActive(show);

            if (show && !isLoading)
            {
                RequestStreetView();
            }
        }

        public void RequestStreetView()
        {
            double lat, lng;
            GetCurrentLatLng(out lat, out lng);

            string url = BuildStreetViewUrl(lat, lng, heading, pitch, fov);
            if (url == lastQueryUrl) return; // avoid duplicate requests

            lastQueryUrl = url;
            StartCoroutine(LoadStreetViewImage(url));
        }

        private void GetCurrentLatLng(out double lat, out double lng)
        {
            if (followSelectedDrone)
            {
                var drone = FindDrone(followDroneId);
                if (drone != null)
                {
                    Vector3 pos = drone.transform.position;
                    // Unity coords: X=right, Z=forward; project coords: X=forward, Y=left
                    // Convert Unity position to approximate lat/lng
                    lng = OriginLng + (pos.x / MetersPerDegLng);
                    lat = OriginLat + (pos.z / MetersPerDegLat);
                    return;
                }
            }
            lat = OriginLat;
            lng = OriginLng;
        }

        private string BuildStreetViewUrl(double lat, double lng, float h, float p, int f)
        {
            // Google Street View Image API (static)
            // https://developers.google.com/maps/documentation/streetview
            return $"https://maps.googleapis.com/maps/api/streetview" +
                   $"?size={imageWidth}x{imageHeight}" +
                   $"&location={lat:F6},{lng:F6}" +
                   $"&heading={h:F1}&pitch={p:F1}&fov={f}" +
                   $"&key={googleApiKey}";
        }

        private IEnumerator LoadStreetViewImage(string url)
        {
            isLoading = true;
            UpdateInfoText();

            using (UnityWebRequest req = UnityWebRequestTexture.GetTexture(url))
            {
                req.timeout = 10;
                yield return req.SendWebRequest();

                if (req.result == UnityWebRequest.Result.Success)
                {
                    Texture2D tex = DownloadHandlerTexture.GetContent(req);
                    displayImage.texture = tex;
                    displayImage.color = Color.white;
                }
                else
                {
                    // If street view not available at this location, show placeholder
                    Debug.LogWarning($"Street View not available at this location. " +
                        $"Error: {req.error}");
                    displayImage.color = loadingColor;
                }
            }

            isLoading = false;
            UpdateInfoText();
        }

        private void UpdateInfoText()
        {
            if (infoText == null) return;

            double lat, lng;
            GetCurrentLatLng(out lat, out lng);

            string droneLabel = followSelectedDrone ? $"Drone #{followDroneId}" : "Fixed";
            string loadingLabel = isLoading ? " [Loading...]" : "";
            infoText.text = $"{droneLabel} " +
                $"| Lat: {lat:F4} Lng: {lng:F4} " +
                $"| Heading: {heading:F0}° | Pitch: {pitch:F0}° " +
                $"| ← → ↑ ↓ rotate | S toggle" +
                loadingLabel;
        }

        public void SetApiKey(string key)
        {
            googleApiKey = key;
            Debug.Log("StreetViewPanel: API key set.");
        }
    }
}
