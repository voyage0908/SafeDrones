#!/usr/bin/env bash
# llm_timeout 场景 demo：C4 双向协议下注入 4 秒 LLM 下线，
# 在 Unity 中观察 Safety Gate 在 LLM 无响应期间独立保持安全。
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
CURRENT_LOG_DIR="$ROOT_DIR/logs/llm_timeout_demo/$RUN_ID"

SEED=0
INTERACTIVE=1
PIDS=()
CLEANED=0

usage() {
  cat <<'EOF'
Usage:
  bash scripts/llm_timeout_demo.sh [--seed 0-9] [--non-interactive]

C4 condition with a 4-second LLM outage injected before every AGH (Agnes) replan.
Geometry and seed targets come from Scenario("llm_timeout") in scripts/stage4_benchmark.py
(head_on crossing geometry, llm_delay_sec=4.0).

Watch in Unity:
  1. two drones cross head-on, Safety Gate turns them yellow/red and separates them;
  2. the LLM replan arrives only after the injected 4s outage (+ real API latency),
     during which the gate alone keeps the drones safe;
  3. after the replan command lands, the drones resume and finish the crossing.

Examples:
  bash scripts/llm_timeout_demo.sh
  bash scripts/llm_timeout_demo.sh --seed 3 --non-interactive
EOF
}

