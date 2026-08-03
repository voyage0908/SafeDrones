# AI 无人机机群对战：LLM-MARL 分层控制架构与双向安全协议

## 项目定位

本方案在学术界前沿的**分层控制架构（Hierarchical Control Architecture）**基础上，针对 LLM+MARL 系统中常见的**高层语义决策与低层安全执行脱节**问题，提出**双向安全协议（Bidirectional Safety Protocol）**——包含 Safety Gate（下行否决）和状态反馈（上行报告）两条通道，使运行时安全层能够在碰撞风险超过阈值时显式覆盖高层指令并主动通报。由**多模态大模型（VLM/LLM）充当高层"指挥官（Commander）"**，负责低频、全局的语义感知、战术协同、角色分配和任务级航点规划；由**多智能体强化学习（MARL）模型或可解释规则控制器充当低层"飞行员（Pilot）"**，负责高频、局部的目标追踪和平滑执行；由**独立 Safety Gate** 负责碰撞风险评估、硬安全否决和紧急避险。真机资源不足时，单台 Crazyflie 用于验证 Sim-to-Real、通信闭环和单机安全接管，多机碰撞消融与 4v4 对抗主要在 MockDrone/Unity 仿真环境中完成。

因此本文不把 MARL 训练定位为主要创新，也不让 MARL 承担最终战术决策或最低安全距离保证。LLM/VLA 输出自然语言战术意图、结构化角色、航点和约束；MARL/Rule Pilot 只消费这些结构化目标和本地多机状态，输出短周期 nominal setpoint；Safety Gate 作为独立运行时安全层检查并可覆盖 Pilot 输出。最终 MARL/ONNX 的作用是提供一个更复杂、更自主的低层协同执行基线，用来评估 Safety Gate 在 learned multi-agent tactical behavior 中是否仍能稳定降低碰撞与近失。

---

## 相关工作（Related Work）

近年来，LLM/VLM 与 MARL、无人机集群控制、运行时安全过滤等方向均已有大量工作。因此本文的定位是：**在已有 LLM-MARL 分层架构和安全过滤方法之上，把低层运行时安全否决、结构化上行反馈、LLM 战术修正和消融验证统一成一个可审计的双向安全协议**。

这里将"完全一致"限定为同时满足以下条件：1）LLM/VLM 作为高层 Commander 生成语义战术、角色分配、约束或航点；2）MARL 或低层 Pilot 负责高频 nominal 控制和目标追踪，而不是硬安全兜底；3）低层存在独立 Safety Gate/Shield，可在运行时显式否决 Pilot 输出或高层指令；4）否决原因以结构化事件反馈给 LLM；5）LLM 根据反馈修正后续战术；6）在无人机机群仿真或真机链路中通过 C2/C3/C4 等消融实验验证该协议贡献。

### LLM + MARL 分层控制架构（最相关）

| 论文 | 年份/会议 | 架构 | 与本工作的区别 |
|------|-----------|------|---------------|
| **RALLY** (Wang et al.) | 2025, IEEE | LLM 语义共识 + RMIX 角色分配 + MARL 导航 + PID 飞控 | 已非常接近 LLM+MARL+UAV swarm 分层控制；但主线是角色分配、协同导航和任务执行，未把低层运行时安全否决与结构化 override 反馈作为核心协议对象 |
| **Cognitive Synergetic Hierarchical Framework** (Wang et al.) | 2026, Frontiers in Neurorobotics | DeepSeek-R1 战略脑 + MAPPO+GAT 战术体，云端-边缘协同推理 | 包含解析层安全校验、非法动作拒绝、反思/演化式调整等机制；但更偏向格式/边界合法性与策略改进，不是基于碰撞风险的高频 Safety Gate 接管和上行事件协议 |
| **LEHCA** (Bai et al.) | 2026, Nature Scientific Reports | LLM Commander 下发子目标 + QMIX 执行 | 以高层子目标分解、语义奖励塑形和 action masking 提升 MARL 学习效率为主，验证环境偏 SMAC/MPE；不是无人机物理安全运行时协议 |
| **Ground Control to SMAX** (Fontes de Matos) | 2025, IST Lisbon | LLM 宏指令 + MAPPO 微操，通过偏离惩罚保证服从 | 有高层语言指令到低层策略的映射和偏离惩罚，但缺少低层显式否决权、override 日志和反馈驱动的 LLM 重规划 |

