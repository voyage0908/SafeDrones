from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

from swarm.safety import (
    DroneSnapshot,
    SafetyConfig,
    SafetyGate,
    build_hover_command,
    build_override_event,
)


LOGGER = logging.getLogger("safety_gate")


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the rule-based Safety Gate.")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--safe-distance", type=float, default=1.5)
    parser.add_argument("--high-threshold", type=float, default=0.75)
    parser.add_argument("--low-threshold", type=float, default=0.35)
    parser.add_argument("--hold-sec", type=float, default=1.0)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = SafetyConfig(
        safe_distance_m=args.safe_distance,
        low_threshold=args.low_threshold,
        high_threshold=args.high_threshold,
        hold_sec=args.hold_sec,
    )
    gate = SafetyGate(config)
    snapshots: dict[int, DroneSnapshot] = {}
    active_overrides: set[int] = set()
    client = build_mqtt_client(client_id="safety-gate")

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        LOGGER.info("connected to MQTT broker %s:%s with result=%s", args.host, args.port, reason_code)
        client.subscribe("swarm/drone/+/telemetry", qos=args.qos)

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            snapshot = DroneSnapshot.from_telemetry(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("ignored invalid telemetry on %s: %s", message.topic, exc)
            return

        snapshots[snapshot.drone_id] = snapshot

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    try:
        while True:
            decisions = gate.evaluate(list(snapshots.values()))
            for decision in decisions:
                if decision.mode == "override" and decision.drone not in active_overrides:
                    command = build_hover_command(decision)
                    event = build_override_event(decision)
                    client.publish(
                        f"swarm/drone/{decision.drone}/command",
                        payload=json.dumps(command, separators=(",", ":")),
                        qos=args.qos,
                    )
                    client.publish(
                        "swarm/commander/override",
                        payload=json.dumps(event, separators=(",", ":")),
                        qos=args.qos,
                    )
                    active_overrides.add(decision.drone)
                    LOGGER.warning(
                        "override drone=%s risk=%.3f target=%s",
                        decision.drone,
                        decision.risk_level,
                        decision.target_drone,
                    )

                if decision.mode != "override":
                    active_overrides.discard(decision.drone)

            time.sleep(args.interval)
    except KeyboardInterrupt:
        LOGGER.info("stopping safety gate")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
