"""确定性进程内基准：可复现的 C2/C3/C4 安全结论。

与 `stage4_benchmark.py`（实时多进程 MQTT 基准）不同，本脚本在**同一进程内、固定步长**地
驱动「MARL 飞控朝目标 + Safety Gate 否决 + MockDroneState 运动学」这条安全闭环的核心，
复用完全相同的场景几何（`Scenario.targets_for_seed`）、碰撞判据（`collision_distance_m`）、
安全门逻辑（`SafetyGate`）与运动学（`MockDroneState`），只去掉 MQTT / 多进程 / 墙钟，
从而得到**逐 bit 可复现**的安全结论。

为什么要它：
- `stage4_benchmark.py` 是实时多进程 MQTT 系统，其 min_distance / 成功率受进程调度与
  消息时序影响，在不同机器 / 负载下会抖动（本机 formation C3 复现 20% vs 文档 100%）。
- 安全结论本身（C2 无安全门=碰撞、C3 仅安全门=0 碰撞）是**确定性**的，应由本脚本给出
  可复现数字；MQTT 基准作为端到端集成冒烟测试保留（证明整套架构能跑通，绝对值指示性）。

建模范围（诚实边界）：
- 本脚本建模「朝目标飞行 + 安全门 override 分流」这一**硬安全底线**，只覆盖无红方追击的
  6 个对称穿越场景（head_on / perpendicular / diagonal / llm_timeout / four_way / formation，
  均 `red_pursuit=False`）。红方追击微控（`RedPursuitController`）属对抗/任务级，不在建模范围内。
- C4 在本脚本里的建模 = C3 的安全门 + **确定性恢复**（改进① `_replay_cached` 缓存「返回原任务
  终点」的确定性航点）。因恢复航点 = 原任务终点 = C3 接管结束后的默认回目标行为，C4 的飞行轨迹
  与 C3 一致，故 C4 的 0 碰撞结论同样确定性可复现；`replan_failure` 恒为 0（确定性缓存不失败）。
- C4 的「AGH/LLM **实时**重规划」（模型输出、非确定性）不在本脚本建模范围内——它由 MQTT 基准的
  「重规划失败=0」+ [DEMO_MCP.md](DEMO_MCP.md) 功能演示体现，是**闭环功能**而非数值可复现主张。

丢包消融（升级 D，`--packet-loss`）：
- 本脚本用**固定种子 RNG** 建模 MQTT 丢包的两处主效应，逐 bit 可复现：① **遥测丢包**——安全门在某
  tick 以概率 p 看不到某架的最新位置（沿用上次成功的遥测，感知滞后）；② **命令丢包**——override 分流
  命令以概率 p 丢失（该 tick 不转向）。安全门每 0.1s 重决策、每 0.5s 重发命令，天然具备重试缓冲，
  丢包消融量化的是「这个重试缓冲在多大丢包率下被击穿」。
- 注意：真实 MQTT 系统的丢包还叠加了进程调度/时序抖动，本脚本的丢包数值是**理想化下界**（只建模丢包
  主效应、无时序噪声）；端到端的丢包注入（`stage4_benchmark.py --packet-loss` + `mqtt_lossy_proxy.py`）
  作为集成冒烟测试保留。

用法：
  python scripts/stage4_benchmark_deterministic.py --scenario all --conditions C2,C3,C4 --seeds 10 --packet-loss 0.0,0.1,0.3,0.5
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import random
import sys

# 复用 stage4_benchmark 的 Scenario 与条件解析，保证几何/碰撞判据完全一致。
_ROOT = Path(__file__).resolve().parents[1]
_HERE = Path(__file__).resolve().parent
for _p in (_ROOT, _HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from swarm.safety import DroneSnapshot, SafetyConfig, SafetyGate  # noqa: E402
from swarm.simulation import MockDroneState, distance  # noqa: E402
from stage4_benchmark import Scenario, SCENARIO_NAMES  # noqa: E402

SUPPORTED_CONDITIONS = {"C2", "C3", "C4"}
_DT = 0.05  # 仿真步长（秒），20Hz 运动学积分
_GATE_INTERVAL = 0.1  # Safety Gate 决策间隔（秒），与 safety_gate.py --interval 一致
_SAFETY_HOLD_SEC = 1.5  # 无人机「安全命令优先」窗口（秒），与 MockDroneState.apply_command 一致
ARRIVAL_RADIUS_M = 0.3
_LOSS_SEED_OFFSET = 1_000_000  # 丢包 RNG 与几何种子解耦，保证丢包模式独立于起点随机

# 本脚本只覆盖「无红方追击」的对称穿越场景（red_pursuit=False）。pursuit_intercept /
# four_v_four 依赖 RedPursuitController（红方主动追击微控），属对抗/任务级，不在「硬安全底线」
# 的确定性建模范围内；且其文档结果本身受 MQTT 时序主导、也非「0 碰撞」强声明，故排除。
DETERMINISTIC_SCENARIOS = [
    "head_on_crossing",
    "perpendicular_crossing",
    "diagonal_crossing",
    "llm_timeout",
    "four_way_crossing",
    "formation_crossing",
]


def _is_dense(scenario: Scenario) -> bool:
    return scenario._geometry() in {
        "four_way_crossing",
        "pursuit_intercept",
        "formation_crossing",
        "four_v_four",
    }


def _drone_speed_accel(scenario: Scenario) -> tuple[float, float]:
    if scenario._geometry() in {
        "four_way_crossing",
        "pursuit_intercept",
        "formation_crossing",
        "four_v_four",
        "perpendicular_crossing",
        "diagonal_crossing",
    }:
        return 1.6, 2.0
    return 1.0, 1.0


def _protect_ids(scenario: Scenario) -> set[int]:
    # 与 build_services 一致：密集多机场景对称保护全部无人机，其余只保护蓝方。
    if scenario.name in {"formation_crossing", "four_v_four"}:
        return set(scenario.all_ids())
    return set(scenario.all_blue_ids())


def run_trial(
    scenario: Scenario,
    condition: str,
    seed: int,
    packet_loss_rate: float = 0.0,
) -> dict[str, float | int]:
    targets = scenario.targets_for_seed(seed)
    ids = scenario.all_ids()
    start = {did: targets[f"drone{did}_start"] for did in ids}
    goal = {did: targets[f"drone{did}_goal"] for did in ids}

    loss_rng = random.Random(seed + _LOSS_SEED_OFFSET)

    speed, accel = _drone_speed_accel(scenario)
    states = {
        did: MockDroneState(
            drone_id=did,
            current_pos=start[did],
            target_pos=start[did],
            speed_mps=speed,
            max_accel_mps2=accel,
        )
        for did in ids
    }

    gate = None
    protect: set[int] = set()
    if condition in ("C3", "C4"):
        dense = scenario.name in {"formation_crossing", "four_v_four"}
        cfg = SafetyConfig(
            vertical_escape_gain=0.5 if dense else 0.0,
            escape_distance_m=1.2,
        )
        gate = SafetyGate(cfg)
        protect = _protect_ids(scenario)

    last_safety: dict[int, float] = {}
    min_distance = float("inf")
    override_count = 0
    active_override: set[int] = set()  # 当前处于 override 的无人机（用于去重「新接管」事件）
    replan_count = 0
    replan_failure = 0  # 确定性恢复（返回原任务终点）恒不失败
    completed = False
    # 安全门「最近一次成功接收的遥测」（丢包时沿用上次，感知滞后）
    gate_seen_pos: dict[int, tuple[float, float, float]] = {did: tuple(start[did]) for did in ids}
    gate_seen_vel: dict[int, tuple[float, float, float]] = {did: (0.0, 0.0, 0.0) for did in ids}

    steps = int(scenario.cross_timeout / _DT)
    gate_every = max(1, round(_GATE_INTERVAL / _DT))
    check_ids = scenario.all_blue_ids() if scenario.red_pursuit else ids

    # 预生成每 (drone, gate_tick) 的丢包阈值。固定 per-seed 序列，逐 tick 与 packet_loss_rate
    # 比较，保证「丢包率越高 → 严格丢弃更多同源事件」（单调嵌套），避免不同丢包率各自独立
    # 抽样导致「30% 反而比 50% 更差」的伪非单调。
    num_gate_ticks = steps // gate_every + 1
    telemetry_u = {did: [loss_rng.random() for _ in range(num_gate_ticks)] for did in ids}
    command_u = {did: [loss_rng.random() for _ in range(num_gate_ticks)] for did in ids}

    for step in range(steps):
        t = step * _DT

        # Safety Gate 决策（C3 / C4）
        if gate is not None and step % gate_every == 0:
            gate_tick_idx = step // gate_every
            # 遥测丢包：以概率 p 看不到最新位置，沿用上次成功遥测（感知滞后）
            snaps = []
            for did in ids:
                if packet_loss_rate <= 0.0 or telemetry_u[did][gate_tick_idx] >= packet_loss_rate:
                    gate_seen_pos[did] = states[did].current_pos
                    gate_seen_vel[did] = states[did].velocity
                snaps.append(
                    DroneSnapshot(
                        did,
                        gate_seen_pos[did],
                        target=goal[did],
                        velocity=gate_seen_vel[did],
                        speed_mps=states[did].speed_mps,
                    )
                )
            for decision in gate.evaluate(snaps, now=t):
                if decision.drone not in protect:
                    continue
                if decision.mode == "override":
                    override_count += 1
                    if decision.drone not in active_override:
                        active_override.add(decision.drone)
                        # C4：新 override 事件触发一次确定性重规划（返回原任务终点），上限 2 次/试
                        if condition == "C4" and replan_count < 2:
                            replan_count += 1
                    # 命令丢包：以概率 p 丢失 override 命令，该 tick 不转向（下个 tick 重发）
                    if decision.safety_waypoint is not None and (
                        packet_loss_rate <= 0.0 or command_u[decision.drone][gate_tick_idx] >= packet_loss_rate
                    ):
                        states[decision.drone].target_pos = decision.safety_waypoint
                        last_safety[decision.drone] = t
                else:
                    active_override.discard(decision.drone)

        # 非接管窗口内的无人机朝目标飞行（「朝目标」= monitor 下发的 goal 命令 + 飞控意图）
        for did in ids:
            if t < last_safety.get(did, -1e9) + _SAFETY_HOLD_SEC:
                continue
            states[did].target_pos = goal[did]
            states[did].status = "flying"

        # 运动学积分
        for did in ids:
            states[did].update(_DT)

        # 最小间距（穿越全程）
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                min_distance = min(
                    min_distance,
                    distance(states[a].current_pos, states[b].current_pos),
                )

        # 完成判据（与 MQTT 基准一致：goal-arrival）
        if all(
            distance(states[did].current_pos, goal[did]) <= ARRIVAL_RADIUS_M
            for did in check_ids
        ):
            completed = True
            break

    collision = int(min_distance < scenario.collision_distance_m)
    task_success = int(bool(completed and collision == 0))
    return {
        "scenario": scenario.name,
        "condition": condition,
        "seed": seed,
        "packet_loss_rate": packet_loss_rate,
        "task_success": task_success,
        "swap_completed": int(completed),
        "collision_count": collision,
        "min_distance_m": round(min_distance, 4) if min_distance != float("inf") else None,
        "override_count": override_count,
        "replan_count": replan_count,
        "replan_failure": replan_failure,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic in-process C2/C3/C4 safety benchmark.")
    parser.add_argument("--scenario", default="all", choices=[*DETERMINISTIC_SCENARIOS, "all"])
    parser.add_argument("--conditions", default="C2,C3,C4")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--packet-loss", default="0.0",
                        help="逗号分隔的丢包率列表（0..1），对遥测与 override 命令各独立丢包。")
    parser.add_argument("--out", default="results/stage4_deterministic")
    args = parser.parse_args()

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    for c in conditions:
        if c not in SUPPORTED_CONDITIONS:
            raise SystemExit(f"本脚本只支持 {sorted(SUPPORTED_CONDITIONS)}，收到 {c}（注：本脚本只建模 C4 的确定性恢复，不含 AGH/LLM 实时重规划）")
    loss_rates = [float(x.strip()) for x in args.packet_loss.split(",") if x.strip()]
    for p in loss_rates:
        if not 0.0 <= p <= 1.0:
            raise SystemExit(f"--packet-loss 每个值须在 0..1 之间，收到 {p}")
    scenarios = [Scenario(n) for n in (DETERMINISTIC_SCENARIOS if args.scenario == "all" else [args.scenario])]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary.csv"
    agg_path = out_dir / "aggregate.csv"

    rows: list[dict[str, float | int]] = []
    for scenario in scenarios:
        for condition in conditions:
            for loss_rate in loss_rates:
                for seed in range(args.seeds):
                    rows.append(run_trial(scenario, condition, seed, packet_loss_rate=loss_rate))

    fieldnames = [
        "scenario", "condition", "packet_loss_rate", "seed", "task_success", "swap_completed",
        "collision_count", "min_distance_m", "override_count",
        "replan_count", "replan_failure",
    ]
    with summary_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    # 聚合
    agg_fieldnames = [
        "scenario", "condition", "packet_loss_rate", "runs", "success_rate", "collision_rate",
        "min_min_distance_m", "avg_min_distance_m", "avg_override_count",
        "avg_replan_count", "replan_failure_count",
    ]
    print(f"{'scenario':22} {'cond':3} {'loss':>4} {'succ':>4} {'coll':>4} {'worst_min':>9} {'avg_min':>8} {'avg_ov':>7} {'avg_rp':>6} {'rp_fail':>7}")
    agg_rows = []
    for scenario in scenarios:
        for condition in conditions:
            for loss_rate in loss_rates:
                group = [r for r in rows
                         if r["scenario"] == scenario.name
                         and r["condition"] == condition
                         and r["packet_loss_rate"] == loss_rate]
                success = sum(r["task_success"] for r in group)
                collisions = sum(r["collision_count"] for r in group)
                mins = [r["min_distance_m"] for r in group if r["min_distance_m"] is not None]
                worst = min(mins) if mins else None
                avg = round(sum(mins) / len(mins), 4) if mins else None
                avg_ov = round(sum(r["override_count"] for r in group) / len(group), 1)
                avg_rp = round(sum(r["replan_count"] for r in group) / len(group), 2)
                rp_fail = sum(r["replan_failure"] for r in group)
                agg = {
                    "scenario": scenario.name, "condition": condition,
                    "packet_loss_rate": loss_rate, "runs": len(group),
                    "success_rate": success / len(group), "collision_rate": collisions / len(group),
                    "min_min_distance_m": worst, "avg_min_distance_m": avg,
                    "avg_override_count": avg_ov, "avg_replan_count": avg_rp,
                    "replan_failure_count": rp_fail,
                }
                agg_rows.append(agg)
                print(
                    f"{scenario.name:22} {condition:3} {loss_rate:4.2f} {success:4}/{len(group)} "
                    f"{collisions:4}/{len(group)} {worst:9.3f} {avg:8.3f} {avg_ov:7.1f} {avg_rp:6.2f} {rp_fail:7}"
                )

    with agg_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=agg_fieldnames)
        writer.writeheader()
        for row in agg_rows:
            writer.writerow(row)

    print(f"\nwrote {summary_path}")
    print(f"wrote {agg_path}")


if __name__ == "__main__":
    main()
