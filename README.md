# SafeDrones —— 基于 AGH 智能体与独立安全门的无人机蜂群安全协同系统

> 2026 江苏省「AI+ 科学与工程创新实践」黑客松（高校组）参赛作品
> 队伍：一次迭代 ｜ 编号：U107 ｜ 组别：本科生组 ｜ 队长：冀晟炜 ｜ 成员：高铭鸿

无人机蜂群里，大模型指挥最怕两件事：决策慢、出错没兜底。SafeDrones 采用
**「AGH 智能体高层指挥官（Agnes 模型）+ MARL 低层飞控 + 独立 Safety Gate 硬安全层」**
三层架构：把「聪明」交给模型、把「不出事」交给确定性安全门，二者经 MCP 双向连通，
构成「下行否决 + 上行反馈 + 重规划」的完整安全闭环。

## 核心思想

- **高层**：AGH（Agnes Harness）作为智能体运行时底座，Agnes 模型负责语义感知与战术决策，
  通过 MCP 桥下发结构化 JSON 航点
- **低层**：MARL 飞控以 20–50Hz 高频执行，保证跟踪的平滑与实时
- **安全层**：独立于模型之外的 Safety Gate，用确定性规则实时评估碰撞风险、当场否决
- **双向安全协议**：安全门下行否决 → 上行 override 事件 → AGH 读事件重规划 → 校验

## 架构

```
AGH 智能体 (agnes-harness CLI + Agnes 模型)
   │  MCP (stdio) → mcp_bridge.py
   │  publish_waypoint / read_telemetry / list_drones / list_safety_events / emergency_abort
   ▼  结构化 JSON 航点 (command_id, confidence, ttl_sec, priority)
MARL 飞控 (marl_pilot.py)  —— 高频微步航点 20–50Hz
   ▼
Safety Gate (safety_gate.py)  ←→  飞控状态/遥测
   │  碰撞风险评估 → 安全否决 / 避障分流航点
   ▼
无人机 (mock_drone.py ／ Crazyflie 真机预留)
   │  遥测 (位置、速度、状态)
   └──→  MQTT 总线 ──→  Unity 可视化 (unity/SwarmUnityDemo，可选)
```

## 双向安全协议

安全层独立于模型，形成两个方向的安全闭环：

1. **下行否决**：Safety Gate 检出碰撞风险（合成风险值 ≥ 阈值）→ 下发 `safety-divert`
   命令当场接管，覆盖高层指令
2. **上行反馈**：否决同时产生 `safety_override` 结构化事件，AGH 通过 `list_safety_events`
   读取后生成恢复航点重新下发，形成重规划闭环

## 模型执行闭环（Agnes Harness + MCP）

> 对应评分维度「Agnes Harness 与模型执行闭环（20%）」。模型调用全部发生在 AGH 内部，
> Python 侧无任何第三方模型（`mcp_bridge.py` 只暴露 MQTT 与结构化航点，无 LLM）。

闭环由 **Agnes 模型（在 AGH 内）自己调用 MCP 工具**完成，而非 Python 侧 thin one-shot 代跑：

1. **上行**：Safety Gate 检出碰撞 → `safety_override` 事件经 MQTT 进入 `mcp_bridge.py` 缓存；
   Agnes 模型调 `list_safety_events` 读到否决事件
2. **感知**：调 `read_telemetry` 读取涉事无人机位置/速度，`list_drones` 确认在线机
3. **重规划**：调 `publish_waypoint` 下发侧向避让航点（保持高度、横向偏移）
4. **兜底**：必要时调 `emergency_abort` 令单机悬停

关键实现点：

- `mcp_bridge.py` 暴露 5 个 MCP 工具（`publish_waypoint` / `read_telemetry` / `list_drones` /
  `list_safety_events` / `emergency_abort`），注册为 AGH 的 `safedrones` 桥（trusted + enabled + ready）；
  读工具带 `read_only_hint`，天然免审批、语义正确
