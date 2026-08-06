# 组 4：复杂场景扩展与 Safety Gate 效果报告

## 任务目标

在组 3 的 GT/Ego Benchmark 基础上，把实验扩展到更复杂、更极端的场景，并汇总所有数据产出最终的 Safety Gate 效果报告和演示视频。本组是最终科研结果的出口。

## 前置阅读

- 总体路线：`docs/plan.md` 文末"后续路线"第三节
- 组 3 文档：`docs/groups/group3_benchmark.md`（runner 改造与 Ego 输入约定）
- 阶段四结果格式参考：`docs/stage4/RESULTS.md`

## 任务拆解

1. **4v4 对抗场景（`four_v_four`）**
   - 8 架 MockDrone 分红蓝两方：蓝方（分层 AI + Safety Gate）友方位置经 telemetry 共享，红方（规则脚本或人工注入航点）位置对蓝方不可见，必须通过组 2 的相机链路（`swarm/drone_seen/{id}/position`）推算。
   - 统计敌方位置估计误差，以及估计误差对碰撞率/近失率/接管次数的影响。
   - 与 Unity 可视化联动，红蓝着色对抗展示。
2. **极端条件场景**
   - `llm_timeout` 提升为正式验收场景：LLM 无响应 3-5 秒期间系统保持安全（悬停/安全保持，无碰撞）。
   - `packet_loss`：随机丢弃 20% MQTT 消息，验证双向协议的鲁棒性。
3. **效果报告**
   - 汇总阶段四 GT 数据、组 3 的 GT vs Ego 消融、本组的复杂场景数据，产出图表：
     - 消融柱状图：各条件碰撞率与任务成功率对比
     - 端到端时延拆解图（VLM 推理 / MQTT 传输 / Pilot 推理 / Safety Gate 判断 / 动作执行）
     - GT vs Ego 3D 轨迹对比图（同场景 C2 vs C4）
     - 安全接管事件时间线（触发、恢复、LLM 修正时刻）
     - 4v4 对战胜率与碰撞率对比表
   - 报告正文写到 `docs/stage5/REPORT.md`，数据落盘 `results/stage5/`。
4. **演示视频**
   - 录制 Unity 对抗演示视频：正常飞行 → 危险注入 → Safety Gate 接管 → LLM 重规划恢复的完整链路。

## 依赖与开工顺序

- 任务 1 的场景搭建（红蓝航点注入、对抗逻辑）可以先基于 GT 模式开发，Ego 接入等组 3 的 runner 就绪。
- 任务 2 只依赖现有 runner，可提前开工。
- 任务 3、4 收尾进行，依赖组 3 数据。

## 验收标准

- 4v4 场景在 Rule Pilot + Safety Gate 下稳定运行，无消息死锁，落盘标准 JSONL/CSV 日志。
- 4v4 与 LLM 下线场景完成 C2/C3/C4 对比，含敌方估计误差统计。
- 效果报告包含上述全部图表 + 演示视频。

## 产出物

- 4v4 与极端条件场景实现及配置
- `results/stage5/` 复杂场景实验数据
- `docs/stage5/REPORT.md` Safety Gate 效果报告（含全部图表）
- 对抗演示视频
