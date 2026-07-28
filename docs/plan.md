# AI 无人机机群对战：LLM-MARL 分层控制架构与双向安全协议

## 项目定位

本方案在学术界前沿的**分层控制架构（Hierarchical Control Architecture）**基础上，针对 LLM+MARL 系统中常见的**高层语义决策与低层安全执行脱节**问题，提出**双向安全协议（Bidirectional Safety Protocol）**——包含 Safety Gate（下行否决）和状态反馈（上行报告）两条通道，使低层 Pilot 能够在碰撞风险超过阈值时显式覆盖 LLM 指令并主动通报。由**多模态大模型（VLM/LLM）充当高层"指挥官（Commander）"**，负责低频、全局的语义感知与战术规划；由**多智能体强化学习（MARL）模型或可解释安全控制器充当低层"飞行员（Pilot）"**，负责高频、局部的物理防撞与安全执行，并在必要时否决危险指令。真机资源不足时，单台 Crazyflie 用于验证 Sim-to-Real、通信闭环和单机安全接管，多机碰撞消融与 4v4 对抗主要在 MockDrone/Unity 仿真环境中完成。

---

## 相关工作（Related Work）

近年来，LLM 与 MARL 协同决策领域涌现了大量工作。以下按与本文方案的相关度组织：

### LLM + MARL 分层控制架构（最相关）

| 论文 | 年份/会议 | 架构 | 与本工作的区别 |
|------|-----------|------|---------------|
| **RALLY** (Wang et al.) | 2025, IEEE | LLM 语义共识 + RMIX 角色分配 + MARL 导航 + PID 飞控 | 重点在角色分配与导航协同，未把运行时安全否决作为显式协议接口 |
| **Cognitive Synergetic Hierarchical Framework** (Wang et al.) | 2026, Frontiers in Neurorobotics | DeepSeek-R1 战略脑 + MAPPO+GAT 战术体，云端-边缘协同推理 | 虽包含反思/演化式调整，但低层安全接管与结构化上行 override 不是核心协议 |
| **LEHCA** (Bai et al.) | 2026, Nature Scientific Reports | LLM Commander 下发子目标 + QMIX 执行 | 以高层子目标分解与低层执行为主，需进一步核验是否存在显式安全否决通道 |
| **Ground Control to SMAX** (Fontes de Matos) | 2025, IST Lisbon | LLM 宏指令 + MAPPO 微操，通过偏离惩罚保证服从 | 有轻度服从机制，但无显式否决权 |

### LLM + MARL 通用框架

| 论文 | 年份/会议 | 核心思路 |
|------|-----------|----------|
| **EALLMs** | 2025, IEEE | 双 LLM 架构——一个做共享策略在线 MARL 更新，一个做信息集成器；SMAC/SMACv2 验证 |
| **pymarl_LLM** (Li et al.) | 2025, Neurocomputing | LLM 解决 MARL 冷启动问题；开源工具包 |
| **LAMARL** | 2025, IEEE RA-L | LLM 自动生成多机器人先验策略与奖励函数，样本效率提升 185.9% |
| **CoMLRL** | 2026, AAAI | 用 MARL 训练多个 LLM 协作 |

### 综述

| 综述 | 来源 |
|------|------|
| Multi-Agent Reinforcement Learning with LLMs: A Comprehensive Review | IEEE, 2025 |
| The Landscape of Agentic Reinforcement Learning for LLMs: A Survey | arXiv, 2025 |
| Large VLM-based Vision-Language-Action Models for Robotic Manipulation: A Survey | arXiv, 2025 |

### 本方案聚焦的安全协议缺口

从运行时安全协议角度看，现有 LLM+MARL 分层架构仍常见以下缺口。这里不是对所有文献的绝对断言，而是本文要重点对齐和验证的系统能力：