- 写工具（`publish_waypoint` / `emergency_abort`）是破坏性动作，正式运行在 AGH TUI 用一等机制
  **`/yolo`** 授信一次（会话级跳过审批、零框架改动），或给预设加 `mcp_safedrones_.*` 的
  `command_policy` allow 规则
- 演示场景 `scripts/agh_mcp_scenario.py`：起 broker + 2 架相向 mock drone + safety_gate，
  自动注入对撞制造 `safety_override`，把「重规划」一步留给 AGH 完成
- 录制步骤见 [DEMO_MCP.md](DEMO_MCP.md)

## 预测式协同避障（ORCA 互惠速度障碍）

> 对应评分维度「技术深度与系统实现（10%）」+「验证严谨性（10%）」。这是升级 B 的**诚实负结果**：
> 我们实现并验证了教科书级 ORCA，但结论是它**不适合**替换本项目的硬安全底线——详见下方消融。

安全门默认的避障是**反应式启发式**（`pair_collision_risk` 合成瞬时距离/接近速度/TTC，
`safety_diversion_waypoint` 背离最坏单对 + 横向 + 垂直）。为考察「预测式协同」是否更强，我们实现了
**ORCA（互惠速度障碍，van den Berg et al. 2011）**：在速度空间求「对所有邻居都无碰撞、且最接近期望
速度」的协同速度。实现为与 RVO2 库等价的 **2D 线性规划**（`swarm/orca.py`，纯函数、无 I/O、精确解）：

- `orca_velocity(...)`：n 体互惠速度求解（`linearProgram1/2/3`）
- `orca_pair_risk(...)`：速度障碍意义下的「到碰撞时间」（预测式风险原语）
- `orca_diversion_waypoint(...)`：用 ORCA 协同速度方向生成分流航点

全部 **config 门控、默认关闭**（`orca_enabled=False`），现有关键路径（C2/C3/C4 的 84 单测 + 消融数据）
零改动、零风险。开关见 `safety_gate.py --orca / --orca-early`，benchmark 见 `stage4_benchmark.py --orca`。

**诚实结论（确定性消融，非 MQTT 时序噪声）**：在对称相向穿越与随机密集交叉的进程内仿真里，ORCA 的
「最小偏离」天性把它推向**更平滑但更危险**的一端——它让接管数下降（相向 6 vs 0、密集 39 vs 166），
却换来**更小最小间距**（相向 1.6m vs 3.6m）与**更多碰撞**（密集 11 vs 0）。根因：ORCA 只保证
`combined_radius`（舒适间距）处「勉强擦过」，而反应式的「背离 + 垂直」把机距拉到安全舒适区之外。
对「硬安全底线」这一角色，**保守的反应式启发式才是正确工具**，ORCA 作为经典算法是合法但不更优的替代。
ORCA 保留的有价值原语是 `orca_pair_risk`——用完整 2D 相对速度向量预判碰撞，比反应式标量 TTC 更准，
供 `--orca-early` 做「提前预警」的学术演示。详见 [RESULTS.md](RESULTS.md) 的 ORCA 对比表。

## 消融实验（C2 / C3 / C4）

| 条件 | 说明 |
|------|------|
| C2 | 单向分层：LLM 航点 → 飞控，无安全门 |
| C3 | C2 + Safety Gate，无模型反馈 |
| C4 | 完整双向协议：航点 + 反馈 + Safety Gate + AGH 重规划 |

固定 10 随机种子 × 8 场景，完整数据见 [RESULTS.md](RESULTS.md)。核心结论：

- C2 在对称交叉场景碰撞率 100%（无硬安全兜底）
- C3/C4 在 5 个对称交叉场景碰撞率 0
- C4 在零碰撞的同时实现安全门否决后的 AGH 恢复航点重规划，重规划失败 0

