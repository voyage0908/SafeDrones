using UnityEngine;

namespace SwarmTelemetry
{
    public sealed class DroneTelemetryView : MonoBehaviour
    {
        [SerializeField] private float smoothing = 8.0f;
        [SerializeField] private bool rotateTowardMotion = true;
        [SerializeField] private Color normalColor = new Color(0.1f, 0.45f, 0.95f);
        [SerializeField] private Color warningColor = new Color(0.95f, 0.72f, 0.18f);
        [SerializeField] private Color overrideColor = new Color(0.95f, 0.2f, 0.16f);
        [SerializeField] private float warningRiskThreshold = 0.32f;
        [SerializeField] private float safetyStateHoldSec = 2.0f;

        private Vector3 targetPosition;
        private bool hasTarget;
        private Renderer cachedRenderer;
        private float safetyStateUntil;
        private string safetyMode;

        public int DroneId { get; private set; }
        public string Status { get; private set; }
        public string LastCommandId { get; private set; }
        public long TimestampMs { get; private set; }

        private void Awake()
        {
            targetPosition = transform.position;
            Status = "unknown";
            LastCommandId = "";
            cachedRenderer = GetComponentInChildren<Renderer>();
            safetyMode = "normal";

            // Auto-add minimap icon if not present
            if (GetComponentInChildren<MinimapDroneIcon>() == null)
            {
                gameObject.AddComponent<MinimapDroneIcon>();
            }
        }

        public void ApplyTelemetry(
            int droneId,
            Vector3 position,
            string status,
            string lastCommandId,
            long timestampMs)
        {
            DroneId = droneId;
            targetPosition = position;
            Status = string.IsNullOrEmpty(status) ? "unknown" : status;
            LastCommandId = string.IsNullOrEmpty(lastCommandId) ? "" : lastCommandId;
            TimestampMs = timestampMs;
            hasTarget = true;
        }

        public void SetBaseColor(Color color)
        {
            normalColor = color;
            ApplyColor(color);
            // Also color the minimap icon if present
            var icon = GetComponentInChildren<MinimapDroneIcon>();
            if (icon != null) icon.SetColor(color);
        }

        public void ApplySafetyState(string mode, float riskLevel)
        {
            safetyMode = string.IsNullOrEmpty(mode) ? "normal" : mode;
            safetyStateUntil = Time.time + safetyStateHoldSec;

            if (safetyMode == "override" || safetyMode == "safety_override")
            {
                ApplyColor(overrideColor);
                return;
            }

            if (safetyMode == "warning" || riskLevel >= warningRiskThreshold)
            {
                ApplyColor(warningColor);
                return;
            }

            ApplyColor(normalColor);
        }

        private void Update()
        {
            if (safetyMode != "normal" && Time.time > safetyStateUntil)
            {
                safetyMode = "normal";
                ApplyColor(normalColor);
            }

            if (!hasTarget)
            {
                return;
            }

            Vector3 previous = transform.position;
            float t = 1.0f - Mathf.Exp(-smoothing * Time.deltaTime);
            transform.position = Vector3.Lerp(previous, targetPosition, t);

            if (Vector3.Distance(transform.position, targetPosition) < 0.001f)
            {
                transform.position = targetPosition;
            }

            Vector3 delta = transform.position - previous;
            if (rotateTowardMotion && delta.sqrMagnitude > 0.000001f)
            {
                // 只用水平分量决定朝向：垂直机动（如出生点瞬移）不应让机体俯仰，
                // 否则机载相机的 meta 位姿会带上虚假的倾角。
                Vector3 horizontalDelta = new Vector3(delta.x, 0.0f, delta.z);
                if (horizontalDelta.sqrMagnitude > 0.000001f)
                {
                    transform.rotation = Quaternion.LookRotation(horizontalDelta.normalized, Vector3.up);
                }
            }
        }

        private void ApplyColor(Color color)
        {
            if (cachedRenderer == null)
            {
                cachedRenderer = GetComponentInChildren<Renderer>();
            }

            if (cachedRenderer != null)
            {
                cachedRenderer.material.color = color;
            }
        }
    }
}
