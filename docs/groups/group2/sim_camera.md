# 第 2 组：Python 感知链路

## 接口

输入 Topic：

- `swarm/cam/drone/{id}/frame`：JPEG 原始帧。
- `swarm/cam/drone/{id}/meta`：相机内外参元数据。
- `swarm/target/+/groundtruth`：地面目标真值，仅用于 `--validate`。
- `swarm/drone/+/telemetry`：己方/友方坐标，用于敌我过滤和 `--validate`。

输出 Topic：

- `swarm/target/{id}/position`：非己方地面目标。
- `swarm/drone_seen/{id}/position`：非友方无人机。

输出 ID 不是持久身份。每轮检测、过滤、去重后，当前非己方对象按检测顺序临时分配 `1,2,3,...`，ID 可随时间变化。消费端应按位置和 TTL 处理，不能把 ID 当作固定编号。

## 坐标反解

内参：

```text
fx = fy = (width / 2) / tan(fov_deg * pi / 360)
cx = (width - 1) / 2
cy = (height - 1) / 2
```

像素转射线：

```text
dx = (u - cx) / fx
dy = (cy - v) / fy
ray = normalize((dx, dy, 1))
```

地面目标明确为红色；空中目标颜色限定为文档调色板中的蓝/绿/黄，且保证不为红色。

- 红色 blob 作为地面目标候选，通过射线-地平面交点和高度校验后输出。
- 蓝/绿/黄空中目标作为空中目标候选，使用通用目标宽度估深：

```text
depth = depth_scale * (fx * object_width_m / pixel_width) + depth_offset_m
t = depth / ray_z
position = cam_pos + t * world_ray
```

对应的相机坐标系坐标为：

```text
dx = (u - cx) / fx
dy = (cy - v) / fy
Xc = dx * depth
Yc = dy * depth
Zc = depth
```

相机正交基：

```text
F' = normalize(cam_forward)
R  = normalize(cross(F', cam_up))
U' = normalize(cross(R, F'))
```

世界坐标：

```text
position = cam_pos + R * Xc + U' * Yc + F' * Zc
```

空中目标得到三维位置后再根据高度判断：

- 解出的高度低于 `min_drone_altitude`，或接近 `ground_z`（容差 `ground_tolerance`）：判定为地面目标，输出射线与地平面交点。
- 解出的高度不低于 `min_drone_altitude`：判定为空中无人机，输出估深得到的位置。

可调参数：

- `--object-width`：默认 `0.35m`，解坐标前使用的通用目标宽度。
- `--depth-scale`：默认 `1.0`。
- `--depth-offset`：默认 `0.0`。
- `--ground-z`：默认 `0.0`。
- `--ground-tolerance`：默认 `0.35m`，解出位置后用于地面高度判定。

## 颜色判定

- 地面目标为红色，HSV 范围：
  ```text
  (0, 80, 60) 到 (10, 255, 255)
  (170, 80, 60) 到 (180, 255, 255)
  ```
- 空中目标颜色限定为蓝/绿/黄：
  ```text
  黄：15, 60, 60 到 40, 255, 255
  绿：60, 60, 60 到 90, 255, 255
  蓝：100, 60, 60 到 130, 255, 255
  ```
- 当前 HSV 范围有意放宽，允许相近颜色以及一定光照/渲染色偏。
- 红色范围与空中蓝/绿/黄范围没有重叠，因此红色不会参与空中目标识别。

## 运行

启动 broker 与 Group 1 相机链路后：

```bash
conda run -n eai-swarm python scripts/sim_cam_perception.py
```

校验误差：

```bash
conda run -n eai-swarm python scripts/sim_cam_perception.py --validate
```

抽样保存检测画面：

```bash
conda run -n eai-swarm python scripts/sim_cam_perception.py \
  --save-frames logs/sim_cam \
  --save-every 20
```

Ego 模式下只把友方 ID 作为己方：

```bash
conda run -n eai-swarm python scripts/sim_cam_perception.py \
  --friend-drone-ids 1,2,3,4
```

## 识别与去重

- 地面目标明确为红色，使用红色 HSV 检测。
- 空中目标颜色限定为文档调色板中的蓝/绿/黄，使用对应 HSV 范围检测。
- 红色 blob 只有满足地面几何约束时才输出为 `target`。
- 蓝/绿/黄空中目标只有满足空中高度、深度和场地范围时才输出为 `drone`。
- 靠近己方 telemetry 的候选会被过滤，默认 `0.5m`。
- 全局 track 合并所有相机结果，同一对象在 `0.2s` 内最多发布一次。
- 目标移动超过 `0.05m` 时立即发布；track 超过 `1.0s` 无新观测后删除。
- 同帧内两个近距离非友方对象保留为两个独立候选，不做简单坐标合并。

## 验收

全量测试：

```bash
conda run -n eai-swarm python -m unittest discover -s tests
```

联调预期：

- 地面目标平面误差 `< 0.2m`。
- 空中无人机 3D 误差 `< 0.5m`。
- 目标移动时持续输出，不发布长期过期坐标。