parse_args() {
  while (($# > 0)); do
    case "$1" in
      --seed)
        if (($# < 2)); then
          echo "[demo] --seed requires a value" >&2
          exit 2
        fi
        SEED="$2"
        shift 2
        ;;
      --non-interactive)
        INTERACTIVE=0
        shift
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        echo "[demo] unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done

  if ! [[ "$SEED" =~ ^[0-9]+$ ]] || (( SEED < 0 || SEED > 9 )); then
    echo "[demo] seed must be an integer from 0 to 9, got: $SEED" >&2
    exit 2
  fi
}

start_service() {
  local name="$1"
  shift
  local log_file="$CURRENT_LOG_DIR/${name}.log"
  echo "[demo] starting $name"
  (
    cd "$ROOT_DIR"
    conda run -n eai-swarm "$@"
  ) >"$log_file" 2>&1 &
  local pid=$!
  PIDS+=("$pid")
  echo "[demo] $name pid=$pid log=$log_file"
}

last_pid() {
  local index=$((${#PIDS[@]} - 1))
  echo "${PIDS[$index]}"
}

ensure_running() {
  local name="$1"
  local pid="$2"
  local log_file="$3"

  if ! kill -0 "$pid" >/dev/null 2>&1; then
    echo "[demo] $name exited unexpectedly. Recent log:" >&2
    sed -n '1,120p' "$log_file" >&2 || true
    exit 1
  fi
}

wait_for_tcp() {
  local host="$1"
  local port="$2"
  local label="$3"
  local timeout_sec="${4:-30}"
  local deadline=$((SECONDS + timeout_sec))

  until (echo >"/dev/tcp/$host/$port") >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "[demo] timed out waiting for $label: $host:$port" >&2
      exit 1
    fi
    sleep 1
  done
}

kill_tree() {
  local pid="$1"
  local children
  children="$(pgrep -P "$pid" || true)"
  for child in $children; do
    kill_tree "$child"
  done
  kill "$pid" >/dev/null 2>&1 || true
}

cleanup() {
  if (( CLEANED == 1 )); then
    return
  fi
  CLEANED=1

  local pid
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill_tree "$pid"
    fi
  done

  sleep 1
  for pid in "${PIDS[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done
}

on_signal() {
  cleanup
  exit 130
}

run_scene_phase() {
  local phase="$1"
  (
    cd "$ROOT_DIR"
    conda run -n eai-swarm --no-capture-output python -u - "$SEED" "$phase" <<'PY'
import json
import math
import sys
import time

from scripts.stage4_benchmark import C4Replanner, Scenario
from scripts.stage4_marl_safety_check import Stage4Monitor, command


def vector_distance(a, b) -> float:
    return math.dist([float(value) for value in a], [float(value) for value in b])


def vector_speed(value) -> float:
    if value is None:
        return 0.0
    return math.sqrt(sum(float(component) ** 2 for component in value))


def start_state_settled(snapshot, targets, position_tolerance_m=0.08, velocity_tolerance_mps=0.05) -> bool:
    for drone_id in (1, 2):
        telemetry = snapshot.get(drone_id)
        if telemetry is None:
            return False
        position = telemetry.get("position")
        if position is None:
            return False
        if vector_distance(position, targets[f"drone{drone_id}_start"]) > position_tolerance_m:
            return False
        if vector_speed(telemetry.get("velocity")) > velocity_tolerance_mps:
            return False
    return True


def wait_for_stable_start(monitor, scenario, targets, timeout, commands=(), stable_sec=0.8) -> None:
    deadline = time.time() + timeout
    stable_since = None
    next_publish = 0.0
    while time.time() < deadline:
        now = time.monotonic()
        if commands and now >= next_publish:
            for payload in commands:
                monitor.publish_command(payload)
            next_publish = now + 1.0
        snapshot = monitor.snapshot()
        settled = scenario.is_separated(snapshot) and start_state_settled(snapshot, targets)
        now = time.time()
        if settled:
            if stable_since is None:
                stable_since = now
            if now - stable_since >= stable_sec:
                print("[demo] initial benchmark start state is settled")
                return
        else:
            stable_since = None
        time.sleep(0.1)
    raise SystemExit("[demo] drones did not reach a settled start state")


seed = int(sys.argv[1])
phase = sys.argv[2]
scenario = Scenario("llm_timeout")
targets = scenario.targets_for_seed(seed)

print(f"[demo] scenario=llm_timeout seed={seed} injected_llm_delay={scenario.llm_delay_sec}s")

monitor = Stage4Monitor("127.0.0.1", 1883, qos=0)
monitor.start()
try:
    monitor.wait_for_telemetry(timeout=8)
    print("[demo] telemetry ready from Drone 1 and Drone 2")

    if phase == "prepare":
        print("[demo] benchmark targets:")
        print(json.dumps(targets, ensure_ascii=False, indent=2))
        start_commands = [
            command(1, targets["drone1_start"], "separate"),
            command(2, targets["drone2_start"], "separate"),
        ]
        print("[demo] start commands published (with periodic republish)")
        wait_for_stable_start(
            monitor,
            scenario,
            targets,
            timeout=scenario.separation_timeout + 6.0,
            commands=start_commands,
        )
        raise SystemExit(0)

    if phase != "cross":
        raise SystemExit(f"[demo] unsupported phase: {phase}")

    replanner = C4Replanner(scenario, targets)
    print(
        "[demo] C4 replanner ready: "
        f"provider={replanner.stats.llm_provider} model={replanner.stats.llm_model} "
        f"injected_delay={scenario.llm_delay_sec}s"
    )

    cross_started_at = time.time()
    goal_commands = [
        command(1, targets["drone1_goal"], "cross"),
        command(2, targets["drone2_goal"], "cross"),
    ]
    print("[demo] crossing goals published (with periodic republish)")

    swap_completed = False
    deadline = time.time() + scenario.cross_timeout + scenario.llm_delay_sec + 10.0
    next_publish = 0.0
    while time.time() < deadline:
        now = time.monotonic()
        if now >= next_publish:
            for payload in goal_commands:
                monitor.publish_command(payload)
            next_publish = now + 2.0
        replanner.process_new_overrides(monitor)
        if scenario.is_complete(monitor.snapshot()):
            swap_completed = True
            break
        time.sleep(0.1)

    duration = time.time() - cross_started_at
    stats = replanner.stats
    summary = monitor.summary(swap_completed=swap_completed)
    summary["scenario"] = scenario.name
    summary["seed"] = seed
    summary["crossing_duration_sec"] = round(duration, 4)
    summary["injected_llm_delay_sec"] = scenario.llm_delay_sec
    summary["llm_provider"] = stats.llm_provider
    summary["llm_model"] = stats.llm_model
    summary["llm_replan_attempts"] = stats.llm_replan_attempts
    summary["llm_replan_count"] = stats.llm_replan_count
    summary["llm_replan_error_count"] = stats.llm_replan_error_count
    summary["llm_avg_latency_ms"] = stats.avg_latency_ms
    summary["llm_replan_events"] = stats.llm_replan_events

    print("[demo] summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))

    if not swap_completed:
        raise SystemExit("[demo] drones did not complete the crossing")
    if summary["min_distance_m"] is not None and summary["min_distance_m"] < 0.25:
        raise SystemExit("[demo] collision detected during LLM outage")
    print("[demo] LLM outage survived: Safety Gate kept the drones collision-free")
finally:
    monitor.stop()
PY
  )
}

trap cleanup EXIT
trap on_signal INT TERM

parse_args "$@"
mkdir -p "$CURRENT_LOG_DIR"

echo "[demo] llm_timeout scenario demo (C4 + 4s injected LLM outage)"
echo "[demo] seed: $SEED"
echo "[demo] logs: $CURRENT_LOG_DIR"
echo "[demo] open unity/SwarmUnityDemo and press Play (Unity stays on default port 1883)"

start_service broker python scripts/dev_broker.py
broker_pid="$(last_pid)"
wait_for_tcp "127.0.0.1" "1883" "MQTT broker" 30
ensure_running "broker" "$broker_pid" "$CURRENT_LOG_DIR/broker.log"

start_service drone1 python mock_drone.py --drone-id 1
drone1_pid="$(last_pid)"
start_service drone2 python mock_drone.py --drone-id 2
drone2_pid="$(last_pid)"
sleep 2
ensure_running "drone1" "$drone1_pid" "$CURRENT_LOG_DIR/drone1.log"
ensure_running "drone2" "$drone2_pid" "$CURRENT_LOG_DIR/drone2.log"

run_scene_phase prepare

if ((INTERACTIVE == 1)); then
  echo "[demo] drones are settled at the start line. Press Enter to start Pilot/Safety Gate and inject crossing goals"
  read -r _
fi

start_service marl_pilot python marl_pilot.py
pilot_pid="$(last_pid)"
start_service safety_gate python safety_gate.py
gate_pid="$(last_pid)"
sleep 2
ensure_running "marl_pilot" "$pilot_pid" "$CURRENT_LOG_DIR/marl_pilot.log"
ensure_running "safety_gate" "$gate_pid" "$CURRENT_LOG_DIR/safety_gate.log"

run_scene_phase cross

echo "[demo] demo complete"
