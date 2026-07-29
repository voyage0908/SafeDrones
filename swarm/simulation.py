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


def add(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def subtract(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(vector: Vector3, factor: float) -> Vector3:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def dot(a: Vector3, b: Vector3) -> float:
    return sum(a_i * b_i for a_i, b_i in zip(a, b))


def norm(vector: Vector3) -> float:
    return math.sqrt(dot(vector, vector))


def clamp_magnitude(vector: Vector3, max_magnitude: float) -> Vector3:
    if max_magnitude < 0:
        raise ValueError("max_magnitude must be non-negative")

    magnitude = norm(vector)
    if magnitude == 0 or magnitude <= max_magnitude:
        return vector
    return scale(vector, max_magnitude / magnitude)


def step_towards(current: Vector3, target: Vector3, max_distance: float) -> Vector3:
    """Move from current toward target by at most max_distance."""
    if max_distance < 0:
        raise ValueError("max_distance must be non-negative")

    remaining = distance(current, target)
    if remaining == 0 or remaining <= max_distance:
        return target

    scale = max_distance / remaining
    return tuple(c + (t - c) * scale for c, t in zip(current, target))  # type: ignore[return-value]


def step_vector_towards(current: Vector3, target: Vector3, max_delta: float) -> Vector3:
    delta = subtract(target, current)
    return add(current, clamp_magnitude(delta, max_delta))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _angle_delta_deg(target: float, current: float) -> float:
    return (target - current + 180.0) % 360.0 - 180.0


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
    velocity: Vector3 = (0.0, 0.0, 0.0)
    yaw_deg: float = 0.0
    speed_mps: float = 1.0
    max_accel_mps2: float = 1.0
    max_yaw_rate_dps: float = 120.0
    min_altitude_m: float = 0.0
    max_altitude_m: float = 5.0
    position_tolerance_m: float = 0.03
    velocity_tolerance_mps: float = 0.02
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
            self.target_pos = self._clamp_position(command.target)
            self.status = "flying" if distance(self.current_pos, self.target_pos) > 0 else "idle"
            return

        if command.action in {"hover", "brake"}:
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
            self.velocity = step_vector_towards(self.velocity, (0.0, 0.0, 0.0), self.max_accel_mps2 * dt_sec)
            self.current_pos = self._clamp_position(add(self.current_pos, scale(self.velocity, dt_sec)))
            self.target_pos = self.current_pos
            self._update_yaw(dt_sec)
            return

        remaining_vector = subtract(self.target_pos, self.current_pos)
        remaining_distance = norm(remaining_vector)
        if remaining_distance <= self.position_tolerance_m and norm(self.velocity) <= self.velocity_tolerance_mps:
            self.current_pos = self.target_pos
            self.velocity = (0.0, 0.0, 0.0)
            self.status = "idle"
            return

        desired_velocity = self._desired_velocity(remaining_vector, remaining_distance)
        self.velocity = step_vector_towards(self.velocity, desired_velocity, self.max_accel_mps2 * dt_sec)
        next_pos = self._clamp_position(add(self.current_pos, scale(self.velocity, dt_sec)))

        if dot(subtract(self.target_pos, self.current_pos), subtract(self.target_pos, next_pos)) < 0:
            next_pos = self.target_pos
            self.velocity = (0.0, 0.0, 0.0)
            self.status = "idle"

        self.current_pos = next_pos
        self._update_yaw(dt_sec)

    def telemetry(self, timestamp_ms: int) -> dict[str, Any]:
        x, y, z = self.current_pos
        vx, vy, vz = self.velocity
        return {
            "drone": self.drone_id,
            "x": x,
            "y": y,
            "z": z,
            "position": [x, y, z],
            "velocity": [vx, vy, vz],
            "yaw_deg": self.yaw_deg,
            "target": list(self.target_pos),
            "speed_mps": self.speed_mps,
            "max_accel_mps2": self.max_accel_mps2,
            "max_yaw_rate_dps": self.max_yaw_rate_dps,
            "status": self.status,
            "last_command_id": self.last_command_id,
            "last_error": self.last_error,
            "timestamp_ms": timestamp_ms,
        }

    def _desired_velocity(self, remaining_vector: Vector3, remaining_distance: float) -> Vector3:
        if remaining_distance == 0:
            return (0.0, 0.0, 0.0)

        direction = scale(remaining_vector, 1.0 / remaining_distance)
        braking_limited_speed = math.sqrt(max(0.0, 2.0 * self.max_accel_mps2 * remaining_distance))
        desired_speed = min(self.speed_mps, braking_limited_speed)
        return scale(direction, desired_speed)

    def _update_yaw(self, dt_sec: float) -> None:
        horizontal_speed = math.sqrt(self.velocity[0] ** 2 + self.velocity[1] ** 2)
        if horizontal_speed <= self.velocity_tolerance_mps:
            return

        target_yaw = math.degrees(math.atan2(self.velocity[1], self.velocity[0]))
        max_delta = self.max_yaw_rate_dps * dt_sec
        delta = _clamp(_angle_delta_deg(target_yaw, self.yaw_deg), -max_delta, max_delta)
        self.yaw_deg = (self.yaw_deg + delta) % 360.0

    def _clamp_position(self, position: Vector3) -> Vector3:
        return (
            position[0],
            position[1],
            _clamp(position[2], self.min_altitude_m, self.max_altitude_m),
        )


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