1. **运行时反馈不足**：低层执行器即使发现航点不可达、存在障碍或偏离过大，也未必能把原因以结构化事件反馈给 LLM；
2. **缺少显式安全否决接口**：低层策略通常通过 reward 或控制误差被动修正危险指令，而不是以可审计的 override 事件拒绝高层指令；
3. **缺少置信度与有效期约束**：LLM 指令往往不附带置信度、有效期、优先级和安全边界，低层执行器难以判断何时降级或拒绝。

本文的工作正是针对上述缺口 1 和 2，提出双向安全协议（Bidirectional Safety Protocol），并把缺口 3 作为通信格式和后续扩展的约束。

---

## 核心创新：双向安全协议（Bidirectional Safety Protocol）

### 问题定义

在现有分层架构中：

```
LLM (Commander) ──单向航点──→ Pilot/MARL ──→ 物理动作
```

LLM 下发指令后，如果系统没有显式反馈协议，就难以及时获知执行层的真实状态；低层 Pilot 即使检测到 LLM 指令将导致碰撞，也可能只能在 reward 函数或局部控制误差中被动修正，而无法（1）明确接管控制权，（2）通知 LLM "你的指令有问题"，（3）为后续实验留下可审计事件日志。

### 协议设计

本方案引入两条新通道：

```
                    ┌── 航点指令 + 优先级 ──→ Pilot/MARL
LLM (Commander) ───┤
                    ←── 状态反馈 / 覆盖通知 ──┘
                             (Safety Gate)
```

#### 通道 1：Safety Gate（下行否决 + 模式切换）

低层 Pilot 侧维护一个独立于 LLM 的碰撞风险评估器。该评估器可以先由规则/控制屏障函数（Control Barrier Function, CBF）实现，再替换或叠加 MARL 策略网络。当风险超过阈值时，控制模式从"追随 LLM"自动切换为"纯安全避险"：

**控制律：**

$$u = \text{clip}\left(\beta \cdot u_{\text{follow\_LLM}} + (1-\beta) \cdot u_{\text{safety}},\; u_{\min},\; u_{\max}\right)$$

其中 $u_{\text{follow\_LLM}}$ 为追踪高层航点的速度/加速度控制量，$u_{\text{safety}}$ 为避障、悬停、降速或返航控制量。最终控制量必须经过速度、加速度、高度、地理围栏和电量等硬约束裁剪。

**正常模式**（$\text{collision\_risk} < \theta_{\text{low}}$ 且未处于最小接管保持时间）:

$$\beta = 1.0 \quad \rightarrow \quad \text{完全追随 LLM 航点}$$

**预警模式**（$\theta_{\text{low}} \leq \text{collision\_risk} < \theta_{\text{high}}$）:

$$\beta = \frac{\theta_{\text{high}} - \text{collision\_risk}}{\theta_{\text{high}} - \theta_{\text{low}}} \quad \rightarrow \quad \text{平滑过渡}$$

**接管模式**（$\text{collision\_risk} \geq \theta_{\text{high}}$）:

$$\beta = 0.0 \quad \rightarrow \quad \text{完全忽略 LLM 航点，只做避险，触发上行通知}$$

为避免模式在阈值附近抖动，Safety Gate 使用迟滞机制：进入接管后至少保持 $T_{\text{hold}}$ 秒，只有当 $\text{collision\_risk} < \theta_{\text{release}}$ 且 $\theta_{\text{release}} < \theta_{\text{low}}$ 时才恢复追随模式。

其中 $\text{collision\_risk}$ 基于多机相对距离、接近速率、预测碰撞时间（TTC）和静态障碍物距离计算。对无人机 $i$ 与对象 $k$（队友或障碍物）定义：

$$r_{ik}^{d} = \text{clip}\left(1 - \frac{d_{ik}}{d_{\text{safe}}},\; 0,\; 1\right)$$

$$r_{ik}^{v} = \text{clip}\left(\frac{v_{ik}^{\text{approach}}}{v_{\max}},\; 0,\; 1\right)$$

