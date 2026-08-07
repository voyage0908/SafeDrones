from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

from swarm.marl import (
    MarlActionConfig,
    MarlObservationBuilder,
    MarlObservationConfig,
    OnnxMarlPilot,
    RuleMarlPilot,
    action_to_waypoint,
)
from swarm.safety import DroneSnapshot
from swarm.simulation import Vector3, norm, subtract


LOGGER = logging.getLogger("marl_pilot")


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


def as_vector3(value: Any, field_name: str) -> Vector3:
    if not isinstance(value, list | tuple) or len(value) != 3:
        raise ValueError(f"{field_name} must be a list of three numbers")
    return (float(value[0]), float(value[1]), float(value[2]))


def target_from_command(payload: dict[str, Any]) -> Vector3 | None:
    priority = str(payload.get("priority") or "normal")
    if priority in {"marl", "safety"}:
        return None

    target_value = payload.get("target", payload.get("waypoint"))
    if target_value is None:
        return None
    return as_vector3(target_value, "target")


def build_micro_waypoint_command(drone_id: int, waypoint: Vector3, timestamp_ms: int) -> dict[str, Any]:
    return {
        "drone": drone_id,
        "action": "move_to",
        "target": list(waypoint),
        "waypoint": list(waypoint),
        "priority": "marl",
        "confidence": 1.0,
        "ttl_sec": 0.5,
        "command_id": f"marl-step-{timestamp_ms}-{drone_id}",
        "rationale": "short-horizon MARL pilot setpoint",
        "timestamp_ms": timestamp_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the low-level MARL Pilot MQTT bridge.")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--onnx-model", default=None)
    parser.add_argument("--max-neighbors", type=int, default=3)
    parser.add_argument("--max-speed", type=float, default=1.0)
    parser.add_argument("--horizon-sec", type=float, default=0.4)
    parser.add_argument("--rule-safe-distance", type=float, default=1.6)
    parser.add_argument("--repulsion-gain", type=float, default=1.2)
    parser.add_argument("--override-hold-sec", type=float, default=1.5)
    parser.add_argument(
        "--drone-ids",
        type=int,
        nargs="*",
        default=None,
        help="只控制这些 drone id，默认空=控制全部",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--telemetry-topic",
        default="swarm/drone/+/telemetry",
        help="Telemetry topic to consume; point at swarm/ego/drone/+/telemetry for Ego mode.",
    )
    args = parser.parse_args()
    pilot_drone_ids: set[int] | None = set(args.drone_ids) if args.drone_ids else None

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.interval <= 0:
        raise SystemExit("--interval must be positive")
    if args.max_speed <= 0:
        raise SystemExit("--max-speed must be positive")
    if args.horizon_sec <= 0:
        raise SystemExit("--horizon-sec must be positive")
    if args.rule_safe_distance <= 0:
        raise SystemExit("--rule-safe-distance must be positive")
    if args.repulsion_gain < 0:
        raise SystemExit("--repulsion-gain must be non-negative")

    observation_config = MarlObservationConfig(max_neighbors=args.max_neighbors)
    observation_builder = MarlObservationBuilder(observation_config)
    action_config = MarlActionConfig(max_speed_mps=args.max_speed, horizon_sec=args.horizon_sec)
    if args.onnx_model:
        pilot: Any = OnnxMarlPilot(args.onnx_model, observation_config=observation_config)
        LOGGER.info("loaded ONNX MARL policy: %s", args.onnx_model)
    else:
        pilot = RuleMarlPilot(
            action_config=action_config,
            safe_distance_m=args.rule_safe_distance,
            repulsion_gain=args.repulsion_gain,
        )
        LOGGER.info("using rule fallback pilot; pass --onnx-model to load a trained policy")

    snapshots: dict[int, DroneSnapshot] = {}
    high_level_targets: dict[int, Vector3] = {}
    override_until: dict[int, float] = {}
    client = build_mqtt_client(client_id="marl-pilot")

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        LOGGER.info("connected to MQTT broker %s:%s with result=%s", args.host, args.port, reason_code)
        client.subscribe(args.telemetry_topic, qos=args.qos)
        client.subscribe("swarm/drone/+/command", qos=args.qos)
        client.subscribe("swarm/commander/status", qos=args.qos)
        client.subscribe("swarm/commander/override", qos=args.qos)

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOGGER.warning("ignored invalid JSON on %s: %s", message.topic, exc)
            return

        if message.topic.endswith("/telemetry"):
            try:
                snapshot = DroneSnapshot.from_telemetry(payload)
            except (KeyError, TypeError, ValueError) as exc:
                LOGGER.warning("ignored invalid telemetry on %s: %s", message.topic, exc)
                return
            snapshots[snapshot.drone_id] = snapshot
            if snapshot.target is not None and snapshot.drone_id not in high_level_targets:
                high_level_targets[snapshot.drone_id] = snapshot.target
            return

        if message.topic.endswith("/command"):
            try:
                target = target_from_command(payload)
            except (TypeError, ValueError) as exc:
                LOGGER.warning("ignored invalid command target on %s: %s", message.topic, exc)
                return
            if target is not None:
                drone_id = int(payload["drone"])
                high_level_targets[drone_id] = target
            return

        drone_id = int(payload.get("drone") or 0)
        mode = str(payload.get("mode") or payload.get("status") or payload.get("event_name") or "")
        if drone_id > 0 and mode in {"override", "safety_override"}:
            override_until[drone_id] = time.monotonic() + args.override_hold_sec

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    try:
        while True:
            now = time.monotonic()
            timestamp_ms = int(time.time_ns() // 1_000_000)
            snapshot_list = list(snapshots.values())
            for snapshot in snapshot_list:
                if pilot_drone_ids is not None and snapshot.drone_id not in pilot_drone_ids:
                    continue
                if now < override_until.get(snapshot.drone_id, 0.0):
                    continue

                target = high_level_targets.get(snapshot.drone_id)
                if target is None:
                    continue

                if isinstance(pilot, OnnxMarlPilot):
                    observation = observation_builder.build(snapshot, snapshot_list, target=target)
                    action = pilot.predict_observation(observation)
                else:
                    action = pilot.predict(snapshot, snapshot_list, target=target)

                # 接近目标时直接发送真实目标点，避免 "位置+速度×horizon" 前馈
                # 导致的冲超-回摆振荡。阈值固定为 0.3m，与 goal-arrival 判据一致，
                # 避免在大速度场景下过早直线冲向目标。
                goal_distance = norm(subtract(target, snapshot.position))
                if goal_distance <= 0.3:
                    waypoint = target
                else:
                    waypoint = action_to_waypoint(snapshot.position, action, action_config)
                command = build_micro_waypoint_command(snapshot.drone_id, waypoint, timestamp_ms)
                client.publish(
                    f"swarm/drone/{snapshot.drone_id}/command",
                    payload=json.dumps(command, separators=(",", ":")),
                    qos=args.qos,
                )

            time.sleep(args.interval)
    except KeyboardInterrupt:
        LOGGER.info("stopping MARL pilot")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
