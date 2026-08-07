# SafeDrones — AI 无人机蜂群安全系统

基于 **LLM/VLM 高层指挥官 + MARL 低层飞控 + 独立安全门** 的分层架构，实现无人机蜂群的安全协同作战。

## 项目定位

- **高层**：多模态 LLM/VLM（DeepSeek）作为"指挥官"，负责语义感知、战术协调、角色分配、任务航点生成
- **低层**：MARL 模型 / 可解释规则控制器作为"飞控"，负责高频目标跟踪与平滑执行
- **安全层**：独立的 **Safety Gate**（安全门），负责碰撞风险评估、硬安全否决、紧急避障
- **核心贡献**：**双向安全协议** — 下行安全否决 + 上行状态反馈，安全层可覆盖高层指令并通过结构化事件通知 LLM 重新规划

## 架构概览

```
LLM/VLM 指挥官 (gateway.py)
    │ 结构化 JSON 航点 (command_id, confidence, ttl, priority)
    ▼
MARL 飞控 (marl_pilot.py)
    │ 高频微步航点 (20-50Hz)
    ▼
Safety Gate (safety_gate.py)  ←→ 飞控状态/遥测
    │ 安全否决 / 避障分流航点
    ▼
无人机 (mock_drone.py / Crazyflie 真机)
    │ 遥测数据 (位置、速度、状态)
    └──→ MQTT 总线 ──→ Unity 可视化 (unity/SwarmUnityDemo)
```

## 四阶段实施

| 阶段 | 内容 | 文档 |
|------|------|------|
| **阶段一** | MockDrone + MQTT 最小通信闭环 | [docs/stage1/README.md](docs/stage1/README.md) |
| **阶段二** | FastAPI 网关 + LLM 指挥官 | [docs/stage2/README.md](docs/stage2/README.md) |
| **阶段三** | Unity 3D 视觉孪生 | [docs/stage3/README.md](docs/stage3/README.md) |
| **阶段四** | Safety Gate + 消融实验基准 | [docs/stage4/README.md](docs/stage4/README.md) |

## 环境要求

- Python ≥ 3.10
- Conda 环境 `eai-swarm`（推荐）或 `pip install -r requirements.txt`
- MQTT Broker（开发用 `amqtt`，生产用 Mosquitto）
- [可选] Unity 2021.3+ + M2Mqtt 插件（可视化）
- [可选] DeepSeek API Key（LLM 指挥）

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动 MQTT Broker（终端 1）
python scripts/dev_broker.py

# 3. 启动虚拟无人机（终端 2）
python mock_drone.py --drone-id 1

# 4. 发送测试指令（终端 3）
python scripts/publish_command.py --drone-id 1 --target 5 5 2

# 5. 验证通信闭环
python scripts/smoke_stage1.py
```

## 全栈集成

```bash
# 终端 1: MQTT Broker
python scripts/dev_broker.py

# 终端 2: 无人机 1
python mock_drone.py --drone-id 1

# 终端 3: 无人机 2
python mock_drone.py --drone-id 2

# 终端 4: FastAPI 网关
LLM_PROVIDER=heuristic uvicorn gateway:app --host 127.0.0.1 --port 8000

# 终端 5: MARL 飞控
python marl_pilot.py

# 终端 6: Safety Gate
python safety_gate.py

# 发送指令
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[5,5,2]}'
```

## 运行测试

```bash
# 全部单元测试（40 个测试）
python -m unittest discover -s tests -v

# 单模块测试
python -m unittest tests.test_simulation
python -m unittest tests.test_safety
python -m unittest tests.test_marl
```

## 消融实验基准

```bash
# C2 vs C3 vs C4 消融实验（阶段四核心）
python scripts/stage4_benchmark.py \
  --scenario all \
  --conditions C2,C3,C4 \
  --seeds 10 \
  --out results/stage4
```

### 实验条件

| 条件 | 说明 |
|------|------|
| C0 | 纯 LLM 直出动作（危险基线，仅仿真） |
| C1 | 纯飞控，无 LLM，无安全 |
| C2 | 单向分层：LLM 航点 → 飞控，无安全门 |
| C3 | C2 + Safety Gate，无 LLM 反馈 |
| C4 | 完整双向协议：航点 + 反馈 + Safety Gate |

### 已验证结果（10 seeds × 3 scenarios）

| 条件 | 成功率 | 碰撞率 | 平均最小距离 | 平均接管次数 |
|------|--------|--------|-------------|-------------|
| C2 | 0% | 100% | 0.00m | 0 |
| C3 | 100% | 0% | 0.89m | 2.13 |
| C4 | 96.7% | 0% | 0.92m | 2.13 |

→ [完整结果](docs/stage4/RESULTS.md)

## 项目结构

```
SafeDrones/
├── swarm/                  # Python 核心包
│   ├── simulation.py       # 无人机运动学模拟
│   ├── safety.py           # Safety Gate 碰撞风险评估
│   ├── marl.py             # MARL 飞控（规则基线 + ONNX 推理）
│   ├── llm_provider.py     # LLM 指挥（DeepSeek / OpenAI 兼容）
│   └── mqtt_gateway.py     # MQTT 网关封装
├── scripts/                # 工具脚本
│   ├── dev_broker.py       # 开发用 MQTT Broker
│   ├── smoke_stage1.py     # 阶段一冒烟测试
│   ├── stage4_benchmark.py # 消融实验自动化 Runner
│   └── stage4_demo.sh      # Unity 演示一键脚本
├── tests/                  # 单元测试（40 个）
├── docs/                   # 文档（中文）
│   ├── plan.md             # 详细技术方案
│   ├── original_plan.md    # 原始课程计划
│   └── stage{1,2,3,4}/     # 各阶段文档
├── unity/                  # Unity 3D 可视化项目
├── config/                 # MQTT 配置
├── gateway.py              # FastAPI 网关入口
├── mock_drone.py           # 虚拟无人机入口
├── safety_gate.py          # Safety Gate 入口
├── marl_pilot.py           # MARL 飞控入口
└── AGENTS.md               # AI Agent 开发指引
```

## 关键技术决策

1. **MQTT 仅承载低频指令/事件**，硬实时安全判断在本地 20-50Hz 闭环执行
2. **LLM 指令必须携带** `command_id`, `confidence`, `ttl_sec`, `priority`，飞控可据此降级/拒绝/过期
3. **所有消融实验固定随机种子**、固定场景定义、统一日志格式
4. **C4 必须使用真实 LLM API**，不允许规则伪 Replanner
5. **MockDrone 模拟 Crazyflie 闭环外部行为**（速度/加速度/偏航率/高度限制），非电机级动力学

## 许可证

待定