$$r_{ik}^{ttc} = \begin{cases}
\text{clip}\left(1 - \frac{TTC_{ik}}{TTC_{\text{safe}}},\; 0,\; 1\right), & v_{ik}^{\text{approach}} > 0 \\
0, & v_{ik}^{\text{approach}} \leq 0
\end{cases}$$

$$r_{ik} = \max(r_{ik}^{d},\; r_{ik}^{d} \cdot r_{ik}^{v},\; r_{ik}^{ttc})$$

$$\text{collision\_risk}_i = \text{clip}\left(\max_k r_{ik},\; 0,\; 1\right)$$

其中 $d_{ik}$ 为无人机 $i$ 与对象 $k$ 的距离，$d_{\text{safe}}$ 为安全距离阈值，$v_{ik}^{\text{approach}}$ 为接近速率，$TTC_{\text{safe}}$ 为最小安全碰撞时间。使用 $\max$ 而非直接求和，是为了避免对象数量增加时风险值无意义膨胀；如果需要表达拥挤环境风险，可额外记录近邻数量作为日志指标。

#### 通道 2：上行状态反馈

低层 Pilot 在以下事件时通过 MQTT 反向通道回传结构化状态报告：

- **接管事件**：Safety Gate 触发时，立即上报原因、偏离量、预计恢复时间
- **定期汇总**：每 1-2 秒上报各机执行进度（航点完成率、当前偏差、异常标记）

LLM 的 System Prompt 中包含反馈解读指令，使其能在收到报告后调整后续战术。MQTT 仅承担低频指令、遥测汇总和事件通知；20Hz-50Hz 的安全判断与控制输出必须在边缘控制进程或机载侧本地完成，不能依赖 MQTT 往返链路作为硬实时安全闭环。

#### 通信格式

```json
// LLM → Pilot/MARL: 航点指令
{
  "drone": 1,
  "waypoint": [10.0, 5.0, 2.0],
  "priority": "normal",
  "confidence": 0.82,
  "ttl_sec": 5.0,
  "command_id": "cmd-0001"
}

// Pilot/MARL → LLM: 覆盖通知
{
  "drone": 1,
  "event": "safety_override",
  "reason": "collision_risk_exceeded",
  "risk_level": 0.87,
  "target_drone": 2,
  "diverted_from": [10.0, 5.0, 2.0],
  "estimated_recovery": 2.3,
  "command_id": "cmd-0001",
  "timestamp_ms": 1720000000000
}

// Pilot/MARL → LLM: 定期状态
{
  "drone": 1,
  "current_waypoint": [10.0, 5.0, 2.0],
  "progress": 0.7,
  "deviation": 1.2,
  "status": "executing",
  "anomalies": [],
  "timestamp_ms": 1720000000000
}
```

---

## 实验设计

### 核心消融实验

为验证双向安全协议的贡献，设计以下消融条件：

| 条件 | LLM→MARL | MARL→LLM 反馈 | Safety Gate | 说明 |
|------|----------|--------------|-------------|------|
| **C0** (纯 LLM) | 直接控制 | 无 | 无 | 纯 LLM 输出动作，作为仿真中的危险 Baseline；真机只允许限速近失测试 |
| **C1** (纯 MARL) | 无 LLM | 无 | 无 | 纯 MARL 自主导航 + 防撞 |
| **C2** (单向分层) | 航点 | 无 | 无 | 复现 RALLY/CogSyn 等现有方案 |
| **C3** (单向 + Gate) | 航点 | 无 | ✅ | 仅加 Safety Gate，无反馈 |
| **C4** (双向完整) | 航点 | ✅ 反馈 | ✅ | 本方案的完整版本 |

### 评价指标

