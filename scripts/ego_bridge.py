"""Ego 感知桥接：把真实相机感知合并成一条 telemetry 同 schema 的状态流。

输入：
- swarm/drone/+/telemetry      自身/友方真值（--self-ids 指定的无人机）
- swarm/drone_seen/+/position  相机反解的非友方位置（组 2 感知输出，ID 非持久）

输出：
- swarm/ego/drone/{id}/telemetry  与 mock telemetry 同 schema。
  自身/友方原样透传；敌方用最近一次目击的位置与有限差分速度。

两机场景里"相机看到的唯一目标 = 对方"，因此默认把目击分配给
`--enemy-ids` 中最近被目击过的那一架；位置超过 --state-ttl-sec 未更新
则保留最后已知值并标记 status=stale。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from typing import Any


LOGGER = logging.getLogger("ego_bridge")


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


def as_position(payload: dict[str, Any]) -> tuple[float, float, float] | None:
    position = payload.get("position")
    if not isinstance(position, list | tuple) or len(position) != 3:
        return None
    return (float(position[0]), float(position[1]), float(position[2]))


def parse_ids(raw: str, flag: str) -> set[int]:
    if not raw:
        return set()
    try:
        return {int(value) for value in raw.split(",") if value.strip()}
    except ValueError as exc:
        raise SystemExit(f"{flag} must be a comma-separated list of integers") from exc


class PerceivedTrack:
    """单个敌方 ID 的目击状态：位置、有限差分速度、最后目击时刻。"""

    def __init__(self) -> None:
        self.position: tuple[float, float, float] | None = None
        self.velocity: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.last_seen_ms: int = 0

    def update(self, position: tuple[float, float, float], timestamp_ms: int) -> None:
        if self.position is not None and timestamp_ms > self.last_seen_ms:
            dt = (timestamp_ms - self.last_seen_ms) / 1000.0
            if dt > 0.001:
                self.velocity = tuple(
                    max(-5.0, min(5.0, (position[i] - self.position[i]) / dt)) for i in range(3)
                )
        self.position = position
        self.last_seen_ms = timestamp_ms

    def telemetry(self, drone_id: int, timestamp_ms: int, ttl_ms: int) -> dict[str, Any] | None:
        if self.position is None:
            return None
        stale = timestamp_ms - self.last_seen_ms > ttl_ms
        return {
            "drone": drone_id,
            "position": list(self.position),
            "x": self.position[0],
            "y": self.position[1],
            "z": self.position[2],
            "velocity": list(self.velocity),
            "speed_mps": math.sqrt(sum(v * v for v in self.velocity)),
            "yaw_deg": 0.0,
            "status": "stale" if stale else "perceived",
            "target": None,
            "last_command_id": None,
            "timestamp_ms": timestamp_ms,
            "source": "sim_cam",
        }


class EgoBridge:
    def __init__(
        self,
        self_ids: set[int],
        enemy_ids: set[int],
        state_ttl_sec: float,
        publish_interval_sec: float,
    ):
        self.self_ids = self_ids
        self.enemy_ids = enemy_ids
        self.ttl_ms = int(state_ttl_sec * 1000.0)
        self.publish_interval_sec = publish_interval_sec
        self.self_telemetry: dict[int, dict[str, Any]] = {}
        self.tracks: dict[int, PerceivedTrack] = {enemy_id: PerceivedTrack() for enemy_id in enemy_ids}

    def handle_telemetry(self, drone_id: int, payload: dict[str, Any]) -> None:
        if drone_id in self.self_ids:
            self.self_telemetry[drone_id] = payload

    def handle_sighting(self, payload: dict[str, Any], timestamp_ms: int) -> int | None:
        """把一条 drone_seen 目击分配给最匹配的敌方 ID，返回该 ID。"""
        position = as_position(payload)
        if position is None or not self.enemy_ids:
            return None

        camera_drone = int(payload.get("camera_drone") or 0)
        if camera_drone in self.enemy_ids:
            return None  # 目击不该来自敌方自身相机

        # 两机场景天然只有一架敌方；多机时选"上次目击位置最近"的 track，
        # 否则选最久没有目击的 track。
        best_id: int | None = None
        best_key: tuple[int, float] | None = None
        for enemy_id in self.enemy_ids:
            track = self.tracks.setdefault(enemy_id, PerceivedTrack())
            if track.position is None:
                key = (0, float("inf"))
            else:
                key = (1, math.dist(position, track.position))
            if best_key is None or key < best_key:
                best_key = key
                best_id = enemy_id

        if best_id is None:
            return None
        self.tracks[best_id].update(position, timestamp_ms)
        return best_id

    def outgoing_messages(self, timestamp_ms: int) -> list[tuple[str, dict[str, Any]]]:
        messages: list[tuple[str, dict[str, Any]]] = []
        for drone_id, payload in self.self_telemetry.items():
            merged = dict(payload)
            merged["timestamp_ms"] = timestamp_ms
            merged.setdefault("source", "telemetry")
            messages.append((f"swarm/ego/drone/{drone_id}/telemetry", merged))
        for enemy_id, track in self.tracks.items():
            telemetry = track.telemetry(enemy_id, timestamp_ms, self.ttl_ms)
            if telemetry is not None:
                messages.append((f"swarm/ego/drone/{enemy_id}/telemetry", telemetry))
        return messages


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge self telemetry and camera sightings into an ego state stream.")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--self-ids", default="1", help="Friendly drones whose telemetry passes through.")
    parser.add_argument("--enemy-ids", default="2", help="Enemy drones that must come from camera sightings.")
    parser.add_argument("--state-ttl-sec", type=float, default=1.0)
    parser.add_argument("--publish-interval", type=float, default=0.1)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    bridge = EgoBridge(
        self_ids=parse_ids(args.self_ids, "--self-ids"),
        enemy_ids=parse_ids(args.enemy_ids, "--enemy-ids"),
        state_ttl_sec=args.state_ttl_sec,
        publish_interval_sec=args.publish_interval,
    )

    client = build_mqtt_client(client_id="ego-bridge")

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        LOGGER.info("connected to MQTT broker %s:%s with result=%s", args.host, args.port, reason_code)
        client.subscribe("swarm/drone/+/telemetry", qos=args.qos)
        client.subscribe("swarm/drone_seen/+/position", qos=args.qos)

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if message.topic.endswith("/telemetry"):
            try:
                drone_id = int(payload["drone"])
            except (KeyError, TypeError, ValueError):
                return
            bridge.handle_telemetry(drone_id, payload)
        elif message.topic.startswith("swarm/drone_seen/"):
            enemy_id = bridge.handle_sighting(payload, now_ms())
            if enemy_id is not None:
                LOGGER.debug("sighting assigned to enemy drone %d", enemy_id)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    LOGGER.info(
        "ego bridge: self=%s enemy=%s -> swarm/ego/drone/*/telemetry",
        sorted(bridge.self_ids),
        sorted(bridge.enemy_ids),
    )
    try:
        while True:
            for topic, payload in bridge.outgoing_messages(now_ms()):
                client.publish(topic, payload=json.dumps(payload, separators=(",", ":")), qos=args.qos)
            time.sleep(args.publish_interval)
    except KeyboardInterrupt:
        LOGGER.info("stopping ego bridge")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
