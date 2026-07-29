using UnityEngine;

namespace SwarmTelemetry
{
    public sealed class DroneTelemetryView : MonoBehaviour
    {
        [SerializeField] private float smoothing = 8.0f;
        [SerializeField] private bool rotateTowardMotion = true;

        private Vector3 targetPosition;
        private bool hasTarget;

        public int DroneId { get; private set; }
        public string Status { get; private set; }
        public string LastCommandId { get; private set; }
        public long TimestampMs { get; private set; }

        private void Awake()
        {
            targetPosition = transform.position;
            Status = "unknown";
            LastCommandId = "";
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

        private void Update()
        {
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
                transform.rotation = Quaternion.LookRotation(delta.normalized, Vector3.up);
            }
        }
    }
}