### LLM + 无人机/机器人安全控制（相近但不等同）

| 工作 | 核心思路 | 与本工作的区别 |
|------|----------|---------------|
| **SwarmGPT** | 用 LLM 生成多无人机编队/编舞指令，并通过安全运动规划或 filter 生成可执行轨迹，包含 Crazyflie 真机展示 | 有 LLM 和安全过滤，但任务主要是编队编舞；低层不是 MARL Pilot，也没有把安全接管事件反馈给 LLM 作为战术修正依据 |
| **TACOS** | LLM Coordinator/Supervisor 将自然语言任务拆解为多无人机 API 调用，并由底层模块保证轨迹安全 | 有闭环监督和安全执行接口，但主线是任务/API 编排；不是 LLM-MARL 分层控制，也没有以 Safety Gate override 为核心消融对象 |
| **Universal LLM Drone / 类似自然语言无人机框架** | 使用 LLM 将自然语言转换为无人机控制命令，并加入人工接管、规则限制或安全防火墙 | 主要解决自然语言可用性和人机交互安全；通常缺少多机 MARL、运行时碰撞风险接管和结构化上行反馈 |

### Safe MARL / Safety Shield / CBF 类工作（安全机制来源）

| 工作类型 | 核心思路 | 与本工作的关系 |
|----------|----------|---------------|
| **安全 MARL / action shield** | 在多智能体强化学习动作输出后增加 safety network、规则 shield 或控制屏障函数，过滤危险动作 | 可作为本方案 Safety Gate 的低层实现来源；但一般没有 LLM Commander，也不研究 LLM 收到安全反馈后的战术修正 |
| **CBF / 人工势场 / MPC 避障控制** | 用可解释控制律保证距离、速度、高度、围栏等硬约束 | 可作为第一版 `u_safety`，用于在 MARL 未收敛或真机实验风险较高时提供确定性安全兜底 |

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

### 本方案聚焦的协议缺口

从运行时安全协议角度看，相关工作已经覆盖了 LLM 分层指挥、MARL 低层执行、安全轨迹过滤和反思式调整等局部能力；本文聚焦的是这些能力之间的接口缺口，而不是否认已有工作中的安全机制。需要重点对齐和验证的系统能力包括：

1. **安全过滤常被隐藏在执行层内部**：已有系统可能有轨迹过滤、动作裁剪或人工接管，但这些机制未必以可审计的 `safety_override` 事件暴露给高层决策器；
2. **低层否决与高层重规划之间缺少稳定协议**：低层即使发现航点不可达、存在碰撞风险或违反围栏约束，也未必把原因、风险等级、关联指令和恢复时间结构化反馈给 LLM；
3. **安全贡献缺少直接消融隔离**：许多系统同时改变任务规划、低层策略和安全约束，难以单独回答 Safety Gate 和上行反馈分别贡献了多少；
4. **LLM 指令缺少运行时约束字段**：高层指令往往不附带置信度、有效期、优先级、命令 ID 和安全边界，低层执行器难以判断何时降级、拒绝或过期丢弃。

本文的工作正是针对上述缺口提出双向安全协议（Bidirectional Safety Protocol）：下行由 Safety Gate 显式否决或平滑接管危险指令，上行由 Safety Gate/Pilot 将状态、原因和恢复预期反馈给 LLM，并通过 C2/C3/C4 消融实验分别衡量"单向分层"、"仅有 Safety Gate"和"完整双向反馈"的差异。

---

## 核心创新：双向安全协议（Bidirectional Safety Protocol）

本文的核心创新不是单独提出 LLM 指挥、MARL 控制或安全避障，这些方向已有相近工作；创新点在于把三者之间的运行时关系协议化：**LLM/VLA 拥有低频战术协同与任务级重规划权，MARL/Rule Pilot 负责高频 nominal 执行，独立 Safety Gate 拥有硬安全否决权，而否决事件必须以结构化、可追踪、可被 LLM 消化的形式返回高层**。这样，系统安全不依赖 LLM 的即时判断，也不把低层安全接管隐藏在不可观察的控制误差中。

### LLM/VLA 与 MARL/Pilot 的接口约束

本方案采用语义层与控制层解耦的双通道接口。LLM/VLA 的自然语言战术意图不直接输入 MARL，而是被转换为可执行、可校验的结构化计划：

