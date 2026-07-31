# 阶段四：Safety Gate 与 MARL Pilot 预演

## 目标

先用规则控制器跑通双向安全协议的最小闭环：

1. `safety_gate.py` 订阅 `swarm/drone/+/telemetry`
2. 根据距离、telemetry 速度和 TTC 计算碰撞风险
3. 风险过高时向对应无人机发布 `hover` 安全指令
4. 同时向 `swarm/commander/override` 发布 `safety_override`
5. FastAPI gateway 通过 `/api/events` 读取 override 事件
6. Unity 场景显示无人机在危险接近时停下

当前可视化 demo 不依赖 ML-Agents 或 ONNX。规则版 Safety Gate 用于验证协议、日志和可视化链路；MARL 训练 scaffold 已单独补充，后续可导出 ONNX 后接入 `marl_pilot.py`。

当前 `MockDrone` 已使用带约束运动学模型：`move_to` 不会瞬间改变速度，`hover` 会按 `max_accel_mps2` 刹停。Safety Gate 会优先使用 telemetry 中的真实 `velocity` 字段；如果旧 telemetry 没有 `velocity`，才回退到由当前位置和目标航点推断速度。

完整演示流程可直接按 [Demo 脚本](./demo_script.md) 执行。
也可以直接运行 `bash scripts/stage4_demo.sh`。
阶段四的 MARL 训练 scaffold 见 [MARL 训练说明](./marl_training.md)。

## 启动顺序

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

终端 4：启动 gateway。

```bash
conda run -n eai-swarm uvicorn gateway:app --host 127.0.0.1 --port 8000
```

Unity：打开 `unity/SwarmUnityDemo`，点击 Play。

## 注入交叉航线

先把两架无人机分开：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[-3,0,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[3,0,1]}'
```

等待它们到位后，让它们相向飞行：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[3,0,1]}'

curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[-3,0,1]}'
```

预期结果：

1. Safety Gate 日志出现 `override`
2. Unity 中无人机停止继续接近
3. Unity 中对应无人机会短暂变红，表示 `safety_override`
4. gateway 事件接口能看到 `safety_override`

如果你是第一次跑这个阶段，建议先让两架无人机拉开距离，再启动 Safety Gate。原因是 MockDrone 默认从同一个原点起飞，Safety Gate 太早接入会把“初始重合”当成真实碰撞风险。

查看事件：

```bash
curl http://127.0.0.1:8000/api/events
```

## Unity 风险可视化

Unity 的 `DroneTelemetrySubscriber` 除了订阅 `swarm/drone/+/telemetry`，还会订阅：

```text
swarm/commander/status
swarm/commander/override
```

显示规则：

- 正常：保持每架无人机的基础颜色
- `warning` 或风险值超过预警阈值：短暂变黄
- `safety_override` / `override`：短暂变红

Safety Gate 会周期性发布 `safety_status`，并在接管时发布 `safety_override`。这些消息同时供 gateway `/api/events` 和 Unity 可视化使用。

## 可调参数

```bash
conda run -n eai-swarm python safety_gate.py \
  --safe-distance 1.6 \
  --low-threshold 0.32 \
  --high-threshold 0.70 \
  --hold-sec 1.0
```

参数含义：

- `safe-distance`: 安全距离，小于该距离时距离风险升高
- `low-threshold`: 预警阈值
- `high-threshold`: 接管阈值
- `hold-sec`: 触发接管后的最小保持时间

当前默认值只做了小幅保守调整：`safe-distance` 从 `1.5m` 增加到 `1.6m`，`high-threshold` 从 `0.75` 降到 `0.70`，`low-threshold` 从 `0.35` 降到 `0.32`。目标是让两架无人机在 Unity demo 里略早刹停，同时避免 Safety Gate 在正常间距下过于频繁接管。

## 测试

```bash
conda run -n eai-swarm python -m unittest discover -s tests
```
