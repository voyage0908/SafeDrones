using UnityEngine;
using SwarmTelemetry;

namespace SwarmCamera
{
    /// <summary>
    /// Attaches an FPV camera (mono or stereo) to a drone GameObject.
    /// Renders to configurable RenderTextures for frame capture.
    /// </summary>
    [RequireComponent(typeof(DroneTelemetryView))]
    public sealed class DroneCameraRig : MonoBehaviour
    {
        [Header("Camera Placement")]
        // 相机必须位于机体前方且不看到自身外壳（胶囊半长 ~0.175m），
        // 否则自体会出现在画面里并被感知端误检为其他无人机。
        [SerializeField] private Vector3 localPosition = new Vector3(0f, 0.05f, 0.3f);
        [SerializeField] [Range(-90f, 0f)] private float pitchDownDeg = 5f;
        [SerializeField] [Range(30f, 120f)] private float fovDeg = 70f;
        [SerializeField] private float nearClipPlane = 0.05f;
        [SerializeField] private float farClipPlane = 1000f;

        [Header("Stereo")]
        [SerializeField] private bool enableStereo = true;
        [Tooltip("Inter-camera distance in meters (baseline).")]
        [SerializeField] [Range(0.02f, 0.5f)] private float stereoBaselineM = 0.12f;

        [Header("Render Target")]
        [SerializeField] private int renderWidth = 640;
        [SerializeField] private int renderHeight = 360;
        [SerializeField] private int cameraDepth = -1;

        [Header("Tracking")]
        [Tooltip("If set, camera looks at this target when drone is stationary.")]
        [SerializeField] private Transform lookAtTarget;

        [Header("Drone Reference")]
        [SerializeField] private DroneTelemetryView droneView;

        // Mono camera (center, always created)
        private Camera fpvCamera;
        private RenderTexture renderTexture;
        private Texture2D readbackTexture;
        private GameObject cameraObject;

        // Stereo cameras
        private Camera leftCamera;
        private Camera rightCamera;
        private RenderTexture leftRenderTexture;
        private RenderTexture rightRenderTexture;
        private Texture2D leftReadbackTexture;
        private Texture2D rightReadbackTexture;
        private GameObject leftCameraObject;
        private GameObject rightCameraObject;

        private Vector3 lastDronePosition;
        private bool droneIsMoving;

        // --- Public accessors ---

        public Camera FpvCamera => fpvCamera;
        public RenderTexture RenderTexture => renderTexture;
        public int RenderWidth => renderWidth;
        public int RenderHeight => renderHeight;
        public float FovDeg => fovDeg;
        public float PitchDownDeg => pitchDownDeg;
        public Transform CameraTransform => fpvCamera != null ? fpvCamera.transform : null;
        public Transform LookAtTarget { get => lookAtTarget; set => lookAtTarget = value; }
        public bool StereoEnabled => enableStereo;
        public float StereoBaselineM => stereoBaselineM;
        public Camera LeftCamera => leftCamera;
        public Camera RightCamera => rightCamera;

        // --- Unity lifecycle ---

        private void Awake()
        {
            droneView = GetComponent<DroneTelemetryView>();
            lastDronePosition = transform.position;
            CreateCameraRig();
        }

        private void LateUpdate()
        {
            Vector3 currentPos = transform.position;
            droneIsMoving = Vector3.Distance(currentPos, lastDronePosition) > 0.001f;
            lastDronePosition = currentPos;

            // 相机始终跟随机体朝向（机头方向 × 固定俯仰角）。
            // 不再支持静止时瞄准 lookAtTarget：当前设计只看空中目标，
            // FPV 语义要求相机朝向与机头一致，否则 meta 位姿不可信。
        }

        private void OnDestroy()
        {
            DestroyTexture(readbackTexture);
            DestroyRT(renderTexture);
            DestroyTexture(leftReadbackTexture);
            DestroyRT(leftRenderTexture);
            DestroyTexture(rightReadbackTexture);
            DestroyRT(rightRenderTexture);
        }

        // --- Frame capture ---

        public Texture2D CaptureFrame()
        {
            return CaptureFromCamera(fpvCamera, renderTexture, ref readbackTexture);
        }

        public Texture2D CaptureFrameLeft()
        {
            return CaptureFromCamera(leftCamera, leftRenderTexture, ref leftReadbackTexture);
        }

        public Texture2D CaptureFrameRight()
        {
            return CaptureFromCamera(rightCamera, rightRenderTexture, ref rightReadbackTexture);
        }

        // --- Project coordinate helpers ---

        public Vector3 GetProjectPosition()
        {
            Vector3 pos = fpvCamera != null ? fpvCamera.transform.position : transform.position;
            return UnityToProject(pos);
        }

        public Vector3 GetProjectForward()
        {
            Vector3 fwd = fpvCamera != null ? fpvCamera.transform.forward : transform.forward;
            return UnityToProjectDirection(fwd);
        }

        public Vector3 GetProjectUp()
        {
            Vector3 up = fpvCamera != null ? fpvCamera.transform.up : transform.up;
            return UnityToProjectDirection(up);
        }