| 指标 | 含义 | 期望趋势 |
|------|------|----------|
| 碰撞率 (Collision Rate) | 每飞行小时的多机碰撞次数 | C4 < C2 < C0 |
| 任务完成率 (Task Success Rate) | LLM 战术目标的达成比例 | C4 ≥ C2 |
| 端到端延迟 (Latency Breakdown) | VLM 推理 → MQTT 传输 → MARL 推理 → 动作执行 的分段时延 | 量化各段贡献 |
| 安全接管次数 (Override Count) | Safety Gate 每小时触发次数 | C3, C4 有统计，C0/C1/C2 无此概念 |
| LLM 指令修正率 (Command Revision Rate) | LLM 收到反馈后调整战术的比例 | 仅 C4 有，衡量反馈的实际效用 |
| 偏离-恢复时间 (Diversion-Recovery Time) | 从接管发生到恢复正常追随的时间 | C4 < C3（有反馈后 LLM 主动调整更快恢复） |

### 故障注入测试

| 故障类型 | 注入方式 | 验证目标 |
|----------|----------|----------|
| 交叉航线碰撞 | 在 MockDrone/Unity 中让 LLM 故意下发两机交叉航点；真机仅做单机限速近失或虚拟障碍测试 | Safety Gate 能否接管并避免碰撞 |
| LLM 幻觉航点 | LLM 输出航点位于障碍物、禁飞区或虚拟围栏外 | Safety Gate 能否检测并修正 |
| LLM 推理超时 | 人为延迟 LLM 响应 3-5 秒 | 系统在无 LLM 指令时能否持续安全飞行 |
| 通信丢包 | 随机丢弃 20% MQTT 消息 | 双向协议在不可靠网络下的鲁棒性 |

---

## 实践前：通信层自研与仿真预演计划

**核心策略**：编写一个名为 `MockDrone`（模拟无人机）的 Python 脚本。它在网络上表现得完全像一架真实的无人机，接收 MQTT 指令，并在内存中通过简单的数学公式更新自己的"虚拟坐标"，然后再把坐标发回网络。

### 阶段一：搭建消息总线与"虚拟无人机" (约 1-2 天)

- **目标**：跑通底层的 MQTT 发布/订阅机制，在没有任何硬件的情况下，让"虚拟飞机"在数据流中飞起来。
- **具体任务**：
    1. **安装环境**：电脑安装并运行 **Eclipse Mosquitto** (作为消息总线中心)；下载并安装桌面端调试工具 **MQTT Explorer** (用于可视化查看数据流)。
    2. **编写 `mock_drone.py`**：
        - 使用 `paho-mqtt` 库连接本地的 Mosquitto。
        - **状态模拟**：在脚本中定义无人机的当前坐标 `current_pos = [0,0,0]` 和目标坐标 `target_pos = [0,0,0]`。
        - **指令订阅**：订阅主题 `swarm/drone/1/command`。当收到指令 `{"action": "move_to", "target": [5, 5, 2]}` 时，更新 `target_pos`。
        - **物理模拟与上报（主循环）**：开启一个 `while True` 循环，每 0.1 秒执行一次：
            - 利用简单的数学逻辑（如逐步逼近），让 `current_pos` 向 `target_pos` 匀速移动（模拟飞行的耗时）。
            - 将计算后的 `current_pos` 封装为 JSON：`{"x": current_pos[0], "y": current_pos[1], "z": current_pos[2], "status": "flying"}`。
            - 发布到主题 `swarm/drone/1/telemetry`。
            - **【新增】预留 Safety Gate 事件发布接口**：由后续 `safety_gate.py` 或网关发布 `swarm/commander/status` 和 `swarm/commander/override`，`mock_drone.py` 只负责接收指令并上报 telemetry。
    3. **阶段验证**：运行脚本后打开 MQTT Explorer，应能看到 `telemetry` 主题在刷新 `[0,0,0]`；手动在工具中向 `command` 发送目标坐标 `[5,5,2]`，能看到 `telemetry` 中的坐标数值平滑变化，直到抵达 `[5,5,2]` 停止。

