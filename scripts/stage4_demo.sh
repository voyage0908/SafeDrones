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

scenario_runtime_args() {
  local scenario="$1"
  local kind="$2"

  if [[ "$scenario" == "perpendicular_crossing" || "$scenario" == "diagonal_crossing" ]]; then
    if [[ "$kind" == "drone" ]]; then
      echo "--speed 1.6 --max-accel 2.0"
      return
    fi
    echo "--rule-safe-distance 0.01 --repulsion-gain 0.0 --max-speed 2.0 --horizon-sec 0.5"
    return
  fi

  echo ""
}

start_base_stack() {
  local scenario="$1"
  local drone_args=()

  CURRENT_LOG_DIR="$LOG_ROOT/${scenario}_seed${SEED}"
  mkdir -p "$CURRENT_LOG_DIR"
  reset_stack_state

  read -r -a drone_args <<<"$(scenario_runtime_args "$scenario" drone)"

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
}

start_control_stack() {
  local scenario="$1"
  local pilot_args=()

  read -r -a pilot_args <<<"$(scenario_runtime_args "$scenario" pilot)"

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

run_scene_phase() {
  local scenario="$1"
  local phase="$2"
  (
    cd "$ROOT_DIR"
    conda run -n eai-swarm --no-capture-output python -u - "$scenario" "$SEED" "$phase" <<'PY'
import json
import math
import sys
import time

from scripts.stage4_benchmark import Scenario
from scripts.stage4_marl_safety_check import Stage4Monitor, command


def vector_distance(a: list[float], b: list[float]) -> float:
    return math.dist([float(value) for value in a], [float(value) for value in b])


def vector_speed(value: list[float] | tuple[float, float, float] | None) -> float:
    if value is None:
        return 0.0
    return math.sqrt(sum(float(component) ** 2 for component in value))


def start_state_settled(
    snapshot: dict[int, dict],
    targets: dict[str, list[float]],
    position_tolerance_m: float = 0.08,
    velocity_tolerance_mps: float = 0.05,
) -> bool:
    for drone_id in (1, 2):
        telemetry = snapshot.get(drone_id)
        if telemetry is None:
            return False

        position = telemetry.get("position")
        if position is None:
            return False

        target = targets[f"drone{drone_id}_start"]
        if vector_distance(position, target) > position_tolerance_m:
            return False

        if vector_speed(telemetry.get("velocity")) > velocity_tolerance_mps:
            return False

    return True


def wait_for_stable_start(
    monitor: Stage4Monitor,
    scenario: Scenario,
    targets: dict[str, list[float]],
    timeout: float,
    stable_sec: float = 0.8,
) -> None:
    deadline = time.time() + timeout
    stable_since = None
    last_snapshot = {}
    while time.time() < deadline:
        snapshot = monitor.snapshot()
        last_snapshot = snapshot
        separated = scenario.is_separated(snapshot)
        settled = separated and start_state_settled(snapshot, targets)
        now = time.time()

        if settled:
            if stable_since is None:
                stable_since = now
            if now - stable_since >= stable_sec:
                positions = {
                    str(drone_id): payload.get("position")
                    for drone_id, payload in sorted(snapshot.items())
                }
                velocities = {
                    str(drone_id): payload.get("velocity")
                    for drone_id, payload in sorted(snapshot.items())
                }
                print("[demo] initial benchmark start state is settled")
                print(json.dumps({"positions": positions, "velocities": velocities}, ensure_ascii=False, indent=2))
                return
        else:
            stable_since = None

        time.sleep(0.1)

    positions = {
        str(drone_id): payload.get("position")
        for drone_id, payload in sorted(last_snapshot.items())
    }
    velocities = {
        str(drone_id): payload.get("velocity")
        for drone_id, payload in sorted(last_snapshot.items())
    }
    raise SystemExit(
        "[demo] drones reached neither a settled benchmark start state nor low velocity; "
        f"positions={positions} velocities={velocities}"
    )


scenario = Scenario(sys.argv[1])
seed = int(sys.argv[2])
phase = sys.argv[3]
targets = scenario.targets_for_seed(seed)

print(f"[demo] replay scenario={scenario.name} seed={seed}")
if phase == "prepare":
    print("[demo] benchmark targets:")
    print(json.dumps(targets, ensure_ascii=False, indent=2))

monitor = Stage4Monitor("127.0.0.1", 1883, qos=0)
monitor.start()
try:
    monitor.wait_for_telemetry(timeout=8)
    print("[demo] telemetry ready from Drone 1 and Drone 2")

    if phase == "prepare":
        monitor.publish_command(command(1, targets["drone1_start"], "separate"))
        monitor.publish_command(command(2, targets["drone2_start"], "separate"))
        print("[demo] start commands published")

        wait_for_stable_start(
            monitor,
            scenario,
            targets,
            timeout=scenario.separation_timeout + 6.0,
        )
        raise SystemExit(0)

    if phase != "cross":
        raise SystemExit(f"[demo] unsupported phase: {phase}")

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

  start_base_stack "$scenario"
  run_scene_phase "$scenario" prepare

  if ((INTERACTIVE == 1)); then
    echo "[demo] benchmark start state is static. Press Enter to start Pilot/Safety Gate and inject crossing goals"
    read -r _
  fi

  start_control_stack "$scenario"
  run_scene_phase "$scenario" cross
  cleanup
  reset_stack_state

  if ((INTERACTIVE == 1 && index + 1 < ${#SCENARIOS[@]})); then
    echo "[demo] press Enter to start the next benchmark scenario"
    read -r _
  fi
done

echo "[demo] demo complete"
