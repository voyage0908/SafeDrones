from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any

from swarm.simulation import Vector3
from swarm.orca import orca_pair_risk, orca_velocity


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _sub(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vector3, value: float) -> Vector3:
    return (a[0] * value, a[1] * value, a[2] * value)


def _dot(a: Vector3, b: Vector3) -> float:
    return sum(a_i * b_i for a_i, b_i in zip(a, b))


def _norm(a: Vector3) -> float:
    return math.sqrt(_dot(a, a))


def _normalize(a: Vector3) -> Vector3:
    length = _norm(a)
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (a[0] / length, a[1] / length, a[2] / length)


def _as_vector3(value: Any, field_name: str) -> Vector3:
    if not isinstance(value, list | tuple) or len(value) != 3:
        raise ValueError(f"{field_name} must be a list of three numbers")
    return (float(value[0]), float(value[1]), float(value[2]))


@dataclass(frozen=True)
class SafetyConfig:
    safe_distance_m: float = 1.6
    ttc_safe_sec: float = 3.0
    max_speed_mps: float = 2.0
    escape_distance_m: float = 1.2
    low_threshold: float = 0.32
    high_threshold: float = 0.70
    release_threshold: float = 0.25
    hold_sec: float = 1.0
    min_override_sec: float = 0.0
    # 垂直避障增益：0 表示纯水平避障，>0 时按无人机奇偶性加入垂直分量。
    vertical_escape_gain: float = 0.0
    # ORCA 互惠速度障碍（预测式协同避障）开关。默认关闭，保持反应式行为不变；
    # 开启后 override/warning 的分流航点改用 ORCA 协同速度方向（检测阈值不变）。
    orca_enabled: bool = False
    # ORCA 提前预警：把速度障碍意义下的「到碰撞时间」叠加到现有风险（只增不减），
    # 让安全门在瞬时距离/TTC 尚未越限时就预判 τ 秒内的碰撞。仅 orca_enabled 时生效。
    orca_early: bool = False
    # ORCA 预测时域（秒）。
    orca_time_horizon_sec: float = 2.0
    # 互惠分摊比例：0.5 = 标准 ORCA（双方各承担一半速度修正）。
    orca_responsibility: float = 0.5
    # ORCA 的几何碰撞半径（双方半径之和）= 期望保持的舒适间距。取 safe_distance_m（1.6）
    # 而非物理碰撞半径（0.25）：否则 ORCA 只保证「物理不碰」、会把机距放到 ~0.25m 才算无碰撞，
    # 达不到安全门的舒适间距语义。反应式门在 ttc≈1.8m 就触发 override，此时 d>1.6 仍在非重叠
    # 分支、能给出正常偏转；仅 d≤1.6 的已重叠情形会退化，由反应式距离风险兜底。
    orca_radius_m: float = 1.6


@dataclass(frozen=True)
class DroneSnapshot:
    drone_id: int
    position: Vector3
    target: Vector3 | None = None
    velocity: Vector3 | None = None
    speed_mps: float = 0.0
    status: str = "unknown"
    last_command_id: str | None = None
    timestamp_ms: int = 0

    @classmethod
    def from_telemetry(cls, payload: dict[str, Any]) -> DroneSnapshot:
        drone_id = int(payload["drone"])
        if "position" in payload:
            position = _as_vector3(payload["position"], "position")
        else:
            position = (float(payload["x"]), float(payload["y"]), float(payload["z"]))

        target = _as_vector3(payload["target"], "target") if payload.get("target") is not None else None
        velocity = _as_vector3(payload["velocity"], "velocity") if payload.get("velocity") is not None else None
        speed = float(payload.get("speed_mps") or 0.0)
        return cls(
            drone_id=drone_id,
            position=position,
            target=target,
            velocity=velocity,
            speed_mps=speed,
            status=str(payload.get("status") or "unknown"),
            last_command_id=payload.get("last_command_id"),
            timestamp_ms=int(payload.get("timestamp_ms") or 0),
        )