```json
{
  "intent_text": "1号从左侧通过，2号保持右侧间隔，风险解除后继续原目标",
  "commands": [
    {
      "drone": 1,
      "role": "left_pass",
      "action": "move_to",
      "waypoint": [3.0, -0.4, 1.0],
      "priority": "normal",
      "ttl_sec": 8.0
    }
  ],
  "constraints": {
    "keep_min_distance_m": 1.6,
    "avoid_center_zone": true
  }
}
```

执行层只消费结构化字段。MARL/Rule Pilot 的输入是数值 observation，例如自身位置/速度、目标相对向量、邻机相对位置/速度、可选角色编码和局部感知估计；输出是速度向量或短周期 micro-waypoint。自然语言 `intent_text` 和 `rationale` 用于人类审计、LLM 反馈重规划和论文日志，不作为低层网络的原始输入。

MARL 的训练数据来自仿真 rollout，而不是人工文本标注：环境不断生成多机 episode，记录 `observation, action, reward, next_observation, done`。第一版应使用 vector observation 和小规模 MLP 策略，在 Python/VMAS/Unity ML-Agents 等轻量环境中训练；图片数据属于感知/VLM 数据集，不作为第一版 MARL 的端到端输入。最终评估关注 `MARL only`、`MARL + Safety Gate`、`Rule Pilot + Safety Gate` 和 `C4 双向协议` 的消融差异，尤其是 Safety Gate 在 learned tactical execution 中降低碰撞和近失的贡献。

### 问题定义

在现有分层架构中：

```
LLM (Commander) ──单向航点──→ Pilot/MARL ──→ 物理动作
```

LLM 下发指令后，如果系统没有显式反馈协议，就难以及时获知执行层的真实状态。已有系统通常会采用三类处理方式：第一类让低层策略通过 reward、动作裁剪或控制误差被动修正危险指令；第二类通过轨迹规划器或安全 filter 在执行前修正动作，但不一定把修正原因反馈给 LLM；第三类让 LLM 在失败后反思，但失败信号往往不是由高频安全层生成的结构化 override 事件。结果是运行时安全层即使检测到 LLM 指令或 Pilot 输出将导致碰撞，也可能无法（1）明确接管控制权，（2）通知 LLM "你的指令有问题"，（3）为后续实验留下可审计事件日志。

### 协议设计

本方案引入两条新通道：

```
                    ┌── 航点指令 + 优先级 ──→ Pilot/MARL
LLM (Commander) ───┤
                    ←── 状态反馈 / 覆盖通知 ──┘
                             (Safety Gate)
```

#### 通道 1：Safety Gate（下行否决 + 模式切换）

低层执行侧维护一个独立于 LLM 和 MARL Pilot 的 Safety Gate 碰撞风险评估器。该评估器可以先由规则/控制屏障函数（Control Barrier Function, CBF）或人工势场实现，后续可升级为更强的安全过滤器，但不应被 nominal MARL Pilot 替代。当风险超过阈值时，控制模式从"追随 LLM/VLA 战术航点"自动切换为"纯安全避险"：

**控制律：**

$$u = \text{clip}\left(\beta \cdot u_{\text{follow\_LLM}} + (1-\beta) \cdot u_{\text{safety}},\; u_{\min},\; u_{\max}\right)$$

其中 $u_{\text{follow\_LLM}}$ 为 Pilot/MARL 追踪高层航点的 nominal 速度/加速度控制量，$u_{\text{safety}}$ 为 Safety Gate 生成的避障、悬停、降速或返航控制量。最终控制量必须经过速度、加速度、高度、地理围栏和电量等硬约束裁剪。

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

### Benchmark 对齐策略

为避免仅在自定义 Unity demo 中验证而形成"自说自话"，实验设计采用三层 Benchmark 结构。本文不把私有场景中的一次演示作为主要证据，而是把双向安全协议放到固定场景、公共环境和可复现消融中共同验证。