**可复现性**：安全结论（C2=碰撞、C3/C4=0 碰撞）是确定性的，由
`scripts/stage4_benchmark_deterministic.py` 在进程内固定步长复现（复用相同 `Scenario` 几何 +
`SafetyGate` + `MockDroneState`），10 种子逐 bit 可复现；`stage4_benchmark.py` 为实时多进程 MQTT
端到端集成基准，其绝对值受时序影响、作冒烟测试用。详见 [RESULTS.md](RESULTS.md) 的「可复现性说明」。

## 环境要求

- Python ≥ 3.10（本项目用 3.13 的 `.venv`）
- Node.js ≥ 20（AGH 底座）
- MQTT Broker（开发用 `amqtt`，127.0.0.1:1883）
- AGH + Agnes 模型账号（高层决策与重规划）
- [可选] Unity 2021.3+ + M2Mqtt 插件（可视化）

## 快速开始

```bash
cd SafeDrones
python -m pip install -r requirements.txt
export PATH="$PWD/.venv/Scripts:$PATH"

# 终端 A：MQTT broker
./.venv/Scripts/amqtt.exe -c config/amqtt.yml    # 127.0.0.1:1883

# 终端 B：全量消融（C2/C3/C4 × 8 场景 × 10 种子 = 240 trials）
./.venv/Scripts/python.exe scripts/stage4_benchmark.py \
  --scenario all --conditions C2,C3,C4 --seeds 10 --out results/stage4
```

交互式演示（自然语言 → 下发 → 否决 → 重规划闭环）走 AGH Web 工作台，
步骤见提交材料中的演示脚本。

## 运行测试

```bash
./.venv/Scripts/python.exe -m unittest discover -s tests -v   # 84 个测试全绿
```

## 项目结构

```
SafeDrones/
├── swarm/                  # 核心包
│   ├── simulation.py       # 无人机运动学模拟
│   ├── safety.py           # Safety Gate 碰撞风险评估
│   ├── orca.py             # ORCA 互惠速度障碍（预测式协同避障，纯函数）
│   ├── marl.py             # MARL 飞控（规则基线 + ONNX 推理）
│   ├── llm_provider.py     # LLM 指挥（AGH/Agnes one-shot）
│   └── mqtt_gateway.py     # MQTT 网关封装
├── mcp_bridge.py           # MCP 桥（stdio，5 工具）→ 暴露执行层给 AGH
├── scripts/                # 工具脚本
│   ├── stage4_benchmark.py # 消融实验自动化 Runner（实时 MQTT，端到端集成）
│   ├── stage4_benchmark_deterministic.py # 进程内确定性基准（可复现 C2/C3 安全结论）
│   ├── agh_mcp_scenario.py # AGH 闭环演示场景（起 broker+drone+gate，注入对撞）
│   ├── dev_broker.py       # 开发用 MQTT broker（amqtt）
│   ├── gen_results_md.py   # 生成 RESULTS.md
│   └── ...
├── tests/                  # 单元测试（84 个）
├── unity/                  # Unity 3D 可视化（可选）
├── gateway.py              # 命令/事件 HTTP 接口（MQTT 网关封装）
├── mock_drone.py           # 虚拟无人机入口
├── safety_gate.py          # Safety Gate 入口
├── marl_pilot.py           # MARL 飞控入口
├── DEMO_MCP.md             # 模型执行闭环演示录制指南
└── RESULTS.md              # C2/C3/C4 消融对比
```

## 致谢

本作品执行层的 MARL 飞控、Safety Gate、MockDrone 与「双向安全协议」思想基于开源项目
[SafeDrones](https://github.com/Vitalrubbish/SafeDrones) 构建，在此致谢。高层指挥官
**AGH 智能体 + Agnes 模型**、MCP 桥与 C4 消融闭环为本作品实现。

## 许可证

上游项目未随附明确许可证（原仓库标注「待定」）。本作品仅用于竞赛演示；第三方组件许可证
见提交材料「第三方来源清单」。