@dataclass(frozen=True)
class PairRisk:
    drone: int
    target_drone: int
    distance_m: float
    approach_mps: float
    ttc_sec: float | None
    risk: float
    target_position: Vector3 | None = None


@dataclass(frozen=True)
class SafetyDecision:
    drone: int
    mode: str
    risk_level: float
    target_drone: int | None
    reason: str | None
    command_id: str | None
    position: Vector3
    diverted_from: Vector3 | None
    safety_waypoint: Vector3 | None
    estimated_recovery: float
    timestamp_ms: int


def velocity_toward_target(snapshot: DroneSnapshot) -> Vector3:
    if snapshot.velocity is not None:
        return snapshot.velocity

    if snapshot.target is None or snapshot.speed_mps <= 0:
        return (0.0, 0.0, 0.0)

    delta = _sub(snapshot.target, snapshot.position)
    length = _norm(delta)
    if length == 0:
        return (0.0, 0.0, 0.0)

    scale = snapshot.speed_mps / length
    return (delta[0] * scale, delta[1] * scale, delta[2] * scale)


def pair_collision_risk(a: DroneSnapshot, b: DroneSnapshot, config: SafetyConfig) -> PairRisk:
    relative_position = _sub(b.position, a.position)
    separation = _norm(relative_position)
    if separation == 0:
        return PairRisk(a.drone_id, b.drone_id, 0.0, config.max_speed_mps, 0.0, 1.0, b.position)

    relative_velocity = _sub(velocity_toward_target(a), velocity_toward_target(b))
    approach_speed = _clip(_dot(relative_velocity, relative_position) / separation, 0.0, config.max_speed_mps)
    ttc = separation / approach_speed if approach_speed > 0 else None

    distance_risk = _clip(1.0 - separation / config.safe_distance_m)
    velocity_risk = _clip(approach_speed / config.max_speed_mps)
    ttc_risk = _clip(1.0 - (ttc or config.ttc_safe_sec) / config.ttc_safe_sec) if ttc is not None else 0.0
    risk = _clip(max(distance_risk, distance_risk * velocity_risk, ttc_risk))
    return PairRisk(a.drone_id, b.drone_id, separation, approach_speed, ttc, risk, b.position)


def safety_diversion_waypoint(snapshot: DroneSnapshot, pair: PairRisk, config: SafetyConfig) -> Vector3:
    if pair.target_position is None:
        return snapshot.position

    away = _sub(snapshot.position, pair.target_position)
    horizontal_away = (away[0], away[1], 0.0)
    if _norm(horizontal_away) == 0:
        horizontal_away = (0.0, 1.0 if snapshot.drone_id <= pair.target_drone else -1.0, 0.0)

    away_dir = _normalize(horizontal_away)
    lateral_dir = _normalize((-away_dir[1], away_dir[0], 0.0))
    horizontal_dir = _normalize(_add(_scale(away_dir, 0.45), _scale(lateral_dir, 0.90)))

    # 垂直避障：pair 内 ID 较小的向上、较大的向下，保证任意相遇对
    # 都能形成垂直分离，不依赖预先约定的高度层或奇偶性。
    vertical_sign = 1.0 if snapshot.drone_id < pair.target_drone else -1.0
    vertical_dir = (0.0, 0.0, vertical_sign * config.vertical_escape_gain)

    safety_dir = _normalize(_add(horizontal_dir, vertical_dir))
    if _norm(safety_dir) == 0:
        safety_dir = away_dir

    escape_distance = max(config.escape_distance_m, config.safe_distance_m * 0.75)
    waypoint = _add(snapshot.position, _scale(safety_dir, escape_distance))
    # 限制垂直避障后的高度在安全区间内，避免多次接管累积触地或过高。
    waypoint = (waypoint[0], waypoint[1], _clamp(waypoint[2], 0.5, 2.0))
    return waypoint


def _horizontal(v: Vector3) -> tuple[float, float]:
    return (v[0], v[1])


