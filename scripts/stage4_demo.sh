#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOG_ROOT="$ROOT_DIR/logs/stage4_demo/$RUN_ID"

SCENARIO="all"
SEED=0
INTERACTIVE=1
SCENARIOS=(head_on_crossing perpendicular_crossing diagonal_crossing)
PIDS=()
CLEANED=0
CURRENT_LOG_DIR=""

usage() {
  cat <<'EOF'
Usage:
  bash scripts/stage4_demo.sh [--scenario all|head_on_crossing|perpendicular_crossing|diagonal_crossing] [--seed 0-9] [--non-interactive]

This script replays the three C3 benchmark scenes used in the 10-seed stage-four tests.
Coordinates come directly from Scenario.targets_for_seed(seed) in scripts/stage4_benchmark.py.

Examples:
  bash scripts/stage4_demo.sh
  bash scripts/stage4_demo.sh --seed 3
  bash scripts/stage4_demo.sh --scenario head_on_crossing --seed 0
EOF
}

validate_scenario() {
  case "$1" in
    all | head_on_crossing | perpendicular_crossing | diagonal_crossing)
      ;;
    *)
      echo "[demo] unsupported scenario: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
}

validate_seed() {
  if ! [[ "$1" =~ ^[0-9]+$ ]] || (( "$1" < 0 || "$1" > 9 )); then
    echo "[demo] seed must be an integer from 0 to 9, got: $1" >&2
    exit 2
  fi
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

  validate_scenario "$SCENARIO"
  validate_seed "$SEED"

  if [[ "$SCENARIO" != "all" ]]; then
    SCENARIOS=("$SCENARIO")
  fi
}

scenario_label() {
  case "$1" in
    head_on_crossing)
      echo "正面对冲交叉"
      ;;
    perpendicular_crossing)
      echo "垂直航线交叉"
      ;;
    diagonal_crossing)
      echo "对角航线交叉"
      ;;
    *)
      echo "$1"
      ;;
  esac
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

reset_stack_state() {
  PIDS=()
  CLEANED=0
}

start_stack() {
  local scenario="$1"
  local drone_args=()
  local pilot_args=()

  CURRENT_LOG_DIR="$LOG_ROOT/${scenario}_seed${SEED}"
  mkdir -p "$CURRENT_LOG_DIR"
  reset_stack_state

  if [[ "$scenario" == "perpendicular_crossing" || "$scenario" == "diagonal_crossing" ]]; then
    drone_args=(--speed 1.6 --max-accel 2.0)
    pilot_args=(--rule-safe-distance 0.01 --repulsion-gain 0.0 --max-speed 2.0 --horizon-sec 0.5)
  fi

  start_service broker python scripts/dev_broker.py
  local broker_pid
  broker_pid="$(last_pid)"
  wait_for_tcp "127.0.0.1" "1883" "MQTT broker" 30
  ensure_running "broker" "$broker_pid" "$CURRENT_LOG_DIR/broker.log"

  start_service drone1 python mock_drone.py --drone-id 1 "${drone_args[@]}"
  local drone1_pid
  drone1_pid="$(last_pid)"
  start_service drone2 python mock_drone.py --drone-id 2 "${drone_args[@]}"
  local drone2_pid
  drone2_pid="$(last_pid)"
  sleep 2
  ensure_running "drone1" "$drone1_pid" "$CURRENT_LOG_DIR/drone1.log"
  ensure_running "drone2" "$drone2_pid" "$CURRENT_LOG_DIR/drone2.log"

  start_service marl_pilot python marl_pilot.py "${pilot_args[@]}"
  local pilot_pid
  pilot_pid="$(last_pid)"
  start_service safety_gate python safety_gate.py
  local safety_pid
  safety_pid="$(last_pid)"
  sleep 2
  ensure_running "marl_pilot" "$pilot_pid" "$CURRENT_LOG_DIR/marl_pilot.log"
  ensure_running "safety_gate" "$safety_pid" "$CURRENT_LOG_DIR/safety_gate.log"
}

