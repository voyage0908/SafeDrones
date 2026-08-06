# 组 3：感知闭环 Benchmark（GT vs Ego 核心消融）

## 任务目标

把阶段四已有的 C2/C3/C4 Benchmark 从"上帝视角"升级为"感知闭环"：Pilot/Safety Gate 的状态输入从 telemetry ground truth 切换为组 2 反解出的感知状态，量化感知噪声下双向安全协议是否仍然有效。本组交付 GT vs Ego 的核心消融数据，是整个修订路线的核心实验。

## 前置阅读

- 总体路线：`docs/plan.md` 文末"后续路线"第二节，以及"实验设计 → 感知闭环 Benchmark"小节
- 阶段四基线：`docs/stage4/README.md`、`docs/stage4/RESULTS.md`（GT 模式结果已有，是 Ego 模式的对照）
- 现有 runner：`scripts/stage4_benchmark.py`；场景定义与指标计算已在其中
- Pilot 实现：`marl_pilot.py` + `swarm/marl.py`（当前为规则 Pilot，吃 telemetry 快照）

## 接口约定（与组 2 的契约）

Ego 模式的状态来源：

- 自身与友方无人机位置：`swarm/drone/+/telemetry`（通信共享，视为已知）
- 地面目标位置：`swarm/target/{id}/position`
- 敌方无人机位置：`swarm/drone_seen/{id}/position`（含 `estimated_depth`）

组 2 保证上述 topic 的发布频率不低于 5 Hz；本组的消费端必须容忍消息丢失和短暂过期——为感知状态设置 TTL，过期即视为"目标丢失"，不得沿用陈旧坐标。

## 任务拆解

1. **runner 改造：GT/Ego 双输入模式**
   - `scripts/stage4_benchmark.py` 增加 `--input-mode gt|ego` 参数，默认 `gt` 保持现有行为不变。
   - Ego 模式下，敌方/目标状态改由组 2 的输出 topic 提供。
   - 结果目录、runs.jsonl、summary.csv 增加 `input_mode` 字段，避免与 GT 数据混淆。
2. **带噪假感知桩（不依赖组 1/2，先开工）**
   - 在组 2 链路 ready 之前，先实现一个"假 Ego"模式：给 ground truth 加高斯噪声 + 随机丢失 + 固定延迟，模拟感知输入。
   - 用它提前验证 runner 改造的正确性，并预估 Ego 模式的噪声容限；组 2 链路接通后切换为真实相机输入，两套结果可对比。
3. **感知闭环消融（核心实验）**
   - 同一批场景（`head_on_crossing`、`perpendicular_crossing`、`diagonal_crossing`）、同一批种子（≥10），跑 `GT × {C2,C3,C4}` 与 `Ego × {C2,C3,C4}`。
   - 对比碰撞率、近失率、接管次数、恢复时间；输出"感知误差 → 安全指标"敏感性分析（可利用假感知桩扫多档噪声水平）。
4. **数据落盘**
   - 结果写到 `results/stage5/`；运行信息、命令和聚合结果记录到 `docs/stage5/RESULTS.md`（新建），格式沿用 `docs/stage4/RESULTS.md`。

## 依赖与开工顺序

- 任务 1、2 不依赖组 1/组 2，立即开工。
- 任务 3 需要组 1 + 组 2 的链路联调完成（真实相机输入部分）。

## 验收标准

- Ego 模式下 C3 在三个交叉场景中仍保持最小安全距离并完成任务（碰撞率 0）。
- GT vs Ego 的 C2/C3/C4 对比，每条件 ≥10 个随机种子，CSV/JSONL 落盘可复现。
- 敏感性分析至少覆盖 3 档噪声水平。

## 产出物

- `scripts/stage4_benchmark.py` 的 `--input-mode` 改造（或新建 `scripts/stage5_benchmark.py`，视改动量而定）
- 假感知桩（噪声/丢失/延迟注入）
- `results/stage5/` 实验数据
- `docs/stage5/RESULTS.md` 核心消融结果记录
