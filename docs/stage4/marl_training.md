# 阶段四：MARL 训练 scaffold

这份说明对应阶段四里尚未完成的 ML-Agents / MARL 部分。当前仓库已经提供：

- `unity/SwarmUnityDemo/Assets/Scripts/SwarmMarl/SwarmTrainingArea.cs`
- `unity/SwarmUnityDemo/Assets/Scripts/SwarmMarl/SwarmDroneAgent.cs`
- `swarm/marl.py`
- `marl_pilot.py`

目标不是先追求复杂对抗，而是先训练一个稳定的低层 Pilot：

1. 观察队友和目标
2. 输出高频速度 setpoint
3. 避免碰撞、越界和剧烈抖动
4. 让 Safety Gate 继续保留最终否决权

## Unity 侧准备

1. 在 Unity Package Manager 安装 `ML-Agents`。
2. 在 Project Settings 里添加 Scripting Define Symbol `SWARM_ML_AGENTS`。
3. 在场景里创建一个空物体，挂载 `SwarmTrainingArea`。
4. 给每架训练无人机挂载 `SwarmDroneAgent`。
5. 为每架无人机准备一个目标点 `Transform`，并拖到 `SwarmTrainingArea.Targets`。

## 观测与动作

`SwarmDroneAgent` 采用固定长度观测：

- 自身位置
- 自身速度
- 目标相对位移
- 最近的若干队友相对位移
- 最近的若干队友相对速度

动作空间是 3 维连续向量：

- `[-1, 1]` 归一化速度方向
- 由训练区域内的 `maxSpeed` 和 `maxAcceleration` 转成实际运动

## 奖励设计

当前 scaffold 采用的 reward 结构是：

- 向目标靠近给正奖励
- 到达目标给成功奖励
- 与队友过近给惩罚
- 碰撞给大惩罚并终止回合
- 越界给惩罚并终止回合
- 动作变化过大给平滑惩罚

## Python 推理接入

训练完后导出 `.onnx`，然后运行：

```bash
conda run -n eai-swarm python marl_pilot.py --onnx-model path/to/policy.onnx
```

如果还没有训练好模型，可以先不传 `--onnx-model`，脚本会使用规则 fallback Pilot，方便把链路先跑通。

## 与 Safety Gate 的关系

MARL 负责正常情况下的低层追踪与局部避障，Safety Gate 仍然是最终的 runtime shield：

```text
LLM target -> MARL Pilot -> Safety Gate -> MockDrone / Unity
```

当 Safety Gate 发布 `safety_override` 时，`marl_pilot.py` 会暂停继续下发该无人机的短周期指令。
