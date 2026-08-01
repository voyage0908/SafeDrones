from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import random
import signal
import socket
import subprocess
import sys
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.stage4_marl_safety_check import Stage4Monitor, command
from swarm.llm_provider import CommanderLLM, LLMProviderError, WaypointPlan, load_llm_settings


SUPPORTED_CONDITIONS = {"C2", "C3", "C4"}
SCENARIO_NAMES = ["head_on_crossing", "perpendicular_crossing", "diagonal_crossing"]


@dataclass(frozen=True)
class Scenario:
    name: str
    separation_timeout: float = 12.0
    cross_timeout: float = 30.0
    near_miss_distance_m: float = 0.8
    collision_distance_m: float = 0.25

    def targets_for_seed(self, seed: int) -> dict[str, list[float]]:
        rng = random.Random(seed)
        lateral_offset = rng.uniform(-0.15, 0.15)
        z_offset = rng.uniform(-0.03, 0.03)
        altitude = 1.0 + z_offset
        if self.name == "head_on_crossing":
            return {
                "drone1_start": [-3.0, lateral_offset, altitude],
                "drone2_start": [3.0, -lateral_offset, altitude],
                "drone1_goal": [3.0, -lateral_offset, altitude],
                "drone2_goal": [-3.0, lateral_offset, altitude],
            }
        if self.name == "perpendicular_crossing":
            return {
                "drone1_start": [-3.0, lateral_offset, altitude],
                "drone2_start": [lateral_offset, -3.0, altitude],
                "drone1_goal": [3.0, -lateral_offset, altitude],
                "drone2_goal": [-lateral_offset, 3.0, altitude],
            }
        if self.name == "diagonal_crossing":
            return {
                "drone1_start": [-3.0, -3.0 + lateral_offset, altitude],
                "drone2_start": [-3.0, 3.0 - lateral_offset, altitude],
                "drone1_goal": [3.0, 3.0 - lateral_offset, altitude],
                "drone2_goal": [3.0, -3.0 + lateral_offset, altitude],
            }
        raise ValueError(f"unsupported scenario: {self.name}")

    def is_separated(self, snapshot: dict[int, dict[str, Any]]) -> bool:
        p1 = snapshot.get(1, {}).get("position")
        p2 = snapshot.get(2, {}).get("position")
        if p1 is None or p2 is None:
            return False
        if self.name == "head_on_crossing":
            return p1[0] < -2.0 and p2[0] > 2.0
        if self.name == "perpendicular_crossing":
            return p1[0] < -2.0 and p2[1] < -2.0
        if self.name == "diagonal_crossing":
            return p1[0] < -2.0 and p1[1] < -2.0 and p2[0] < -2.0 and p2[1] > 2.0
        raise ValueError(f"unsupported scenario: {self.name}")

    def is_complete(self, snapshot: dict[int, dict[str, Any]]) -> bool:
        p1 = snapshot.get(1, {}).get("position")
        p2 = snapshot.get(2, {}).get("position")
        if p1 is None or p2 is None:
            return False
        if self.name == "head_on_crossing":
            return p1[0] > 2.0 and p2[0] < -2.0
        if self.name == "perpendicular_crossing":
            return p1[0] > 2.0 and p2[1] > 2.0
        if self.name == "diagonal_crossing":
            return p1[0] > 2.0 and p1[1] > 2.0 and p2[0] > 2.0 and p2[1] < -2.0
        raise ValueError(f"unsupported scenario: {self.name}")

    def pilot_args(self) -> list[str]:
        if self.name in {"perpendicular_crossing", "diagonal_crossing"}:
            return [
                "--rule-safe-distance",
                "0.01",
                "--repulsion-gain",
                "0.0",
                "--max-speed",
                "2.0",
                "--horizon-sec",
                "0.5",
            ]
        return []

    def drone_args(self) -> list[str]:
        if self.name in {"perpendicular_crossing", "diagonal_crossing"}:
            return ["--speed", "1.6", "--max-accel", "2.0"]
        return []


@dataclass
class C4ReplanStats:
    llm_provider: str = ""
    llm_model: str = ""
    llm_replan_attempts: int = 0
    llm_replan_count: int = 0
    llm_replan_error_count: int = 0
    llm_replan_latency_ms: list[float] = field(default_factory=list)
    command_revision_count: int = 0
    llm_replan_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def avg_latency_ms(self) -> float:
        return _average(self.llm_replan_latency_ms)

    @property
    def max_latency_ms(self) -> float:
        if not self.llm_replan_latency_ms:
            return 0.0
        return round(max(self.llm_replan_latency_ms), 4)


