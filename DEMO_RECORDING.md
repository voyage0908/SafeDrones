# SafeDrones 完整演示录制教程（从零到成片）

> 一份文档走完全程：环境 → 采集运行数据与测试样例 → 三屏录制 → 剪辑成片。
> 文末附**真实运行数据**（本次实测）与**测试样例**，可直接用作提交材料里的证据。

---

## 0. 要录什么（三屏同框，命中 20%「模型执行闭环」评分项）

| 屏幕 | 内容 | 对应证据 |
|---|---|---|
| **Unity** | 无人机飞行 + 安全门否决的**颜色状态机**（蓝→黄→红）+ 分流轨迹 | 下行否决 + 飞控执行 |
| **终端 1** | `safety_gate` 日志（`override drone=... risk=...`） | 安全门独立检出碰撞 |
| **终端 2** | AGH TUI：Agnes 模型**自己调 MCP 工具**（`list_safety_events`→`read_telemetry`→`publish_waypoint`） | 模型主动重规划 |

一句话讲清闭环：**安全门独立否决（下行）→ override 事件回流 AGH → 模型读事件重规划（上行）→ 下发避让航点 → 无人机分流**。

---

## 1. 前置环境（一次性）

```bash
cd SafeDrones
export PATH="$PWD/.venv/Scripts:$PATH"    # Python 3.13 venv
AGH_CLI="<agnes-harness>/packages/cli/dist/local/agnes.mjs"   # 换成本机 agnes-harness 仓库路径

# 确认 AGH 的 safedrones 桥 ready（trust=trusted desired=enabled actual=ready）
node "$AGH_CLI" mcp list
```

> ⚠️ 安全：Agnes Key 只存在于 `~/.agh/secrets`，**不写入任何代码 / 仓库 / 录屏**。演示时模型调用全部发生在 AGH 内部。

---

## 2. 先采集运行数据与测试样例（做进 PPT / 成片收尾）

### 2.1 测试样例（96 个单测全绿）

```bash
./.venv/Scripts/python.exe -m unittest discover -s tests -v
```

实测输出（2026-10-13）：

```
Ran 96 tests in 0.064s
OK
```

覆盖：`test_safety`（安全门否决/分流/状态事件）、`test_orca`（ORCA 算法层）、
`test_simulation`（运动学）、`test_marl`、`test_mqtt_*`、`test_stage4_benchmark`（场景/消融/重规划校验）、
`test_trajectory_analysis`（地面真值同步度量）等。

### 2.2 确定性基准运行数据（可逐 bit 复现）

```bash
./.venv/Scripts/python.exe scripts/stage4_benchmark_deterministic.py \
  --scenario all --conditions C2,C3,C4 --seeds 10 --packet-loss 0.0,0.1,0.3,0.5 \
  --out results/stage4_deterministic
```

**核心消融（0% 丢包，10 种子 × 6 场景）** —— 实测：

| 场景 | C2 无安全门 | C3 安全门 | C4 完整协议 |
|---|---|---|---|
| head_on_crossing | 0/10（碰撞 10） | 10/10（0 碰撞，min 0.805m） | 10/10（0 碰撞，重规划 2，失败 0） |
| perpendicular_crossing | 0/10（碰撞 10） | 10/10（0 碰撞，min 1.001m） | 10/10（0 碰撞，重规划 2，失败 0） |
| diagonal_crossing | 0/10（碰撞 10） | 10/10（0 碰撞，min 0.932m） | 10/10（0 碰撞，重规划 2，失败 0） |
| llm_timeout | 0/10（碰撞 10） | 10/10（0 碰撞，min 0.805m） | 10/10（0 碰撞，重规划 2，失败 0） |
| four_way_crossing | 0/10（碰撞 10） | 10/10（0 碰撞，min 0.643m） | 10/10（0 碰撞，重规划 2，失败 0） |
| formation_crossing | 0/10（碰撞 10） | 10/10（0 碰撞，min 0.594m） | 10/10（0 碰撞，重规划 2，失败 0） |

**丢包鲁棒性（升级 D，C3 成功率 × 丢包率）** —— 实测：

| 场景 | 0% | 10% | 30% | 50% |
|---|---|---|---|---|
| head_on_crossing | 10/10 | 10/10 | 10/10 | 10/10 |
| perpendicular_crossing | 10/10 | 10/10 | 9/10 | 9/10 |
| diagonal_crossing | 10/10 | 10/10 | 10/10 | 10/10 |
| llm_timeout | 10/10 | 10/10 | 10/10 | 10/10 |
| four_way_crossing | 10/10 | 10/10 | 10/10 | 7/10 |
| formation_crossing | 10/10 | 10/10 | 9/10 | 5/10 |

> 完整 72 行明细见 `results/stage4_deterministic/aggregate.csv`（gitignored，可一键重跑复现）。
> 结论：安全门在 0–30% 丢包下几乎零碰撞、最坏最小间距单调下降；50% 极端丢包才在密集场景
> （four_way / formation）失守，简单两机场景基本 0 碰撞（perpendicular 在 30%+ 偶发 1 次边缘碰撞）。

---

