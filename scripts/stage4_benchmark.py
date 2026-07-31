from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
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


SUPPORTED_CONDITIONS = {"C2", "C3"}


@dataclass(frozen=True)
class Scenario:
    name: str
    separation_timeout: float = 12.0
    cross_timeout: float = 30.0
    near_miss_distance_m: float = 0.8
    collision_distance_m: float = 0.25

    def targets_for_seed(self, seed: int) -> dict[str, list[float]]:
        rng = random.Random(seed)
        y_offset = rng.uniform(-0.15, 0.15)
        z_offset = rng.uniform(-0.03, 0.03)
        altitude = 1.0 + z_offset
        return {
            "drone1_start": [-3.0, y_offset, altitude],
            "drone2_start": [3.0, -y_offset, altitude],
            "drone1_goal": [3.0, -y_offset, altitude],
            "drone2_goal": [-3.0, y_offset, altitude],
        }


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


def build_services(condition: str, log_dir: Path) -> list[ManagedService]:
    python = sys.executable
    services = [
        ManagedService("broker", [python, "scripts/dev_broker.py"], log_dir),
        ManagedService("drone1", [python, "mock_drone.py", "--drone-id", "1"], log_dir),
        ManagedService("drone2", [python, "mock_drone.py", "--drone-id", "2"], log_dir),
        ManagedService("marl_pilot", [python, "marl_pilot.py"], log_dir),
    ]
    if condition == "C3":
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


def run_trial(condition: str, scenario: Scenario, seed: int, log_dir: Path) -> dict[str, Any]:
    services = build_services(condition, log_dir)
    monitor = Stage4Monitor("127.0.0.1", 1883, qos=0)
    targets = scenario.targets_for_seed(seed)
    try:
        start_services(services)
        monitor.start()
        monitor.wait_for_telemetry(timeout=8)

        monitor.publish_command(command(1, targets["drone1_start"], "separate"))
        monitor.publish_command(command(2, targets["drone2_start"], "separate"))
        separated = monitor.wait_until(
            lambda: (
                (snapshot := monitor.snapshot()).get(1, {}).get("position", [0, 0])[0] < -2.0
                and snapshot.get(2, {}).get("position", [0, 0])[0] > 2.0
            ),
            timeout=scenario.separation_timeout,
            label=f"{condition} seed={seed} initial separation",
        )

        cross_started_at = time.time()
        if separated:
            monitor.publish_command(command(1, targets["drone1_goal"], "cross"))
            monitor.publish_command(command(2, targets["drone2_goal"], "cross"))

        swap_completed = separated and monitor.wait_until(
            lambda: (
                (snapshot := monitor.snapshot()).get(1, {}).get("position", [0, 0])[0] > 2.0
                and snapshot.get(2, {}).get("position", [0, 0])[0] < -2.0
            ),
            timeout=scenario.cross_timeout,
            label=f"{condition} seed={seed} position swap",
        )
        crossing_duration_sec = time.time() - cross_started_at

        summary = monitor.summary(swap_completed=swap_completed)
        min_distance = summary["min_distance_m"]
        collision_count = int(min_distance is not None and min_distance < scenario.collision_distance_m)
        near_miss_count = int(min_distance is not None and min_distance < scenario.near_miss_distance_m)
        task_success = bool(swap_completed and collision_count == 0)

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
        "crossing_duration_sec",
        "log_dir",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({field: result.get(field) for field in fields})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run stage-four C2/C3 benchmark trials.")
    parser.add_argument("--scenario", default="head_on_crossing", choices=["head_on_crossing"])
    parser.add_argument("--conditions", default="C2,C3")
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--out", default="results/stage4")
    args = parser.parse_args()

    if args.seeds <= 0:
        raise SystemExit("--seeds must be positive")

    conditions = parse_conditions(args.conditions)
    scenario = Scenario(args.scenario)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.out) / scenario.name / run_id
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    runs_path = output_dir / "runs.jsonl"
    results: list[dict[str, Any]] = []
    with runs_path.open("w", encoding="utf-8") as runs_file:
        for condition in conditions:
            for seed in range(args.seeds):
                trial_log_dir = logs_dir / f"{condition}_seed_{seed}"
                trial_log_dir.mkdir(parents=True, exist_ok=True)
                print(f"[stage4-benchmark] scenario={scenario.name} condition={condition} seed={seed}")
                result = run_trial(condition, scenario, seed, trial_log_dir)
                results.append(result)
                runs_file.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
                runs_file.flush()
                print(
                    "[stage4-benchmark] "
                    f"success={result['task_success']} min_distance={result['min_distance_m']} "
                    f"overrides={result['override_count']}"
                )

    write_summary_csv(output_dir / "summary.csv", results)
    print(f"[stage4-benchmark] wrote {runs_path}")
    print(f"[stage4-benchmark] wrote {output_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