def mission_goal_for_event(targets: dict[str, list[float]], event: dict[str, Any]) -> list[float]:
    drone_id = int(event.get("drone") or 0)
    key = f"drone{drone_id}_goal"
    if key not in targets:
        raise ValueError(f"unsupported drone in override event: {drone_id}")
    return targets[key]


def build_c4_replan_text(
    scenario: Scenario,
    targets: dict[str, list[float]],
    event: dict[str, Any],
    snapshot: dict[int, dict[str, Any]],
) -> str:
    drone_id = int(event.get("drone") or 0)
    mission_goal = mission_goal_for_event(targets, event)
    current_state = snapshot.get(drone_id, {})
    return (
        "Safety Gate 已完成高频紧急避障接管。你现在只做低频任务级恢复规划，"
        "不要替代 Safety Gate 做即时避障。请输出一个 JSON 对象，字段必须包含 "
        "drone, waypoint, priority, confidence, rationale。"
        f" 场景={scenario.name}; 无人机={drone_id};"
        f" 原任务终点={mission_goal}; 当前无人机状态={current_state};"
        f" Safety Gate override 事件={event};"
        " 如果原任务终点仍在边界内，请优先返回原任务终点作为恢复航点，"
        "priority 使用 high，rationale 简短说明收到 override 后恢复任务。"
        " waypoint 必须在 x/y [-4,4]、z [0.5,2.0] 内。"
    )


def validate_stage4_replan(plan: WaypointPlan, expected_drone: int) -> None:
    if plan.drone != expected_drone:
        raise LLMProviderError(f"LLM replanned drone {plan.drone}, expected {expected_drone}")

    x, y, z = plan.waypoint
    if not (-4.0 <= x <= 4.0 and -4.0 <= y <= 4.0 and 0.5 <= z <= 2.0):
        raise LLMProviderError(f"LLM waypoint outside stage4 benchmark bounds: {plan.waypoint}")


def build_llm_replan_command(plan: WaypointPlan, event: dict[str, Any], timestamp_ms: int) -> dict[str, Any]:
    return {
        "drone": plan.drone,
        "action": "move_to",
        "target": [float(value) for value in plan.waypoint],
        "waypoint": [float(value) for value in plan.waypoint],
        "priority": "high",
        "confidence": plan.confidence,
        "ttl_sec": 8.0,
        "command_id": f"llm-replan-{timestamp_ms}-{plan.drone}",
        "rationale": plan.rationale or "LLM recovery replan after safety override",
        "source_event": "safety_override",
        "source_command_id": event.get("command_id"),
        "timestamp_ms": timestamp_ms,
    }


