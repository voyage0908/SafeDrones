# Stage 4 Unity Safety Gate Demo 脚本

本脚本用于展示两架虚拟无人机在 Unity 中相向飞行时，规则版 Safety Gate 如何提前预警、接管并阻止继续接近。

一键运行：

```bash
bash scripts/stage4_demo.sh
```

## 展示目标

1. Unity 能同时显示 `Drone 1` 和 `Drone 2`。
2. 两架无人机先在安全距离外分开移动。
3. 下发交叉航线后，Safety Gate 检测碰撞风险。
4. Unity 中无人机短暂变黄表示 `warning`，触发接管时变红表示 `safety_override`。
5. `/api/events` 能看到结构化 `safety_override` 事件。

当前 demo 默认参数：

```text
safe_distance_m = 1.6
low_threshold = 0.32
high_threshold = 0.70
hold_sec = 1.0
```

这组参数只比上一版略保守，目标是让无人机停止时距离稍微拉开，同时不把 Safety Gate 调成过度敏感。

## 1. 启动后端

在 WSL 中进入仓库根目录：

```bash
cd $root
```

终端 1：启动 MQTT broker。

```bash
conda run -n eai-swarm python scripts/dev_broker.py
```

终端 2：启动 1 号无人机。

```bash
conda run -n eai-swarm python mock_drone.py --drone-id 1
```

终端 3：启动 2 号无人机。

```bash
conda run -n eai-swarm python mock_drone.py --drone-id 2
```

终端 4：启动 FastAPI gateway。

```bash
conda run -n eai-swarm uvicorn gateway:app --host 127.0.0.1 --port 8000
```

检查 gateway：

```bash
curl http://127.0.0.1:8000/api/health
```

## 2. 启动 Unity

1. 在 Unity Hub 打开 `unity/SwarmUnityDemo`。
2. 打开保存了 `SwarmTelemetryManager` 的场景。
3. 点击 Play。
4. Console 应看到 MQTT 订阅日志。

如果场景里暂时没有无人机，保持 Play 状态，等 MockDrone telemetry 发布后会自动生成。

## 3. 演示步骤

### A. 先把两架无人机分开

这一步先不要启动 Safety Gate。原因是两架 MockDrone 默认都从原点启动，Safety Gate 如果太早接入，会把“同点起飞”误当成高风险接近，导致一开始就接管。

在任意 WSL 终端执行：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[-3,0,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[3,0,1]}'
```

观察点：

- Unity 中两架无人机向左右两侧分开。
- 此时颜色应保持各自基础颜色，最多只短暂出现低风险状态。

等待约 6 到 8 秒，让它们基本到位。

### B. 启动 Safety Gate

等两架无人机已经拉开后，再启动 Safety Gate：

```bash
conda run -n eai-swarm python safety_gate.py
```

### C. 下发交叉航线

执行：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[3,0,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[-3,0,1]}'
```

观察点：

- 两架无人机开始相向移动。
- 风险升高时，Unity 中无人机短暂变黄。
- 接管触发时，Safety Gate 终端出现 `override` 日志。
- Unity 中对应无人机短暂变红，并停止继续接近。

### D. 查看事件

执行：

```bash
curl http://127.0.0.1:8000/api/events
```

预期能看到类似字段：

```json
{
  "event": "safety_override",
  "event_name": "safety_override",
  "reason": "collision_risk_exceeded",
  "risk_level": 0.7,
  "target_drone": 2
}
```

`risk_level` 和 `target_drone` 会根据实际触发时刻变化。

### E. 恢复到安全位置

展示完接管后，可以把两架无人机重新拉开：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[-3,1,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[3,-1,1]}'
```

观察点：

- 风险下降后，后续 `safety_status` 应回到 `normal` 或 `warning` 以下。
- Unity 颜色会在短暂保持后恢复到基础颜色。

## 4. 结束演示

按以下顺序关闭：

1. Unity 停止 Play。
2. 在 Safety Gate 终端按 `Ctrl+C`。
3. 在 gateway、MockDrone 和 broker 终端分别按 `Ctrl+C`。

需要停止的后端进程包括：

```text
safety_gate.py
uvicorn gateway:app
mock_drone.py --drone-id 1
mock_drone.py --drone-id 2
scripts/dev_broker.py
```
