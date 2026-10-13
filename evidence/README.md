# 运行证据（确定性基准原始数据）

本目录存放**确定性进程内基准**的原始输出，作为「验证严谨性与异常处理」维度的可复现证据。

- `deterministic_aggregate.csv` —— 聚合结果（73 行 = 6 场景 × 3 条件 × 4 丢包率）
- `deterministic_summary.csv` —— 逐种子明细（720 行 = 6 场景 × 3 条件 × 4 丢包率 × 10 种子）

## 复现命令

```bash
./.venv/Scripts/python.exe scripts/stage4_benchmark_deterministic.py \
  --scenario all --conditions C2,C3,C4 --seeds 10 --packet-loss 0.0,0.1,0.3,0.5 \
  --out results/stage4_deterministic
```

输出与本目录文件逐 bit 一致（固定种子、进程内固定步长，无 MQTT 时序噪声）。

## 结论摘要

- **C2（无安全门）**：6 场景 10/10 碰撞（无硬安全兜底）
- **C3/C4（有安全门）**：0% 丢包下 6 场景 0 碰撞；C4 重规划失败恒 0
- **丢包鲁棒性**：0–30% 几乎零碰撞、最坏最小间距单调侵蚀；50% 极端丢包才在密集场景
  （four_way / formation）失守，简单两机场景基本 0 碰撞（perpendicular 在 30%+ 偶发 1 次边缘碰撞）

详见 `../DEMO_RECORDING.md` 2.2 节与 `../RESULTS.md`。生成时间：2026-10-13。