class C4Replanner:
    def __init__(
        self,
        scenario: Scenario,
        targets: dict[str, list[float]],
        max_replans_per_trial: int = 2,
        max_llm_attempts: int = 3,
        retry_delay_sec: float = 1.0,
        commander: CommanderLLM | None = None,
    ):
        self.scenario = scenario
        self.targets = targets
        self.max_replans_per_trial = max_replans_per_trial
        self.max_llm_attempts = max_llm_attempts
        self.retry_delay_sec = retry_delay_sec
        self.commander = commander or CommanderLLM(load_llm_settings())
        self.stats = C4ReplanStats(
            llm_provider=self.commander.settings.provider,
            llm_model=self.commander.settings.model,
        )
        self._seen_events: set[tuple[int, str, int]] = set()

    def process_new_overrides(self, monitor: Stage4Monitor) -> None:
        if self.stats.llm_replan_attempts >= self.max_replans_per_trial:
            return

        for event in monitor.commander_events():
            if self.stats.llm_replan_attempts >= self.max_replans_per_trial:
                return
            if (event.get("event_name") or event.get("event")) != "safety_override":
                continue

            drone_id = int(event.get("drone") or 0)
            event_key = (
                drone_id,
                str(event.get("command_id") or ""),
                int(event.get("timestamp_ms") or 0),
            )
            if event_key in self._seen_events:
                continue

            self._seen_events.add(event_key)
            self._replan_once(monitor, event)

    def _replan_once(self, monitor: Stage4Monitor, event: dict[str, Any]) -> None:
        drone_id = int(event.get("drone") or 0)
        self.stats.llm_replan_attempts += 1
        prompt = build_c4_replan_text(self.scenario, self.targets, event, monitor.snapshot())
        started_at = time.perf_counter()

        plan: WaypointPlan | None = None
        last_error: Exception | None = None
        api_attempts = 0
        for api_attempts in range(1, self.max_llm_attempts + 1):
            try:
                plan = asyncio.run(self.commander.plan(prompt, drone=drone_id, context=[event]))
                validate_stage4_replan(plan, expected_drone=drone_id)
                break
            except Exception as exc:
                last_error = exc
                if api_attempts < self.max_llm_attempts:
                    time.sleep(self.retry_delay_sec)

        if plan is None:
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            self.stats.llm_replan_error_count += 1
            self.stats.llm_replan_events.append(
                {
                    "drone": drone_id,
                    "ok": False,
                    "latency_ms": round(latency_ms, 4),
                    "api_attempts": api_attempts,
                    "error": str(last_error),
                }
            )
            print(f"[stage4-benchmark] C4 LLM replan failed for drone={drone_id}: {last_error}")
            return

        latency_ms = (time.perf_counter() - started_at) * 1000.0
        command_payload = build_llm_replan_command(
            plan,
            event,
            timestamp_ms=int(time.time_ns() // 1_000_000),
        )
        monitor.publish_command(command_payload)

        self.stats.llm_replan_latency_ms.append(round(latency_ms, 4))
        self.stats.llm_replan_count += 1
        self.stats.command_revision_count += 1
        self.stats.llm_replan_events.append(
            {
                "drone": plan.drone,
                "ok": True,
                "latency_ms": round(latency_ms, 4),
                "api_attempts": api_attempts,
                "waypoint": list(plan.waypoint),
                "confidence": plan.confidence,
                "rationale": plan.rationale,
                "command_id": command_payload["command_id"],
            }
        )
        print(
            "[stage4-benchmark] "
            f"C4 LLM replan drone={plan.drone} waypoint={list(plan.waypoint)} "
            f"latency_ms={round(latency_ms, 1)}"
        )


class ManagedService:
    def __init__(self, name: str, argv: list[str], log_dir: Path):
        self.name = name
        self.argv = argv
        self.log_path = log_dir / f"{name}.log"
        self.log_file = None
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.log_file = self.log_path.open("wb")
        self.process = subprocess.Popen(
            self.argv,
            cwd=ROOT_DIR,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def ensure_running(self) -> None:
        if self.process is None:
            raise RuntimeError(f"{self.name} was not started")
        if self.process.poll() is not None:
            recent_log = self.log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"{self.name} exited early with code {self.process.returncode}\n{recent_log}")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if self.process.poll() is None:
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.process.wait(timeout=5)
        if self.log_file is not None:
            self.log_file.close()


def wait_for_tcp(host: str, port: int, timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for TCP {host}:{port}")


def parse_conditions(raw: str) -> list[str]:
    conditions = [item.strip().upper() for item in raw.split(",") if item.strip()]
    unknown = [condition for condition in conditions if condition not in SUPPORTED_CONDITIONS]
    if unknown:
        raise SystemExit(f"unsupported condition(s): {', '.join(unknown)}")
    return conditions


def build_services(condition: str, scenario: Scenario, log_dir: Path) -> list[ManagedService]:
    python = sys.executable
    services = [
        ManagedService("broker", [python, "scripts/dev_broker.py"], log_dir),
        ManagedService("drone1", [python, "mock_drone.py", "--drone-id", "1", *scenario.drone_args()], log_dir),
        ManagedService("drone2", [python, "mock_drone.py", "--drone-id", "2", *scenario.drone_args()], log_dir),
        ManagedService("marl_pilot", [python, "marl_pilot.py", *scenario.pilot_args()], log_dir),
    ]
    if condition in {"C3", "C4"}:
        services.append(ManagedService("safety_gate", [python, "safety_gate.py"], log_dir))
    return services


def start_services(services: list[ManagedService]) -> None:
    services[0].start()
    wait_for_tcp("127.0.0.1", 1883, timeout_sec=30)
    services[0].ensure_running()

    for service in services[1:3]:
        service.start()
    time.sleep(2)
    for service in services[1:3]:
        service.ensure_running()

    for service in services[3:]:
        service.start()
    time.sleep(2)
    for service in services[3:]:
        service.ensure_running()


def stop_services(services: list[ManagedService]) -> None:
    for service in reversed(services):
        service.stop()
    time.sleep(0.5)


def wait_for_crossing_completion(
    condition: str,
    scenario: Scenario,
    monitor: Stage4Monitor,
    timeout: float,
    replanner: C4Replanner | None = None,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if replanner is not None:
            replanner.process_new_overrides(monitor)
        if scenario.is_complete(monitor.snapshot()):
            return True
        time.sleep(0.1)

    print(f"[stage4-check] timed out waiting for {condition} position swap")
    return False


def run_trial(condition: str, scenario: Scenario, seed: int, log_dir: Path) -> dict[str, Any]:
    services = build_services(condition, scenario, log_dir)
    monitor = Stage4Monitor("127.0.0.1", 1883, qos=0)
    targets = scenario.targets_for_seed(seed)
    replanner = C4Replanner(scenario, targets) if condition == "C4" else None
    try:
        start_services(services)
        monitor.start()
        monitor.wait_for_telemetry(timeout=8)

        monitor.publish_command(command(1, targets["drone1_start"], "separate"))
        monitor.publish_command(command(2, targets["drone2_start"], "separate"))
        separated = monitor.wait_until(
            lambda: scenario.is_separated(monitor.snapshot()),
            timeout=scenario.separation_timeout,
            label=f"{condition} seed={seed} initial separation",
        )

        cross_started_at = time.time()
        if separated:
            monitor.publish_command(command(1, targets["drone1_goal"], "cross"))
            monitor.publish_command(command(2, targets["drone2_goal"], "cross"))

        swap_completed = separated and wait_for_crossing_completion(
            condition=condition,
            scenario=scenario,
            monitor=monitor,
            timeout=scenario.cross_timeout,
            replanner=replanner,
        )
        if replanner is not None:
            replanner.process_new_overrides(monitor)
        crossing_duration_sec = time.time() - cross_started_at

        summary = monitor.summary(swap_completed=swap_completed)
        min_distance = summary["min_distance_m"]
        collision_count = int(min_distance is not None and min_distance < scenario.collision_distance_m)
        near_miss_count = int(min_distance is not None and min_distance < scenario.near_miss_distance_m)
        c4_replan_success = replanner is None or (
            replanner.stats.llm_replan_count > 0 and replanner.stats.llm_replan_error_count == 0
        )
        task_success = bool(swap_completed and collision_count == 0 and c4_replan_success)
        llm_stats = replanner.stats if replanner is not None else C4ReplanStats()
        command_revision_rate = (
            round(llm_stats.command_revision_count / summary["override_count"], 4)
            if summary["override_count"]
            else 0.0
        )

        return {
            "scenario": scenario.name,
            "condition": condition,
            "seed": seed,
            "task_success": task_success,
            "swap_completed": swap_completed,
            "collision_count": collision_count,
            "near_miss_count": near_miss_count,
            "min_distance_m": min_distance,
            "override_count": summary["override_count"],
            "warning_count": summary["warning_count"],
            "marl_command_count": summary["marl_command_count"],
            "llm_provider": llm_stats.llm_provider,
            "llm_model": llm_stats.llm_model,
            "llm_replan_attempts": llm_stats.llm_replan_attempts,
            "llm_replan_count": llm_stats.llm_replan_count,
            "llm_replan_error_count": llm_stats.llm_replan_error_count,
            "command_revision_count": llm_stats.command_revision_count,
            "command_revision_rate": command_revision_rate,
            "llm_avg_latency_ms": llm_stats.avg_latency_ms,
            "llm_max_latency_ms": llm_stats.max_latency_ms,
            "llm_replan_events": llm_stats.llm_replan_events,
            "crossing_duration_sec": round(crossing_duration_sec, 4),
            "final_positions": summary["final_positions"],
            "final_statuses": summary["final_statuses"],
            "log_dir": str(log_dir),
        }
    finally:
        monitor.stop()
        stop_services(services)


def write_summary_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "scenario",
        "condition",
        "seed",
        "task_success",
        "swap_completed",
        "collision_count",
        "near_miss_count",
        "min_distance_m",
        "override_count",
        "warning_count",
        "marl_command_count",
        "llm_provider",
        "llm_model",
        "llm_replan_attempts",
        "llm_replan_count",
        "llm_replan_error_count",
        "command_revision_count",
        "command_revision_rate",
        "llm_avg_latency_ms",
        "llm_max_latency_ms",
        "crossing_duration_sec",
        "log_dir",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({field: result.get(field) for field in fields})


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def write_aggregate_csv(path: Path, results: list[dict[str, Any]]) -> None:
    fields = [
        "scenario",
        "condition",
        "runs",
        "success_rate",
        "collision_rate",
        "near_miss_rate",
        "override_rate",
        "avg_min_distance_m",
        "min_min_distance_m",
        "avg_override_count",
        "avg_warning_count",
        "avg_llm_replan_count",
        "avg_llm_replan_error_count",
        "avg_command_revision_rate",
        "avg_llm_latency_ms",
        "max_llm_latency_ms",
        "avg_crossing_duration_sec",
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in results:
        key = (str(result["scenario"]), str(result["condition"]))
        grouped.setdefault(key, []).append(result)

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for (scenario, condition), group in sorted(grouped.items()):
            min_distances = [
                float(result["min_distance_m"])
                for result in group
                if result.get("min_distance_m") is not None
            ]
            row = {
                "scenario": scenario,
                "condition": condition,
                "runs": len(group),
                "success_rate": _average([float(bool(result["task_success"])) for result in group]),
                "collision_rate": _average([float(result["collision_count"]) for result in group]),
                "near_miss_rate": _average([float(result["near_miss_count"]) for result in group]),
                "override_rate": _average([float(result["override_count"] > 0) for result in group]),
                "avg_min_distance_m": _average(min_distances),
                "min_min_distance_m": round(min(min_distances), 4) if min_distances else "",
                "avg_override_count": _average([float(result["override_count"]) for result in group]),
                "avg_warning_count": _average([float(result["warning_count"]) for result in group]),
                "avg_llm_replan_count": _average(
                    [float(result.get("llm_replan_count") or 0) for result in group]
                ),
                "avg_llm_replan_error_count": _average(
                    [float(result.get("llm_replan_error_count") or 0) for result in group]
                ),
                "avg_command_revision_rate": _average(
                    [float(result.get("command_revision_rate") or 0.0) for result in group]
                ),
                "avg_llm_latency_ms": _average(
                    [
                        float(result.get("llm_avg_latency_ms") or 0.0)
                        for result in group
                        if float(result.get("llm_avg_latency_ms") or 0.0) > 0.0
                    ]
                ),
                "max_llm_latency_ms": round(
                    max([float(result.get("llm_max_latency_ms") or 0.0) for result in group]),
                    4,
                ),
                "avg_crossing_duration_sec": _average(
                    [float(result["crossing_duration_sec"]) for result in group]
                ),
            }
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run stage-four C2/C3/C4 benchmark trials.")
    parser.add_argument(
        "--scenario",
        default="head_on_crossing",
        choices=[*SCENARIO_NAMES, "all"],
    )
    parser.add_argument("--conditions", default="C2,C3")
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--out", default="results/stage4")
    args = parser.parse_args()

    if args.seeds <= 0:
        raise SystemExit("--seeds must be positive")

    conditions = parse_conditions(args.conditions)
    scenarios = [Scenario(name) for name in (SCENARIO_NAMES if args.scenario == "all" else [args.scenario])]
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_name = "all_scenarios" if args.scenario == "all" else scenarios[0].name
    output_dir = Path(args.out) / output_name / run_id
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    runs_path = output_dir / "runs.jsonl"
    results: list[dict[str, Any]] = []
    with runs_path.open("w", encoding="utf-8") as runs_file:
        for scenario in scenarios:
            for condition in conditions:
                for seed in range(args.seeds):
                    trial_log_dir = logs_dir / scenario.name / f"{condition}_seed_{seed}"
                    trial_log_dir.mkdir(parents=True, exist_ok=True)
                    print(f"[stage4-benchmark] scenario={scenario.name} condition={condition} seed={seed}")
                    result = run_trial(condition, scenario, seed, trial_log_dir)
                    results.append(result)
                    runs_file.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
                    runs_file.flush()
                    print(
                        "[stage4-benchmark] "
                        f"success={result['task_success']} min_distance={result['min_distance_m']} "
                        f"overrides={result['override_count']} llm_replans={result['llm_replan_count']}"
                    )

    write_summary_csv(output_dir / "summary.csv", results)
    write_aggregate_csv(output_dir / "aggregate.csv", results)
    print(f"[stage4-benchmark] wrote {runs_path}")
    print(f"[stage4-benchmark] wrote {output_dir / 'summary.csv'}")
    print(f"[stage4-benchmark] wrote {output_dir / 'aggregate.csv'}")


if __name__ == "__main__":
    main()
