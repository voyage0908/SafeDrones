using System.Collections.Generic;
using UnityEngine;

namespace SwarmTelemetry
{
    /// <summary>
    /// Renders a top-down minimap in the corner of the screen.
    /// Attach to a second Camera in the scene.
    /// </summary>
    public sealed class MinimapCamera : MonoBehaviour
    {
        [Header("Viewport")]
        [SerializeField] private Vector2 anchor = new Vector2(1f, 1f);
        [SerializeField] private Vector2 size = new Vector2(0.22f, 0.28f);
        [SerializeField] private Vector2 offset = new Vector2(-10f, -10f);

        [Header("Map")]
        [SerializeField] private float followHeight = 25f;
        [SerializeField] private float mapRadius = 12f;
        [SerializeField] private bool followCenterOfMass = true;

        [Header("Visual")]
        [SerializeField] private Color backgroundColor = new Color(0.08f, 0.08f, 0.12f, 0.92f);
        [SerializeField] private Color gridColor = new Color(0.25f, 0.30f, 0.40f, 0.6f);
        [SerializeField] private float gridSpacing = 5f;

        private Camera minimapCam;
        private DroneTelemetrySubscriber subscriber;

        private void Awake()
        {
            minimapCam = GetComponent<Camera>();
            if (minimapCam == null)
            {
                minimapCam = gameObject.AddComponent<Camera>();
            }

            minimapCam.orthographic = true;
            minimapCam.orthographicSize = mapRadius;
            minimapCam.clearFlags = CameraClearFlags.SolidColor;
            minimapCam.backgroundColor = backgroundColor;
            minimapCam.depth = 10f; // render on top
            minimapCam.cullingMask = LayerMask.GetMask("Default");
            minimapCam.rect = new Rect(
                1f - size.x + offset.x / Screen.width,
                1f - size.y + offset.y / Screen.height,
                size.x,
                size.y);

            subscriber = FindObjectOfType<DroneTelemetrySubscriber>();
        }

        private void LateUpdate()
        {
            Vector3 center = Vector3.zero;

            if (followCenterOfMass && subscriber != null)
            {
                var drones = GetActiveDrones();
                if (drones.Count > 0)
                {
                    foreach (var d in drones)
                    {
                        center += d.position;
                    }
                    center /= drones.Count;
                }
            }

            transform.position = new Vector3(center.x, followHeight, center.z);
            transform.rotation = Quaternion.Euler(90f, 0f, 0f);

            // Keep viewport stable regardless of screen size
            minimapCam.rect = new Rect(
                anchor.x - size.x + offset.x / Screen.width,
                anchor.y - size.y + offset.y / Screen.height,
                size.x,
                size.y);
        }

        private List<Transform> GetActiveDrones()
        {
            var list = new List<Transform>();
            foreach (var view in FindObjectsOfType<DroneTelemetryView>())
            {
                if (view.gameObject.activeInHierarchy)
                {
                    list.Add(view.transform);
                }
            }
            return list;
        }

        private void OnDrawGizmosSelected()
        {
            Gizmos.color = gridColor;
            Vector3 origin = transform.position;
            origin.y = 0f;

            for (float x = -mapRadius; x <= mapRadius; x += gridSpacing)
            {
                Vector3 a = origin + new Vector3(x, 0f, -mapRadius);
                Vector3 b = origin + new Vector3(x, 0f, mapRadius);
                Gizmos.DrawLine(a, b);
            }
            for (float z = -mapRadius; z <= mapRadius; z += gridSpacing)
            {
                Vector3 a = origin + new Vector3(-mapRadius, 0f, z);
                Vector3 b = origin + new Vector3(mapRadius, 0f, z);
                Gizmos.DrawLine(a, b);
            }
        }
    }
}
