# 从零启动 Unity 可视化 Demo

本文面向 Unity 新手，目标是从零安装 Unity Hub 和 Unity Editor，然后直接打开仓库内置 Unity 项目，启动本项目的可视化 demo。

最终链路：

```text
WSL/Conda 后端
  MockDrone -> MQTT telemetry -> FastAPI command gateway

Windows Unity
  DroneTelemetrySubscriber -> 场景里的 Drone 物体平滑移动
```

## 1. 安装 Unity Hub 和 Unity Editor

Unity Hub 是 Unity 的项目和编辑器管理器，Unity Editor 才是真正打开场景、运行 Play 的程序。

1. 打开 Unity 官方下载页：

   ```text
   https://unity.com/download
   ```

2. 下载并安装 Unity Hub。
3. 打开 Unity Hub 并登录 Unity 账号。
4. 左侧进入 `Installs`，安装一个 Unity Editor。
5. Editor 版本建议选择 Unity Hub 推荐的 LTS 或当前稳定版本。
6. 安装模块保持最小即可。Visual Studio 可以不装；如果你已经习惯 VS Code，可以后续在 Unity Editor 里设置。

Unity Hub 只是启动器，所以在 Hub 里看不到 `Edit` 菜单。只有打开具体 Unity 项目后，Unity Editor 顶部才会出现：

```text
File  Edit  Assets  GameObject  Component  Window  Help
```

## 2. 打开仓库内置 Unity 项目

本仓库已经包含可直接运行的 Unity 项目，不需要新建项目，也不需要再手动导入 MQTT 插件。

Unity 项目路径：

```text
$root\unity\SwarmUnityDemo
```

在 Unity Hub 里：

1. 进入 `Projects`。
2. 点击 `Add`、`Open` 或 `Add project from disk`。
3. 选择这个目录：

   ```text
   $root\unity\SwarmUnityDemo
   ```

4. 打开项目，等待 Unity 导入资源和编译脚本。

仓库结构应保持为：

```text
project/
  docs/
  swarm/                  # Python 包，不要把 Unity 项目放这里
  unity/
    SwarmUnityDemo/       # 完整 Unity 项目
      Assets/
      Packages/
      ProjectSettings/
```

不要把 Unity 项目移动到仓库根目录，也不要放到 Python 包目录 `swarm/` 下面。

## 3. 设置 VS Code 作为脚本编辑器

如果你想用 VS Code，不需要使用 Unity 自动安装的 Visual Studio。

在 Unity Editor 顶部菜单：

```text
Edit -> Preferences -> External Tools
```

把 `External Script Editor` 改成：

```text
Visual Studio Code
```

如果有 `Regenerate project files` 按钮，点一次。

## 4. 确认 MQTT 依赖

本 demo 使用 M2Mqtt 的命名空间：

```csharp
uPLibrary.Networking.M2Mqtt
```

仓库内置 Unity 项目已经包含必需的免费 MQTT 依赖：

```text
unity/SwarmUnityDemo/Assets/M2Mqtt
unity/SwarmUnityDemo/Assets/M2MqttUnity
```

来源是免费开源项目：

```text
https://github.com/gpvigano/M2MqttUnity
```

所以正常情况下你不需要再从 Asset Store 或 GitHub 手动导入。

如果 Unity Console 出现这个 warning，可以忽略：

```text
SslProtocols.Ssl3 is obsolete
```

原因是 M2Mqtt 旧代码里保留了 SSL 3.0 兼容分支；本 demo 使用本地非加密 MQTT `127.0.0.1:1883`，不会走 SSL。

如果 Console 报找不到 `uPLibrary.Networking.M2Mqtt`，说明 `Assets/M2Mqtt` 或 `Assets/M2MqttUnity` 缺失。此时再打开上面的 GitHub 地址，下载 ZIP，把其中的 `Assets/M2Mqtt` 和 `Assets/M2MqttUnity` 复制到 `unity/SwarmUnityDemo/Assets/`。

## 5. 确认 SwarmTelemetry 脚本

脚本已经在仓库内置 Unity 项目内，不需要再复制。

```text
$root\unity\SwarmUnityDemo\Assets\Scripts\SwarmTelemetry
  DroneTelemetrySubscriber.cs
  DroneTelemetryView.cs
```

Unity 重新编译后，Console 不能有红色 Error。黄色 Warning 可以先忽略。

## 6. 确认 Unity 场景配置

仓库内置项目可能已经保存了 `SwarmTelemetryManager`。先在左侧 `Hierarchy` 里找：

```text
SwarmTelemetryManager
```

如果已经存在，选中它并确认右侧 `Inspector` 上有：

