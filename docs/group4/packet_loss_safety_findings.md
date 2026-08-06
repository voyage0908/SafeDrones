# packet_loss 场景发现：安全闭环位置偏差与机载 Gate 消融

日期：2026-08-06。本文记录组 4 在 packet_loss 场景开发与验证过程中的关键发现和架构修正。

## 1. 实现与文档的偏差

`docs/plan.md` 通道 2 一节明确要求：

> 20Hz-50Hz 的安全判断与控制输出必须在边缘控制进程或机载侧本地完成，不能依赖 MQTT 往返链路作为硬实时安全闭环。

但阶段四的实现把整条安全闭环放在了 MQTT 上：

- `safety_gate.py` 是独立网络进程，风险评估输入来自 MQTT 订阅的 telemetry；
- 接管指令通过 MQTT 发回 `swarm/drone/{id}/command`；
- `mock_drone.py` 自身没有任何本地安全逻辑。

这是阶段四为验证协议接口（C2/C3/C4 消融）做的有意简化：localhost 无损环境下 MQTT 延迟毫秒级，Gate 放在网络侧不影响结果，阶段四结论仍然有效。但该偏差是潜伏的——packet_loss 场景把它暴露了。

## 2. 关键发现

### 2.1 关键指令必须重发

首轮 packet_loss 冒烟（20% 丢包）中，交叉目标指令只发一次且恰好被丢光，任务永远卡死在起点。修复：runner 与 demo 对关键指令（起点、交叉目标）周期重发（每 1-2 秒）直到条件满足。重发指令幂等（同一 command_id、同一目标），无损环境行为不变。

### 2.2 丢包下测量链本身不可信

监控端也走丢包代理时，记录的 `min_distance_m` 只是"采样到的最小值"，真实最近点可能根本没采到。Unity 画面还会因为插值"切角"显示两机相撞的假象。修复：`mock_drone.py --log-trajectory` 把真实位置以 10Hz 写本地 JSONL（不过 MQTT），`scripts/trajectory_analysis.py` 离线计算窗口化（排除起飞前原点重合）的真值最小距离，作为碰撞判据。

### 2.3 20% 丢包下网络版 Gate 偶发失效

同一 seed 多次运行结果不稳定：一轮真值最小距离 0.683m（安全），另一轮监控端在交叉阶段采到 0.068m（低于 0.25m 碰撞阈值）。机制：Gate 的 telemetry 过时 + 接管指令单程到达率约 64%（两段链路各丢 20%）+ 接管指令 0.5s 重发间隔在对冲 2m/s 相对速度下等于 1m 接近距离。

这直接验证了 plan.md 的架构原则：安全层不能骑在会丢包的网络上。

## 3. 修正：机载 Gate 消融

`mock_drone.py` 新增机载安全模式：

- `--onboard-gate`：在无人机进程内运行 `swarm.safety.SafetyGate`，每 0.1s 用本地真值状态评估风险；
- `--peer-trajectory ID=PATH`：通过读取友机本地轨迹文件获取邻机位置（模拟机载感知，不经过 MQTT）；
- 接管时直接对本地状态应用安全指令，并在接管期间忽略一切非 `safety` 优先级的 MQTT 指令；
- override 事件仍发布到 `swarm/commander/override`（best effort，仅用于 LLM 反馈与日志）。

对比开关：`scripts/packet_loss_demo.sh --gate-mode network|onboard`。

**预期结果**：onboard 模式下即使 MQTT 全部丢光，安全也不受影响（安全闭环零网络依赖）；network 模式下偶发危险接近。两模式各跑多种子对比真值最小距离分布，即为"安全层必须部署在边缘"的直接实验证据。

## 4. 复现命令

```bash
# 网络版 Gate（基线，可能偶发危险接近）
bash scripts/packet_loss_demo.sh --non-interactive --seed 0 --gate-mode network

# 机载 Gate（预期真值最小距离保持健康）
bash scripts/packet_loss_demo.sh --non-interactive --seed 0 --gate-mode onboard
```

summary 中 `true_min_distance_m` 为碰撞判据，`mqtt_observed_min_distance_m` 仅作对比。

## 5. 结果记录

2026-08-06，head_on_crossing、20% 丢包、两模式各 5 个种子（0-4）的真值最小距离（m）：

| seed | network（现有实现） | onboard（机载 Gate） |
|------|--------------------:|--------------------:|
| 0 | 0.746 | 0.736 |
| 1 | **0.292** | 0.722 |
| 2 | 0.485 | 0.465 |
| 3 | 0.409 | 0.462 |
| 4 | 0.612 | 0.679 |
| 平均 | 0.509 | **0.613** |
| 最差 | **0.292** | **0.462** |

解读：

1. onboard 模式在最差值（0.462 vs 0.292）和平均值（0.613 vs 0.509）上都明显优于 network 模式；network 模式 seed 1 的 0.292m 已逼近 0.25m 碰撞阈值，另有一次历史运行曾观测到 0.068m。
2. 两种模式相比无损基线（head_on C3 平均约 0.82m）都有下降，因为 **nominal 控制（Pilot 的 micro-waypoint）仍然走丢包链路**——机载 Gate 保证的是"不撞"，不是"轨迹不变形"。
3. n=5 只能算趋势性证据，正式报告需要 ≥10 种子，并建议补一档"丢包率扫描"（0/10%/20%/30%）画安全裕度曲线。
4. onboard 模式的接管事件仍能通过 MQTT 上报（best effort），LLM 反馈通道不受影响。
