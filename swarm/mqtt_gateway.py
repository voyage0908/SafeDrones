from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any


LOGGER = logging.getLogger("mqtt_gateway")


@dataclass(frozen=True)
class MqttSettings:
    host: str = "127.0.0.1"
    port: int = 1883
    qos: int = 0


class MqttGateway:
    def __init__(self, settings: MqttSettings, max_events: int = 500, event_log_path: str | None = None):
        self.settings = settings
        self.event_log_path = Path(event_log_path) if event_log_path else None
        self.events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._client = self._build_client()

    def _build_client(self):
        try:
            import paho.mqtt.client as mqtt
        except ModuleNotFoundError as exc:
            raise RuntimeError("missing dependency: paho-mqtt") from exc

        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gateway")
        except AttributeError:
            return mqtt.Client(client_id="gateway")

    def start(self) -> None:
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect(self.settings.host, self.settings.port, keepalive=30)
        self._client.loop_start()
        if not self._connected.wait(timeout=5):
            raise RuntimeError("gateway failed to connect to MQTT broker")

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        LOGGER.info("gateway connected to MQTT broker with result=%s", reason_code)
        client.subscribe("swarm/commander/status", qos=self.settings.qos)
        client.subscribe("swarm/commander/override", qos=self.settings.qos)
        self._connected.set()

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"raw": message.payload.decode("utf-8", errors="replace")}

        event = {
            "topic": message.topic,
            "payload": payload,
            "timestamp_ms": time.time_ns() // 1_000_000,
        }
        with self._lock:
            self.events.append(event)
            self._append_event_log(event)

    def _append_event_log(self, event: dict[str, Any]) -> None:
        if self.event_log_path is None:
            return

        self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    def publish_command(self, command: dict[str, Any]) -> None:
        drone_id = int(command["drone"])
        topic = f"swarm/drone/{drone_id}/command"
        payload = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
        result = self._client.publish(topic, payload=payload, qos=self.settings.qos, retain=False)
        result.wait_for_publish(timeout=5)

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events)[-limit:]
