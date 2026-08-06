from __future__ import annotations

import argparse
import json
import time


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
    parser = argparse.ArgumentParser(description="Publish a one-shot move_to command.")
    parser.add_argument("--drone-id", type=int, default=1)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--target", nargs=3, type=float, metavar=("X", "Y", "Z"), required=True)
    parser.add_argument("--speed", type=float, default=None)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0)
    args = parser.parse_args()

    payload = {
        "action": "move_to",
        "target": args.target,
        "command_id": f"manual-{int(time.time() * 1000)}",
    }
    if args.speed is not None:
        payload["speed_mps"] = args.speed

    topic = f"swarm/drone/{args.drone_id}/command"
    client = build_mqtt_client(client_id=f"command-publisher-{args.drone_id}")
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()
    result = client.publish(topic, json.dumps(payload), qos=args.qos)
    result.wait_for_publish(timeout=5)
    client.loop_stop()
    client.disconnect()
    print(f"published to {topic}: {json.dumps(payload)}")


if __name__ == "__main__":
    main()