        /// <summary>Returns left camera position in project coordinates.</summary>
        public Vector3 GetStereoLeftProjectPosition()
        {
            Vector3 pos = leftCamera != null ? leftCamera.transform.position : transform.position;
            return UnityToProject(pos);
        }

        /// <summary>Returns right camera position in project coordinates.</summary>
        public Vector3 GetStereoRightProjectPosition()
        {
            Vector3 pos = rightCamera != null ? rightCamera.transform.position : transform.position;
            return UnityToProject(pos);
        }

        // --- Static coordinate conversion ---

        public static Vector3 UnityToProject(Vector3 unityPos)
        {
            return new Vector3(unityPos.x, unityPos.z, unityPos.y);
        }

        public static Vector3 ProjectToUnity(Vector3 projectPos)
        {
            return new Vector3(projectPos.x, projectPos.z, projectPos.y);
        }

        private static Vector3 UnityToProjectDirection(Vector3 unityDir)
        {
            return new Vector3(unityDir.x, unityDir.z, unityDir.y);
        }

        // --- Internal ---

        private void CreateCameraRig()
        {
            // Mono center camera
            cameraObject = CreateChildCamera("FpvCamera", Vector3.zero, out fpvCamera);
            renderTexture = CreateRT("FpvCam_" + gameObject.name);
            fpvCamera.targetTexture = renderTexture;

            // Stereo
            if (enableStereo)
            {
                float half = stereoBaselineM * 0.5f;
                leftCameraObject = CreateChildCamera("FpvCamera_L", new Vector3(-half, 0f, 0f), out leftCamera);
                rightCameraObject = CreateChildCamera("FpvCamera_R", new Vector3(+half, 0f, 0f), out rightCamera);

                leftRenderTexture = CreateRT("FpvCam_L_" + gameObject.name);
                rightRenderTexture = CreateRT("FpvCam_R_" + gameObject.name);
                leftCamera.targetTexture = leftRenderTexture;
                rightCamera.targetTexture = rightRenderTexture;

                Debug.Log("[DroneCameraRig] Stereo rig created on " + gameObject.name
                    + " | baseline=" + stereoBaselineM + "m | " + renderWidth + "x" + renderHeight);
            }
            else
            {
                Debug.Log("[DroneCameraRig] Mono rig created on " + gameObject.name
                    + " | " + renderWidth + "x" + renderHeight + " @ " + fovDeg + "° FOV");
            }
        }

        private GameObject CreateChildCamera(string childName, Vector3 offset, out Camera cam)
        {
            Transform existing = transform.Find(childName);
            GameObject obj;
            if (existing != null)
            {
                obj = existing.gameObject;
                cam = obj.GetComponent<Camera>();
                if (cam == null) cam = obj.AddComponent<Camera>();
            }
            else
            {
                obj = new GameObject(childName);
                obj.transform.SetParent(transform, worldPositionStays: false);
                cam = obj.AddComponent<Camera>();
            }

            obj.transform.localPosition = localPosition + offset;
            obj.transform.localRotation = Quaternion.Euler(pitchDownDeg, 0f, 0f);

            cam.fieldOfView = fovDeg;
            cam.nearClipPlane = nearClipPlane;
            cam.farClipPlane = farClipPlane;
            cam.depth = cameraDepth;
            cam.clearFlags = CameraClearFlags.Skybox;

            AudioListener al = obj.GetComponent<AudioListener>();
            if (al != null) Destroy(al);

            return obj;
        }

        private RenderTexture CreateRT(string name)
        {
            RenderTexture rt = new RenderTexture(renderWidth, renderHeight, 24, RenderTextureFormat.ARGB32);
            rt.name = name;
            rt.Create();
            return rt;
        }

        private Texture2D CaptureFromCamera(Camera cam, RenderTexture rt, ref Texture2D tex)
        {
            if (cam == null || rt == null) return null;
            EnsureReadbackTexture(ref tex);
            RenderTexture prevActive = RenderTexture.active;
            RenderTexture.active = rt;
            tex.ReadPixels(new Rect(0f, 0f, renderWidth, renderHeight), 0, 0);
            tex.Apply();
            RenderTexture.active = prevActive;
            return tex;
        }

        private void EnsureReadbackTexture(ref Texture2D tex)
        {
            if (tex != null && tex.width == renderWidth && tex.height == renderHeight) return;
            if (tex != null) Destroy(tex);
            tex = new Texture2D(renderWidth, renderHeight, TextureFormat.RGB24, false);
            tex.name = "Readback_" + gameObject.name;
        }

        private void PointCameraAt(Camera cam, Vector3 worldTarget)
        {
            Vector3 dir = (worldTarget - cam.transform.position).normalized;
            if (dir.sqrMagnitude > 0.001f)
            {
                Quaternion rot = Quaternion.LookRotation(dir, Vector3.up);
                cam.transform.rotation = rot * Quaternion.Euler(pitchDownDeg, 0f, 0f);
            }
        }

        private static void DestroyTexture(Texture2D t) { if (t != null) Destroy(t); }
        private static void DestroyRT(RenderTexture rt) { if (rt != null) { rt.Release(); Destroy(rt); } }
    }
}
