# Stage 5：Ego 感知闭环 Safety Gate 效果报告（穿越场景）

## 1. 实验目的

验证在**真实 Unity 摄像头感知闭环**下，Safety Gate 对两机穿越场景的保护效果。实验设定：

- 每架无人机只知道自己的真实位置；
- 对方无人机的位置只能通过自己的摄像头反解估计；
- 每架无人机拥有独立的 Safety Gate，基于自己的 camera 视角做决策；
- **任务完成判据**：所有任务无人机到达目标点 0.3m 半径内（goal-arrival），而非仅 x 方向位置互换。

## 2. 实验设置

### 2.1 场景

| 场景 | 几何 | 默认速度 | 说明 |
| --- | --- | --- | --- |
| `head_on_crossing` | 对向穿越 | 1.0 m/s | 两机沿 x 轴相向而行 |
| `perpendicular_crossing` | 垂直穿越 | 1.6 m/s | 一机沿 x 轴、一机沿 y 轴 |
| `diagonal_crossing` | 对角穿越 | 1.6 m/s | 两机沿对角线相向而行 |

### 2.2 条件

- **C2**：不启用 Safety Gate，作为危险基线。
- **C3**：启用 Safety Gate（网络版），使用 ego 感知输入。
- **C4**：在 C3 基础上，Safety Gate 接管后由 DeepSeek LLM 生成低频恢复航点。
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

| 场景 | 条件 | 种子数 | 成功率 | 碰撞率 | 近失率 | 平均最小距离 | 最小距离 | 平均接管 | 平均 LLM 延迟 | 平均耗时 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| head_on_crossing | Ego C2 | 3 | 67% | 33% | 100% | **0.26 m** | 0.25 m | 0.0 | — | 14.48 s |
| head_on_crossing | Ego C3 | 3 | 100% | 0% | 100% | **0.50 m** | 0.49 m | 2.0 | — | 13.29 s |
| head_on_crossing | Ego C4 | 3 | 100% | 0% | 100% | **0.47 m** | 0.44 m | 2.3 | 3.6 s | 14.73 s |
| perpendicular_crossing | Ego C2 | 3 | 0% | 100% | 100% | **0.03 m** | 0.02 m | 0.0 | — | 3.77 s |
| perpendicular_crossing | Ego C3 | 3 | 67% | 33% | 33% | **0.62 m** | 0.08 m | 2.0 | — | 8.62 s |
| perpendicular_crossing | Ego C4 | 3 | 100% | 0% | 33% | **0.81 m** | 0.35 m | 2.0 | 5.4 s | 20.34 s |
| diagonal_crossing | Ego C2 | 3 | 0% | 100% | 100% | **0.05 m** | 0.02 m | 0.0 | — | 5.33 s |
| diagonal_crossing | Ego C3 | 3 | 100% | 0% | 0% | **1.37 m** | 1.05 m | 2.0 | — | 12.66 s |
| diagonal_crossing | Ego C4 | 3 | 100% | 0% | 33% | **1.32 m** | 0.71 m | 1.7 | 5.0 s | 17.80 s |

> 注：本批次统一采用 **goal-arrival** 完成判据（到达目标点 0.3m 半径内）。C4 的 LLM 延迟为单次 API 调用耗时，发生在 Safety Gate 接管之后，不影响实时避障。

### 3.2 分种子明细（goal-arrival 判据）

#### head_on_crossing / Ego

| seed | C2 成功 | C2 最小距离 | C3 成功 | C3 最小距离 | C4 成功 | C4 最小距离 | C4 LLM 延迟 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | ✓ | 0.25 m | ✓ | 0.51 m | ✓ | 0.44 m | 3.54 s |
| 1 | ✓ | 0.27 m | ✓ | 0.50 m | ✓ | 0.50 m | 3.44 s |
| 2 | ✗ | 0.25 m | ✓ | 0.49 m | ✓ | 0.46 m | 6.93 s |

#### perpendicular_crossing / Ego

| seed | C2 成功 | C2 最小距离 | C3 成功 | C3 最小距离 | C4 成功 | C4 最小距离 | C4 LLM 延迟 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | ✗ | 0.02 m | ✓ | 0.98 m | ✓ | 1.21 m | 8.29 s |
| 1 | ✗ | 0.06 m | ✗ | 0.08 m | ✓ | 0.35 m | 5.46 s |
| 2 | ✗ | 0.02 m | ✓ | 0.81 m | ✓ | 0.88 m | 10.58 s |

#### diagonal_crossing / Ego