### 阶段二：打通"人 - AI - 虚拟无人机"闭环 (约 1-2 天)

- **目标**：开发 FastAPI 网关，用大模型替代人手发指令，同时搭建 MQTT 反向通道基础设施。
- **具体任务**：
    1. **搭建 FastAPI 网关**：编写 `gateway.py`，暴露接口 `/api/command` 接收文本（例如："让 1 号无人机飞到左前方的侦察点"）。
    2. **接入 LLM (Prompt 编排)**：在网关中调用大模型 API，默认使用 DeepSeek，同时保留 OpenAI-Compatible Provider 接口。设计 System Prompt："你是一个无人机蜂群指挥官，你的任务是将自然语言转化为 JSON 格式的航点目标。坐标系中 X 是前，Y 是左，Z 是高。侦察点坐标预设为 `[10, 5, 2]`..."
    3. **网关与 MQTT 对接**：FastAPI 解析出 LLM 的 JSON 坐标后，作为 MQTT 客户端将其发布到对应的 `command` 主题。开发环境可使用 `amqtt` broker，现场环境可替换为 Mosquitto，业务 topic 不变。
    4. **【新增】搭建反向 MQTT 通道**：网关同时订阅 `swarm/commander/status` 和 `swarm/commander/override`，收到消息后记录到内存事件缓存和 JSONL 事件日志，并作为后续 LLM 请求的上下文。
    5. **阶段验证**：运行 `mock_drone.py` 和 FastAPI 网关。向网关发送文字："去侦察点"。在 MQTT Explorer 或命令行订阅工具中看到 `command` 生成了指令，随后 `telemetry` 的坐标开始移动；同时通过 `/api/events` 验证反向通道事件可读。

### 阶段三：Unity 视觉孪生前置开发 (约 1-2 天)

- **目标**：在 Unity 中看到数据的具象化表现，验证"线上仿真"的基础。
- **具体任务**：
    1. **Unity 环境准备**：新建 3D 项目，导入 C# 的 MQTT 插件（如 M2Mqtt），在场景中创建一个简单的 3D 模型代表无人机。
    2. **数据解析与渲染**：编写 C# 脚本挂载到模型上，连接本地的 Mosquitto，订阅 `swarm/drone/+/telemetry`。每次收到 JSON 数据，提取 X, Y, Z 并赋值给虚拟物体的 `transform.position`（使用 `Vector3.Lerp` 保证移动平滑）。
    3. **阶段验证**：启动系统（Mock Drone + 网关 + Unity），对着网关发送文字指令，Unity 里的模型会平滑飞向目标点。

### 阶段四：MARL 低层防撞模型与 Safety Gate 预演 (约 2-3 天)

- **目标**：在 Unity 中搭建 MARL 环境，训练高频防撞策略，并实现 Safety Gate 门控逻辑。
- **具体任务**：
    1. **MARL 环境搭建**：在 Unity 中引入 **ML-Agents** 插件。设置 2-4 架虚拟无人机，并定义状态空间（自身位置、队友位置、目标航点）与动作空间（高频速度向量）。
    2. **定义安全奖励函数**：设定"安全追踪航点"的奖励机制——向目标靠拢得正分（+1.0），多机间距过近或发生碰撞扣除重分（-10.0）。
    3. **【新增】实现 Safety Gate 模块**：编写 `safety_gate.py`，维护独立于 MARL 策略网络的碰撞风险评估器。第一版可用规则/CBF/人工势场实现 $u_{\text{safety}}$，确保在 MARL 未收敛时也能完成安全实验；随后再接入 MARL 策略网络。
    4. **【新增】实现碰撞风险评估器**：基于多机相对距离、接近速率和预测碰撞时间（TTC），实时计算 `collision_risk ∈ [0, 1]`。
    5. **模型导出**：在 Unity 仿真环境中进行加速训练，待避障策略收敛后，导出训练好的策略网络为 **`.onnx` 格式**，并固定 observation/action schema，保证 Python `onnxruntime` 推理端与 Unity 训练端一致。
    6. **阶段验证**：
        - 编写 Python 脚本调用 `onnxruntime` 加载模型，模拟多台 `mock_drone.py` 在收到极近的交叉目标时，MARL 模型或规则安全控制器输出安全速度向量，在 Unity 中验证多机无碰撞交叉运行。
        - **【新增】Safety Gate 触发测试**：故意注入碰撞航线，验证 gate 能否正确检测风险、平滑切换模式、并发送上行通知。

