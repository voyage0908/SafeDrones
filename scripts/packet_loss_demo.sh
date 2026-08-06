#!/usr/bin/env bash
# packet_loss 场景 demo：MQTT PUBLISH 随机丢弃（默认 20%），
# 在 Unity 中观察 Pilot + Safety Gate 在不可靠通信下的表现。
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOG_ROOT="$ROOT_DIR/logs/packet_loss_demo/$RUN_ID"

SCENARIO="head_on_crossing"
SEED=0
DROP_RATE=0.2
INTERACTIVE=1
PROXY_PORT=1884
GATE_MODE="network"
PIDS=()
CLEANED=0
CURRENT_LOG_DIR=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/packet_loss_demo.sh [--scenario head_on_crossing|perpendicular_crossing|diagonal_crossing|llm_timeout]
                                   [--seed 0-9] [--drop-rate 0.2] [--gate-mode network|onboard] [--non-interactive]

All business processes (drones / Pilot / Safety Gate / monitor) connect through
scripts/mqtt_lossy_proxy.py on port 1884, which randomly drops MQTT PUBLISH
packets in both directions. The broker itself stays on 1883.

Watch in Unity:
  - default Unity config (Broker Port 1883) shows the lossless broker-side view;
  - to experience the same loss as the drones, set the SwarmTelemetryManager
    Broker Port to 1884 in the Inspector before pressing Play;
  - the crossing should still complete without collision (C3 condition).

Examples:
  bash scripts/packet_loss_demo.sh
  bash scripts/packet_loss_demo.sh --scenario perpendicular_crossing --seed 3 --drop-rate 0.3
EOF
}