def orca_diversion_waypoint(
    snapshot: DroneSnapshot,
    snapshots: list[DroneSnapshot],
    config: SafetyConfig,
    worst: PairRisk | None,
) -> Vector3 | None:
    """用 ORCA 协同速度给 override/warning 生成分流航点（水平面）。

    与 safety_diversion_waypoint 的区别：避障方向不再「背离最坏单对」，而是在速度空间
    对所有邻居做互惠速度障碍求解，取「最接近期望速度、且对所有邻居都无碰撞」的协同速度
    方向——邻居间互惠分摊避让责任，避免密集场景多机各自躲反而互相挤。垂直分离仍叠加
    config.vertical_escape_gain（符号按 worst 对 ID 大小决定，与反应式一致）。

    若 ORCA 退化（无邻居或协同速度近零，极少数共点情形）返回 None，由调用方回退到
    safety_diversion_waypoint。
    """
    neighbors = [
        (_horizontal(o.position), _horizontal(velocity_toward_target(o)))
        for o in snapshots
        if o.drone_id != snapshot.drone_id
    ]
    if not neighbors:
        return None

    position = _horizontal(snapshot.position)
    current = _horizontal(velocity_toward_target(snapshot))
    v_new = orca_velocity(
        position,
        current,
        current,  # preferred = 当前期望速度（朝目标），ORCA 求最接近它的无碰撞速度
        neighbors,
        combined_radius=config.orca_radius_m,
        time_horizon=config.orca_time_horizon_sec,
        responsibility=config.orca_responsibility,
        max_speed=config.max_speed_mps,
    )

    horizontal_dir = (v_new[0], v_new[1], 0.0)
    if _norm(horizontal_dir) < 1e-6:
        return None

    vertical_sign = 1.0 if (worst is not None and snapshot.drone_id < worst.target_drone) else -1.0
    vertical_dir = (0.0, 0.0, vertical_sign * config.vertical_escape_gain)
    safety_dir = _normalize(_add(horizontal_dir, vertical_dir))

    escape_distance = max(config.escape_distance_m, config.safe_distance_m * 0.75)
    waypoint = _add(snapshot.position, _scale(safety_dir, escape_distance))
    waypoint = (waypoint[0], waypoint[1], _clamp(waypoint[2], 0.5, 2.0))
    return waypoint