| 层级 | 作用 | 候选环境/任务 | 主要回答的问题 |
|------|------|---------------|----------------|
| 公共 MARL / SafeRL 层 | 验证低层多智能体导航与安全约束能力 | VMAS、PettingZoo/MPE、Safety-Gymnasium | 低层 Pilot 与 Safety Gate 是否在标准化多智能体避障任务中降低碰撞/约束违反 |
| 无人机动力学层 | 验证无人机运动约束下的可迁移性 | gym-pybullet-drones、Unity/Crazyflie 近似动力学 | 策略在速度、加速度、高度和刹车距离约束下是否仍然有效 |
| 系统协议层 | 验证 LLM Commander、Pilot、Safety Gate、上行反馈的接口贡献 | MockDrone/Unity 固定场景套件 | C2/C3/C4 消融中，Safety Gate 与反馈通道分别贡献多少 |

所有系统协议实验必须固定场景定义、随机种子、日志 schema 和指标计算脚本。至少包含以下场景：

| 场景 | 描述 | 目标 |
|------|------|------|
| `head_on_crossing` | 两机相向交换位置 | 验证正面对冲风险检测、避让和恢复 |
| `perpendicular_crossing` | 两机垂直航线交叉 | 验证 TTC 风险和侧向避让 |
| `narrow_passage` | 多机通过狭窄通道 | 验证拥挤环境下的排队/让行 |
| `moving_obstacle` | 移动障碍物穿过航线 | 验证动态障碍物风险 |
| `geofence_violation` | LLM 下发越界航点 | 验证软围栏和拒绝/改写逻辑 |
| `llm_timeout` | LLM 响应延迟 3-5 秒 | 验证无新指令时的安全保持 |
| `packet_loss` | 随机丢弃 MQTT 消息 | 验证协议在不可靠通信下的鲁棒性 |

这样论文贡献可以表述为：不是提出一个孤立 demo，而是在已有 MARL/SafeRL/无人机仿真 Benchmark 之上，给 LLM-MARL 分层控制系统补充一套可审计的安全协议评测方法。

### 核心消融实验

为验证双向安全协议的贡献，设计以下消融条件：

| 条件 | LLM→Pilot | Pilot→LLM 反馈 | Safety Gate | 说明 |
|------|----------|--------------|-------------|------|
| **C0** (纯 LLM) | 直接控制 | 无 | 无 | 纯 LLM 输出动作，作为仿真中的危险 Baseline；真机只允许限速近失测试 |
| **C1** (纯 Pilot) | 无 LLM | 无 | 无 | 规则 Pilot 或 MARL Pilot 自主导航，无独立硬安全兜底 |
| **C2** (单向分层) | 航点 | 无 | 无 | 复现 RALLY/CogSyn 等现有方案 |
| **C3** (单向 + Gate) | 航点 | 无 | ✅ | 仅加 Safety Gate，无反馈 |
| **C4** (双向完整) | 航点 | ✅ 反馈 | ✅ | 本方案的完整版本 |

### 评价指标

| 指标 | 含义 | 期望趋势 |
|------|------|----------|
| 碰撞率 (Collision Rate) | 每飞行小时的多机碰撞次数 | C4 < C2 < C0 |
| 任务完成率 (Task Success Rate) | LLM 战术目标的达成比例 | C4 ≥ C2 |
| 端到端延迟 (Latency Breakdown) | VLM 推理 → MQTT 传输 → Pilot/MARL 推理 → Safety Gate 判断 → 动作执行 的分段时延 | 量化各段贡献 |
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

### 仿真抽象与 Crazyflie 真机接口对齐

本项目不把 Unity/MockDrone 作为电机级或螺旋桨级动力学仿真器，而是把它作为 **Crazyflie 闭环飞控外部行为的近似**。根据 Bitcraze 官方 `cflib` 和 Crazyflie Commander Framework，真实 Crazyflie 通常接收的是 position、velocity、hover 或 full-state setpoint；固件内部再完成位置/速度控制、姿态控制、电机混控和 PWM/推力输出。因此本项目的 MARL/Pilot/Safety Gate 不直接输出 motor RPM，而是输出可解释、可裁剪的航点、速度、悬停或刹车指令。

仿真层需要建模的是闭环执行边界，而不是电机细节：

- `max_speed_mps`
- `max_accel_mps2` / `max_decel_mps2`
- `max_yaw_rate_dps`
- 高度上下限与软围栏
- 命令延迟、定位噪声和刹车距离（后续扩展）

这些参数后续通过低风险真机辨识实验得到，例如阶跃速度响应、刹车距离、悬停噪声和命令延迟测试；在真机接入前，MockDrone 使用保守默认值。这样 Unity 仿真、Safety Gate 和 Crazyflie 真机接口保持同一层抽象：上层发安全 setpoint，下层执行受约束闭环运动。