---

## 线下实战：5 天科研实践课题规划

### 第一天：Sim-to-Real（仿真到实体）与真机控制觉醒

#### 1. 课题描述
把预演阶段写好的 `mock_drone.py`（虚拟无人机）替换为真实的物理无人机。若真机只有一台，则本日重点验证单机通信闭环、定位、限速、悬停、软围栏和异常保护；多机交互由 MockDrone/Unity 补齐。体验"代码走向现实"的过程，解决真实物理世界中不可避免的网络延迟、丢包、传感器噪声和物理漂移问题。
工作流程：1. 组装 Crazyflie 无人机及通信基站（Crazyradio），确认可用定位方案（Lighthouse、Loco、MoCap 或 Flow Deck 等）；2. 将 `mock_drone.py` 改造为 `real_drone.py`，接入官方 `cflib`；3. 处理真实飞行中的控制频率、姿态稳定、急停、限高、限速和软围栏；4. 通过前期搭好的网关，用语音或文字指挥真机起飞。

#### 2. 课题目的
掌握底层飞控 API 集成，理解并解决"仿真模型"与"真实物理世界"的差异（Sim-to-Real Gap）。

#### 3. 验收评分
- **基础技能（60分）**：成功用 `real_drone.py` 连通真机，通过前置开发的 MQTT 网关下发指令，实现真机平稳起飞、悬停 10 秒并安全降落。
- **进阶技能（80分）**：在可用定位系统支持下实现三维坐标航点飞行（Waypoint Navigation）。若仅有 Flow Deck 或定位精度不足，则以小范围相对位移、稳定悬停和误差记录作为验收重点。
- **卓越技能（100分）**：实现完善的安全与异常处理机制。如检测到电量过低自动向 MQTT 发布警告并安全返航；在网络断开或丢包时，无人机能自动悬停而非失控。

---

### 第二天：物理世界的图像识别与空间坐标解算

#### 1. 课题描述
让无人机系统具备"感知"能力。通过外置上帝视角摄像头（或机载摄像头）捕捉真实世界的画面，利用目标检测/多目标追踪模型识别实体目标，再通过摄像头标定和场地坐标系完成空间坐标解算。VLM 主要用于语义理解和战术决策，不直接承担高精度几何测量。
工作流程：1. 架设全局视觉摄像头，拉取实时视频流；2. 部署目标检测模型或接入 VLM，识别画面中的实体目标（如扮演"恐怖分子"的移动小车）；3. 完成相机内参、外参和场地尺度标定，将 2D 像素坐标映射为物理空间坐标；4. 将目标位置通过 MQTT 实时发布。

#### 2. 课题目的
掌握真实的计算机视觉（CV）在机器人控制中的应用，实现物理坐标的解算与实时感知。

#### 3. 验收评分
- **基础技能（60分）**：成功拉取实时视频流，跑通目标检测算法，能够在画面中正确识别出"恐怖分子"或"营救目标"，并将像素坐标发布到 MQTT。
- **进阶技能（80分）**：实现高精度的 **2D-to-3D 空间映射**。当目标在地面移动时，能够实时、稳定地计算出其在物理场地中的 3D 空间坐标（误差在合理范围内）。
- **卓越技能（100分）**：实现多目标实时追踪（Multi-Object Tracking），并针对视频噪声和光照抖动进行卡尔曼滤波（Kalman Filter）平滑处理，输出高精度的目标运动轨迹。

