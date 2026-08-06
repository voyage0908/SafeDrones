using UnityEngine;

namespace SwarmTelemetry
{
    /// <summary>
    /// Renders a flat icon above each drone visible in the minimap camera.
    /// Attach as a child GameObject to every Drone.
    /// </summary>
    public sealed class MinimapDroneIcon : MonoBehaviour
    {
        [Header("Icon")]
        [SerializeField] private float iconSize = 1.2f;
        [SerializeField] private Color iconColor = new Color(0.1f, 0.45f, 0.95f, 0.9f);
        [SerializeField] private Color directionColor = Color.white;

        private Transform body;
        private Transform arrow;
        private Renderer bodyRenderer;
        private Renderer arrowRenderer;

        private void Awake()
        {
            CreateIcon();
        }

        private void CreateIcon()
        {
            // Body (flat quad facing up, visible from top-down camera)
            body = GameObject.CreatePrimitive(PrimitiveType.Quad).transform;
            body.name = "MinimapBody";
            body.SetParent(transform, false);
            body.localPosition = new Vector3(0f, 3.5f, 0f); // float high above drone
            body.localRotation = Quaternion.Euler(90f, 0f, 0f); // face up
            body.localScale = Vector3.one * iconSize;
            Destroy(body.GetComponent<Collider>());

            bodyRenderer = body.GetComponent<Renderer>();
            bodyRenderer.material = new Material(Shader.Find("Unlit/Color"));
            bodyRenderer.material.color = iconColor;

            // Direction arrow (small triangle pointing forward)
            arrow = GameObject.CreatePrimitive(PrimitiveType.Quad).transform;
            arrow.name = "MinimapArrow";
            arrow.SetParent(body, false);
            arrow.localPosition = new Vector3(0f, 0.001f, 0.45f * iconSize);
            arrow.localRotation = Quaternion.Euler(90f, 0f, 0f);
            arrow.localScale = new Vector3(0.22f, 0.4f, 1f) * iconSize;
            Destroy(arrow.GetComponent<Collider>());

            arrowRenderer = arrow.GetComponent<Renderer>();
            arrowRenderer.material = new Material(Shader.Find("Unlit/Color"));
            arrowRenderer.material.color = directionColor;
        }

        public void SetColor(Color color)
        {
            if (bodyRenderer != null)
            {
                bodyRenderer.material.color = color;
            }
        }

        public void SetScale(float scale)
        {
            if (body != null)
            {
                body.localScale = Vector3.one * scale;
            }
        }
    }
}