### 阶段一：搭建消息总线与"虚拟无人机" (约 1-2 天)

- **目标**：跑通底层的 MQTT 发布/订阅机制，在没有任何硬件的情况下，让"虚拟飞机"在数据流中飞起来。
- **具体任务**：
    1. **安装环境**：电脑安装并运行 **Eclipse Mosquitto** (作为消息总线中心)；下载并安装桌面端调试工具 **MQTT Explorer** (用于可视化查看数据流)。
    2. **编写 `mock_drone.py`**：
        - 使用 `paho-mqtt` 库连接本地的 Mosquitto。
        - **状态模拟**：在脚本中定义无人机的当前坐标 `current_pos = [0,0,0]`、速度 `velocity = [0,0,0]`、朝向 `yaw_deg` 和目标坐标 `target_pos = [0,0,0]`。
        - **指令订阅**：订阅主题 `swarm/drone/1/command`。当收到指令 `{"action": "move_to", "target": [5, 5, 2]}` 时，更新 `target_pos`。
        - **物理模拟与上报（主循环）**：开启一个 `while True` 循环，每 0.1 秒执行一次：
            - 利用带约束的运动学逻辑，让 `current_pos` 在 `max_speed_mps`、`max_accel_mps2` 和 `max_yaw_rate_dps` 限制下向 `target_pos` 移动，而不是瞬间跳转或无惯性匀速移动。
            - 将计算后的状态封装为 JSON：`{"position": [...], "velocity": [...], "yaw_deg": ..., "status": "flying"}`。
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

### 阶段四：Benchmark 化 Safety Gate 与低层 Pilot 消融预演 (约 2-3 天)

- **目标**：先建立可复现的安全协议 Benchmark 和 C2/C3/C4 消融流程，其中 C4 必须接入真实 DeepSeek LLM API 做反馈重规划；再把 MARL/ONNX 作为可替换低层 Pilot 接入。阶段四的第一优先级不是训练出复杂 MARL，而是证明 LLM/VLA 战术航点、低层 nominal Pilot、独立 Safety Gate 和上行反馈之间的接口贡献可以被稳定测量。
- **具体任务**：
    1. **固定 Benchmark 场景与数据格式**：定义 `head_on_crossing`、`perpendicular_crossing`、`narrow_passage`、`moving_obstacle`、`geofence_violation`、`llm_timeout`、`packet_loss` 等场景。每个场景固定初始位置、目标点、障碍物、随机种子、运行时长和成功条件。
    2. **实现统一指标与日志**：每轮实验落盘 JSONL 事件日志和 CSV 指标，至少包含 `collision_count`、`near_miss_count`、`min_distance_m`、`ttc_violation_count`、`task_success`、`override_count`、`recovery_time_sec`、`path_efficiency`、`latency_breakdown`、`command_revision_rate`。
    3. **实现 C2/C3/C4 runner**：C2 为单向分层（LLM/VLA 或 Benchmark Commander 下发战术航点，Pilot 追随航点，无 Gate）；C3 为单向 + Safety Gate（有否决但无 LLM 反馈重规划）；C4 为完整双向协议（Gate 触发后向真实 DeepSeek LLM Replanner 上报，随后由 LLM 返回恢复航点、绕行航点或换机指令）。不把规则伪 Replanner 当作 C4 结果。
    4. **完善 Safety Gate 模块**：编写并测试 `safety_gate.py`，维护独立于 MARL 策略网络的碰撞风险评估器。第一版使用规则/CBF/人工势场实现 $u_{\text{safety}}$，必须避免"只悬停不恢复"的死锁；高风险时接管，风险下降后恢复追踪。
    5. **实现碰撞风险评估器**：基于多机相对距离、接近速率、预测碰撞时间（TTC）和软围栏约束，实时计算 `collision_risk ∈ [0, 1]`，并记录触发对象、风险来源和恢复耗时。
    6. **实现低层 Pilot 基线**：先提供 Rule Pilot 作为可解释 nominal 执行基线，输出高频速度向量或短周期 micro-waypoint；Rule Pilot 不承担硬安全避险，侧向安全分离和最低安全距离由 Safety Gate 负责。再预留 MARL Pilot 接口，保证 observation/action schema 固定。
    7. **公共 Benchmark 对齐**：至少选择一个公共环境（VMAS/PettingZoo/MPE/Safety-Gymnasium/gym-pybullet-drones）复现同类 crossing 或 navigation 任务，并用相同指标记录 Rule Pilot / MARL Pilot / Safety Gate 的差异。MARL 只作为 learned nominal Pilot，不作为本文主要算法贡献。
    8. **MARL/ONNX 后置接入**：在 Benchmark harness 稳定后，再在 Unity ML-Agents、VMAS 或 gym-pybullet-drones 中训练 MARL Pilot。训练目标是更平滑地执行 LLM/VLA 给出的战术航点、减少不必要接管、提高任务效率；收敛后导出 `.onnx`，用 Python `onnxruntime` 接入 `marl_pilot.py`，与 Rule Pilot 在同一套场景中对比。
    9. **阶段验证**：
        - 固定交叉航线场景中，C3 必须触发 Safety Gate、保持最小安全距离，并能在风险解除后继续完成任务；
        - C3 vs C2 至少完成 10 个随机种子统计，输出碰撞率/近失率/接管次数/恢复时间对比；
        - C4 至少用真实 DeepSeek LLM API 完成一次"收到 override 后重新规划"闭环，并记录 LLM 延迟、修正次数和失败原因；
        - Unity 可视化能展示正常、预警、接管和恢复状态。

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

