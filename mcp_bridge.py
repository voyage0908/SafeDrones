"""mcp_bridge.py — SafeDrones 执行层 → AGH 的 MCP 桥。

把蜂群执行层（MQTT → MockDrone/Crazyflie + Safety Gate 事件）暴露成 MCP 工具，
供 AGH 智能体（Agnes 模型）作为「高层指挥官」调用。

设计约束（比赛红线）：
- 本文件不含任何 LLM / 模型调用 —— 所有模型调用只发生在 AGH 内部。
- Python 侧不得出现任何第三方模型 API（评测会用大小写不敏感检索核实），这里只有 MQTT 与结构化航点。

运行（stdio，先启动 MQTT broker `scripts/dev_broker.py`）：
    ./.venv/Scripts/python.exe mcp_bridge.py
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from mcp.server.mcpserver import MCPServer

LOGGER = logging.getLogger("mcp_bridge")

TELEMETRY_TOPIC = "swarm/drone/+/telemetry"
OVERRIDE_TOPIC = "swarm/commander/override"
STATUS_TOPIC = "swarm/commander/status"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def command_payload(
    drone: int,
    waypoint: list[float] | tuple[float, float, float],
    priority: str = "normal",
    confidence: float = 0.8,
    ttl_sec: float = 5.0,
    rationale: str = "",
) -> dict[str, Any]:
    """结构化航点指令（与 gateway.py 的 command_payload 同构）。"""
    waypoint_list = [float(v) for v in waypoint]
    return {
        "drone": int(drone),
        "action": "move_to",
        "target": waypoint_list,
        "waypoint": waypoint_list,
        "priority": priority,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "ttl_sec": ttl_sec,
        "command_id": f"cmd-{uuid4().hex[:12]}",
        "rationale": rationale,
        "timestamp_ms": now_ms(),
    }


def hover_payload(drone: int, reason: str = "emergency_abort_from_commander") -> dict[str, Any]:
    """紧急悬停指令（与 swarm/safety.py 的 build_hover_command 同构）。"""
    return {
        "drone": int(drone),
        "action": "hover",
        "priority": "safety",
        "confidence": 1.0,
        "command_id": f"safety-hover-{now_ms()}-{drone}",
        "reason": reason,
        "timestamp_ms": now_ms(),
    }


class SwarmBridge:
    """MQTT 客户端：缓存遥测 + 安全事件，并下发指令。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 1883, qos: int = 0) -> None:
        self.host = host
        self.port = port
        self.qos = qos
        self._telemetry: dict[int, dict[str, Any]] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=500)
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._client = self._build_client()

    def _build_client(self) -> Any:
        try:
            import paho.mqtt.client as mqtt
        except ModuleNotFoundError as exc:
            raise RuntimeError("missing dependency: paho-mqtt") from exc
        # 允许多个 bridge 实例并存（例如 daemon 桥 + 临时测试桥），避免 MQTT client_id 冲突互相挤下线。
        client_id = os.environ.get("SAFEDRONES_MQTT_CLIENT_ID", "mcp-bridge")
        try:
            return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:
            return mqtt.Client(client_id=client_id)

    def start(self) -> None:
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        # connect_async + loop_start: paho reconnects in the background, so the MCP server stays
        # alive (and its tool catalog stays discoverable) even if the broker is not up yet.
        self._client.connect_async(self.host, self.port, keepalive=30)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        LOGGER.info("mcp-bridge connected to MQTT broker result=%s", reason_code)
        client.subscribe(TELEMETRY_TOPIC, qos=self.qos)
        client.subscribe(OVERRIDE_TOPIC, qos=self.qos)
        client.subscribe(STATUS_TOPIC, qos=self.qos)
        self._connected.set()

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOGGER.warning("ignored invalid payload on %s: %s", message.topic, exc)
            return

        topic = message.topic
        with self._lock:
            if topic.startswith("swarm/drone/") and topic.endswith("/telemetry"):
                drone_id = int(payload.get("drone", 0))
                self._telemetry[drone_id] = payload
            else:
                # 安全事件：状态报告 / 否决事件，保留 payload 自带时间戳供过滤
                event_ts = int(payload.get("timestamp_ms") or now_ms())
                self._events.append({"topic": topic, "payload": payload, "timestamp_ms": event_ts})

    def publish_command(self, command: dict[str, Any]) -> dict[str, Any]:
        drone_id = int(command["drone"])
        topic = f"swarm/drone/{drone_id}/command"
        payload = json.dumps(command, ensure_ascii=False, separators=(",", ":"))
        if not self._client.is_connected():
            raise RuntimeError("MQTT broker 未连接：请先启动 broker（amqtt，127.0.0.1:1883）再重试")
        result = self._client.publish(topic, payload=payload, qos=self.qos, retain=False)
        result.wait_for_publish(timeout=2)
        return command

    def latest_telemetry(self, drone_id: int) -> dict[str, Any] | None:
        with self._lock:
            return self._telemetry.get(drone_id)

    def known_drones(self) -> list[int]:
        with self._lock:
            return sorted(self._telemetry.keys())

    def recent_events(self, since_ms: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            events = [e for e in self._events if e["timestamp_ms"] >= since_ms]
            return events[-limit:]


BRIDGE = SwarmBridge()


@asynccontextmanager
async def lifespan(_: MCPServer):
    BRIDGE.start()
    try:
        yield
    finally:
        BRIDGE.stop()


server = MCPServer(
    name="safedrones-swarm",
    title="SafeDrones Swarm 执行层桥",
    description="无人机蜂群执行层（MQTT + Safety Gate）工具：下发航点、读遥测、读安全事件、紧急悬停。",
    version="0.1.0",
    lifespan=lifespan,
)


@server.tool(
    name="publish_waypoint",
    description=(
        "给指定无人机下发一个航点指令（move_to）。返回 command_id。"
        "坐标系：X 前、Y 左、Z 高，单位米。"
        "waypoint 为 [x, y, z] 三元素列表；priority 为 normal|high|emergency；"
        "confidence 0~1 表示置信度；ttl_sec 为指令有效期秒数。"
        "预设点参考：侦察点=[10,5,2]，基地=[0,0,1]，左前方=[5,5,2]，右前方=[5,-5,2]。"
    ),
)
def publish_waypoint(
    drone: int,
    waypoint: list[float],
    priority: str = "normal",
    confidence: float = 0.8,
    ttl_sec: float = 5.0,
    rationale: str = "",
) -> dict[str, Any]:
    if len(waypoint) != 3:
        raise ValueError("waypoint 必须是 [x, y, z] 三元素")
    command = command_payload(drone, waypoint, priority, confidence, ttl_sec, rationale)
    BRIDGE.publish_command(command)
    return {"published": True, "command_id": command["command_id"], "command": command}


@server.tool(
    name="read_telemetry",
    description="读取指定无人机的最新遥测（位置 position、速度 velocity、status、last_command_id 等）。若暂无遥测返回 null。",
)
def read_telemetry(drone: int) -> dict[str, Any] | None:
    return BRIDGE.latest_telemetry(drone)


@server.tool(
    name="list_drones",
    description="列出当前有遥测上报的所有无人机 id。用于确认哪些无人机在线。",
)
def list_drones() -> list[int]:
    return BRIDGE.known_drones()


@server.tool(
    name="list_safety_events",
    description=(
        "读取 Safety Gate 的安全事件（safety_override 否决事件 + safety_status 状态报告）。"
        "since_ms 只返回该毫秒时间戳之后的事件；返回列表按时间升序。"
        "这是「双向安全协议」的上行通道：AGH 收到 safety_override 后应重新规划航点。"
    ),
)
def list_safety_events(since_ms: int = 0, limit: int = 20) -> list[dict[str, Any]]:
    return BRIDGE.recent_events(since_ms=since_ms, limit=limit)


@server.tool(
    name="emergency_abort",
    description="紧急让指定无人机悬停（hover），优先级 safety。用于需要立即停止运动的场景。返回 command_id。",
)
def emergency_abort(drone: int) -> dict[str, Any]:
    command = hover_payload(drone)
    BRIDGE.publish_command(command)
    return {"published": True, "command_id": command["command_id"], "command": command}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server.run(transport="stdio")