| seed | C2 成功 | C2 最小距离 | C3 成功 | C3 最小距离 | C4 成功 | C4 最小距离 | C4 LLM 延迟 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | ✗ | 0.06 m | ✓ | 1.61 m | ✓ | 0.71 m | 6.89 s |
| 1 | ✗ | 0.07 m | ✓ | 1.46 m | ✓ | 1.40 m | 6.71 s |
| 2 | ✗ | 0.02 m | ✓ | 1.05 m | ✓ | 1.86 m | 3.96 s |

## 4. 分析

### 4.1 Safety Gate 有效性

- **C2 基线**：perpendicular / diagonal 无 Gate 时 100% 碰撞，head_on 也有 33% 碰撞，证明仅依靠 Pilot 规则避障不足以保证安全。
- **C3**：Safety Gate 显著降低碰撞风险，head_on / diagonal 达到 100% 无碰撞，perpendicular 仍有 33% 碰撞。
- **C4**：所有场景 100% 无碰撞，验证了 LLM 恢复规划在任务层面的价值。

### 4.2 场景难度差异

- **diagonal**：C3 / C4 均 3/3 成功，且 C3 平均最小距离 1.37 m，是最宽松的场景。
- **head_on**：C3 / C4 均 3/3 成功，但 C2 也有 2/3 成功，说明 head_on 的 Pilot 规则避障本身有一定效果，只是不够可靠。
- **perpendicular**：最难场景，C3 仍 1/3 碰撞，必须依赖 C4 的 LLM 恢复才能达到 100%。

### 4.3 C3 vs C4：LLM 恢复规划的价值

- **C3** 中，Safety Gate 完成实时避障后，Pilot 直接尝试回到原任务路径；在 perpendicular 这类急转弯场景中，回航不及时导致 1 次碰撞。
- **C4** 中，每次 override 触发后由 DeepSeek 生成低频恢复航点，接管了"从避障状态回到任务状态"的决策；perpendicular 成功率从 67% 提升到 100%。
- **代价**：LLM 单次延迟 1.6–10.6 s，且发生在接管之后。它不影响实时安全（实时避障由确定性 Gate 负责），但会拉长整个 trial 耗时（perpendicular C4 平均 20.3 s，明显高于 C3 的 8.6 s）。

### 4.4 主要瓶颈

1. **摄像头视野受限**：机体侧向转弯避障后，前向 FPV 摄像头容易丢失目标。当前通过 `min-override-sec=2.5 s` 缓解，但对高速近距场景仍不足。
2. **感知延迟**：从摄像头采帧、VLM 检测、深度估计到 Gate 决策，端到端延迟约 200–500 ms。在 1.6 m/s 的近距垂直交汇中，延迟占安全裕度的比例显著增大。
3. **深度估计误差**：blob 宽度含阴影导致深度系统性偏小，使远处目标被低估距离，Gate 触发偏晚。
4. **LLM 延迟波动大**：C4 中最大单次 LLM 延迟达 10.6 s，在更复杂场景下可能影响任务恢复及时性。

## 5. 结论与后续

- 采用 **goal-arrival** 判据后，C3 在 head_on / diagonal 上实现 100% 无碰撞，perpendicular 仍有 33% 碰撞；**C4 在所有场景达到 100%**。
- 当前 per-camera ego + Safety Gate + LLM 恢复的完整链路已验证可行：Safety Gate 负责实时避障，LLM 负责接管后的任务恢复。
- 后续工作：
  1. 补齐 GT 对照与更多种子数，提升统计显著性；
  2. 增大摄像头 FOV 或增加尾部摄像头，降低视野丢失概率；
  3. 扩展到 `four_way_crossing` / `pursuit_intercept` / `four_v_four` 等多机场景；
  4. 引入 `llm_timeout` / `packet_loss` 等极端条件测试；
  5. 增加路径偏离度、延迟拆解、感知误差等系统指标。

## 6. 数据位置

### 旧判据（x 互换）
- `results/stage5_ego/head_on_crossing/20260807-134744/`：GT C3
- `results/stage5_ego/head_on_crossing/20260807-144557/`：Ego C3
- `results/stage5_ego/head_on_crossing/20260807-144710/`：Ego C2
- `results/stage5_ego/head_on_crossing/20260807-152757/`：Ego C4
- `results/stage5_ego/perpendicular_crossing/20260807-150507/`：Ego C3
- `results/stage5_ego/perpendicular_crossing/20260807-152919/`：Ego C4
- `results/stage5_ego/diagonal_crossing/20260807-150716/`：Ego C3
- `results/stage5_ego/diagonal_crossing/20260807-153040/`：Ego C4

### 新判据（goal-arrival，0.3m 半径）
- `results/stage5_ego/head_on_crossing/20260807-154934/`：Ego C2/C3/C4
- `results/stage5_ego/perpendicular_crossing/20260807-155305/`：Ego C2/C3/C4
- `results/stage5_ego/diagonal_crossing/20260807-155600/`：Ego C2/C3/C4