class SafetyGate:
    def __init__(self, config: SafetyConfig | None = None):
        self.config = config or SafetyConfig()
        self._override_until: dict[int, float] = {}
        self._min_override_until: dict[int, float] = {}

    def evaluate(self, snapshots: list[DroneSnapshot], now: float | None = None) -> list[SafetyDecision]:
        now = time.monotonic() if now is None else now
        decisions: list[SafetyDecision] = []

        for snapshot in snapshots:
            worst = self._worst_pair(snapshot, snapshots)
            risk = worst.risk if worst else 0.0
            if self.config.orca_enabled and self.config.orca_early and worst is not None:
                # 预测式提前预警：把速度障碍的「到碰撞时间」叠加到现有风险（只增不减），
                # 在瞬时距离/TTC 尚未越限时就预判 τ 秒内的碰撞。
                peer = self._snapshot_by_id(snapshots, worst.target_drone)
                if peer is not None:
                    risk = max(
                        risk,
                        orca_pair_risk(
                            _horizontal(snapshot.position),
                            _horizontal(velocity_toward_target(snapshot)),
                            _horizontal(peer.position),
                            _horizontal(velocity_toward_target(peer)),
                            self.config.orca_radius_m,
                            self.config.orca_time_horizon_sec,
                        ),
                    )
            mode = self._mode(snapshot.drone_id, risk, now)
            reason = "collision_risk_exceeded" if mode == "override" else None
            safety_waypoint = None
            if mode in {"override", "warning"} and worst is not None:
                if self.config.orca_enabled:
                    safety_waypoint = orca_diversion_waypoint(snapshot, snapshots, self.config, worst) \
                        or safety_diversion_waypoint(snapshot, worst, self.config)
                else:
                    safety_waypoint = safety_diversion_waypoint(snapshot, worst, self.config)
            decisions.append(
                SafetyDecision(
                    drone=snapshot.drone_id,
                    mode=mode,
                    risk_level=risk,
                    target_drone=worst.target_drone if worst else None,
                    reason=reason,
                    command_id=snapshot.last_command_id,
                    position=snapshot.position,
                    diverted_from=snapshot.target,
                    safety_waypoint=safety_waypoint,
                    estimated_recovery=self.config.hold_sec if mode == "override" else 0.0,
                    timestamp_ms=int(time.time_ns() // 1_000_000),
                )
            )

        return decisions

    def _worst_pair(self, snapshot: DroneSnapshot, snapshots: list[DroneSnapshot]) -> PairRisk | None:
        pairs = [
            pair_collision_risk(snapshot, other, self.config)
            for other in snapshots
            if other.drone_id != snapshot.drone_id
        ]
        return max(pairs, key=lambda pair: pair.risk, default=None)

    @staticmethod
    def _snapshot_by_id(snapshots: list[DroneSnapshot], drone_id: int | None) -> DroneSnapshot | None:
        if drone_id is None:
            return None
        for snapshot in snapshots:
            if snapshot.drone_id == drone_id:
                return snapshot
        return None

    def _mode(self, drone_id: int, risk: float, now: float) -> str:
        held_until = self._override_until.get(drone_id, 0.0)
        min_held_until = self._min_override_until.get(drone_id, 0.0)
        if risk >= self.config.high_threshold:
            self._override_until[drone_id] = now + self.config.hold_sec
            if drone_id not in self._min_override_until:
                self._min_override_until[drone_id] = now + self.config.min_override_sec
            return "override"

        if now < held_until and risk >= self.config.release_threshold:
            return "override"

        # 最短接管时间：即使风险已下降或目标丢失，也继续 override。
        if now < min_held_until:
            return "override"

        self._override_until.pop(drone_id, None)
        self._min_override_until.pop(drone_id, None)

        if risk >= self.config.low_threshold:
            return "warning"

        return "normal"


def build_hover_command(decision: SafetyDecision) -> dict[str, Any]:
    return {
        "drone": decision.drone,
        "action": "hover",
        "priority": "safety",
        "confidence": 1.0,
        "command_id": f"safety-hover-{decision.timestamp_ms}-{decision.drone}",
        "reason": decision.reason or "safety_gate",
        "timestamp_ms": decision.timestamp_ms,
    }


def build_safety_command(decision: SafetyDecision) -> dict[str, Any]:
    if decision.safety_waypoint is None:
        return build_hover_command(decision)

    return {
        "drone": decision.drone,
        "action": "move_to",
        "target": list(decision.safety_waypoint),
        "waypoint": list(decision.safety_waypoint),
        "priority": "safety",
        "confidence": 1.0,
        "ttl_sec": max(0.5, decision.estimated_recovery),
        "command_id": f"safety-divert-{decision.timestamp_ms}-{decision.drone}",
        "reason": decision.reason or "safety_gate",
        "rationale": "minimum-intervention safety diversion",
        "timestamp_ms": decision.timestamp_ms,
    }


def build_override_event(decision: SafetyDecision) -> dict[str, Any]:
    return {
        "drone": decision.drone,
        "event": "safety_override",
        "event_name": "safety_override",
        "reason": decision.reason or "collision_risk_exceeded",
        "risk_level": round(decision.risk_level, 4),
        "target_drone": decision.target_drone,
        "diverted_from": list(decision.diverted_from) if decision.diverted_from is not None else None,
        "estimated_recovery": decision.estimated_recovery,
        "command_id": decision.command_id,
        "timestamp_ms": decision.timestamp_ms,
    }


def build_status_event(decision: SafetyDecision) -> dict[str, Any]:
    return {
        "drone": decision.drone,
        "event": "safety_status",
        "event_name": "safety_status",
        "mode": decision.mode,
        "risk_level": round(decision.risk_level, 4),
        "target_drone": decision.target_drone,
        "status": "safety_override" if decision.mode == "override" else decision.mode,
        "anomalies": ["collision_risk"] if decision.mode == "override" else [],
        "timestamp_ms": decision.timestamp_ms,
    }
