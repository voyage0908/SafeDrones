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


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def dist(a: list[float], b: list[float]) -> float:
    return math.dist(a, b)


def command(drone_id: int, target: list[float], label: str) -> dict[str, Any]:
    timestamp_ms = now_ms()
    return {
        "drone": drone_id,
        "action": "move_to",
        "target": target,
        "waypoint": target,
        "priority": "normal",
        "confidence": 1.0,
        "ttl_sec": 5.0,
        "command_id": f"stage4-{label}-{timestamp_ms}-{drone_id}",
        "rationale": f"stage4 automated {label} command",
        "timestamp_ms": timestamp_ms,
    }


class Stage4Monitor:
    def __init__(self, host: str, port: int, qos: int):
        self.host = host
        self.port = port
        self.qos = qos
        self.connected = threading.Event()
        self.telemetry_ready = threading.Event()
        self.lock = threading.Lock()
        self.telemetry: dict[int, dict[str, Any]] = {}
        self.marl_command_count = 0
        self.override_count = 0
        self.warning_count = 0
        self.min_distance_m: float | None = None
        self.events: list[dict[str, Any]] = []
        self.client = build_mqtt_client("stage4-marl-safety-check")

    def start(self) -> None:
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        if not self.connected.wait(timeout=5):
            raise SystemExit("failed to connect to MQTT broker")

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def publish_command(self, payload: dict[str, Any]) -> None:
        drone_id = int(payload["drone"])
        result = self.client.publish(
            f"swarm/drone/{drone_id}/command",
            payload=json.dumps(payload, separators=(",", ":")),
            qos=self.qos,
        )
        result.wait_for_publish(timeout=5)

    def wait_for_telemetry(self, timeout: float) -> None:
        if not self.telemetry_ready.wait(timeout=timeout):
            raise SystemExit("timed out waiting for telemetry from both drones")

    def wait_until(self, predicate, timeout: float, label: str) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.1)
        print(f"[stage4-check] timed out waiting for {label}")
        return False

    def snapshot(self) -> dict[int, dict[str, Any]]:
        with self.lock:
            return dict(self.telemetry)

    def commander_events(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.events)

    def summary(self, swap_completed: bool) -> dict[str, Any]:
        with self.lock:
            positions = {
                str(drone_id): payload.get("position")
                for drone_id, payload in sorted(self.telemetry.items())
            }
            command_ids = {
                str(drone_id): payload.get("last_command_id")
                for drone_id, payload in sorted(self.telemetry.items())
            }
            statuses = {
                str(drone_id): payload.get("status")
                for drone_id, payload in sorted(self.telemetry.items())
            }

        return {
            "swap_completed": swap_completed,
            "min_distance_m": None if self.min_distance_m is None else round(self.min_distance_m, 4),
            "marl_command_count": self.marl_command_count,
            "override_count": self.override_count,
            "warning_count": self.warning_count,
            "final_positions": positions,
            "final_statuses": statuses,
            "final_command_ids": command_ids,
        }

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        client.subscribe("swarm/drone/+/telemetry", qos=self.qos)
        client.subscribe("swarm/drone/+/command", qos=self.qos)
        client.subscribe("swarm/commander/status", qos=self.qos)
        client.subscribe("swarm/commander/override", qos=self.qos)
        self.connected.set()

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if message.topic.endswith("/telemetry"):
            self._record_telemetry(payload)
            return

        if message.topic.endswith("/command"):
            if payload.get("priority") == "marl":
                self.marl_command_count += 1
            return

        event_name = payload.get("event_name") or payload.get("event")
        mode = payload.get("mode") or payload.get("status")
        with self.lock:
            if event_name == "safety_override":
                self.override_count += 1
                self.events.append(payload)
            if mode == "warning":
                self.warning_count += 1

    def _record_telemetry(self, payload: dict[str, Any]) -> None:
        try:
            drone_id = int(payload["drone"])
            position = [float(value) for value in payload["position"]]
        except (KeyError, TypeError, ValueError):
            return

        with self.lock:
            self.telemetry[drone_id] = payload
            if {1, 2}.issubset(self.telemetry):
                p1 = self.telemetry[1].get("position")
                p2 = self.telemetry[2].get("position")
                if p1 is not None and p2 is not None:
                    current_distance = dist(p1, p2)
                    if self.min_distance_m is None or current_distance < self.min_distance_m:
                        self.min_distance_m = current_distance
                self.telemetry_ready.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the stage-four MARL + Safety Gate crossing check.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--separation-timeout", type=float, default=12.0)
    parser.add_argument("--cross-timeout", type=float, default=30.0)
    parser.add_argument("--min-marl-commands", type=int, default=8)
    parser.add_argument("--require-override", action="store_true")
    parser.add_argument("--min-distance", type=float, default=0.25)
    args = parser.parse_args()

    monitor = Stage4Monitor(args.host, args.port, args.qos)
    monitor.start()
    try:
        monitor.wait_for_telemetry(timeout=8)
        print("[stage4-check] telemetry ready")

        monitor.publish_command(command(1, [-3.0, 0.0, 1.0], "separate"))
        monitor.publish_command(command(2, [3.0, 0.0, 1.0], "separate"))
        print("[stage4-check] separation commands published")

        separated = monitor.wait_until(
            lambda: (
                (snapshot := monitor.snapshot()).get(1, {}).get("position", [0])[0] < -2.0
                and snapshot.get(2, {}).get("position", [0])[0] > 2.0
            ),
            timeout=args.separation_timeout,
            label="initial separation",
        )
        if not separated:
            raise SystemExit("drones did not reach initial separation")

        monitor.publish_command(command(1, [3.0, 0.0, 1.0], "cross"))
        monitor.publish_command(command(2, [-3.0, 0.0, 1.0], "cross"))
        print("[stage4-check] crossing commands published")

        swap_completed = monitor.wait_until(
            lambda: (
                (snapshot := monitor.snapshot()).get(1, {}).get("position", [0])[0] > 2.0
                and snapshot.get(2, {}).get("position", [0])[0] < -2.0
            ),
            timeout=args.cross_timeout,
            label="position swap",
        )

        summary = monitor.summary(swap_completed=swap_completed)
        print(json.dumps(summary, indent=2, sort_keys=True))

        if not swap_completed:
            raise SystemExit("drones did not complete the crossing swap")
        if summary["marl_command_count"] < args.min_marl_commands:
            raise SystemExit(
                f"expected at least {args.min_marl_commands} MARL commands, "
                f"observed {summary['marl_command_count']}"
            )
        if args.require_override and summary["override_count"] < 1:
            raise SystemExit("expected at least one safety override")
        if summary["min_distance_m"] is not None and summary["min_distance_m"] < args.min_distance:
            raise SystemExit(
                f"minimum distance {summary['min_distance_m']}m is below required {args.min_distance}m"
            )

        print("[stage4-check] passed")
    finally:
        monitor.stop()


if __name__ == "__main__":
    main()
