# 阶段五场景扩展实施计划：llm_timeout / packet_loss / four_v_four

本文档是组 4 第一批任务的实施计划，对应 `docs/groups/group4_scenarios_report.md` 的任务 1、2。全部工作基于 GT 模式，不依赖组 1/2 的相机链路。

## 目标

1. `llm_timeout`：C4 条件下 LLM 重规划延迟/下线，验证系统仅靠 Safety Gate 保持安全；
2. `packet_loss`：MQTT 丢包 20%，验证协议鲁棒性；
3. `four_v_four`：4v4 红蓝对抗（GT 模式），蓝方 Pilot + Safety Gate，红方追击。

## 现状关键事实（已核实）

- `scripts/stage4_benchmark.py`：`Scenario` 硬编码 2 机（drone1/drone2）；`build_services` 启动 broker + 2 × mock_drone + marl_pilot（+safety_gate）；`run_trial` 流程 = 分离 → 交叉 → 汇总。
- `scripts/stage4_marl_safety_check.py` 的 `Stage4Monitor._record_telemetry`：min_distance 只算 1、2 两机（`{1, 2}.issubset(...)`），4v4 需泛化为全配对。
- `marl_pilot.py`：priority 为 `marl`/`safety` 的 command 不会被采纳为高层目标——红方指令可借此避开 Pilot，但更干净的做法是给 Pilot 加 id 白名单。
- `safety_gate.py`：订阅所有 telemetry，对所有无人机发 override；4v4 需要"只保护蓝方、但仍把红方当障碍物"。
- 所有服务（mock_drone / marl_pilot / safety_gate / Stage4Monitor）都支持 `--host/--port`，丢包代理可以透明插入。
- `C4Replanner`（`stage4_benchmark.py`）：override 后同步调 DeepSeek，天然适合注入延迟/下线。

## 实施步骤

### 阶段 A：llm_timeout + packet_loss（两机 runner 上扩展）

#### A1. llm_timeout 场景（改 `scripts/stage4_benchmark.py`）

- `Scenario` 增加字段 `llm_delay_sec: float = 0.0`；`SCENARIO_NAMES` 增加 `"llm_timeout"`。
- `llm_timeout` 复用 `head_on_crossing` 的几何与判定（`targets_for_seed`/`is_separated`/`is_complete` 走 head_on 分支），但 `llm_delay_sec=4.0`。
- `C4Replanner._replan_once` 在调 API 前 `time.sleep(self.scenario.llm_delay_sec)`（模拟 LLM 下线 4 秒），`llm_replan_events` 增加 `injected_delay_sec` 字段。
- 验收口径：C4 + llm_timeout 下碰撞率必须为 0、min_distance ≥ C3 同场景水平——证明 LLM 延迟期间 Safety Gate 独立保安全。

#### A2. packet_loss：MQTT 丢包代理（新建 `scripts/mqtt_lossy_proxy.py`）

- 纯 TCP 代理：监听 `--listen-port 1884`，每个客户端连接配对一条到 broker（`--target-port 1883`）的连接。
- 按 MQTT v3.1.1 帧解析（首字节 + remaining length 变长编码）切包；对 PUBLISH 包（高 4 位 = 3）按 `--drop-rate`（默认 0.2）随机整包丢弃，其余包原样转发；双向独立生效。
- runner 增加 `--packet-loss 0.2` 选项：开启时在 services 最前面插入 proxy 服务，drone/pilot/gate/monitor 全部指向 1884；结果 dict 记录 `packet_loss_rate`。
- 不改动任何业务进程代码。

#### A3. 单元测试

- `tests/test_mqtt_lossy_proxy.py`：MQTT 帧切分函数（单包、粘包、半包、remaining length 多字节）+ 丢包判定确定性（seed 固定）。
- `tests/test_stage4_benchmark.py` 补充：`llm_timeout` 场景目标生成、replanner 注入延迟字段。

### 阶段 B：four_v_four（GT 模式红蓝对抗）

