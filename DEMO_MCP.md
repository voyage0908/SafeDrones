# 升级 A · AGH 走 MCP 工具闭环的演示录制指南

> 对应评分维度「Agnes Harness 与模型执行闭环（20%）」。
> 目标：让 **Agnes 模型**（在 AGH 内部）自己调用 `mcp_bridge.py` 暴露的 MCP 工具，
> 完成「上行反馈 → 重规划 → 下行避让」的闭环，而不是由 Python 侧 thin one-shot 代跑。

## 背景：为什么卡过一下

AGH 的 MCP 工具循环其实**早就接好了**（`safedrones` 桥 `trusted + enabled + ready`，模型也能看见、
会主动调用那 5 个工具），但被 AGH 的「工具执行审批」闸门拦住了：非交互模式下没有「人」来批准，
而预设里的 `command_policy`（允许列表）没有给 MCP 工具放行，于是工具调用被 `approval rejected`。

解决办法用 AGH 自带的一等机制 **`/yolo`**（本会话跳过全部审批、开启后不可撤销），零框架改动。

## 前置检查

```bash
AGH_CLI="C:/Users/Lenovo/Desktop/AI+黑马竞赛/agnes-harness/packages/cli/dist/local/agnes.mjs"
# 1) AGH daemon 已启动、safedrones 桥 ready
node "$AGH_CLI" mcp list
#    应看到：safedrones ... trust=trusted desired=enabled actual=ready
```

## 录制流程（两个终端）

### 终端 1 —— 起蜂群对撞场景（先就位，`--delay` 秒后才注入对撞）

```bash
cd /c/Users/Lenovo/Desktop/新建文件夹/SafeDrones
./.venv/Scripts/python.exe scripts/agh_mcp_scenario.py --delay 30 --hold-sec 60
```

它会：起 broker → 起 1/2 号 mock drone（相向）+ safety_gate 就位 → 打印「蜂群已就位」后
进入 **30s 等待**（这 30s 拿去操作终端 2）→ 自动注入对撞并每 12s 重新注入一次，
保持存活 60s。期间本脚本不自己做任何 MCP 调用，把闭环留给 AGH。

### 终端 2 —— 在 30s 等待窗口内，AGH TUI 授信并下发监控指令

```bash
AGH_CLI="C:/Users/Lenovo/Desktop/AI+黑马竞赛/agnes-harness/packages/cli/dist/local/agnes.mjs"
node "$AGH_CLI"
```

进 TUI 后依次操作：

1. 输入 `/yolo` 回车 —— 授信本会话，之后所有工具调用自动通过（录屏时保留这一步，正好展示「人授信一次」）。
2. 输入监控指令（一行）：

```
监控 swarm 蜂群安全事件：一旦 list_safety_events 出现 safety_override，
就用 read_telemetry 读取涉事两架无人机的位置，再给碰撞风险更高的一侧用
publish_waypoint 下发一个侧向避让航点（保持高度、横向偏移），完成重规划。
```

3. 回车让模型执行 —— 它会自动、可观测地调用 `list_safety_events → read_telemetry → publish_waypoint`。

## 预期看到什么（评审点）

- 终端 2 里 AGH 的 **tool_call 记录**：`mcp_safedrones_*_list_safety_events`、
  `..._read_telemetry`、`..._publish_waypoint` 依次出现，带真实入参/出参。
- 终端 1 里 `safety_gate` 日志 `override drone=... risk=...`（下行否决）与
  mcp_bridge 下发的避让航点 `swarm/drone/N/command`（重规划）形成闭环。
- 一句话讲清闭环：**Safety Gate 独立检出碰撞 → 下行 safety 命令接管 + 上行 override 事件
  经 MCP 回流 AGH → Agnes 模型读遥测、重规划、下发避让航点**。

## Unity 可视化（双屏/三屏录制，可选但强烈推荐）