---

### 第三天：大模型高层战术决策与单向分层 Baseline 建立（对照组）

#### 1. 课题描述
将多模态大模型（VLM/LLM）接入控制闭环，作为系统的"战略指挥官"。大模型通过分析视觉反馈产生高级战术，下发宏观航点。**本日建立纯单向分层架构（C2 条件），作为后续 Safety Gate（C3/C4）的对比 Baseline。**
工作流程：1. 网关将摄像头画面与当前无人机状态拼接后发送给 VLM；2. 设计 System Prompt 引导大模型输出 JSON 格式的战术坐标（如包抄、迂回航点）和简短可审计决策理由；3. 单台真机只执行限速、限高、软围栏内的安全航点，多机危险航点由 MockDrone/Unity 执行；4. **【科研任务】记录 Baseline 数据**：在仿真中观察大模型因推理延迟（约 1-2 秒）及缺乏防撞否决机制导致的近距离冲突或碰撞，在真机中只记录受控近失、延迟、偏差和软围栏拦截事件。

#### 2. 课题目的
掌握大模型的提示词工程（Prompt Engineering）与结构化输出设计，建立单向分层架构的完整性能 Baseline（包括仿真中的故意危险场景数据），为第四天引入 Safety Gate 提供对照组。

#### 3. 验收评分
- **基础技能（60分）**：实现 VLM 的感知决策闭环。大模型接收图片输入后，能自主分析战场态势并正确生成 JSON 格式的战术航点指令。
- **进阶技能（80分）**：展示清晰的**结构化决策理由**。控制台能实时输出简短、可审计的战术依据（例如："1 号机电量足，适合侦察；目标点为..."），不依赖或强制展示模型内部完整 Chain-of-Thought。
- **卓越技能（100分）**：完整采集单向分层 Baseline 数据——在仿真中的正常场景和故意注入的交叉航线/危险航点场景下，记录多机运行轨迹、碰撞次数、端到端时延数据；在单台真机上记录限速航点、软围栏拦截、延迟与偏差，为后续消融实验提供 C0/C1/C2 三组对照数据。

---

### 第四天：Safety Gate + 双向安全协议实机部署与验证（核心实验）

#### 1. 课题描述
在第三天的单向分层架构基础上，部署本方案的核心创新——**双向安全协议**。加载 Safety Gate 模块与 MQTT 反向反馈通道，验证当 LLM 下发危险指令时，低层 Pilot 能够显式接管控制权并向上通报。
工作流程：1. 将 Unity 导出的 `.onnx` MARL 模型或规则安全控制器加载至 Python 边缘控制网关；2. 部署 Safety Gate 模块（碰撞风险评估器 + β 平滑切换逻辑）；3. 编写本地高频控制回路（20Hz-50Hz），持续注入无人机当前坐标和 VLM 的宏观目标点，MQTT 只负责低频命令和事件；4. 配置 MQTT 反向通道，使 Pilot 在接管事件时向 LLM 网关发送 `override` 和 `status` 消息；5. **对比实验**：在仿真中复现第三天中导致碰撞的危险指令场景，在单台真机上复现虚拟障碍、越界航点或过近目标点，验证 Safety Gate 能否阻止危险动作。

#### 2. 课题目的
验证双向安全协议的有效性——Safety Gate 能否可靠检测碰撞风险并接管，LLM 收到反馈后能否调整后续战术。掌握安全控制器/策略网络在仿真与真机边缘网关中的部署，实现分层控制架构中低层安全否决权的落地。

