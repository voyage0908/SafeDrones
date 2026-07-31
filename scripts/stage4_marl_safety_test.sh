#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs/stage4_marl_safety_test"
mkdir -p "$LOG_DIR"

PIDS=()
CLEANED=0

start_service() {
  local name="$1"
  shift
  local log_file="$LOG_DIR/${name}.log"
  echo "[stage4-test] starting $name"
  (
    cd "$ROOT_DIR"
    conda run -n eai-swarm "$@"
  ) >"$log_file" 2>&1 &
  local pid=$!
  PIDS+=("$pid")
  echo "[stage4-test] $name pid=$pid log=$log_file"
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
    echo "[stage4-test] $name exited unexpectedly. Recent log:" >&2
    sed -n '1,160p' "$log_file" >&2 || true
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
      echo "[stage4-test] timed out waiting for $label: $host:$port" >&2
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

trap cleanup EXIT
trap on_signal INT TERM

if ss -ltnp | rg '(:1883|:8000)' >/dev/null 2>&1; then
  echo "[stage4-test] ports 1883 or 8000 are already in use; stop existing demo services first" >&2
  ss -ltnp | rg '(:1883|:8000)' >&2 || true
  exit 1
fi

echo "[stage4-test] logs will be written to $LOG_DIR"

start_service broker python scripts/dev_broker.py
BROKER_PID="$(last_pid)"
wait_for_tcp "127.0.0.1" "1883" "MQTT broker" 30
ensure_running "broker" "$BROKER_PID" "$LOG_DIR/broker.log"

start_service drone1 python mock_drone.py --drone-id 1
DRONE1_PID="$(last_pid)"
start_service drone2 python mock_drone.py --drone-id 2
DRONE2_PID="$(last_pid)"
sleep 2
ensure_running "drone1" "$DRONE1_PID" "$LOG_DIR/drone1.log"
ensure_running "drone2" "$DRONE2_PID" "$LOG_DIR/drone2.log"

start_service marl_pilot python marl_pilot.py
MARL_PID="$(last_pid)"
sleep 2
ensure_running "marl_pilot" "$MARL_PID" "$LOG_DIR/marl_pilot.log"

start_service safety_gate python safety_gate.py
SAFETY_PID="$(last_pid)"
sleep 2
ensure_running "safety_gate" "$SAFETY_PID" "$LOG_DIR/safety_gate.log"

(
  cd "$ROOT_DIR"
  conda run -n eai-swarm python scripts/stage4_marl_safety_check.py "$@"
)
