# 组 2：Python 感知链路（检测与坐标反解）

## 任务目标

订阅组 1 发布的相机帧与元数据，完成目标检测和像素坐标 → 场地物理坐标的反解，把目标位置发布回 MQTT，供组 3 的 Ego 模式 Benchmark 和后续 LLM 战术决策消费。

## 前置阅读

- 总体路线：`docs/plan.md` 文末"后续路线"第一节
- 环境约定：`AGENTS.md`（一律使用 `conda run -n eai-swarm`）
- 现有遥测 schema：`mock_drone.py`、`swarm/mqtt_gateway.py`

## 接口约定（与组 1、组 3 的契约，不得单方面改动）

输入（来自组 1）：

- `swarm/cam/drone/{id}/frame`：JPEG 原始字节，约 10 Hz
- `swarm/cam/drone/{id}/meta`：`drone_id, frame_id, timestamp_ms, width, height, fov_deg, pitch_deg, cam_pos_xyz, cam_forward_xyz, cam_up_xyz`（项目坐标系，z 向上）
- `swarm/target/1/groundtruth`：`{"target": 1, "position": [x, y, z], "timestamp_ms": ...}`（用于误差标定）
- `swarm/drone/+/telemetry`：现有无人机遥测（作为空中无人机反解的 ground truth）

输出（供组 3、组 4 消费）：

```json
// swarm/target/1/position
{"target": 1, "position": [3.0, -1.0, 0.0], "pixel": [320, 240],
 "source": "sim_cam", "camera_drone": 1, "timestamp_ms": 1720000000000}

// swarm/drone_seen/{id}/position
{"target": 2, "position": [2.0, 1.0, 1.5], "pixel": [300, 180],
 "estimated_depth": 4.2, "source": "sim_cam", "camera_drone": 1,
 "timestamp_ms": 1720000000000}
```

发布频率不低于 5 Hz。

## 任务拆解

1. **依赖**：`requirements.txt` 增加 `opencv-python>=4.10`；`conda run -n eai-swarm pip install opencv-python`。
2. **新建 `swarm/perception.py`**（纯函数模块，不依赖 MQTT，便于单测）：
   - `camera_intrinsics(width, height, fov_deg)` → fx, fy, cx, cy
   - `pixel_to_ray(u, v, intrinsics)` → 相机系单位方向向量
   - `ray_to_ground(cam_pos, cam_forward, cam_up, ray, ground_z=0.0)`：射线-地平面求交；射线水平或向上返回 None
   - `depth_from_apparent_size(pixel_width, real_width_m, fx)`：单目测深（机体宽度 0.35m）
   - `detect_color_blob(frame_bgr, hsv_range)`：HSV 阈值分割，返回质心、包围盒宽度、面积
   - 颜色范围：地面目标 = 红色；各无人机 = 组 1 `FallbackColor` 调色板（蓝/红/绿/黄），相机自身 id 对应的颜色不参与"其它无人机"检测
3. **新建 `scripts/sim_cam_perception.py`**（运行入口）：
   - 订阅 frame/meta/groundtruth/telemetry，每帧检测 + 反解 + 发布上述两个输出 topic
   - `--validate` 模式：与 ground truth 对比，滚动打印误差均值/最大值
   - `--save-frames logs/sim_cam/`：抽样落盘画面供人工检查
4. **新建 `tests/test_perception.py`**：
   - 已知位姿正投影/反投影往返误差 < 1e-6
   - 相机 (0,0,2) 下俯 15°、目标前方地面 (5,0,0) 的解算正确性
   - 水平/向上射线返回 None；估深反推误差 < 1e-6

## 验收标准

- `conda run -n eai-swarm python -m unittest discover -s tests` 全绿。
- 联合组 1 验收：地面目标反解与 groundtruth 平面误差 < 0.2m（目标在画面中时）。
- 空中无人机反解与其 telemetry 三维误差 < 0.5m。
- 目标移动时能持续输出轨迹（无长时间丢失）。

## 产出物

- `swarm/perception.py`、`scripts/sim_cam_perception.py`、`tests/test_perception.py`
- `requirements.txt` 更新
- 误差验证记录（贴到 `docs/stage2/sim_camera.md`，文档由本组维护）
