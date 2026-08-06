# 阶段四：Safety Gate 与 MARL Pilot 预演

## 目标

先用规则控制器跑通双向安全协议的最小闭环：

1. `safety_gate.py` 订阅 `swarm/drone/+/telemetry`
2. 根据距离、telemetry 速度和 TTC 计算碰撞风险
3. 风险过高时向对应无人机发布最小干预的安全分离 `move_to` setpoint；没有可用分离点时回退到 `hover`
4. 同时向 `swarm/commander/override` 发布 `safety_override`
5. FastAPI gateway 通过 `/api/events` 读取 override 事件
6. Unity 场景显示无人机在危险接近时短时分离，并在风险解除后恢复追踪目标

当前可视化 demo 不依赖 ML-Agents 或 ONNX。规则版 Safety Gate 用于验证协议、日志和可视化链路；MARL 训练 scaffold 已单独补充，后续可导出 ONNX 后接入 `marl_pilot.py`。

当前 `MockDrone` 已使用带约束运动学模型：`move_to` 不会瞬间改变速度，`hover` 会按 `max_accel_mps2` 刹停。Safety Gate 会优先使用 telemetry 中的真实 `velocity` 字段；如果旧 telemetry 没有 `velocity`，才回退到由当前位置和目标航点推断速度。

当前 Safety Gate 不做完整路径规划。它只在 `override` 状态下生成短时安全分离 setpoint，把无人机带出高风险区域；风险低于释放阈值后停止覆盖，低层 Pilot 继续追踪缓存的高层目标。若多次接管仍无法释放，后续 C4 Replanner 再负责生成任务级绕行点或换机策略。

完整演示流程可直接按 [Demo 脚本](./demo_script.md) 执行。
也可以直接运行 `bash scripts/stage4_demo.sh`。当前脚本会使用 `Scenario.targets_for_seed(seed)` 复现当初 10 轮测试中的 `head_on_crossing`、`perpendicular_crossing`、`diagonal_crossing` 三种 C3 benchmark 场景，并等待无人机在避险后继续完成原目标。若只演示单个场景，可运行 `bash scripts/stage4_demo.sh --scenario head_on_crossing --seed 0`；`--seed` 支持 `0` 到 `9`。
阶段四的 MARL 训练 scaffold 见 [MARL 训练说明](./marl_training.md)。

## 启动顺序

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

终端 4：启动 gateway。

```bash
conda run -n eai-swarm uvicorn gateway:app --host 127.0.0.1 --port 8000
```

终端 5：启动规则 Pilot。这里故意降低规则 Pilot 自身排斥强度，让危险主要由 Safety Gate 接管。

```bash
conda run -n eai-swarm python marl_pilot.py \
  --rule-safe-distance 0.01 \
  --repulsion-gain 0.0 \
  --max-speed 2.0 \
  --horizon-sec 0.5
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

先启动 Safety Gate：

```bash
conda run -n eai-swarm python safety_gate.py
```

再发送交叉目标：

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
2. Unity 中无人机沿安全分离方向错开，并随后继续前往目标点
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
  --escape-distance 1.2 \
  --low-threshold 0.32 \
  --high-threshold 0.70 \
  --hold-sec 1.0
```

参数含义：

- `safe-distance`: 安全距离，小于该距离时距离风险升高
- `escape-distance`: 接管时发布的短时安全分离 setpoint 距离
- `low-threshold`: 预警阈值
- `high-threshold`: 接管阈值
- `hold-sec`: 触发接管后的最小保持时间
- `override-command-interval`: 接管保持期间刷新安全分离 setpoint 的周期

当前默认值只做了小幅保守调整：`safe-distance` 从 `1.5m` 增加到 `1.6m`，`high-threshold` 从 `0.75` 降到 `0.70`，`low-threshold` 从 `0.35` 降到 `0.32`。目标是让两架无人机在 Unity demo 里略早触发安全分离，同时避免 Safety Gate 在正常间距下过于频繁接管。

## 测试

```bash
conda run -n eai-swarm python -m unittest discover -s tests
```

## Benchmark

当前 benchmark runner 支持：

- `head_on_crossing`
- `perpendicular_crossing`
- `diagonal_crossing`

其中 `perpendicular_crossing` 和 `diagonal_crossing` 会降低规则 Pilot 的排斥强度，以制造更接近故障注入的高风险交叉场景；C3 条件下仍由 Safety Gate 负责最终接管。

```bash
conda run -n eai-swarm python scripts/stage4_benchmark.py \
  --scenario all \
  --conditions C2,C3 \
  --seeds 10 \
  --out results/stage4
```

该命令会自动启动 broker、两架 MockDrone、`marl_pilot.py` 和 `safety_gate.py`，并在 `results/stage4/...` 下落盘：

- `runs.jsonl`: 每轮完整结果
- `summary.csv`: 每轮表格结果
- `aggregate.csv`: 按场景和条件聚合的成功率、碰撞率、最小距离、接管次数等指标

当前基准场景中，C2 会稳定失败，C3 会稳定完成交叉并保持安全距离。

### C4 DeepSeek Replanner

C4 会真实调用 DeepSeek API：Safety Gate 触发 `safety_override` 后，benchmark runner 把 override 事件、当前无人机状态和原任务终点交给 `CommanderLLM`，由 DeepSeek 返回恢复航点，再发布为高优先级任务命令。Safety Gate 仍负责紧急避障，DeepSeek 只负责低频任务级恢复规划。

C4 runner 会对单次 LLM replan 做最多 3 次尝试，以吸收临时 DNS/API 抖动；如果 DeepSeek 连续无法返回可解析 JSON，该轮会记录 `llm_replan_error_count`，并按严格 C4 成功条件记为失败。

先配置 API key：

```bash
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=...
# 可选：export DEEPSEEK_MODEL=...
```

运行单场景 smoke：

```bash
conda run -n eai-swarm python scripts/stage4_benchmark.py \
  --scenario head_on_crossing \
  --conditions C4 \
  --seeds 1 \
  --out results/stage4
```

需要把 C4 纳入完整对照时，再显式运行：

```bash
conda run -n eai-swarm python scripts/stage4_benchmark.py \
  --scenario all \
  --conditions C2,C3,C4 \
  --seeds 10 \
  --out results/stage4
```

`aggregate.csv` 会额外输出 `avg_llm_replan_count`、`avg_llm_replan_error_count`、`avg_command_revision_rate`、`avg_llm_latency_ms` 和 `max_llm_latency_ms`。C4 的 `task_success` 不只要求安全完成交叉，也要求至少一次 LLM replan 成功且没有 LLM 错误。

阶段四正式结果记录见 `docs/stage4/RESULTS.md`。
