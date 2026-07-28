from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one MQTT-backed mock drone.")
    parser.add_argument("--drone-id", type=int, default=1)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--speed", type=float, default=1.0, help="Default speed in meters per second.")
    parser.add_argument("--interval", type=float, default=0.1, help="Telemetry/update interval in seconds.")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.speed <= 0:
        raise SystemExit("--speed must be positive")
    if args.interval <= 0:
        raise SystemExit("--interval must be positive")

    state = MockDroneState(drone_id=args.drone_id, speed_mps=args.speed)
    command_topic = f"swarm/drone/{args.drone_id}/command"
    telemetry_topic = f"swarm/drone/{args.drone_id}/telemetry"
    client = build_mqtt_client(client_id=f"mock-drone-{args.drone_id}")

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        LOGGER.info("connected to MQTT broker %s:%s with result=%s", args.host, args.port, reason_code)
        client.subscribe(command_topic, qos=args.qos)
        LOGGER.info("subscribed command topic: %s", command_topic)

    def on_message(client: Any, userdata: Any, message: Any) -> None:
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

    try:
        while True:
            now = time.monotonic()
            dt_sec = now - last_tick
            last_tick = now

            state.update(dt_sec)
            payload = json.dumps(state.telemetry(now_ms()), separators=(",", ":"))
            client.publish(telemetry_topic, payload=payload, qos=args.qos, retain=False)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        LOGGER.info("stopping mock drone")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()