评分项「Agnes Harness 与模型执行闭环（20%）」最直观的证据是**双屏同框**：

- **Unity**：无人机运动 + 安全门否决的**颜色状态机**（蓝 → 黄 → 红）
- **终端 1**：`safety_gate` 日志（下行否决）
- **终端 2**：AGH TUI（模型读事件 → 重规划）

Unity 侧**无需改任何代码**：`DroneTelemetrySubscriber` 已挂在 `SampleScene` 里，Play 即自动连接
`127.0.0.1:1883`，订阅 `swarm/drone/+/telemetry`、`swarm/commander/status`、`swarm/commander/override`
（brokerHost/brokerPort 是 Inspector 序列化字段，默认已对齐本机 broker）。

### Unity 就位（在终端 1 起场景之前）

1. Unity Hub 打开 `unity/SwarmUnityDemo`，打开 `Assets/Scenes/SampleScene.unity`
2. 点 ▶ Play。控制台应出现 `Subscribed to MQTT telemetry topic: swarm/drone/+/telemetry`
3. 摆机位：俯拍全局（能同时看到两架相向无人机 + 分流轨迹）；若画面里有 `FlightHUD` 就保留右下角
   （它实时显示每架机的 X/Y/Z + `⚠ override` 状态，是比颜色更可读的文字证据）

### 时间轴对拍（评审点）

| 时刻 | Unity 画面 | 终端 1（safety_gate） | 终端 2（AGH TUI） |
|---|---|---|---|
| 0s | 1 号机（蓝）、2 号机（红）相向接近 | 蜂群就位 | 输 `/yolo` + 监控指令 |
| ~30s | 接近阈值，**1 号机蓝 → 黄 → 红**，两机开始横向分流 | `override drone=... risk=...` | 模型自动调 `list_safety_events` 读到 `safety_override` |
| ~31s | 两机沿避让航点转向、拉开距离 | mcp_bridge 下发 `swarm/drone/N/command` | `read_telemetry` → `publish_waypoint` 依次出现 |
| ~33s+ | 颜色回落蓝色，回到原任务方向 | — | 重规划完成 |

### 颜色状态机（DroneTelemetryView）

`normal` 蓝 `(0.10,0.45,0.95)` → `warning` 黄 `(0.95,0.72,0.18)`（mode=warning 或 risk≥0.32）→
`override` 红 `(0.95,0.20,0.16)`；每次 override 状态保持 **2s** 后回落蓝色。黄（warning）来自
`swarm/commander/status` 的 `mode=warning` 事件，是否出现取决于安全门阈值路径，**红（override）是稳定信号**。

> **录制坑（务必注意）**：`FallbackColor` 里**奇数 ID = 蓝方、偶数 ID = 红方**，而 override 红与「红方」
> 基色是同一个红 `(0.95,0.20,0.16)`。所以 `agh_mcp_scenario.py` 的 **2 号机（偶数）本来就是红色**，override
> 时颜色不变、看不出否决。录制时把镜头焦点放在 **1 号机**的蓝→黄→红变化上；2 号机的否决靠**轨迹分流**体现。

## 说明与边界

- 本演示只用于**展示「模型执行闭环」**；benchmark（`stage4_benchmark.py`）仍走 thin one-shot
  的确定性重规划，因为那是实测 100% 的关键路径，不该把 LLM 延迟塞回去。
- `mcp_bridge.py` 的读工具（`list_drones`/`read_telemetry`/`list_safety_events`）已补
  `read_only_hint`，正规部署下天然免审批、语义正确；写工具（`publish_waypoint`/`emergency_abort`）
  仍是破坏性动作，靠 `/yolo` 授信或后续加 `command_policy` 放行。
- 若要让演示**全自动**（无需敲 `/yolo`），需给 AGH 预设加 `mcp_safedrones_.*` 的
  `command_policy` allow 规则并重打包 CLI（改框架、跟版本绑定），当前不推荐。
