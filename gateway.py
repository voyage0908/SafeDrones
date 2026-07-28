from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from swarm.llm_provider import CommanderLLM, LLMProviderError, load_llm_settings
from swarm.mqtt_gateway import MqttGateway, MqttSettings


logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("gateway")


class CommandRequest(BaseModel):
    text: str = Field(min_length=1)
    drone: int = 1
    ttl_sec: float = 5.0


class DirectCommandRequest(BaseModel):
    drone: int = 1
    waypoint: list[float] = Field(min_length=3, max_length=3)
    priority: str = "normal"
    confidence: float = 1.0
    ttl_sec: float = 5.0


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def mqtt_settings_from_env() -> MqttSettings:
    return MqttSettings(
        host=os.getenv("MQTT_HOST", "127.0.0.1"),
        port=int(os.getenv("MQTT_PORT", "1883")),
        qos=int(os.getenv("MQTT_QOS", "0")),
    )


def command_payload(
    drone: int,
    waypoint: tuple[float, float, float] | list[float],
    priority: str,
    confidence: float,
    ttl_sec: float,
    rationale: str = "",
) -> dict[str, Any]:
    return {
        "drone": drone,
        "action": "move_to",
        "target": [float(value) for value in waypoint],
        "waypoint": [float(value) for value in waypoint],
        "priority": priority,
        "confidence": max(0.0, min(1.0, confidence)),
        "ttl_sec": ttl_sec,
        "command_id": f"cmd-{uuid4().hex[:12]}",
        "rationale": rationale,
        "timestamp_ms": now_ms(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_gateway = MqttGateway(mqtt_settings_from_env())
    try:
        mqtt_gateway.start()
    except Exception as exc:
        LOGGER.error("failed to start MQTT gateway: %s", exc)
        raise

    app.state.mqtt_gateway = mqtt_gateway
    app.state.commander_llm = CommanderLLM(load_llm_settings())
    try:
        yield
    finally:
        mqtt_gateway.stop()


app = FastAPI(title="SafeDrones Gateway", version="0.2.0", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, Any]:
    settings = app.state.commander_llm.settings
    return {
        "ok": True,
        "llm_provider": settings.provider,
        "llm_model": settings.model,
        "mqtt": {
            "host": app.state.mqtt_gateway.settings.host,
            "port": app.state.mqtt_gateway.settings.port,
        },
    }


@app.post("/api/command")
async def command(request: CommandRequest) -> dict[str, Any]:
    context = app.state.mqtt_gateway.recent_events(limit=10)
    try:
        plan = await app.state.commander_llm.plan(request.text, drone=request.drone, context=context)
    except LLMProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    payload = command_payload(
        drone=plan.drone,
        waypoint=plan.waypoint,
        priority=plan.priority,
        confidence=plan.confidence,
        ttl_sec=request.ttl_sec,
        rationale=plan.rationale,
    )
    app.state.mqtt_gateway.publish_command(payload)
    return {"published": True, "command": payload}


@app.post("/api/direct-command")
def direct_command(request: DirectCommandRequest) -> dict[str, Any]:
    payload = command_payload(
        drone=request.drone,
        waypoint=request.waypoint,
        priority=request.priority,
        confidence=request.confidence,
        ttl_sec=request.ttl_sec,
        rationale="manual direct command",
    )
    app.state.mqtt_gateway.publish_command(payload)
    return {"published": True, "command": payload}


@app.get("/api/events")
def events(limit: int = 20) -> dict[str, Any]:
    return {"events": app.state.mqtt_gateway.recent_events(limit=limit)}