### 第四天：Benchmark 化 Safety Gate + 双向安全协议部署与验证（核心实验）

#### 1. 课题描述
在第三天的单向分层架构基础上，部署本方案的核心创新——**双向安全协议**，并把验证方式从单次 demo 改为固定 Benchmark 消融。加载 Safety Gate 模块、低层 Pilot、MQTT 反向反馈通道和 DeepSeek LLM Replanner，验证当 LLM/VLA 下发危险战术航点或 Pilot 输出危险 micro-waypoint 时，独立 Safety Gate 能够显式接管控制权、上报原因，随后由 LLM 消费 override 事件并发布任务级恢复航点。
工作流程：1. 使用规则 Pilot 作为第一版低层 nominal 控制基线，后续可替换为 ONNX MARL Pilot；2. 部署 Safety Gate 模块（碰撞风险评估器 + β 平滑切换/安全恢复逻辑）；3. 编写本地高频控制回路（20Hz-50Hz），持续注入无人机当前坐标和 VLM 的宏观目标点，MQTT 只负责低频命令和事件；4. 配置 MQTT 反向通道，使 Safety Gate/Pilot 在接管事件时向 LLM 网关发送 `override` 和 `status` 消息；5. 在固定 Benchmark 场景中运行 C2/C3/C4 消融，C4 必须真实调用 DeepSeek API 完成反馈重规划；6. 在单台真机上只复现虚拟障碍、越界航点或过近目标点等受控危险输入，验证 Safety Gate 能否阻止危险动作并记录 Sim-to-Real 数据。

#### 2. 课题目的
验证双向安全协议的有效性，并保证结果可复现、可对比。核心问题包括：Safety Gate 能否可靠检测风险并接管；接管是否会导致任务死锁；DeepSeek 收到结构化 override 后是否能生成有效恢复航点；同一危险场景下 C3/C4 相比 C2 是否显著降低碰撞率和近失率。

#### 3. 验收评分
- **基础技能（60分）**：在 Python 边缘控制网关中成功部署规则 Pilot + Safety Gate，跑通 C3 条件（单向 + Gate）的仿真多机控制流程。Safety Gate 能发布 `safety_override` 和 `safety_status`，并在固定交叉航线中保持最小安全距离。单台真机能完成越界航点拒绝、限速悬停或虚拟障碍避让。
- **进阶技能（80分）**：完成 C3 vs C2 的固定 Benchmark 消融对比。多机相撞航点在 MockDrone/Unity 中复现；C2 记录碰撞/近失/风险升高，C3 记录主动接管、最小距离、恢复时间和任务完成率。每个核心场景至少运行 10 个随机种子，并导出 JSONL/CSV 结果。
- **卓越技能（100分）**：完成 **C4（双向完整协议）**的全链路验证。DeepSeek LLM Replanner 在收到 Safety Gate/Pilot 回传的覆盖通知后，能据此调整后续战术（如重新规划恢复航点、绕行路线或替换执行机；真机只有一台时，替换执行机在仿真中验证）。采集 C0-C4 五组仿真消融数据，并至少接入一个公共 Benchmark 环境用于外部对齐。产出包含碰撞率对比、安全接管统计、偏离-恢复时间、LLM 指令修正率和 LLM 延迟等指标的学术图表。

