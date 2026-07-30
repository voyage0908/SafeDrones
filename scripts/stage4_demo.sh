#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs/stage4_demo"
mkdir -p "$LOG_DIR"

PIDS=()
CLEANED=0

start_service() {
  local name="$1"
  shift
  local log_file="$LOG_DIR/${name}.log"
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

wait_for_http() {
  local url="$1"
  local label="$2"
  local timeout_sec="${3:-30}"
  local deadline=$((SECONDS + timeout_sec))

  until curl -fsS "$url" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "[demo] timed out waiting for $label: $url" >&2
      exit 1
    fi
    sleep 1
  done
}

wait_for_telemetry() {
  local timeout_sec="${1:-15}"
  echo "[demo] waiting for telemetry from Drone 1 and Drone 2"
  (
    cd "$ROOT_DIR"
    conda run -n eai-swarm python -c '
import json
import sys
import threading
import time

import paho.mqtt.client as mqtt

timeout = float(sys.argv[1])
seen = set()
connected = threading.Event()
done = threading.Event()

def on_connect(client, userdata, flags, reason_code, properties=None):
    client.subscribe("swarm/drone/+/telemetry")
    connected.set()

def on_message(client, userdata, message):
    try:
        payload = json.loads(message.payload.decode("utf-8"))
        drone_id = int(payload.get("drone", 0))
    except Exception:
        return
    if drone_id in {1, 2}:
        seen.add(drone_id)
    if seen == {1, 2}:
        done.set()

try:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="stage4-demo-telemetry-check")
except AttributeError:
    client = mqtt.Client(client_id="stage4-demo-telemetry-check")

client.on_connect = on_connect
client.on_message = on_message
client.connect("127.0.0.1", 1883, keepalive=30)
client.loop_start()
try:
    if not connected.wait(timeout=5):
        raise SystemExit("failed to connect to MQTT broker")
    if not done.wait(timeout=timeout):
        raise SystemExit(f"timed out waiting for telemetry; seen={sorted(seen)}")
finally:
    client.loop_stop()
    client.disconnect()
' "$timeout_sec"
  )
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

echo "[demo] stage 4 demo starting from $ROOT_DIR"
echo "[demo] logs will be written to $LOG_DIR"

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
wait_for_telemetry 15

start_service gateway uvicorn gateway:app --host 127.0.0.1 --port 8000

wait_for_http "http://127.0.0.1:8000/api/health" "gateway health" 30
echo "[demo] gateway is ready"

echo "[demo] sending initial separation commands"
curl -fsS -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[-3,0,1]}' >/dev/null
curl -fsS -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[3,0,1]}' >/dev/null

echo "[demo] wait a few seconds for the drones to separate"
sleep 6

echo "[demo] open unity/SwarmUnityDemo in Unity Hub, press Play, then press Enter here"
read -r _

start_service safety_gate python safety_gate.py
echo "[demo] waiting for safety gate to connect"
sleep 2

echo "[demo] sending cross-flight commands"
curl -fsS -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[3,0,1]}' >/dev/null
curl -fsS -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":2,"waypoint":[-3,0,1]}' >/dev/null

echo "[demo] waiting for safety status and override events"
sleep 8

echo "[demo] recent events"
curl -fsS "http://127.0.0.1:8000/api/events?limit=20" | sed -n '1,120p'

echo "[demo] demo complete"
echo "[demo] press Enter to stop all services and clean up"
read -r _
