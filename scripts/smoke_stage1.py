from __future__ import annotations

import argparse
import json
import math
import threading
import time
from typing import Any


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


def far_target_from(position: list[float]) -> list[float]:
    if math.dist(position, [0.0, 0.0, 0.0]) > 2.0:
        return [0.0, 0.0, 0.0]
    return [5.0, 5.0, 2.0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a stage-one MQTT smoke test against a running MockDrone.")
    parser.add_argument("--drone-id", type=int, default=1)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--speed", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    command_topic = f"swarm/drone/{args.drone_id}/command"
    telemetry_topic = f"swarm/drone/{args.drone_id}/telemetry"
    command_id = f"smoke-{int(time.time() * 1000)}"
    first_telemetry = threading.Event()
    connected = threading.Event()
    samples: list[dict[str, Any]] = []
    publish_after_first_sample = {"done": False}

    client = build_mqtt_client(client_id=f"stage1-smoke-{args.drone_id}")

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        client.subscribe(telemetry_topic)
        connected.set()

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        telemetry = json.loads(message.payload.decode("utf-8"))
        samples.append(telemetry)
        first_telemetry.set()

        if not publish_after_first_sample["done"]:
            target = far_target_from(telemetry["position"])
            payload = {
                "action": "move_to",
                "target": target,
                "speed_mps": args.speed,
                "command_id": command_id,
            }
            client.publish(command_topic, json.dumps(payload))
            publish_after_first_sample["done"] = True

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    try:
        if not connected.wait(timeout=3):
            raise SystemExit("failed to connect to broker")
        if not first_telemetry.wait(timeout=3):
            raise SystemExit("no telemetry received before command")

        deadline = time.time() + args.timeout
        while time.time() < deadline:
            matching = [s for s in samples if s.get("last_command_id") == command_id]
            positions = {tuple(round(v, 3) for v in s["position"]) for s in matching}
            if len(positions) >= 3:
                print("stage1 smoke test passed")
                for sample in matching[:5]:
                    print(json.dumps(sample, separators=(",", ":")))
                return
            time.sleep(0.1)

        raise SystemExit("did not observe at least three moving telemetry positions")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