---

### 第五天：MARL/ONNX Pilot 接入、Benchmark 扩展与科研数据整合

#### 1. 课题描述
在第四天已经稳定的 Benchmark harness 上接入真正的 MARL/ONNX Pilot，并开展最终的多机集群对抗（4v4）。第五天不再从零开始设计 Benchmark，而是扩大场景规模、补公共环境对齐、采集多种 Pilot 和协议条件下的数据，系统性整理科研结果。这里的 MARL 不是高层战术大脑，而是 learned low-level multi-agent motion controller；多机战术协同（分工、包抄、等待、恢复航点）由 LLM/VLA Commander 输出，MARL/Rule Pilot 负责把结构化战术目标平滑执行，Safety Gate 负责运行时硬安全。
工作流程：
1. 在 Unity ML-Agents、VMAS 或 gym-pybullet-drones 中训练低层 MARL Pilot，固定 observation/action schema；训练数据来自仿真 rollout，而不是自然语言文本，输入为自身状态、目标相对向量、邻机相对位置/速度和可选角色编码；
2. 导出 `.onnx` 策略网络，并用 Python `onnxruntime` 接入 `marl_pilot.py`；
3. 在同一套 Benchmark 场景中对比 Rule Pilot、Rule Pilot + Gate、MARL Pilot、MARL Pilot + Gate、C4 双向协议；
4. 开启全仿真链路：Unity 场景渲染画面 → VLM/LLM 接收图像和状态生成战术意图、角色和航点 → 结构化战术命令下发至 Pilot + Safety Gate → Pilot 结合多机状态高频输出 nominal setpoint → Safety Gate 检查并可覆盖 → 驱动 Unity 仿真无人机集群（4v4）协同围堵移动目标；
5. 在 Unity 环境中开展对抗演练："红军（传统规则/人类玩家操控）vs 蓝军（分层 AI 控制集群 + Safety Gate）"；
6. 运行仿真数据采集系统，导出三维轨迹、决策时延、碰撞率、安全接管统计与对战胜率，完成科研闭环。

#### 2. 课题目的
实现复杂智能系统在数字孪生环境中的全链路集成，验证 LLM/VLA 战术协同、MARL/Rule Pilot 低层执行和 Safety Gate 硬安全之间的接口贡献；MARL Pilot 相比规则 Pilot 的增益作为次要结果，系统性 Benchmark 评价和协议消融仍是主线。

#### 3. 验收评分
- **基础技能（60分）**：完成 Unity 仿真全链路系统联调。4 架以上虚拟无人机能够稳定运行在 Rule Pilot + Safety Gate 架构下，系统无崩溃或消息死锁，并能落盘标准 JSONL/CSV 日志。
- **进阶技能（80分）**：完成五组消融条件的 Benchmark 数据采集（C0-C4），并完成 Rule Pilot vs MARL Pilot 的至少一组对比。在 Unity 对抗中展现出 Safety Gate 带来的明显安全提升——碰撞率显著下降，同时任务完成率不降或微降。导出碰撞率对比柱状图、安全接管统计表、端到端延迟拆解图。
- **卓越技能（100分）**：成功落盘完整科研数据，并完成公共 Benchmark 对齐实验。导出包含以下全部标准学术图表的论文级实验成果：
    - **消融实验柱状图**：C0-C4 五组条件的碰撞率与任务成功率对比
    - **端到端时延拆解图（Latency Breakdown）**：VLM推理 / MQTT传输 / Pilot/MARL推理 / Safety Gate判断 / 动作执行的各段延迟占比
    - **3D 仿真飞行轨迹对比图**：C2（单向分层，有碰撞）vs C4（双向完整，安全避让）在同一场景下的轨迹可视化
    - **安全接管事件时间线（Timeline）**：标注接管触发时刻、恢复时刻、LLM 指令修正时刻
    - **对战胜率与碰撞率对比表**：红军 vs 蓝军在不同消融条件下的对抗结果
    - 完成仿真对抗展示视频的录制