run_scene() {
  local scenario="$1"
  (
    cd "$ROOT_DIR"
    conda run -n eai-swarm --no-capture-output python -u - "$scenario" "$SEED" "$INTERACTIVE" <<'PY'
import json
import sys
import time

from scripts.stage4_benchmark import Scenario
from scripts.stage4_marl_safety_check import Stage4Monitor, command


def wait_for_enter(message: str) -> None:
    print(message, end="", flush=True)
    try:
        with open("/dev/tty", "r", encoding="utf-8") as tty:
            tty.readline()
    except OSError:
        print("\n[demo] no interactive terminal attached; continuing")


scenario = Scenario(sys.argv[1])
seed = int(sys.argv[2])
interactive = sys.argv[3] == "1"
targets = scenario.targets_for_seed(seed)

print(f"[demo] replay scenario={scenario.name} seed={seed}")
print("[demo] benchmark targets:")
print(json.dumps(targets, ensure_ascii=False, indent=2))

monitor = Stage4Monitor("127.0.0.1", 1883, qos=0)
monitor.start()
try:
    monitor.wait_for_telemetry(timeout=8)
    print("[demo] telemetry ready from Drone 1 and Drone 2")

    monitor.publish_command(command(1, targets["drone1_start"], "separate"))
    monitor.publish_command(command(2, targets["drone2_start"], "separate"))
    print("[demo] start commands published")

    separated = monitor.wait_until(
        lambda: scenario.is_separated(monitor.snapshot()),
        timeout=scenario.separation_timeout,
        label=f"{scenario.name} seed={seed} initial separation",
    )
    if not separated:
        raise SystemExit("[demo] drones did not reach initial separation")

    print("[demo] initial separation reached")
    if interactive:
        wait_for_enter("[demo] press Enter to inject benchmark crossing goals")

    cross_started_at = time.time()
    monitor.publish_command(command(1, targets["drone1_goal"], "cross"))
    monitor.publish_command(command(2, targets["drone2_goal"], "cross"))
    print("[demo] crossing goals published")

    swap_completed = monitor.wait_until(
        lambda: scenario.is_complete(monitor.snapshot()),
        timeout=scenario.cross_timeout,
        label=f"{scenario.name} seed={seed} position swap",
    )
    duration = time.time() - cross_started_at
    summary = monitor.summary(swap_completed=swap_completed)
    summary["scenario"] = scenario.name
    summary["seed"] = seed
    summary["crossing_duration_sec"] = round(duration, 4)
    summary["recent_override_events"] = monitor.commander_events()[-5:]

    print("[demo] summary:")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))

    if not swap_completed:
        raise SystemExit("[demo] drones did not complete the benchmark crossing")
finally:
    monitor.stop()
PY
  )
}

on_signal() {
  cleanup
  exit 130
}

trap cleanup EXIT
trap on_signal INT TERM

parse_args "$@"
mkdir -p "$LOG_ROOT"

echo "[demo] stage 4 benchmark-scene demo"
echo "[demo] scenarios: ${SCENARIOS[*]}"
echo "[demo] seed: $SEED"
echo "[demo] logs: $LOG_ROOT"
echo "[demo] open unity/SwarmUnityDemo and press Play before injecting goals"

for index in "${!SCENARIOS[@]}"; do
  scenario="${SCENARIOS[$index]}"
  label="$(scenario_label "$scenario")"
  echo "[demo] ================================================"
  echo "[demo] scenario $((index + 1))/${#SCENARIOS[@]}: $label ($scenario)"

  start_stack "$scenario"
  run_scene "$scenario"
  cleanup
  reset_stack_state

  if ((INTERACTIVE == 1 && index + 1 < ${#SCENARIOS[@]})); then
    echo "[demo] press Enter to start the next benchmark scenario"
    read -r _
  fi
done

echo "[demo] demo complete"
