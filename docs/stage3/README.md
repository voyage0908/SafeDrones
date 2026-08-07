# 阶段三：Unity 视觉孪生

## 目标

把阶段一、阶段二已经跑通的 MQTT telemetry 显示到 Unity 场景中：

1. Unity 作为 MQTT 客户端订阅 `swarm/drone/+/telemetry`
2. 解析 `mock_drone.py` 发布的 JSON 坐标
3. 自动为每架无人机创建或更新场景物体
4. 使用平滑插值把离散 telemetry 渲染成连续运动

本阶段只负责视觉孪生，不参与高频安全控制。后续 Safety Gate 和 MARL 仍应在 Python 边缘控制进程或 Unity ML-Agents 环境中独立实现。

如果你是第一次使用 Unity，建议先按 [从零启动 Unity 可视化 Demo](../unity_demo_from_zero.md) 完成安装、Asset 导入和联调。

## 文件

新增 Unity 脚本位于：

```text
unity/SwarmUnityDemo/Assets/Scripts/SwarmTelemetry/
  DroneTelemetrySubscriber.cs
  DroneTelemetryView.cs
```

脚本依赖 M2Mqtt 的命名空间：

```csharp
using uPLibrary.Networking.M2Mqtt;
```

## Unity 环境准备

1. 新建 Unity 3D 项目。
2. 导入 C# MQTT 插件，推荐 M2Mqtt 或兼容 `uPLibrary.Networking.M2Mqtt` 命名空间的 Unity 包。
3. 如果使用仓库内置 Unity 项目，直接在 Unity Hub 中打开 `unity/SwarmUnityDemo`。
4. 如果新建自己的 Unity 项目，将本仓库的 `unity/SwarmUnityDemo/Assets/Scripts/SwarmTelemetry` 目录放入 Unity 项目的 `Assets/Scripts/` 下。
5. 在场景中创建一个空物体，例如 `SwarmTelemetryManager`。
6. 给该空物体挂载 `DroneTelemetrySubscriber`。
7. Inspector 中保持默认配置：
   - `Broker Host`: `127.0.0.1`
   - `Broker Port`: `1883`
   - `Topic Filter`: `swarm/drone/+/telemetry`
   - `Use Unity Y As Altitude`: enabled

如果没有指定 `Drone Prefab`，脚本会自动用 Capsule 创建临时无人机模型。正式展示时可以把自己的无人机模型做成 prefab，并拖入 `Drone Prefab`。

## 坐标约定

Python 侧项目坐标遵循计划文档：

```text
X = 前方
Y = 左方
Z = 高度
```

Unity 默认 `Y` 轴是高度。因此脚本默认开启 `Use Unity Y As Altitude`，映射关系为：

```text
project [x, y, z] -> Unity (x, z, y)
```

如果你的 Unity 场景已经按 `transform.position = (x, y, z)` 建模，可以关闭 `Use Unity Y As Altitude`。

## 启动联调

终端 1：启动开发 MQTT broker。

```bash
python scripts/dev_broker.py
```

终端 2：启动一架或多架 MockDrone。

```bash
python mock_drone.py --drone-id 1
python mock_drone.py --drone-id 2
```

终端 3：启动网关。

```bash
uvicorn gateway:app --host 127.0.0.1 --port 8000
```

打开 Unity 场景并运行 Play。随后发送直接航点：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[5,5,2]}'
```

也可以直接使用阶段一脚本：

```bash
python scripts/publish_command.py --drone-id 1 --target 5 5 2
```

预期结果：Unity 场景里名为 `Drone 1` 的物体会平滑移动到对应位置；如果启动了 `--drone-id 2`，收到 telemetry 后会自动生成 `Drone 2`。

## Telemetry 格式

脚本兼容当前 `MockDroneState.telemetry()` 的输出：

```json
{
  "drone": 1,
  "x": 1.0,
  "y": 2.0,
  "z": 0.5,
  "position": [1.0, 2.0, 0.5],
  "target": [5.0, 5.0, 2.0],
  "speed_mps": 1.0,
  "status": "flying",
  "last_command_id": "cmd-0001",
  "timestamp_ms": 1720000000000
}
```

优先读取 `position`，如果缺失则回退到 `x/y/z`。

## 阶段验收

通过以下检查即可认为阶段三完成：

1. Unity Console 显示已经订阅 `swarm/drone/+/telemetry`
2. 启动 `mock_drone.py` 后，场景中自动出现 `Drone 1`
3. 发送航点后，模型沿连续轨迹移动，而不是瞬移
4. 同时启动两架 MockDrone 时，Unity 能独立显示 `Drone 1` 和 `Drone 2`
5. `/api/events` 和 Unity 显示互不干扰，说明阶段二反向通道仍可继续工作

## 常见问题

如果 Unity Console 报找不到 `uPLibrary.Networking.M2Mqtt`，说明 MQTT 插件没有导入，或导入的包命名空间不同。优先更换为 M2Mqtt Unity 包；如果使用 MQTTnet，需要改写 `DroneTelemetrySubscriber` 的连接和订阅部分，`DroneTelemetryView` 可以保持不变。

如果 Unity 没有任何物体生成，先确认 broker、MockDrone 和 Unity 使用同一个 host/port，再用 MQTT Explorer 检查 `swarm/drone/1/telemetry` 是否在刷新。