```text
DroneTelemetrySubscriber
```

如果没有，按下面步骤创建：

1. 在左侧 `Hierarchy` 右键 `Create Empty`。
2. 命名为 `SwarmTelemetryManager`。
3. 选中它，在右侧 `Inspector` 点击 `Add Component`。
4. 搜索并添加 `DroneTelemetrySubscriber`。

参数保持默认：

```text
Broker Host: 127.0.0.1
Broker Port: 1883
Topic Filter: swarm/drone/+/telemetry
Use Unity Y As Altitude: enabled
Drone Prefab: empty
```

`Drone Prefab` 可以先空着。脚本会自动创建 Capsule 作为临时无人机模型。

坐标映射默认是：

```text
项目坐标 [x, y, z] -> Unity 坐标 (x, z, y)
```

这样项目里的 `z` 高度会映射到 Unity 的 `Y` 高度轴。

## 7. 启动后端 Server

以下命令在 WSL 里运行。

进入项目根目录：

```bash
cd $root
```

启动 MQTT broker：

```bash
conda run -n eai-swarm python scripts/dev_broker.py
```

启动 MockDrone：

```bash
conda run -n eai-swarm python mock_drone.py --drone-id 1
```

启动 FastAPI gateway：

```bash
conda run -n eai-swarm uvicorn gateway:app --host 127.0.0.1 --port 8000
```

三个命令建议分别放在三个终端里运行。

检查 gateway：

```bash
curl http://127.0.0.1:8000/api/health
```

预期返回：

```json
{"ok":true}
```

实际返回里还会包含 LLM provider 和 MQTT host/port。

## 8. 运行 Unity Demo

1. 回到 Unity Editor。
2. 点击顶部 Play 按钮。
3. Console 应出现：

   ```text
   Subscribed to MQTT telemetry topic: swarm/drone/+/telemetry
   ```

4. 场景里会自动出现：

   ```text
   Drone 1
   ```

发送测试航点：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[5,5,2]}'
```

预期结果：Unity 场景中的 `Drone 1` 平滑移动到新位置。

再发回原点附近：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[0,0,1]}'
```

## 9. 多无人机测试

再开一个 WSL 终端，启动第二架 MockDrone：

```bash
conda run -n eai-swarm python mock_drone.py --drone-id 2
```

发送第二架无人机航点：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[-3,4,2]}'
```

预期结果：Unity 会自动生成 `Drone 2`，并独立移动。

## 10. 常见问题

### Add Component 里搜不到 DroneTelemetrySubscriber

先看 Unity Console 是否有红色 Error。只要有任何红色 Error，Unity 就不会把脚本显示为可挂载组件。

确认脚本路径是：

```text
Assets/Scripts/SwarmTelemetry/DroneTelemetrySubscriber.cs
```

也可以直接把 `DroneTelemetrySubscriber.cs` 从 Project 面板拖到 `SwarmTelemetryManager`。

### MqttClient constructor 报错

确认第 68 行使用的是 6 参数构造函数：

```csharp
client = new MqttClient(brokerHost, brokerPort, false, null, null, MqttSslProtocols.None);
```

### Unity 连不上 MQTT

先确认 WSL 里 gateway 正常：

```bash
curl http://127.0.0.1:8000/api/health
```

如果 gateway 正常，但 Unity Console 没有订阅成功日志：

1. 先确认 MQTT broker 终端还在运行。
2. Unity 的 `Broker Host` 先用 `127.0.0.1`。
3. 如果 Windows Unity 无法访问 WSL localhost，在 WSL 里查 IP：

   ```bash
   hostname -I
   ```

4. 把 Unity 里的 `Broker Host` 改成输出的第一个 IP。

如果使用 WSL IP，可能还需要把 `config/amqtt.yml` 的 bind 从：

```yaml
bind: 127.0.0.1:1883
```

临时改成：

```yaml
bind: 0.0.0.0:1883
```

然后重启 broker。

### 关闭所有 server

在三个 WSL 终端里分别按：

```text
Ctrl+C
```

需要停止的进程是：

```text
scripts/dev_broker.py
mock_drone.py
uvicorn gateway:app
```

## 11. 阶段三验收标准

满足以下条件即可认为 Unity 可视化 demo 完成：

1. Unity 没有红色编译 Error。
2. `SwarmTelemetryManager` 成功挂载 `DroneTelemetrySubscriber`。
3. Play 后 Console 显示已订阅 MQTT telemetry。
4. 场景中自动生成 `Drone 1`。
5. 发送 `/api/direct-command` 后，`Drone 1` 平滑移动。
6. 启动第二架 MockDrone 后，Unity 能自动生成并移动 `Drone 2`。
