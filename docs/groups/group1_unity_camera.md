# 组 1：Unity 相机链路与仿真场景

## 任务目标

在 Unity 仿真中给每架虚拟无人机挂载前视 FPV 相机，把画面以 JPEG 帧经 MQTT 发出，并随路发布相机内外参元数据；同时搭建感知目标（地面红色目标）并发布其 ground truth，供组 2 标定反解误差。

本组交付后，组 2 不需要打开 Unity 就能拿到按约定 schema 发布的视频流与相机参数。

## 前置阅读

- 总体路线：`docs/plan.md` 文末"后续路线"第一节
- Unity 项目路径：`unity/SwarmUnityDemo`
- 现有遥测链路：`Assets/Scripts/SwarmTelemetry/DroneTelemetrySubscriber.cs`（无人机由 `GetOrCreateDrone()` 动态生成）
- 坐标系约定：项目坐标 `[x, y, z]`（z 为高度）→ Unity 坐标 `(x, z, y)`；**对外发布的所有位姿必须转换回项目坐标系**

## 接口约定（与组 2、组 3 的契约，不得单方面改动）

| Topic | 方向 | 内容 |
|-------|------|------|
| `swarm/cam/drone/{id}/frame` | Unity → Python | JPEG 原始字节，QoS 0，不 retain，默认 10 Hz |
| `swarm/cam/drone/{id}/meta` | Unity → Python | JSON 元数据，约 1 Hz + 参数变化时 |
| `swarm/target/1/groundtruth` | Unity → Python | 目标项目坐标 JSON，约 5 Hz |

meta 字段：

```json
{
  "drone_id": 1,
  "frame_id": 1234,
  "timestamp_ms": 1720000000000,
  "width": 640,
  "height": 360,
  "fov_deg": 70.0,
  "pitch_deg": 15.0,
  "cam_pos_xyz": [1.0, 2.0, 1.5],
  "cam_forward_xyz": [0.97, 0.0, -0.26],
  "cam_up_xyz": [0.26, 0.0, 0.97]
}
```

groundtruth 字段：

```json
{"target": 1, "position": [3.0, -1.0, 0.0], "timestamp_ms": 1720000000000}
```

## 任务拆解

1. **新建 `Assets/Scripts/SwarmCamera/DroneCameraRig.cs`**
   - 在无人机 GameObject 下创建子物体 `FpvCamera`，本地位置 `(0, 0, 0.15)`（机头前方）。
   - 俯仰角可序列化配置，默认下俯 15°；FOV 默认 70°；近裁剪 0.05m。
   - 渲染到 `RenderTexture`（默认 640×360，可配置）；关闭 AudioListener；depth 低于 Main Camera。
2. **新建 `CameraFramePublisher.cs`**（挂在场景新物体 `SwarmCameraManager`）
   - 自带一个只发布的 `MqttClient`（与遥测订阅的 client 分开）。
   - 可配置帧率（默认 10 Hz）、JPEG 质量（默认 70）、分辨率。
   - 按约定发布 frame 与 meta；meta 位姿转回项目坐标系。
   - 暴露 `RegisterDrone(droneId, DroneTelemetryView)`。
3. **最小改动 `DroneTelemetrySubscriber.cs`**
   - 增加 `cameraPublisher` 引用和 `attachFpvCamera` 开关（默认开）；`GetOrCreateDrone()` 创建无人机后注册。
4. **新建 `SimTarget.cs` + 场景搭建**
   - 地面 Plane（约 20×20m，灰色）；红色 Capsule 目标 `TargetTerrorist`（直径约 0.3m，贴地）。
   - 目标静止或简单轨迹移动（可开关）；按约定发布 groundtruth。
   - 优先提供 Editor 菜单一键生成脚本 `SimSceneSetup.cs`，避免手改场景 YAML。

## 验收标准

- Unity Console 无红色 Error；Play 后 broker 上能抓到 10 Hz 的 frame 和 meta。
- meta 中 `cam_pos_xyz` 与对应无人机 telemetry 位置一致（机头偏移除外）。
- 目标移动时 groundtruth 持续刷新。
- 组 2 用 Python 订阅端能解码出正常画面（联合验收）。

## 运行环境

- Unity 项目打开方式见 `docs/unity_demo_from_zero.md`。
- 本地 broker：`conda run -n eai-swarm python scripts/dev_broker.py`。
- 调试用 MQTT Explorer 观察 `swarm/cam/#` 和 `swarm/target/#`。

## 产出物

- `unity/SwarmUnityDemo/Assets/Scripts/SwarmCamera/` 下 4 个脚本（含 .meta）
- `DroneTelemetrySubscriber.cs` 的小幅改动
- 场景中的 Plane、`TargetTerrorist`、`SwarmCameraManager`