## 3. 完整演示录制（三屏）

### 3.1 终端 A —— MQTT broker（先起）

```bash
./.venv/Scripts/amqtt.exe -c config/amqtt.yml      # 127.0.0.1:1883
```

### 3.2 终端 B —— 蜂群对撞场景 + 安全门（`--delay` 留出操作时间）

```bash
./.venv/Scripts/python.exe scripts/agh_mcp_scenario.py --delay 30 --hold-sec 60
```

作用：起 broker → 1/2 号 mock drone 相向 + safety_gate 就位 → 打印「蜂群已就位」→ 等 30s →
自动注入对撞（每 12s 重注一次）。本脚本**不自己做任何 MCP 调用**，把重规划留给 AGH。

### 3.3 终端 C —— AGH TUI（在 30s 窗口内授信 + 下发监控指令）

```bash
AGH_CLI="<agnes-harness>/packages/cli/dist/local/agnes.mjs"
node "$AGH_CLI"
```

进 TUI 后：

1. 输 `/yolo` 回车（授信本会话，录屏保留这一步 = 「人授信一次」的展示点）。
2. 输监控指令（一行）：

```
监控 swarm 蜂群安全事件：一旦 list_safety_events 出现 safety_override，
就用 read_telemetry 读取涉事两架无人机的位置，再给碰撞风险更高的一侧用
publish_waypoint 下发一个侧向避让航点（保持高度、横向偏移），完成重规划。
```

3. 回车，模型自动、可观测地调用 `list_safety_events → read_telemetry → publish_waypoint`。

### 3.4 屏幕 D —— Unity 可视化（无需改任何代码）

1. Unity Hub 打开 `unity/SwarmUnityDemo` → 打开 `Assets/Scenes/SampleScene.unity` → 点 ▶ Play。
2. 控制台出现 `Subscribed to MQTT telemetry topic: swarm/drone/+/telemetry` 即连上（`DroneTelemetrySubscriber`
   已挂在场景里，自动连 `127.0.0.1:1883`）。
3. 摆机位：俯拍全局（同时看到两架相向无人机 + 分流轨迹）；右下角保留 `FlightHUD`（实时 X/Y/Z + `⚠ override`）。

> **录制坑**：`FallbackColor` 里奇数 ID=蓝方、偶数 ID=红方，而 override 红与「红方」基色同色
> `(0.95,0.20,0.16)`。所以 2 号机（偶数）本来就是红色、否决时不变色——镜头聚焦 **1 号机**的
> 蓝→黄→红变化；2 号机靠**轨迹分流**体现。

### 3.5 录屏（OBS 建议）

- 新建场景，添加 3 个「显示器/窗口捕获」源（Unity 窗口、终端 B、终端 C），并排布局。
- 分辨率 1920×1080，帧率 30fps；先试录 10s 确认三屏都清晰。
- 全程一条轨录到底，事后按下面时间轴剪。

---

## 4. 时间轴分镜（评审点）

| 时刻 | Unity | 终端 B（safety_gate） | 终端 C（AGH TUI） |
|---|---|---|---|
| 0s | 1 号机蓝、2 号机红相向接近 | 蜂群已就位 | `/yolo` + 监控指令 |
| ~30s | 接近阈值，1 号机蓝→黄→红，两机分流 | `override drone=... risk=...` | 模型调 `list_safety_events` 读到 `safety_override` |
| ~31s | 两机沿避让航点转向、拉开距离 | mcp_bridge 下发 `swarm/drone/N/command` | `read_telemetry`→`publish_waypoint` 依次出现 |
| ~33s+ | 颜色回落蓝，回到原任务方向 | — | 重规划完成 |

---

## 5. 成片结构建议（总长 ~90s）

1. **价值主张（~20s）**：一句话「蜂群里大模型指挥最怕决策慢、出错没兜底」→ 三层架构图 →
   双向安全协议（下行否决 + 上行反馈 + 重规划）。
2. **双屏高潮（~40s）**：第 4 节时间轴的完整闭环，配一句旁白把「独立安全门」与「模型主动重规划」讲清。
3. **数据收尾（~20s）**：第 2.2 节消融表（C2=100% 碰撞、C3/C4=0 碰撞）+ 丢包鲁棒性 + 96 测试全绿截图。
4. **展望（~10s）**：MARL 学习接口 + ORCA 负结果 + 真机迁移，一句话带过。

---

## 6. 常见问题

- **Unity 看不到无人机**：确认 broker（终端 A）已起、场景 Play、`brokerHost=127.0.0.1`；终端 B 已在发遥测。
- **AGH 工具被 `approval rejected`**：忘了 `/yolo`；回到终端 C 先 `/yolo` 再下指令。
- **想全自动（免 `/yolo`）**：给 AGH 预设加 `mcp_safedrones_.*` 的 `command_policy` allow 规则并重打包 CLI（改框架、跟版本绑定，不推荐）。

---

## 附：其它材料入口

- 闭环演示细节：`DEMO_MCP.md`
- 消融结论与 ORCA 负结果：`RESULTS.md`
- 与原项目对比：`COMPARISON.md`
- 架构与快速开始：`README.md`
