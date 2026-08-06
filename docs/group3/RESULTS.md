# 阶段五：GT vs Ego 感知闭环 Benchmark

## 运行边界

- `gt`：Pilot 与 Safety Gate 直接使用 `swarm/drone/+/telemetry`。
- `ego`：Pilot 保留自身 telemetry；其他无人机位置只消费 `swarm/drone_seen/{id}/position`。
- Ego 估计超过 `--ego-state-ttl-sec` 后会被丢弃，不复用旧坐标。
- 当前结果使用 `scripts/fake_ego_perception.py` 生成带噪桩；它模拟组 2 未来发布的位置 topic，不应表述为真实相机感知结果。
- C4 需要有效的 DeepSeek API Key；未配置时不得把 C4 记为已完成实验。

## 可复现命令

先确保没有手动 broker 或 MockDrone 占用 1883，再由 runner 自行管理服务：

```bash
conda run -n eai-swarm python scripts/stage4_benchmark.py \
  --scenario all --conditions C2,C3 --seeds 10 \
  --input-mode gt --out results/stage5

conda run -n eai-swarm python scripts/stage4_benchmark.py \
  --scenario all --conditions C2,C3 --seeds 10 \
  --input-mode ego --ego-noise-std-m 0.05 --out results/stage5
```

噪声敏感性扫档：

```bash
for noise in 0.00 0.05 0.15; do
  conda run -n eai-swarm python scripts/stage4_benchmark.py \
    --scenario all --conditions C3 --seeds 10 \
    --input-mode ego --ego-noise-std-m "$noise" --out results/stage5
done
```

## 输出字段

每轮 `runs.jsonl` 与 `summary.csv` 包含：`input_mode`、`ego_noise_std_m`、`ego_drop_rate`、`ego_delay_sec`、`ego_state_ttl_sec`、`ego_safety_margin_m`，以及既有的碰撞、近失、接管、LLM 重规划和时长指标。`aggregate.csv` 以 `scenario × input_mode × condition` 聚合。

## 结果状态

已完成低噪 Ego 的 C2、C3 正式 10-seed 批次（每个条件 3 个场景共 30 轮）：

```bash
conda run -n eai-swarm python scripts/stage4_benchmark.py \
  --scenario all --conditions C2,C3 --seeds 10 \
  --input-mode ego --ego-noise-std-m 0.05 --out results/stage5
```

结果目录：C2 为 `results/stage5/all_scenarios/20260806-122204`，C3 为 `results/stage5/all_scenarios/20260806-121207`。参数为 `noise_std=0.05m`、`drop_rate=0`、`delay=0s`、Ego 状态 TTL 为默认值；Gate 在 C3 中自动将安全距离从 1.6m 扩大为 2.55m（Ego safety margin 0.95m）。

| 场景 | C2 成功/碰撞 | C2 最小间距 | C3 成功/碰撞 | C3 最小间距 |
| --- | ---: | ---: | ---: | ---: |
| 对向穿越 | 60% / 40% | 0.2385m | 100% / 0% | 0.6188m |
| 垂直穿越 | 0% / 100% | 0.0034m | 100% / 0% | 0.8013m |
| 对角穿越 | 10% / 90% | 0.0334m | 100% / 0% | 0.8463m |
| 合计 | 23.3% / 76.7% | 0.0034m | 100% / 0% | 0.6188m |

这是**模拟 Ego 感知桩**在低噪条件下的 C2/C3 对照：C2 没有 Safety Gate，30 轮中发生 23 次碰撞；C3 启用保守裕度、TTL 丢弃与确定性 Safety Gate 后，30 轮均成功且没有碰撞。这不代表真实相机或真实无人机已验证。

### GT 同版本对照

GT 对照使用相同场景、相同 seeds 与相同 Benchmark 版本：`results/stage5/all_scenarios/20260806-125748`。

| 输入 | 条件 | 轮数 | 成功率 | 碰撞率 | 全批次最小间距 |
| --- | --- | ---: | ---: | ---: | ---: |
| GT | C2 | 30 | 33.3% | 66.7% | 0.0030m |
| GT | C3 | 30 | 100% | 0% | 0.6502m |
| Ego（0.05m） | C2 | 30 | 23.3% | 76.7% | 0.0034m |
| Ego（0.05m） | C3 | 30 | 100% | 0% | 0.6188m |

这组对照表明，在本模拟桩、低噪条件和当前安全裕度下，Ego 输入仍能维持 C3 的 0 碰撞结果，但最低间距略低于 GT。它不能证明真实视觉误差下的等效安全性。

### Ego 噪声敏感性（C3）

| 位置噪声标准差 | 轮数 | 成功率 | 碰撞率 | 全批次最小间距 | 结果目录 |
| ---: | ---: | ---: | ---: | ---: | --- |
| 0.00m | 30 | 100% | 0% | 0.6404m | `20260806-131326` |
| 0.05m | 30 | 100% | 0% | 0.6188m | `20260806-121207` |
| 0.15m | 30 | 100% | 0% | 0.4477m | `20260806-132221` |

0.15m 档仍未发生碰撞，但最小间距下降到 0.4477m，且低于该仿真中 0.5m 的 near-miss 阈值；因此它只说明当前 Safety Gate 没有触发碰撞，不能将 0.15m 噪声称为已充分安全。后续应增加丢帧、延迟和更高噪声的组合压力测试。

### C4 正式结果

已在相同低噪 Ego 条件下完成真实 DeepSeek 的全场景 10-seed 批次：

```bash
conda run -n eai-swarm python scripts/stage4_benchmark.py \
  --scenario all --conditions C4 --seeds 10 \
  --input-mode ego --ego-noise-std-m 0.05 --out results/stage5
```

结果目录：`results/stage5/all_scenarios/20260806-124620`。30 轮均成功、碰撞率为 0%，没有重规划解析/调用错误；每轮平均触发 2 次 Gate 接管与 2 次 DeepSeek 恢复规划。

| 场景 | 轮数 | 成功率 | 碰撞率 | 平均最小间距 | 最小间距 | 平均 API 延迟 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 对向穿越 | 10 | 100% | 0% | 0.7630m | 0.5767m | 1180ms |
| 垂直穿越 | 10 | 100% | 0% | 1.0176m | 0.7753m | 1200ms |
| 对角穿越 | 10 | 100% | 0% | 0.9784m | 0.7868m | 1136ms |

这验证了 C4 的职责边界：DeepSeek 仅在 Gate 已经接管后生成低频恢复航点（本批次每轮 2 次），而实时避障仍由确定性 Gate 负责；不应将其表述为 LLM 直接控制飞行或真实无人机验证。

已完成当前任务约定的 GT 同版本对照与 `0/0.05/0.15m` 噪声扫档。下一阶段应增加丢帧/延迟组合压力测试，并将模拟 Ego 桩替换为组 2 的真实相机位置发布器；不得把这些仿真结果表述为真实飞行验证。
