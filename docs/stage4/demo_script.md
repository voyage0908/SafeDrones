# 阶段四 Unity Safety Gate Demo 脚本

本脚本用于展示 C3 条件下的 Safety Gate 可视化效果：两架虚拟无人机收到危险交叉航点后，Safety Gate 负责短时安全分离，风险解除后由 `marl_pilot.py` 继续推动无人机完成原始高层目标。

一键运行：

```bash
bash scripts/stage4_demo.sh
```

默认脚本会使用 `seed=0` 依次复现当初 10 轮测试中的三种 C3 benchmark 场景：

1. `head_on_crossing`：正面对冲交叉；
2. `perpendicular_crossing`：垂直航线交叉；
3. `diagonal_crossing`：对角航线交叉。

如果只想演示单个场景，使用 `--scenario`：

```bash
bash scripts/stage4_demo.sh --scenario head_on_crossing --seed 0
bash scripts/stage4_demo.sh --scenario perpendicular_crossing --seed 0
bash scripts/stage4_demo.sh --scenario diagonal_crossing --seed 0
```

如果要复现当初 10 轮测试中的其它轮次，调整 `--seed`，范围为 `0` 到 `9`：

```bash
bash scripts/stage4_demo.sh --scenario all --seed 3
```

## 展示目标

1. Unity 能同时显示 `Drone 1` 和 `Drone 2`。
2. 两架无人机先移动到当前场景的起点。
3. 注入危险交叉目标后，Safety Gate 检测碰撞风险并发布安全分离 setpoint。
4. Unity 中无人机短暂变黄表示 `warning`，触发接管时变红表示 `safety_override`。
5. 接管结束后，`marl_pilot.py` 继续追踪缓存的原始目标，无人机完成该场景。
6. 终端会打印 benchmark 坐标、最小距离、接管次数、预警次数和最近的 `safety_override` 事件。

## 一键脚本流程

脚本会自动启动：

```text
scripts/dev_broker.py
mock_drone.py --drone-id 1 [scenario-specific args]
mock_drone.py --drone-id 2 [scenario-specific args]
marl_pilot.py [scenario-specific args]
safety_gate.py
```

其中 `perpendicular_crossing` 和 `diagonal_crossing` 会使用和 benchmark runner 相同的高风险参数：MockDrone 使用 `--speed 1.6 --max-accel 2.0`，`marl_pilot.py` 使用 `--rule-safe-distance 0.01 --repulsion-gain 0.0 --max-speed 2.0 --horizon-sec 0.5`。`head_on_crossing` 使用 benchmark 中的默认参数。

脚本会通过 `Scenario.targets_for_seed(seed)` 生成起点和交叉目标。每个场景会先只启动 broker 和两架 MockDrone，发送起点，并等待两机位置到达 benchmark 起点且速度稳定为 0；你确认 Unity 中的静态起点后按 Enter，脚本才启动 `marl_pilot.py`、`safety_gate.py` 并注入交叉目标。

运行过程中，脚本会提示：

```text
open unity/SwarmUnityDemo in Unity Hub, press Play, then press Enter here
```

此时回到 Unity Editor 点击 Play。确认场景里出现 `Drone 1` 和 `Drone 2` 后，再回到终端。脚本会在每个场景达到静止起点后提示你按 Enter 启动 Pilot/Safety Gate 并注入交叉目标；默认总入口会依次运行三种场景。

每个场景开始前，脚本会先发送起点并等待位置、速度稳定；注入危险交叉目标前，会再次等待你按 Enter。这样你可以在 Unity 中清楚观察“静止起点 -> 危险目标注入 -> Safety Gate 接管 -> Pilot 恢复原目标”的完整过程。

## 命令行分步运行

如果需要逐个终端观察日志，可以手动运行。

终端 1：启动 MQTT broker。

```bash
conda run -n eai-swarm python scripts/dev_broker.py
```

终端 2：启动 1 号无人机。

```bash
conda run -n eai-swarm python mock_drone.py --drone-id 1 --speed 1.6 --max-accel 2.0
```

终端 3：启动 2 号无人机。

```bash
conda run -n eai-swarm python mock_drone.py --drone-id 2 --speed 1.6 --max-accel 2.0
```

终端 4：启动 FastAPI gateway。

```bash
conda run -n eai-swarm uvicorn gateway:app --host 127.0.0.1 --port 8000
```

终端 5：启动规则 Pilot。

```bash
conda run -n eai-swarm python marl_pilot.py \
  --rule-safe-distance 0.01 \
  --repulsion-gain 0.0 \
  --max-speed 2.0 \
  --horizon-sec 0.5
```

先发送第一组起点，等待两机拉开后再启动 Safety Gate：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[-3,0,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[3,0,1]}'
```

终端 6：启动 Safety Gate。

```bash
conda run -n eai-swarm python safety_gate.py
```

之后按三种场景依次注入起点和目标。下面的手工命令使用便于输入的固定可视化坐标；`scripts/stage4_demo.sh` 和正式 benchmark runner 都使用 `Scenario.targets_for_seed(seed)` 生成带随机种子的 benchmark 坐标。

### 场景一：正面对冲交叉

起点：`Drone 1 -> [-3,0,1]`，`Drone 2 -> [3,0,1]`。

目标：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[3,0,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[-3,0,1]}'
```

### 场景二：垂直航线交叉

起点：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[-3,0,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[0,-3,1]}'
```

目标：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[3,0,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[0,3,1]}'
```

### 场景三：对角航线交叉

起点：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[-3,-3,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[-3,3,1]}'
```

目标：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[3,3,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[3,-3,1]}'
```

查看事件：

```bash
curl "http://127.0.0.1:8000/api/events?limit=20"
```

## 结束演示

按以下顺序关闭：

1. Unity 停止 Play。
2. 在 Safety Gate、Pilot、gateway、MockDrone 和 broker 终端分别按 `Ctrl+C`。

需要停止的后端进程包括：

```text
safety_gate.py
marl_pilot.py
uvicorn gateway:app
mock_drone.py --drone-id 1
mock_drone.py --drone-id 2
scripts/dev_broker.py
```
