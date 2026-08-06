# Group 2 — 双目摄像头 (Stereo Camera) 验收文档

## 概述

在 Unity 仿真中为每架无人机挂载双目摄像头（左眼 + 右眼），通过 MQTT 发布立体画面对，供下游模块（立体匹配、深度估计）使用。

---

## 架构

```
┌─ Unity Drone ─────────────────────────────┐
│  DroneCameraRig (enableStereo = true)     │
│  ├─ FpvCamera_L  (RenderTexture L)        │
│  └─ FpvCamera_R  (RenderTexture R)        │
│           │ JPEG Encode                    │
│           ▼                                │
│  CameraFramePublisher (Round-Robin)        │
└──────────────────┬─────────────────────────┘
                   │ MQTT
                   ▼
┌─ Python Viewer (group2_camera_viewer.py) ─┐
│  paho-mqtt → cv2.imdecode → hstack(L,R)   │
│  Vertical stacking: MainCam + 3× Stereo    │
└────────────────────────────────────────────┘
```

---

## MQTT 话题设计

### 帧话题

| 模式 | Topic | QoS | 说明 |
|------|-------|-----|------|
| 单目（兼容旧版） | `swarm/cam/drone/{id}/frame` | 0 | 中心相机 JPEG |
| **双目左眼** | `swarm/cam/drone/{id}/left/frame` | 0 | 左眼 JPEG |
| **双目右眼** | `swarm/cam/drone/{id}/right/frame` | 0 | 右眼 JPEG |
| 主相机 | `swarm/cam/main/frame` | 0 | Unity Main Camera JPEG |

### 元数据话题

`swarm/cam/drone/{id}/meta` (QoS 0, 1 Hz)

```json
{
  "drone_id": 1,
  "frame_id": 120,
  "timestamp_ms": 1722900000000,
  "width": 640,
  "height": 360,
  "fov_deg": 70.0,
  "pitch_deg": 15.0,
  "is_stereo": true,
  "baseline_m": 0.12,
  "cam_pos_xyz": [1.5, 0.5, 2.0],
  "cam_forward_xyz": [0.0, 0.0, -1.0],
  "cam_up_xyz": [0.0, 1.0, 0.0],
  "left_cam_pos_xyz": [1.44, 0.5, 2.0],
  "right_cam_pos_xyz": [1.56, 0.5, 2.0]
}
```

新增字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `is_stereo` | bool | 是否为双目模式 |
| `baseline_m` | float | 瞳距（基线长度，米） |
| `left_cam_pos_xyz` | float[3] | 左相机世界坐标（项目坐标系） |
| `right_cam_pos_xyz` | float[3] | 右相机世界坐标（项目坐标系） |

### 目标真值话题

`swarm/target/{id}/groundtruth` (QoS 0, 5 Hz)

---

## Unity 参数配置

### DroneCameraRig (Inspector)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `enableStereo` | `true` | 启用双目模式 |
| `stereoBaselineM` | `0.12` | 瞳距（米），范围 0.02–0.5 |
| `renderWidth` | `640` | 单眼分辨率宽度 |
| `renderHeight` | `360` | 单眼分辨率高度 |
| `fovDeg` | `70` | 视场角（度） |
| `pitchDownDeg` | `15` | 俯角（度） |
| `localPosition` | `(0, 0, 0.15)` | 相机相对无人机的位置 |

### CameraFramePublisher (Inspector)

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `targetFrameRate` | `10` | 每眼目标帧率 (Hz) |
| `jpegQuality` | `70` | JPEG 压缩质量 (10–100) |

### 发布调度

- Round-Robin 轮转：3 架无人机 × 2 眼 = 6 个槽位
- 每帧发布一个槽位，避免 GPU ReadPixels 阻塞
- 实际每眼帧率 ≈ targetFrameRate（10 Hz）
- 双目模式下跳过中心相机以节省带宽

---

## Python 查看器

### 运行

```bash
conda activate eai-swarm
python scripts/group2_camera_viewer.py
```

### 参数

| 参数 | 说明 |
|------|------|
| `--drone-id N` | 仅显示指定无人机（弹出独立窗口） |
| `--save-frames` | 每 30 帧保存到 `logs/frames/` |
| `--broker-host` | MQTT Broker 地址（默认 127.0.0.1） |
| `--broker-port` | MQTT Broker 端口（默认 1883） |

### 窗口布局

```
┌─ MAIN CAMERA ────────────────────────────┐
│  (Unity 主相机画面, 1280×N)               │
├─ DRONE 1 (STEREO L|R) ───────────────────┤
│  L眼            │分割线│  R眼              │
├─ DRONE 2 (STEREO L|R) ───────────────────┤
│  L眼            │分割线│  R眼              │
├─ DRONE 3 (STEREO L|R) ───────────────────┤
│  L眼            │分割线│  R眼              │
└───────────────────────────────────────────┘
```

- 所有面板统一缩放到 1280px 宽
- 纵向堆叠，可滚动
- 双目画面：左眼 | 右眼 并排 + 黄色分割线 + `L`/`R` 标签
- 覆盖文字：无人机 ID、STEREO 标识、FOV、基线距离、位置、帧计数
- 按 `ESC` 退出

---

## 文件清单

```
unity/SwarmUnityDemo/Assets/Scripts/SwarmCamera/
├── DroneCameraRig.cs          ← 双目硬件层 (enableStereo, 左右子相机)
├── CameraFramePublisher.cs    ← MQTT 发布 (left/right 话题, meta 扩展)
├── SimTarget.cs               ← 目标真值发布
├── SimSceneSetup.cs           ← 编辑器场景搭建
└── MainCameraPublisher.cs     ← 主相机发布

scripts/
└── group2_camera_viewer.py    ← Python OpenCV 查看器 (支持双目)

docs/groups/
├── group1_unity_camera.md     ← Group 1 任务文档
└── group2_stereo_camera.md    ← 本文档
```

---

## 坐标系约定

项目坐标系 `[x, y, z]` ↔ Unity 坐标系 `(x, z, y)`：
- 项目 x = Unity x
- 项目 y = Unity z（高度）
- 项目 z = Unity y

`cam_pos_xyz`、`left_cam_pos_xyz`、`right_cam_pos_xyz` 均使用项目坐标系。

---

## 验证步骤

1. 启动 Mosquitto Broker：
   ```bash
   mosquitto -v -p 1883
   ```

2. 启动 Mock 无人机（3 个终端）：
   ```bash
   python mock_drone.py --drone-id 1 --interval 0.1
   python mock_drone.py --drone-id 2 --interval 0.1
   python mock_drone.py --drone-id 3 --interval 0.1
   ```

3. Unity 进入 Play 模式

4. 运行查看器：
   ```bash
   conda activate eai-swarm
   python scripts/group2_camera_viewer.py
   ```

5. 确认：
   - [ ] 每架无人机显示为双目画面（左 | 右）
   - [ ] Meta 数据中 `is_stereo: true`、`baseline_m` 非零
   - [ ] 左/右相机位置 `left_cam_pos_xyz` ≠ `right_cam_pos_xyz`（差值 ≈ baseline）
   - [ ] 目标真值持续更新
   - [ ] 主相机画面正常

---

## 更新记录

| 日期 | 内容 |
|------|------|
| 2026-08-06 | 初始版本：Mono FPV 相机 + Main Camera |
| 2026-08-06 | 添加双目摄像头：left/right 话题、Python 查看器适配、文档 |
