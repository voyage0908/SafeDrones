using UnityEngine;

namespace SwarmTelemetry
{
    /// <summary>
    /// Auto-configures the StreetViewPanel with the Google API key.
    /// Attach this to SwarmTelemetryManager alongside StreetViewPanel.
    /// </summary>
    public sealed class StreetViewSetup : MonoBehaviour
    {
        [Header("Google API Key (from drone-navigation client/config.json)")]
        [SerializeField] private string googleApiKey = "AIzaSyAQehckc3Z2mo4mfxua0sI57_WgbJrNc-g";

        private void Start()
        {
            var streetView = GetComponent<StreetViewPanel>();
            if (streetView == null)
            {
                streetView = FindObjectOfType<StreetViewPanel>();
            }

            if (streetView != null && !string.IsNullOrEmpty(googleApiKey))
            {
                streetView.SetApiKey(googleApiKey);
                Debug.Log("StreetViewSetup: API key configured.");
            }
            else
            {
                Debug.LogWarning("StreetViewSetup: No StreetViewPanel found or API key is empty.");
            }
        }
    }
}
