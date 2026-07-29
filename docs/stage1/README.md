# 阶段一：MockDrone + MQTT

## 目标

跑通最小无人机通信闭环：

1. `mock_drone.py` 订阅 `swarm/drone/1/command`
2. 收到 `move_to` 指令后，在内存中按速度、加速度、yaw rate 约束更新虚拟坐标
3. 每 0.1 秒向 `swarm/drone/1/telemetry` 发布 telemetry

## Conda 环境

优先复用当前 Conda 环境：

```bash
python -m pip install -r requirements.txt
```

如果不想改动已有环境，可以新建专用环境：

```bash
conda env create -f environment.yml
conda activate eai-swarm
```

## 启动 MQTT Broker

本阶段提供一个基于 `amqtt` 的开发 broker，适合纯 Conda 环境：

```bash
python scripts/dev_broker.py
```

如果本机已经安装 Mosquitto，也可以使用 Mosquitto：

```bash
mosquitto -p 1883
```

如果 broker 在其他机器上运行，启动脚本时传入 `--host`。

## 运行 MockDrone

```bash
python mock_drone.py --drone-id 1 --host localhost --port 1883
```

可选运动约束参数：

```bash
python mock_drone.py --drone-id 1 \
  --speed 1.0 \
  --max-accel 1.0 \
  --max-yaw-rate 120 \
  --min-altitude 0 \
  --max-altitude 5
```

## 发送测试指令

使用项目自带命令行工具：

```bash
python scripts/publish_command.py --drone-id 1 --target 5 5 2
```

或使用 MQTT Explorer 向 `swarm/drone/1/command` 发布：

```json
{"action":"move_to","target":[5,5,2]}
```

观察 `swarm/drone/1/telemetry`，应能看到 `position` 从 `[0,0,0]` 平滑移动到 `[5,5,2]`。telemetry 同时包含 `velocity`、`yaw_deg`、`max_accel_mps2` 和 `max_yaw_rate_dps`，供 Safety Gate 和后续真机参数对齐使用。

也可以运行自动 smoke test。它会先订阅 telemetry，再自动发布一个远离当前位置的航点，并检查是否捕捉到至少三条连续变化的位置：

```bash
python scripts/smoke_stage1.py
```

## 离线测试

不启动 MQTT 也可以先测试运动学逻辑：

```bash
python -m unittest
```
