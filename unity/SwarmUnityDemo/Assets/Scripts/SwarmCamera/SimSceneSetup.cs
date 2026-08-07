#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using SwarmTelemetry;

namespace SwarmCamera
{
    /// <summary>
    /// Editor utility to one-click set up the simulation scene
    /// with ground plane, target, and camera manager.
    /// </summary>
    public static class SimSceneSetup
    {
        private const string MenuRoot = "SafeDrones/";

        [MenuItem(MenuRoot + "Setup Simulation Scene", false, 10)]
        private static void SetupSimulationScene()
        {
            SetupSimulationSceneInternal();

            Selection.activeObject = GameObject.Find("SwarmCameraManager");

            Scene scene = SceneManager.GetActiveScene();
            EditorSceneManager.MarkSceneDirty(scene);
            EditorSceneManager.SaveScene(scene, scene.path);
            AssetDatabase.SaveAssets();

            Debug.Log("[SimSceneSetup] Scene setup complete and saved.");
        }

        /// <summary>
        /// Public entry point for batch mode (-executeMethod).
        /// Creates all simulation objects and saves the scene.
        /// </summary>
        public static void SetupSimulationSceneBatch()
        {
            // Explicitly open the target scene — batch mode may start with an empty scene.
            const string scenePath = "Assets/Scenes/SampleScene.unity";
            Scene scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);

            SetupSimulationSceneInternal();

            EditorSceneManager.MarkSceneDirty(scene);
            bool saved = EditorSceneManager.SaveScene(scene, scenePath);
            AssetDatabase.SaveAssets();

            Debug.Log("[SimSceneSetup] Batch scene setup complete. Saved=" + saved + " path=" + scenePath);
        }

        private static void SetupSimulationSceneInternal()
        {
            // 1. Create or find ground plane (20x20m, gray)
            CreateOrReusePlane("GroundPlane", 20f, Color.gray);

            // 2. 当前设计只分析无人机间碰撞，没有地面目标；
            //    不再创建 TargetTerrorist（红色会干扰红方无人机检测）。

            // 3. Create or find SwarmCameraManager with CameraFramePublisher
            CreateOrReuseCameraManager("SwarmCameraManager");

            // 4. Ensure a DroneTelemetrySubscriber exists in the scene
            EnsureDroneTelemetrySubscriber();
        }

        [MenuItem(MenuRoot + "Remove Simulation Objects", false, 11)]
        private static void RemoveSimulationObjects()
        {
            DestroyImmediateSafe("GroundPlane");
            DestroyImmediateSafe("TargetTerrorist");
            DestroyImmediateSafe("SwarmCameraManager");
            Debug.Log("[SimSceneSetup] Simulation objects removed.");
        }

        private static void CreateOrReusePlane(string name, float size, Color color)
        {
            GameObject existing = GameObject.Find(name);
            if (existing != null)
            {
                Debug.Log("[SimSceneSetup] Using existing " + name);
                return;
            }

            GameObject plane = GameObject.CreatePrimitive(PrimitiveType.Plane);
            plane.name = name;
            plane.transform.position = Vector3.zero;

            // Plane primitive is 10x10 units, scale to desired size
            float scale = size / 10f;
            plane.transform.localScale = new Vector3(scale, 1f, scale);

            Renderer renderer = plane.GetComponent<Renderer>();
            if (renderer != null)
            {
                Material mat = new Material(Shader.Find("Standard"));
                mat.color = color;
                renderer.sharedMaterial = mat;
            }

            Debug.Log("[SimSceneSetup] Created " + name);
        }

        private static void CreateOrReuseTarget(string name, Color color, float diameter)
        {
            GameObject existing = GameObject.Find(name);
            if (existing != null)
            {
                Object.DestroyImmediate(existing);
                Debug.Log("[SimSceneSetup] Destroyed existing " + name + " to recreate with latest defaults");
            }

            // Use a sphere instead of capsule — more visible from all angles
            GameObject target = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            target.name = name;
            // Place centrally so drones can see it from their positions
            target.transform.position = new Vector3(0f, 0.5f, 0f);
            float d = 0.8f; // bigger size for FPV visibility
            target.transform.localScale = new Vector3(d, d, d);

            Renderer renderer = target.GetComponent<Renderer>();
            if (renderer != null)
            {
                // Use Unlit shader — always bright, no lighting dependence
                Material mat = new Material(Shader.Find("Unlit/Color"));
                if (mat == null)
                {
                    mat = new Material(Shader.Find("Standard"));
                    mat.color = color;
                    mat.EnableKeyword("_EMISSION");
                    mat.SetColor("_EmissionColor", color * 0.8f);
                }
                else
                {
                    mat.color = color;
                }
                renderer.sharedMaterial = mat;
            }

            // Ensure SimTarget component is attached
            if (target.GetComponent<SimTarget>() == null)
            {
                target.AddComponent<SimTarget>();
            }

            Debug.Log("[SimSceneSetup] Created " + name);
        }

        private static void CreateOrReuseCameraManager(string name)
        {
            GameObject existing = GameObject.Find(name);
            if (existing != null)
            {
                // Ensure MainCameraPublisher is added (newly added script)
                if (existing.GetComponent<MainCameraPublisher>() == null)
                {
                    existing.AddComponent<MainCameraPublisher>();
                    Debug.Log("[SimSceneSetup] Added MainCameraPublisher to existing " + name);
                }
                Debug.Log("[SimSceneSetup] Using existing " + name);
                return;
            }

            GameObject manager = new GameObject(name);
            manager.AddComponent<CameraFramePublisher>();
            manager.AddComponent<MainCameraPublisher>();

            Debug.Log("[SimSceneSetup] Created " + name);
        }

        private static void EnsureDroneTelemetrySubscriber()
        {
            DroneTelemetrySubscriber existing = Object.FindObjectOfType<DroneTelemetrySubscriber>();
            if (existing != null)
            {
                Debug.Log("[SimSceneSetup] DroneTelemetrySubscriber already exists: " + existing.name);

                // Auto-wire camera publisher if not already set
                CameraFramePublisher publisher = Object.FindObjectOfType<CameraFramePublisher>();
                if (publisher != null)
                {
                    SerializedObject so = new SerializedObject(existing);
                    SerializedProperty prop = so.FindProperty("cameraPublisher");
                    if (prop != null && prop.objectReferenceValue == null)
                    {
                        prop.objectReferenceValue = publisher;
                        so.ApplyModifiedProperties();
                        Debug.Log("[SimSceneSetup] Auto-wired CameraPublisher on DroneTelemetrySubscriber");
                    }
                }
                return;
            }

            GameObject go = new GameObject("TelemetrySubscriber");
            go.AddComponent<DroneTelemetrySubscriber>();

            CameraFramePublisher pub = Object.FindObjectOfType<CameraFramePublisher>();
            if (pub != null)
            {
                SerializedObject so = new SerializedObject(go.GetComponent<DroneTelemetrySubscriber>());
                SerializedProperty prop = so.FindProperty("cameraPublisher");
                if (prop != null)
                {
                    prop.objectReferenceValue = pub;
                    so.ApplyModifiedProperties();
                }
            }

            Debug.Log("[SimSceneSetup] Created TelemetrySubscriber with auto-wired CameraPublisher");
        }

        private static void DestroyImmediateSafe(string objectName)
        {
            GameObject obj = GameObject.Find(objectName);
            if (obj != null)
            {
                Object.DestroyImmediate(obj);
                Debug.Log("[SimSceneSetup] Destroyed " + objectName);
            }
        }
    }
}
#endif
