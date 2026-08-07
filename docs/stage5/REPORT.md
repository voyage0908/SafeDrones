# Stage 5：Ego 感知闭环 Safety Gate 效果报告（穿越场景）

## 1. 实验目的

验证在**真实 Unity 摄像头感知闭环**下，Safety Gate 对两机穿越场景的保护效果。实验设定：

- 每架无人机只知道自己的真实位置；
- 对方无人机的位置只能通过自己的摄像头反解估计；
- 每架无人机拥有独立的 Safety Gate，基于自己的 camera 视角做决策。

## 2. 实验设置

### 2.1 场景

| 场景 | 几何 | 默认速度 | 说明 |
| --- | --- | --- | --- |
| `head_on_crossing` | 对向穿越 | 1.0 m/s | 两机沿 x 轴相向而行 |
| `perpendicular_crossing` | 垂直穿越 | 1.6 m/s | 一机沿 x 轴、一机沿 y 轴 |
| `diagonal_crossing` | 对角穿越 | 1.6 m/s | 两机沿对角线相向而行 |

### 2.2 条件

- **C3**：启用 Safety Gate（网络版），使用 ego 感知输入。
- **C2**：不启用 Safety Gate，作为危险基线。
- **GT C3**：使用地面真值 telemetry 的 Safety Gate 对照。

### 2.3 Ego 安全参数

基于 head_on 场景的调优结果，本批次所有 Ego C3 实验统一使用：

- `safe-distance = 3.0 m`
- `min-override-sec = 2.5 s`
- 每架 self 无人机配独立 Safety Gate，消费 `swarm/ego/camera{i}/drone/+/telemetry`

### 2.4 关键代码修复

- **MQTT client ID 去重**：多个 `ego_bridge` / `safety_gate` 实例原本使用相同 client_id，互相踢下线，导致只有一个摄像头视角生效。已按 namespace / 保护 ID 生成唯一 client_id。
- **最短接管时间**：新增 `SafetyConfig.min_override_sec`，避免避障转弯后摄像头丢失目标导致 Gate 提前释放。
- **per-camera ego 架构**：每架无人机维护自己的 ego 视角（自身真值 + 自己摄像头估计的他人位置），由独立 Gate 保护。

## 3. 实验结果

### 3.1 汇总表

| 场景 | 条件 | 种子数 | 成功率 | 碰撞率 | 近失率 | 平均最小距离 | 最小距离 | 平均接管 | 平均耗时 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| head_on_crossing | GT C3 | 3 | 100% | 0% | 33% | **0.80 m** | 0.77 m | 2.0 | 8.71 s |
| head_on_crossing | Ego C3 | 3 | 100% | 0% | 100% | **0.50 m** | 0.48 m | 2.0 | 12.21 s |
| head_on_crossing | Ego C2 | 1 | 0% | 100% | 100% | **0.18 m** | 0.18 m | 0.0 | 13.57 s |
| perpendicular_crossing | Ego C3 | 3 | 67% | 33% | 33% | **0.69 m** | 0.24 m | 2.7 | 9.30 s |
| diagonal_crossing | Ego C3 | 3 | 100% | 0% | 33% | **1.11 m** | 0.57 m | 1.7 | 9.78 s |

> 注：Ego 输入的近失率较高，是因为感知误差导致 Safety Gate 频繁触发，最小距离仍大于碰撞阈值 0.25 m。

### 3.2 分种子明细

#### head_on_crossing / Ego C3

| seed | 成功 | 最小距离 | 接管 |
| ---: | ---: | ---: | ---: |
| 0 | ✓ | 0.54 m | 2 |
| 1 | ✓ | 0.48 m | 2 |
| 2 | ✓ | 0.49 m | 2 |

#### perpendicular_crossing / Ego C3

| seed | 成功 | 最小距离 | 接管 |
| ---: | ---: | ---: | ---: |
| 0 | ✓ | 0.98 m | 2 |
| 1 | ✗ | 0.24 m | 3 |
| 2 | ✓ | 0.85 m | 3 |

#### diagonal_crossing / Ego C3

| seed | 成功 | 最小距离 | 接管 |
| ---: | ---: | ---: | ---: |
| 0 | ✓ | 0.57 m | 1 |
| 1 | ✓ | 1.04 m | 2 |
| 2 | ✓ | 1.72 m | 2 |

## 4. 分析

### 4.1 Safety Gate 有效性

- **head_on**：Ego C3 与 GT C3 均实现 100% 无碰撞，Ego 最小距离（0.50 m）低于 GT（0.80 m），符合感知链路延迟/误差的预期。
- **C2 基线**：无 Gate 时 head_on 碰撞，最小距离 0.18 m，证明 Gate 是必要的。

### 4.2 场景难度差异

- **diagonal**：表现最好，3/3 成功，平均最小距离 1.11 m。对角航线使两机在交汇前有更充裕的侧向空间。
- **perpendicular**：表现最差，1/3 碰撞。该场景速度更快（1.6 m/s），且交汇几何更急；seed 1 中即使触发 3 次接管，仍因感知延迟和机体转弯后摄像头丢目标而未能完全避开。

### 4.3 主要瓶颈

1. **摄像头视野受限**：机体侧向转弯避障后，前向 FPV 摄像头容易丢失目标。当前通过 `min-override-sec=2.5 s` 缓解，但对高速近距场景仍不足。
2. **感知延迟**：从摄像头采帧、VLM 检测、深度估计到 Gate 决策，端到端延迟约 200–500 ms。在 1.6 m/s 的近距垂直交汇中，延迟占安全裕度的比例显著增大。
3. **深度估计误差**：blob 宽度含阴影导致深度系统性偏小，使远处目标被低估距离，Gate 触发偏晚。

## 5. 结论与后续

- 在 **head_on / diagonal** 等相对低速或侧向空间充足的场景中，当前 per-camera ego + Safety Gate 架构已实现 100% 无碰撞。
- **perpendicular** 等高速近距场景仍有碰撞风险，需进一步调优：
  - 增大摄像头 FOV 或增加尾部/侧向摄像头；
  - 引入目标跟踪与预测，在丢失视野后仍能维持安全决策；
  - 对高速场景进一步提高 `safe-distance` 或 `min-override-sec`。
- 后续应扩展到 4v4 等多机对抗场景，并引入 LLM 下线、丢包等极端条件测试。

## 6. 数据位置

- `results/stage5_ego/head_on_crossing/20260807-134744/`：GT C3
- `results/stage5_ego/head_on_crossing/20260807-144557/`：Ego C3
- `results/stage5_ego/head_on_crossing/20260807-144710/`：Ego C2
- `results/stage5_ego/perpendicular_crossing/20260807-150507/`：Ego C3
- `results/stage5_ego/diagonal_crossing/20260807-150716/`：Ego C3
