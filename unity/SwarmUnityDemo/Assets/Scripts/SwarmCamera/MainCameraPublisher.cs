using System;
using System.Text;
using UnityEngine;
using uPLibrary.Networking.M2Mqtt;
using uPLibrary.Networking.M2Mqtt.Messages;

namespace SwarmCamera
{
    /// <summary>
    /// Captures the Unity Main Camera view and publishes it over MQTT
    /// so Group 2 can see the overview alongside drone FPV feeds.
    /// Attach to SwarmCameraManager or any scene GameObject.
    /// </summary>
    public sealed class MainCameraPublisher : MonoBehaviour
    {
        [Header("MQTT")]
        [SerializeField] private string brokerHost = "127.0.0.1";
        [SerializeField] private int brokerPort = 1883;

        [Header("Capture")]
        [SerializeField] [Range(1f, 15f)] private float publishRateHz = 5f;
        [SerializeField] [Range(10, 100)] private int jpegQuality = 70;
        [SerializeField] private int outputWidth = 640;
        [SerializeField] private int outputHeight = 360;

        private MqttClient client;
        private Camera mainCamera;
        private RenderTexture renderTexture;
        private Texture2D readbackTexture;
        private float publishTimer;
        private bool isQuitting;

        private void Start()
        {
            mainCamera = Camera.main;
            if (mainCamera == null)
            {
                Debug.LogWarning("[MainCameraPublisher] No MainCamera found in scene.");
                enabled = false;
                return;
            }

            renderTexture = new RenderTexture(outputWidth, outputHeight, 24, RenderTextureFormat.ARGB32);
            renderTexture.name = "MainCamRT";
            renderTexture.Create();

            readbackTexture = new Texture2D(outputWidth, outputHeight, TextureFormat.RGB24, false);
            readbackTexture.name = "MainCamReadback";

            ConnectMQTT();
        }

        private void Update()
        {
            if (client == null || !client.IsConnected || mainCamera == null) return;

            float interval = 1f / Mathf.Max(publishRateHz, 0.1f);
            publishTimer += Time.deltaTime;
            if (publishTimer < interval) return;
            publishTimer -= interval;

            PublishMainCameraFrame();
        }

        private void PublishMainCameraFrame()
        {
            // Render main camera to our RT
            RenderTexture previousTarget = mainCamera.targetTexture;
            mainCamera.targetTexture = renderTexture;
            mainCamera.Render();
            mainCamera.targetTexture = previousTarget;

            // Read back
            RenderTexture previousActive = RenderTexture.active;
            RenderTexture.active = renderTexture;
            readbackTexture.ReadPixels(new Rect(0, 0, outputWidth, outputHeight), 0, 0);
            readbackTexture.Apply();
            RenderTexture.active = previousActive;

            byte[] jpeg = readbackTexture.EncodeToJPG(jpegQuality);
            if (jpeg == null || jpeg.Length == 0) return;

            client.Publish("swarm/cam/main/frame", jpeg, MqttMsgBase.QOS_LEVEL_AT_MOST_ONCE, retain: false);
        }

        private void ConnectMQTT()
        {
            if (client != null && client.IsConnected) return;
            string clientId = "unity-maincam-" + Guid.NewGuid().ToString("N").Substring(0, 8);
            client = new MqttClient(brokerHost, brokerPort, false, null, null, MqttSslProtocols.None);
            client.Connect(clientId);
            Debug.Log("[MainCameraPublisher] Connected as " + clientId);
        }

        private void DisconnectMQTT()
        {
            if (client == null) return;
            if (client.IsConnected) client.Disconnect();
            client = null;
        }

        private void OnApplicationQuit()
        {
            isQuitting = true;
            DisconnectMQTT();
        }

        private void OnDestroy()
        {
            if (!isQuitting) DisconnectMQTT();
            if (renderTexture != null) { renderTexture.Release(); Destroy(renderTexture); }
            if (readbackTexture != null) Destroy(readbackTexture);
        }
    }
}