parse_args() {
  while (($# > 0)); do
    case "$1" in
      --scenario)
        if (($# < 2)); then
          echo "[demo] --scenario requires a value" >&2
          exit 2
        fi
        SCENARIO="$2"
        shift 2
        ;;
      --seed)
        if (($# < 2)); then
          echo "[demo] --seed requires a value" >&2
          exit 2
        fi
        SEED="$2"
        shift 2
        ;;
      --drop-rate)
        if (($# < 2)); then
          echo "[demo] --drop-rate requires a value" >&2
          exit 2
        fi
        DROP_RATE="$2"
        shift 2
        ;;
      --gate-mode)
        if (($# < 2)); then
          echo "[demo] --gate-mode requires a value" >&2
          exit 2
        fi
        GATE_MODE="$2"
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

  case "$SCENARIO" in
    head_on_crossing | perpendicular_crossing | diagonal_crossing | llm_timeout)
      ;;
    *)
      echo "[demo] unsupported scenario: $SCENARIO" >&2
      usage >&2
      exit 2
      ;;
  esac

  if ! [[ "$SEED" =~ ^[0-9]+$ ]] || (( SEED < 0 || SEED > 9 )); then
    echo "[demo] seed must be an integer from 0 to 9, got: $SEED" >&2
    exit 2
  fi

  case "$GATE_MODE" in
    network | onboard)
      ;;
    *)
      echo "[demo] unsupported gate mode: $GATE_MODE (expected network|onboard)" >&2
      exit 2
      ;;
  esac
}

scenario_runtime_args() {
  local kind="$1"

  if [[ "$SCENARIO" == "perpendicular_crossing" || "$SCENARIO" == "diagonal_crossing" ]]; then
    if [[ "$kind" == "drone" ]]; then
      echo "--speed 1.6 --max-accel 2.0"
      return
    fi
    echo "--rule-safe-distance 0.01 --repulsion-gain 0.0 --max-speed 2.0 --horizon-sec 0.5"
    return
  fi

  echo ""
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
    conda run -n eai-swarm --no-capture-output python -u - "$SCENARIO" "$SEED" "$phase" "$PROXY_PORT" "$CURRENT_LOG_DIR" <<'PY'
import json
import math
import os
import sys
import time

from scripts.stage4_benchmark import Scenario
from scripts.stage4_marl_safety_check import Stage4Monitor, command
from scripts.trajectory_analysis import true_min_distance


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


scenario = Scenario(sys.argv[1])
seed = int(sys.argv[2])
phase = sys.argv[3]
port = int(sys.argv[4])
log_dir = sys.argv[5]
targets = scenario.targets_for_seed(seed)

print(f"[demo] scenario={scenario.name} seed={seed} mqtt_port={port} (lossy proxy)")

monitor = Stage4Monitor("127.0.0.1", port, qos=0)
monitor.start()
try:
    monitor.wait_for_telemetry(timeout=12)
    print("[demo] telemetry ready from Drone 1 and Drone 2 (through lossy proxy)")

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
            timeout=scenario.separation_timeout + 10.0,
            commands=start_commands,
        )
        raise SystemExit(0)

    if phase != "cross":
        raise SystemExit(f"[demo] unsupported phase: {phase}")

    cross_started_at = time.time()
    cross_started_ms = int(cross_started_at * 1000)
    goal_commands = [
        command(1, targets["drone1_goal"], "cross"),
        command(2, targets["drone2_goal"], "cross"),
    ]
    print("[demo] crossing goals published (with periodic republish)")

    swap_completed = False
    deadline = time.time() + scenario.cross_timeout + 10.0
    next_publish = 0.0
    while time.time() < deadline:
        now = time.monotonic()
        if now >= next_publish:
            for payload in goal_commands:
                monitor.publish_command(payload)
            next_publish = now + 2.0
        if scenario.is_complete(monitor.snapshot()):
            swap_completed = True
            break
        time.sleep(0.1)
    duration = time.time() - cross_started_at
    true_min = true_min_distance(
        [
            os.path.join(log_dir, "drone1_trajectory.jsonl"),
            os.path.join(log_dir, "drone2_trajectory.jsonl"),
        ],
        start_ms=cross_started_ms,
    )
    summary = monitor.summary(swap_completed=swap_completed)
    summary["scenario"] = scenario.name
    summary["seed"] = seed
    summary["crossing_duration_sec"] = round(duration, 4)
    summary["mqtt_observed_min_distance_m"] = summary["min_distance_m"]
    summary["true_min_distance_m"] = None if true_min is None else round(true_min, 4)

    print("[demo] summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))

    if not swap_completed:
        raise SystemExit("[demo] drones did not complete the crossing under packet loss")
    collision_reference = summary["true_min_distance_m"]
    if collision_reference is None:
        collision_reference = summary["min_distance_m"]
    if collision_reference is not None and collision_reference < 0.25:
        raise SystemExit("[demo] collision detected under packet loss (ground truth)")
    print(
        "[demo] packet loss survived: crossing completed without collision "
        f"(true min distance: {collision_reference}m)"
    )
finally:
    monitor.stop()
PY
  )
}

trap cleanup EXIT
trap on_signal INT TERM

parse_args "$@"

CURRENT_LOG_DIR="$LOG_ROOT/${SCENARIO}_seed${SEED}_drop${DROP_RATE}"
mkdir -p "$CURRENT_LOG_DIR"

echo "[demo] packet_loss demo (C3, drop_rate=$DROP_RATE via proxy on port $PROXY_PORT)"
echo "[demo] scenario: $SCENARIO  seed: $SEED"
echo "[demo] logs: $CURRENT_LOG_DIR"
echo "[demo] Unity hint: Broker Port 1883 = lossless view; 1884 = same lossy feed as the drones"

start_service broker python scripts/dev_broker.py
broker_pid="$(last_pid)"
wait_for_tcp "127.0.0.1" "1883" "MQTT broker" 30
ensure_running "broker" "$broker_pid" "$CURRENT_LOG_DIR/broker.log"

start_service mqtt_proxy python scripts/mqtt_lossy_proxy.py --listen-port "$PROXY_PORT" --target-port 1883 --drop-rate "$DROP_RATE" --seed "$SEED"
proxy_pid="$(last_pid)"
wait_for_tcp "127.0.0.1" "$PROXY_PORT" "lossy MQTT proxy" 30
ensure_running "mqtt_proxy" "$proxy_pid" "$CURRENT_LOG_DIR/mqtt_proxy.log"

DRONE_ARGS=()
read -r -a DRONE_ARGS <<<"$(scenario_runtime_args drone)"
PILOT_ARGS=()
read -r -a PILOT_ARGS <<<"$(scenario_runtime_args pilot)"

DRONE1_EXTRA=()
DRONE2_EXTRA=()
if [[ "$GATE_MODE" == "onboard" ]]; then
  DRONE1_EXTRA=(--onboard-gate --peer-trajectory "2=$CURRENT_LOG_DIR/drone2_trajectory.jsonl")
  DRONE2_EXTRA=(--onboard-gate --peer-trajectory "1=$CURRENT_LOG_DIR/drone1_trajectory.jsonl")
fi

start_service drone1 python mock_drone.py --drone-id 1 --port "$PROXY_PORT" --log-trajectory "$CURRENT_LOG_DIR/drone1_trajectory.jsonl" "${DRONE_ARGS[@]}" "${DRONE1_EXTRA[@]}"
drone1_pid="$(last_pid)"
start_service drone2 python mock_drone.py --drone-id 2 --port "$PROXY_PORT" --log-trajectory "$CURRENT_LOG_DIR/drone2_trajectory.jsonl" "${DRONE_ARGS[@]}" "${DRONE2_EXTRA[@]}"
drone2_pid="$(last_pid)"
sleep 2
ensure_running "drone1" "$drone1_pid" "$CURRENT_LOG_DIR/drone1.log"
ensure_running "drone2" "$drone2_pid" "$CURRENT_LOG_DIR/drone2.log"

run_scene_phase prepare

if ((INTERACTIVE == 1)); then
  echo "[demo] drones are settled at the start line. Press Enter to start Pilot/Safety Gate and inject crossing goals"
  read -r _
fi

start_service marl_pilot python marl_pilot.py --port "$PROXY_PORT" "${PILOT_ARGS[@]}"
pilot_pid="$(last_pid)"
if [[ "$GATE_MODE" == "network" ]]; then
  start_service safety_gate python safety_gate.py --port "$PROXY_PORT"
  gate_pid="$(last_pid)"
fi
sleep 2
ensure_running "marl_pilot" "$pilot_pid" "$CURRENT_LOG_DIR/marl_pilot.log"
if [[ "$GATE_MODE" == "network" ]]; then
  ensure_running "safety_gate" "$gate_pid" "$CURRENT_LOG_DIR/safety_gate.log"
else
  echo "[demo] onboard gate mode: safety runs inside each drone process, no network safety_gate"
fi

run_scene_phase cross

echo "[demo] demo complete"
