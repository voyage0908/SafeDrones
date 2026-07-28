from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any


Vector3 = tuple[float, float, float]


def _as_vector3(value: Any, field_name: str) -> Vector3:
    if not isinstance(value, list | tuple) or len(value) != 3:
        raise ValueError(f"{field_name} must be a list of three numbers")

    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain only numbers") from exc


def distance(a: Vector3, b: Vector3) -> float:
    return math.sqrt(sum((a_i - b_i) ** 2 for a_i, b_i in zip(a, b)))


def step_towards(current: Vector3, target: Vector3, max_distance: float) -> Vector3:
    """Move from current toward target by at most max_distance."""
    if max_distance < 0:
        raise ValueError("max_distance must be non-negative")

    remaining = distance(current, target)
    if remaining == 0 or remaining <= max_distance:
        return target

    scale = max_distance / remaining
    return tuple(c + (t - c) * scale for c, t in zip(current, target))  # type: ignore[return-value]


@dataclass
class DroneCommand:
    action: str
    target: Vector3 | None = None
    speed_mps: float | None = None
    command_id: str | None = None


@dataclass
class MockDroneState:
    drone_id: int
    current_pos: Vector3 = (0.0, 0.0, 0.0)
    target_pos: Vector3 = (0.0, 0.0, 0.0)
    speed_mps: float = 1.0
    status: str = "idle"
    last_command_id: str | None = None
    last_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def apply_command(self, command: DroneCommand) -> None:
        self.last_error = None
        self.last_command_id = command.command_id

        if command.speed_mps is not None:
            if command.speed_mps <= 0:
                raise ValueError("speed_mps must be positive")
            self.speed_mps = command.speed_mps

        if command.action == "move_to":
            if command.target is None:
                raise ValueError("move_to requires a target")
            self.target_pos = command.target
            self.status = "flying" if distance(self.current_pos, self.target_pos) > 0 else "idle"
            return

        if command.action == "hover":
            self.target_pos = self.current_pos
            self.status = "hovering"
            return

        raise ValueError(f"unsupported action: {command.action}")

    def update(self, dt_sec: float) -> None:
        if dt_sec < 0:
            raise ValueError("dt_sec must be non-negative")

        if self.status not in {"flying", "hovering"}:
            return

        if self.status == "hovering":
            self.target_pos = self.current_pos
            return

        self.current_pos = step_towards(self.current_pos, self.target_pos, self.speed_mps * dt_sec)
        if distance(self.current_pos, self.target_pos) == 0:
            self.status = "idle"

    def telemetry(self, timestamp_ms: int) -> dict[str, Any]:
        x, y, z = self.current_pos
        return {
            "drone": self.drone_id,
            "x": x,
            "y": y,
            "z": z,
            "position": [x, y, z],
            "target": list(self.target_pos),
            "speed_mps": self.speed_mps,
            "status": self.status,
            "last_command_id": self.last_command_id,
            "last_error": self.last_error,
            "timestamp_ms": timestamp_ms,
        }


def parse_command(payload: bytes | str) -> DroneCommand:
    import json

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")

    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc

    if not isinstance(raw, dict):
        raise ValueError("command payload must be a JSON object")

    action = raw.get("action")
    target_value = raw.get("target")

    # Also accept the protocol shape used by the LLM commander section.
    if action is None and "waypoint" in raw:
        action = "move_to"
        target_value = raw["waypoint"]

    if action is None:
        raise ValueError("command must include action or waypoint")

    target = _as_vector3(target_value, "target") if target_value is not None else None
    speed = raw.get("speed_mps")
    speed_mps = float(speed) if speed is not None else None
    command_id = raw.get("command_id")

    if command_id is not None and not isinstance(command_id, str):
        raise ValueError("command_id must be a string")

    return DroneCommand(
        action=str(action),
        target=target,
        speed_mps=speed_mps,
        command_id=command_id,
    )

