from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

from swarm.safety import (
    DroneSnapshot,
    SafetyDecision,
    SafetyGate,
    build_override_event,
    build_safety_command,
)
from swarm.simulation import MockDroneState, parse_command


LOGGER = logging.getLogger("mock_drone")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def build_mqtt_client(client_id: str):
    try:
        import paho.mqtt.client as mqtt
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: paho-mqtt. Install it in your active conda env with "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def build_self_snapshot(state: MockDroneState, timestamp_ms: int) -> DroneSnapshot:
    return DroneSnapshot(
        drone_id=state.drone_id,
        position=state.current_pos,
        target=state.target_pos,
        velocity=state.velocity,
        speed_mps=state.speed_mps,
        status=state.status,
        last_command_id=state.last_command_id,
        timestamp_ms=timestamp_ms,
    )


def read_peer_snapshots(peer_trajectories: dict[int, str]) -> list[DroneSnapshot]:
    """Read the latest ground-truth record from each peer's local trajectory file.

    This simulates onboard local sensing: the data path never touches MQTT.
    """
    snapshots: list[DroneSnapshot] = []
    for path in peer_trajectories.values():
        try:
            last_line = None
            with open(path, encoding="utf-8") as file:
                for line in file:
                    line = line.strip()
                    if line:
                        last_line = line
        except OSError:
            continue
        if last_line is None:
            continue
        try:
            snapshots.append(DroneSnapshot.from_telemetry(json.loads(last_line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return snapshots


def onboard_gate_tick(
    state: MockDroneState,
    gate: SafetyGate,
    peer_trajectories: dict[int, str],
    timestamp_ms: int,
) -> SafetyDecision | None:
    """Evaluate the local safety gate and apply the safety command directly.

    Returns the override decision when the gate took over, else None.
    """
    snapshots = [build_self_snapshot(state, timestamp_ms)]
    snapshots.extend(read_peer_snapshots(peer_trajectories))

    for decision in gate.evaluate(snapshots):
        if decision.drone != state.drone_id:
            continue
        if decision.mode == "override":
            command_payload = build_safety_command(decision)
            state.apply_command(parse_command(json.dumps(command_payload)))
            return decision
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one MQTT-backed mock drone.")
    parser.add_argument("--drone-id", type=int, default=1)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--speed", type=float, default=1.0, help="Default speed in meters per second.")
    parser.add_argument("--max-accel", type=float, default=1.0, help="Maximum acceleration in m/s^2.")
    parser.add_argument("--max-yaw-rate", type=float, default=120.0, help="Maximum yaw rate in deg/s.")
    parser.add_argument("--min-altitude", type=float, default=0.0, help="Minimum simulated altitude in meters.")
    parser.add_argument("--max-altitude", type=float, default=5.0, help="Maximum simulated altitude in meters.")
    parser.add_argument("--interval", type=float, default=0.1, help="Telemetry/update interval in seconds.")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--log-trajectory",
        default=None,
        help="Optional path for a local JSONL ground-truth trajectory log (bypasses MQTT).",
    )
    parser.add_argument(
        "--onboard-gate",
        action="store_true",
        help="Run the safety gate locally inside the drone process (no MQTT in the safety loop).",
    )
    parser.add_argument(
        "--peer-trajectory",
        action="append",
        default=[],
        metavar="ID=PATH",
        help="Peer drone trajectory file used by the onboard gate as local sensing.",
    )
    parser.add_argument(
        "--start-position",
        default=None,
        metavar="X,Y,Z",
        help="Spawn position, e.g. --start-position -3,0,1 (default: shared origin).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.speed <= 0:
        raise SystemExit("--speed must be positive")
    if args.max_accel <= 0:
        raise SystemExit("--max-accel must be positive")
    if args.max_yaw_rate <= 0:
        raise SystemExit("--max-yaw-rate must be positive")
    if args.min_altitude > args.max_altitude:
        raise SystemExit("--min-altitude must be less than or equal to --max-altitude")
    if args.interval <= 0:
        raise SystemExit("--interval must be positive")

    start_position = (0.0, 0.0, 0.0)
    if args.start_position:
        try:
            start_position = tuple(float(value) for value in args.start_position.split(","))
        except ValueError:
            raise SystemExit(f"--start-position must be X,Y,Z numbers, got: {args.start_position}")
        if len(start_position) != 3:
            raise SystemExit(f"--start-position must have exactly 3 values, got: {args.start_position}")

    state = MockDroneState(
        drone_id=args.drone_id,
        current_pos=start_position,
        target_pos=start_position,
        speed_mps=args.speed,
        max_accel_mps2=args.max_accel,
        max_yaw_rate_dps=args.max_yaw_rate,
        min_altitude_m=args.min_altitude,
        max_altitude_m=args.max_altitude,
    )
    command_topic = f"swarm/drone/{args.drone_id}/command"
    telemetry_topic = f"swarm/drone/{args.drone_id}/telemetry"
    client = build_mqtt_client(client_id=f"mock-drone-{args.drone_id}")

    peer_trajectories: dict[int, str] = {}
    for entry in args.peer_trajectory:
        if "=" not in entry:
            raise SystemExit(f"--peer-trajectory must be ID=PATH, got: {entry}")
        peer_id_raw, peer_path = entry.split("=", 1)
        peer_id = int(peer_id_raw)
        if peer_id == args.drone_id:
            raise SystemExit("--peer-trajectory must not reference the drone itself")
        peer_trajectories[peer_id] = peer_path

    onboard_gate = SafetyGate() if args.onboard_gate else None
    runtime = {"onboard_override": False}

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        LOGGER.info("connected to MQTT broker %s:%s with result=%s", args.host, args.port, reason_code)
        client.subscribe(command_topic, qos=args.qos)
        LOGGER.info("subscribed command topic: %s", command_topic)

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        if runtime["onboard_override"]:
            try:
                raw = json.loads(message.payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            if raw.get("priority") != "safety":
                return
        try:
            command = parse_command(message.payload)
            state.apply_command(command)
            LOGGER.info("accepted command: action=%s target=%s", command.action, command.target)
        except ValueError as exc:
            state.last_error = str(exc)
            LOGGER.warning("rejected command on %s: %s", message.topic, exc)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    LOGGER.info("publishing telemetry topic: %s", telemetry_topic)
    last_tick = time.monotonic()

    trajectory_file = None
    if args.log_trajectory:
        trajectory_file = open(args.log_trajectory, "w", encoding="utf-8")
        LOGGER.info("logging ground-truth trajectory to %s", args.log_trajectory)

    try:
        while True:
            now = time.monotonic()
            dt_sec = now - last_tick
            last_tick = now

            state.update(dt_sec)
            if onboard_gate is not None:
                decision = onboard_gate_tick(state, onboard_gate, peer_trajectories, now_ms())
                was_overridden = runtime["onboard_override"]
                runtime["onboard_override"] = decision is not None
                if decision is not None and not was_overridden:
                    event = build_override_event(decision)
                    client.publish(
                        "swarm/commander/override",
                        payload=json.dumps(event, separators=(",", ":")),
                        qos=args.qos,
                    )
                    LOGGER.warning(
                        "onboard override: risk=%.3f target=%s",
                        decision.risk_level,
                        decision.target_drone,
                    )
            payload = json.dumps(state.telemetry(now_ms()), separators=(",", ":"))
            client.publish(telemetry_topic, payload=payload, qos=args.qos, retain=False)
            if trajectory_file is not None:
                trajectory_file.write(payload + "\n")
                trajectory_file.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        LOGGER.info("stopping mock drone")
    finally:
        if trajectory_file is not None:
            trajectory_file.close()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