#### B1. Monitor 泛化（`scripts/stage4_marl_safety_check.py`）

- `_record_telemetry` 的 min_distance 改为对所有已上报无人机做全配对最小距离（2 机场景行为不变）；`telemetry_ready` 改为按期望机数（构造参数 `expected_drones`，默认 2）。

#### B2. Pilot / Gate 的队伍过滤

- `marl_pilot.py` 增加 `--drone-ids 1,2,3,4`（默认空 = 全部，向后兼容）：只对名单内无人机发 micro-waypoint。
- `safety_gate.py` 增加 `--protect-ids 1,2,3,4`（默认空 = 全部）：仍订阅全部 telemetry 做风险评估，但只对保护名单内无人机发 override command/event。不改 `swarm/safety.py`。

#### B3. 场景与红方控制器（改 `scripts/stage4_benchmark.py`）

- `SCENARIO_NAMES` 增加 `"four_v_four"`；`Scenario` 增加 `blue_ids`/`red_ids`（本场景为 1-4 / 5-8，默认空 = 2 机逻辑不变）。
- `targets_for_seed`：蓝方起点 x=-3（y 均布 -1.5~1.5，加种子抖动），目标 x=+3 镜像；红方起点 x=+3 中线附近。
- `is_separated`：蓝方全部 x < -2.0；`is_complete`：蓝方全部 x > 2.0。
- 新增 `RedPursuitController`：交叉阶段每 2 秒对每架红机发布 priority=`"red"` 的追击指令，目标 = 最近蓝机当前位置（含少量提前量）；红方指令不进 Pilot（B2 白名单保证）。
- `build_services` 按场景机数启动 8 个 mock_drone；`run_trial` 交叉循环里加入红方 tick。
- 新增结果字段：`blue_reached_count`、`blue_win`（全部到达且零碰撞）、`red_win`（发生碰撞）、`pairwise_min_distance_m`。

#### B4. 单元测试

- 8 机场景目标生成、蓝方完成/分离判定、红方追击目标选择逻辑、Monitor 全配对 min_distance。

### 阶段 C：验证与数据落盘

- `conda run -n eai-swarm python -m unittest discover -s tests` 全绿。
- 冒烟运行（真实进程，写入 `results/stage5/`）：
  - `llm_timeout` C3/C4 各 3 种子（C4 走真实 DeepSeek，验证延迟注入下零碰撞）；
  - `packet_loss` C3 3 种子（drop-rate 0.2，head_on）；
  - `four_v_four` C2/C3 各 3 种子（C2 预期红方拦截成功/有碰撞，C3 预期零碰撞）。
- 更新 `docs/groups/group4_scenarios_report.md`：标记任务 1、2 的场景实现状态与运行命令。
- 10 种子全量跑留待冒烟通过后另行执行。

## 关键设计取舍

- **丢包用独立 TCP 代理**：业务代码零侵入，所有进程本来就有 `--port`；代理只做帧切分 + 整包丢弃，不理解业务语义。
- **llm_timeout 实现为"场景 = 几何 + LLM 延迟注入"**，而非新几何：它验收的是协议在 LLM 下线时的安全性，不是新航线。
- **红方不进 Pilot、不受 Gate 保护**：通过 `--drone-ids`/`--protect-ids` 白名单实现，Pilot/Gate 默认行为不变，2 机场景零回归。
- **红方追击逻辑放在 runner 进程内**（`RedPursuitController`），不新增常驻进程，生命周期与 trial 一致。

## 预期交付文件

- 修改：`scripts/stage4_benchmark.py`、`scripts/stage4_marl_safety_check.py`、`marl_pilot.py`、`safety_gate.py`、`tests/test_stage4_benchmark.py`
- 新建：`scripts/mqtt_lossy_proxy.py`、`tests/test_mqtt_lossy_proxy.py`
- 文档：`docs/groups/group4_scenarios_report.md` 状态更新
- 数据：`results/stage5/` 冒烟结果