#### 3. 验收评分
- **基础技能（60分）**：在 Python 边缘控制网关中成功导入 ONNX 格式的 MARL 模型或规则安全控制器并部署 Safety Gate 模块，跑通 C3 条件（单向 + Gate）的仿真多机控制流程。单台真机能完成越界航点拒绝、限速悬停或虚拟障碍避让，MQTT 反向通道能正确发送覆盖通知。
- **进阶技能（80分）**：完成 C3 vs C2 的消融对比实验。多机相撞航点在 MockDrone/Unity 中复现；单台真机只执行虚拟障碍、软围栏、过近目标点等受控危险输入。在 C2 下记录风险升高或软围栏拦截，在 C3 下门控主动触发接管。记录并对比碰撞率/近失率、安全接管次数与恢复时间。
- **卓越技能（100分）**：完成 **C4（双向完整协议）**的全链路验证。VLM 在收到 Pilot 回传的覆盖通知后，能据此调整后续战术（如重新规划绕行路线或替换执行机；真机只有一台时，替换执行机在仿真中验证）。采集完整的仿真消融实验数据（C0-C4 五组），产出包含碰撞率对比、安全接管统计、偏离-恢复时间、LLM 指令修正率等指标的学术图表。真机侧采集单机 Sim-to-Real 鲁棒性数据，包括定位噪声、延迟、软围栏触发和紧急悬停/降落事件。

---

### 第五天：仿真集群对抗、Benchmark 扩展与科研数据整合

#### 1. 课题描述
在 Unity 数字孪生战场中开展最终的多机集群对抗（4v4），在更大规模下验证双向安全协议的可扩展性。同时运行多组 Baseline 条件完成 Benchmark 数据采集，系统性整理全部科研数据。
工作流程：
1. 开启全仿真链路：Unity 场景渲染画面 → VLM 接收图像生成战术航点 → 航点下发至 MARL + Safety Gate → MARL 结合多机状态高频输出控制量 → 驱动 Unity 仿真无人机集群（4v4）协同围堵移动目标；
2. 在 Unity 环境中开展对抗演练："红军（传统规则/人类玩家操控）vs 蓝军（分层 AI 控制集群 + Safety Gate）"；
3. **Benchmark 扩展**：在 Unity 仿真中运行完整的五组消融条件（C0-C4），每组采集多轮对抗数据；
4. 运行仿真数据采集系统，导出三维轨迹、决策时延、碰撞率、安全接管统计与对战胜率，完成科研闭环。

#### 2. 课题目的
实现复杂智能系统在数字孪生环境中的全链路集成，完成系统性 Benchmark 评价，整理科研成果图表。

#### 3. 验收评分
- **基础技能（60分）**：完成 Unity 仿真全链路系统联调。4 架以上虚拟无人机能够稳定运行在分层控制 + Safety Gate 架构下，系统无崩溃或消息死锁。
- **进阶技能（80分）**：完成五组消融条件的 Benchmark 数据采集（C0-C4）。在 Unity 对抗中展现出 Safety Gate 带来的明显安全提升——碰撞率显著下降，同时任务完成率不降或微降。导出碰撞率对比柱状图、安全接管统计表、端到端延迟拆解图。
- **卓越技能（100分）**：成功落盘完整科研数据。导出包含以下全部标准学术图表的论文级实验成果：
    - **消融实验柱状图**：C0-C4 五组条件的碰撞率与任务成功率对比
    - **端到端时延拆解图（Latency Breakdown）**：VLM推理 / MQTT传输 / MARL推理 / Safety Gate判断 / 动作执行的各段延迟占比
    - **3D 仿真飞行轨迹对比图**：C2（单向分层，有碰撞）vs C4（双向完整，安全避让）在同一场景下的轨迹可视化
    - **安全接管事件时间线（Timeline）**：标注接管触发时刻、恢复时刻、LLM 指令修正时刻
    - **对战胜率与碰撞率对比表**：红军 vs 蓝军在不同消融条件下的对抗结果
    - 完成仿真对抗展示视频的录制
