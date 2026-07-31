from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from swarm.safety import DroneSnapshot
from swarm.simulation import Vector3, add, clamp_magnitude, distance, norm, scale, subtract


def _safe_div(value: float, divisor: float) -> float:
    if divisor == 0:
        return 0.0
    return value / divisor


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _velocity(snapshot: DroneSnapshot) -> Vector3:
    return snapshot.velocity or (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class MarlObservationConfig:
    max_neighbors: int = 3
    position_scale_m: float = 10.0
    velocity_scale_mps: float = 2.0
    target_scale_m: float = 10.0

    @property
    def observation_size(self) -> int:
        # own position, own velocity, target delta, then rel pos/rel vel for each neighbor
        return 9 + self.max_neighbors * 6


@dataclass(frozen=True)
class MarlActionConfig:
    max_speed_mps: float = 1.0
    horizon_sec: float = 0.4


@dataclass(frozen=True)
class MarlRewardConfig:
    progress_weight: float = 1.0
    success_reward: float = 5.0
    near_penalty_weight: float = 1.0
    collision_penalty: float = 10.0
    boundary_penalty: float = 5.0
    action_smooth_penalty: float = 0.05
    safe_distance_m: float = 1.6
    collision_distance_m: float = 0.25
    success_distance_m: float = 0.2
    bounds_min: Vector3 = (-5.0, -5.0, 0.0)
    bounds_max: Vector3 = (5.0, 5.0, 5.0)


@dataclass(frozen=True)
class MarlRewardBreakdown:
    total: float
    progress: float
    success: float
    separation: float
    collision: float
    boundary: float
    smoothness: float


class MarlObservationBuilder:
    def __init__(self, config: MarlObservationConfig | None = None):
        self.config = config or MarlObservationConfig()

    def build(
        self,
        self_snapshot: DroneSnapshot,
        snapshots: Sequence[DroneSnapshot],
        target: Vector3 | None = None,
    ) -> list[float]:
        goal = target or self_snapshot.target or self_snapshot.position
        observation: list[float] = []

        observation.extend(self._scale_vector(self_snapshot.position, self.config.position_scale_m))
        observation.extend(self._scale_vector(_velocity(self_snapshot), self.config.velocity_scale_mps))
        observation.extend(self._scale_vector(subtract(goal, self_snapshot.position), self.config.target_scale_m))

        neighbors = sorted(
            (snapshot for snapshot in snapshots if snapshot.drone_id != self_snapshot.drone_id),
            key=lambda snapshot: distance(self_snapshot.position, snapshot.position),
        )
        for neighbor in neighbors[: self.config.max_neighbors]:
            observation.extend(
                self._scale_vector(subtract(neighbor.position, self_snapshot.position), self.config.position_scale_m)
            )
            observation.extend(
                self._scale_vector(subtract(_velocity(neighbor), _velocity(self_snapshot)), self.config.velocity_scale_mps)
            )

        while len(observation) < self.config.observation_size:
            observation.extend([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

        return observation

    @staticmethod
    def _scale_vector(vector: Vector3, scale_value: float) -> list[float]:
        return [_clip(_safe_div(value, scale_value)) for value in vector]


def normalize_action(action: Sequence[float]) -> Vector3:
    if len(action) < 3:
        raise ValueError("MARL action must contain at least three values")

    raw = (_clip(float(action[0])), _clip(float(action[1])), _clip(float(action[2])))
    return clamp_magnitude(raw, 1.0)


def action_to_velocity(action: Sequence[float], config: MarlActionConfig | None = None) -> Vector3:
    config = config or MarlActionConfig()
    return scale(normalize_action(action), config.max_speed_mps)


def action_to_waypoint(
    position: Vector3,
    action: Sequence[float],
    config: MarlActionConfig | None = None,
) -> Vector3:
    config = config or MarlActionConfig()
    velocity = action_to_velocity(action, config)
    return add(position, scale(velocity, config.horizon_sec))


def reward_for_transition(
    previous_position: Vector3,
    current_position: Vector3,
    target: Vector3,
    peer_positions: Sequence[Vector3],
    action: Sequence[float],
    previous_action: Sequence[float] | None = None,
    config: MarlRewardConfig | None = None,
) -> MarlRewardBreakdown:
    config = config or MarlRewardConfig()
    progress = (distance(previous_position, target) - distance(current_position, target)) * config.progress_weight
    success = config.success_reward if distance(current_position, target) <= config.success_distance_m else 0.0

    separation = 0.0
    collision = 0.0
    for peer_position in peer_positions:
        peer_distance = distance(current_position, peer_position)
        if peer_distance <= config.collision_distance_m:
            collision -= config.collision_penalty
        elif peer_distance < config.safe_distance_m:
            separation -= config.near_penalty_weight * (config.safe_distance_m - peer_distance) / config.safe_distance_m

    boundary = 0.0
    for value, low, high in zip(current_position, config.bounds_min, config.bounds_max):
        if value < low or value > high:
            boundary -= config.boundary_penalty
            break

    smoothness = 0.0
    if previous_action is not None:
        normalized = normalize_action(action)
        previous = normalize_action(previous_action)
        smoothness = -config.action_smooth_penalty * sum((a - b) ** 2 for a, b in zip(normalized, previous))

    total = progress + success + separation + collision + boundary + smoothness
    return MarlRewardBreakdown(total, progress, success, separation, collision, boundary, smoothness)


class RuleMarlPilot:
    def __init__(
        self,
        action_config: MarlActionConfig | None = None,
        safe_distance_m: float = 1.6,
        repulsion_gain: float = 1.2,
    ):
        self.action_config = action_config or MarlActionConfig()
        self.safe_distance_m = safe_distance_m
        self.repulsion_gain = repulsion_gain

    def predict(
        self,
        self_snapshot: DroneSnapshot,
        snapshots: Sequence[DroneSnapshot],
        target: Vector3 | None = None,
    ) -> Vector3:
        goal = target or self_snapshot.target or self_snapshot.position
        goal_vector = subtract(goal, self_snapshot.position)
        goal_distance = norm(goal_vector)
        desired = (0.0, 0.0, 0.0) if goal_distance == 0 else scale(goal_vector, 1.0 / goal_distance)

        avoidance = (0.0, 0.0, 0.0)
        for snapshot in snapshots:
            if snapshot.drone_id == self_snapshot.drone_id:
                continue
            away = subtract(self_snapshot.position, snapshot.position)
            peer_distance = norm(away)
            if peer_distance == 0 or peer_distance >= self.safe_distance_m:
                continue
            strength = (self.safe_distance_m - peer_distance) / self.safe_distance_m
            avoidance = add(avoidance, scale(away, self.repulsion_gain * strength / peer_distance))

        return clamp_magnitude(add(desired, avoidance), 1.0)


class OnnxMarlPilot:
    def __init__(
        self,
        model_path: str | Path,
        observation_config: MarlObservationConfig | None = None,
        input_name: str | None = None,
        output_name: str | None = None,
    ):
        try:
            import numpy as np
            import onnxruntime as ort
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "onnxruntime and numpy are required only when --onnx-model is used. "
                "Install them in a compatible training/inference environment before loading ONNX."
            ) from exc

        self._np = np
        self.observation_config = observation_config or MarlObservationConfig()
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_name = input_name or self.session.get_inputs()[0].name
        self.output_name = output_name or self.session.get_outputs()[0].name

    def predict_observation(self, observation: Sequence[float]) -> Vector3:
        if len(observation) != self.observation_config.observation_size:
            raise ValueError(
                f"expected observation size {self.observation_config.observation_size}, got {len(observation)}"
            )

        batch = self._np.asarray([observation], dtype=self._np.float32)
        output = self.session.run([self.output_name], {self.input_name: batch})[0]
        return normalize_action(output[0].tolist())
