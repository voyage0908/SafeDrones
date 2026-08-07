"""
无人机感知链路纯函数模块。

本模块只负责数学计算、颜色/前景检测、敌我过滤、跨帧/跨相机去重和临时 ID
分配，不依赖 MQTT，便于单元测试和后续替换不同相机输入源。

坐标约定：
- 项目坐标系为 [x, y, z]，其中 z 向上。
- 图像像素坐标为 (u, v)，u 向右，v 向下。
- Group 1 提供的 cam_forward/cam_up 已经是项目坐标系，脚本不再做 Unity 坐标映射。

目标解算策略：
- 地面目标明确为红色，先通过红色 HSV 检测得到地面目标候选。
- 空中目标颜色限定为文档调色板中的蓝/绿/黄，且保证不为红色。
- 蓝/绿/黄空中目标使用 object_width_m 求出相机前向距离 Zc。
- 红色目标结合射线与地平面交点求坐标，并校验高度接近地面。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import cv2
import numpy as np


Vector3 = tuple[float, float, float]
HsvRange = tuple[tuple[int, int, int], tuple[int, int, int]]

# 地面目标明确为红色。红色在 OpenCV HSV 中跨越 0 度附近，因此拆成两段；
# 饱和度下限提高到 100，避免低饱和的天空/阴影被误检。
RED_HSV_RANGES: tuple[HsvRange, ...] = (
    ((0, 130, 60), (10, 255, 255)),
    ((170, 130, 60), (180, 255, 255)),
)

# 空中目标颜色：文档 FallbackColor 中排除红色后的蓝/绿/黄。
# 饱和度下限 130：天空（S≈74）、地平线辉光（S≈105）和地面阴影（S≈67）
# 都被排除，机体纯色（S>150）即使在暗面也保留。
AIRBORNE_HSV_RANGES: tuple[HsvRange, ...] = (
    ((15, 130, 60), (40, 255, 255)),   # 黄
    ((60, 130, 60), (90, 255, 255)),   # 绿
    ((100, 130, 60), (130, 255, 255)), # 蓝
)


# 针孔相机内参。fx/fy 为焦距，cx/cy 为图像主点。
@dataclass(frozen=True)
class Intrinsics:
    fx: float
    fy: float
    cx: float
    cy: float


@dataclass(frozen=True)
class ForegroundBlob:
    """单个前景/颜色连通域的通用表示，本身不携带目标类型。"""
    center: tuple[float, float]
    bbox_width: float
    bbox_height: float
    area: float


# Group 1 相机 meta 的结构化表示。from_dict 负责把 MQTT JSON 转成强类型。
@dataclass(frozen=True)
class CameraMeta:
    drone_id: int
    frame_id: int
    timestamp_ms: int
    width: int
    height: int
    fov_deg: float
    pitch_deg: float
    cam_pos_xyz: Vector3
    cam_forward_xyz: Vector3
    cam_up_xyz: Vector3

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> CameraMeta:
        """把 Group 1 的 meta JSON 转换为 CameraMeta。"""
        def vector(key: str) -> Vector3:
            value = raw[key]
            if not isinstance(value, list | tuple) or len(value) != 3:
                raise ValueError(f"{key} must be a list of three numbers")
            return (float(value[0]), float(value[1]), float(value[2]))

        return cls(
            drone_id=int(raw["drone_id"]),
            frame_id=int(raw["frame_id"]),
            timestamp_ms=int(raw["timestamp_ms"]),
            width=int(raw["width"]),
            height=int(raw["height"]),
            fov_deg=float(raw["fov_deg"]),
            pitch_deg=float(raw.get("pitch_deg") or 0.0),
            cam_pos_xyz=vector("cam_pos_xyz"),
            cam_forward_xyz=vector("cam_forward_xyz"),
            cam_up_xyz=vector("cam_up_xyz"),
        )


@dataclass(frozen=True)
class SceneBounds:
    """场地范围，用于过滤明显不合理的目标坐标。"""
    min_x: float = -12.0
    max_x: float = 12.0
    min_y: float = -12.0
    max_y: float = 12.0
    min_z: float = 0.0
    max_z: float = 5.0


@dataclass(frozen=True)
class PerceptionConfig:
    """感知参数。object_width_m 是解坐标阶段对所有目标统一使用的宽度。"""
    # 解坐标前使用的通用物理宽度，不携带地面/空中的类型信息。
    object_width_m: float = 0.35
    # 深度修正参数：depth = scale * raw_depth + offset。
    depth_scale: float = 1.0
    depth_offset_m: float = 0.0
    # 地面高度与解出位置后的地面判定容差。
    ground_z: float = 0.0
    ground_tolerance_m: float = 0.35
    # 检测与空中判定参数。
    min_blob_area: float = 20.0
    min_drone_altitude: float = 0.2
    max_depth_m: float = 20.0
    scene_bounds: SceneBounds = SceneBounds()
    # 空中候选的仰角上限（度）：同高度飞行时，目标不可能出现在地平线以上。
    # 天边的天空碎块仰角为正，会被该门限排除。设为 90 则不限制。
    max_elevation_deg: float = 3.0
    # 是否启用"红色=地面目标"语义。关闭后红色并入空中候选，
    # 适用于没有地面目标、红蓝两队无人机互为敌方的设计。
    detect_ground_targets: bool = True


@dataclass(frozen=True)
class Observation:
    """一次感知候选：可能是地面目标，也可能是非友方无人机。"""
    kind: str
    position: Vector3
    pixel: tuple[float, float]
    camera_drone: int
    timestamp_ms: int
    depth: float
    confidence: float
    bbox_width: float


@dataclass
class Track:
    """内部跟踪对象，用于跨帧/跨相机去重，不直接作为输出 ID。"""
    track_id: int
    kind: str
    position: Vector3
    pixel: tuple[float, float]
    camera_drone: int
    timestamp_ms: int
    depth: float
    confidence: float
    bbox_width: float
    last_seen: float
    last_publish_time: float | None = None
    last_published_position: Vector3 | None = None
    updated: bool = False
    matched: bool = False


def _normalize(vector: Vector3) -> Vector3:
    """归一化三维向量；零向量会抛出 ValueError。"""
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        raise ValueError("cannot normalize a zero vector")
    return tuple(value / length for value in vector)  # type: ignore[return-value]


def _cross(a: Vector3, b: Vector3) -> Vector3:
    """三维向量叉积。"""
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vector3, b: Vector3) -> float:
    """三维向量点积。"""
    return sum(a_i * b_i for a_i, b_i in zip(a, b))


def _scale(vector: Vector3, factor: float) -> Vector3:
    """向量数乘。"""
    return tuple(value * factor for value in vector)  # type: ignore[return-value]


def _add(a: Vector3, b: Vector3) -> Vector3:
    """向量相加。"""
    return tuple(a_i + b_i for a_i, b_i in zip(a, b))  # type: ignore[return-value]


def distance(a: Vector3, b: Vector3) -> float:
    return math.dist(a, b)


def camera_intrinsics(width: int, height: int, fov_deg: float) -> Intrinsics:
    """根据水平 FOV 和图像尺寸计算针孔相机内参。"""
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if fov_deg <= 0 or fov_deg >= 180:
        raise ValueError("fov_deg must be in (0, 180)")

    focal = (width / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    return Intrinsics(
        fx=focal,
        fy=focal,
        cx=(width - 1) / 2.0,
        cy=(height - 1) / 2.0,
    )


def pixel_to_ray(u: float, v: float, intrinsics: Intrinsics) -> Vector3:
    """把像素坐标转成相机系单位射线方向。v 向下，所以 y 方向取负。"""
    dx = (u - intrinsics.cx) / intrinsics.fx
    dy = (intrinsics.cy - v) / intrinsics.fy
    length = math.hypot(dx, dy, 1.0)
    return (dx / length, dy / length, 1.0 / length)


def camera_basis(cam_forward: Vector3, cam_up: Vector3) -> tuple[Vector3, Vector3, Vector3]:
    """由 forward/up 构造正交基：right, up, forward。"""
    forward = _normalize(cam_forward)
    right = _normalize(_cross(forward, cam_up))
    up = _normalize(_cross(right, forward))
    return right, up, forward


def world_ray(cam_forward: Vector3, cam_up: Vector3, ray: Vector3) -> Vector3:
    """把相机系射线转换到项目世界坐标系。"""
    right, up, forward = camera_basis(cam_forward, cam_up)
    return _add(_scale(right, ray[0]), _add(_scale(up, ray[1]), _scale(forward, ray[2])))


def ray_to_ground(
    cam_pos: Vector3,
    cam_forward: Vector3,
    cam_up: Vector3,
    ray: Vector3,
    ground_z: float = 0.0,
) -> Vector3 | None:
    """射线与 ground_z 平面求交；射线水平或向上时返回 None。"""
    direction = world_ray(cam_forward, cam_up, ray)
    if direction[2] >= -1e-9:
        return None

    t = (ground_z - cam_pos[2]) / direction[2]
    if t <= 0:
        return None
    return _add(cam_pos, _scale(direction, t))


def depth_from_apparent_size(
    pixel_width: float,
    real_width_m: float,
    fx: float,
    scale: float = 1.0,
    offset_m: float = 0.0,
) -> float | None:
    """由画面宽度反推相机前向距离 Zc，支持 scale/offset 修正。"""
    if pixel_width <= 0 or real_width_m <= 0 or fx <= 0:
        return None
    raw_depth = fx * real_width_m / pixel_width
    corrected_depth = scale * raw_depth + offset_m
    return corrected_depth if corrected_depth > 0 else None


def position_from_pixel_depth(
    cam_pos: Vector3,
    cam_forward: Vector3,
    cam_up: Vector3,
    ray: Vector3,
    depth: float,
) -> Vector3 | None:
    """使用光轴距离 depth 恢复世界坐标。

    相机坐标系中目标的坐标为：
    Xc = dx * depth
    Yc = dy * depth
    Zc = depth

    然后通过相机正交基转换到项目世界坐标系。离轴像素通过
    ray_forward 修正，使 Zc 能正确展开为射线实际长度。
    """
    if depth is None or depth <= 0:
        return None
    direction = world_ray(cam_forward, cam_up, ray)
    right, up, forward = camera_basis(cam_forward, cam_up)
    ray_forward = _dot(direction, forward)
    if ray_forward <= 0:
        return None
    return _add(cam_pos, _scale(direction, depth / ray_forward))


def project_to_pixel(
    point: Vector3,
    cam_pos: Vector3,
    cam_forward: Vector3,
    cam_up: Vector3,
    intrinsics: Intrinsics,
) -> tuple[float, float] | None:
    """把项目坐标投影回像素，主要用于测试和人工校验。"""
    right, up, forward = camera_basis(cam_forward, cam_up)
    local = tuple(point[i] - cam_pos[i] for i in range(3))  # type: ignore[assignment]
    denominator = _dot(local, forward)
    if denominator <= 0:
        return None
    u = intrinsics.cx + intrinsics.fx * _dot(local, right) / denominator
    v = intrinsics.cy - intrinsics.fy * _dot(local, up) / denominator
    return (u, v)


def _contours_to_blobs(contours: Iterable[object], min_area: float) -> list[ForegroundBlob]:
    """把 OpenCV 轮廓转换为 ForegroundBlob，并按面积从大到小排序。"""
    blobs: list[ForegroundBlob] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        center = (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])
        x, y, width, height = cv2.boundingRect(contour)
        blobs.append(ForegroundBlob(center=center, bbox_width=float(width), bbox_height=float(height), area=area))
    blobs.sort(key=lambda blob: blob.area, reverse=True)
    return blobs


def detect_color_blobs(
    frame_bgr: np.ndarray,
    hsv_ranges: tuple[HsvRange, ...],
    min_area: float = 0.0,
) -> list[ForegroundBlob]:
    """按 HSV 范围检测目标候选，可检测地面红色或空中蓝/绿/黄。"""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lower, upper in hsv_ranges:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, np.array(lower), np.array(upper)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return _contours_to_blobs(contours, min_area)


def _inside_bounds(position: Vector3, bounds: SceneBounds) -> bool:
    """判断位置是否落在场地范围内。"""
    return (
        bounds.min_x <= position[0] <= bounds.max_x
        and bounds.min_y <= position[1] <= bounds.max_y
        and bounds.min_z <= position[2] <= bounds.max_z
    )


def perceive_frame(
    frame_bgr: np.ndarray,
    meta: CameraMeta,
    config: PerceptionConfig | None = None,
) -> list[Observation]:
    """处理一帧：地面目标为红色，空中目标限定为文档中的蓝/绿/黄。"""
    config = config or PerceptionConfig()
    intrinsics = camera_intrinsics(meta.width, meta.height, meta.fov_deg)
    observations: list[Observation] = []

    if config.detect_ground_targets:
        # 地面目标明确为红色。红色 blob 再通过地面交点与高度约束确认。
        red_blobs = detect_color_blobs(frame_bgr, RED_HSV_RANGES, config.min_blob_area)
        for blob in red_blobs:
            ray = pixel_to_ray(blob.center[0], blob.center[1], intrinsics)
            ground_point = ray_to_ground(
                meta.cam_pos_xyz,
                meta.cam_forward_xyz,
                meta.cam_up_xyz,
                ray,
                config.ground_z,
            )
            object_depth = depth_from_apparent_size(
                blob.bbox_width,
                config.object_width_m,
                intrinsics.fx,
                config.depth_scale,
                config.depth_offset_m,
            )
            if object_depth is None:
                continue
            object_position = position_from_pixel_depth(
                meta.cam_pos_xyz,
                meta.cam_forward_xyz,
                meta.cam_up_xyz,
                ray,
                object_depth,
            )
            if object_position is None:
                continue

            # 红色代表地面目标候选；只有落在地面附近才输出为 target。
            if (
                ground_point is not None
                and _inside_bounds(ground_point, config.scene_bounds)
                and (
                    object_position[2] < config.min_drone_altitude
                    or abs(object_position[2] - config.ground_z) <= config.ground_tolerance_m
                )
            ):
                observations.append(
                    Observation(
                        kind="target",
                        position=ground_point,
                        pixel=blob.center,
                        camera_drone=meta.drone_id,
                        timestamp_ms=meta.timestamp_ms,
                        depth=object_depth,
                        confidence=blob.area / (1.0 + object_depth),
                        bbox_width=blob.bbox_width,
                    )
                )

    # 空中目标：默认限定蓝/绿/黄；关闭地面目标语义后红色并入空中候选
    # （红蓝两队无人机互为敌方的设计）。
    airborne_ranges = AIRBORNE_HSV_RANGES
    if not config.detect_ground_targets:
        airborne_ranges = AIRBORNE_HSV_RANGES + RED_HSV_RANGES
    airborne_blobs = detect_color_blobs(frame_bgr, airborne_ranges, config.min_blob_area)
    max_ray_z = math.sin(math.radians(config.max_elevation_deg))
    for blob in airborne_blobs:
        ray = pixel_to_ray(blob.center[0], blob.center[1], intrinsics)
        world_direction = world_ray(meta.cam_forward_xyz, meta.cam_up_xyz, ray)
        if world_direction[2] > max_ray_z:
            continue  # 仰角超过上限（天空碎块），不可能是同高度无人机
        object_depth = depth_from_apparent_size(
            blob.bbox_width,
            config.object_width_m,
            intrinsics.fx,
            config.depth_scale,
            config.depth_offset_m,
        )
        if object_depth is None:
            continue
        object_position = position_from_pixel_depth(
            meta.cam_pos_xyz,
            meta.cam_forward_xyz,
            meta.cam_up_xyz,
            ray,
            object_depth,
        )
        if object_position is None:
            continue

        if (
            object_position[2] >= config.min_drone_altitude
            and object_depth <= config.max_depth_m
            and _inside_bounds(object_position, config.scene_bounds)
        ):
            observations.append(
                Observation(
                    kind="drone",
                    position=object_position,
                    pixel=blob.center,
                    camera_drone=meta.drone_id,
                    timestamp_ms=meta.timestamp_ms,
                    depth=object_depth,
                    confidence=blob.area / (1.0 + object_depth),
                    bbox_width=blob.bbox_width,
                )
            )

    return observations


class FriendFilter:
    """根据己方/友方 telemetry 坐标过滤候选，避免把己方识别成敌方。"""

    def __init__(self, radius_m: float = 0.5):
        if radius_m < 0:
            raise ValueError("radius_m must be non-negative")
        self.radius_m = radius_m
        self.positions: list[Vector3] = []

    def set_positions(self, positions: Iterable[Vector3]) -> None:
        """更新己方/友方坐标集合。"""
        self.positions = [tuple(position) for position in positions]  # type: ignore[misc]

    def is_friend(self, position: Vector3) -> bool:
        """判断候选位置是否与任一己方/友方坐标过近。"""
        return any(distance(position, friend) <= self.radius_m for friend in self.positions)


class TrackRegistry:
    """全局去重器：合并所有相机结果，内部 track 只用于去重。"""

    def __init__(
        self,
        match_radius_m: float = 0.75,
        min_publish_interval_sec: float = 0.2,
        min_move_m: float = 0.05,
        stale_timeout_sec: float = 1.0,
    ):
        self.match_radius_m = match_radius_m
        self.min_publish_interval_sec = min_publish_interval_sec
        self.min_move_m = min_move_m
        self.stale_timeout_sec = stale_timeout_sec
        self._tracks: dict[int, Track] = {}
        self._next_track_id = 1

    def update(self, observations: Iterable[Observation], now: float) -> None:
        """把新观测匹配到已有 track；未匹配的观测创建新 track。"""
        self._prune(now)
        for track in self._tracks.values():
            track.updated = False
            track.matched = False

        ordered = sorted(observations, key=lambda observation: observation.confidence, reverse=True)
        for observation in ordered:
            candidates = [
                track
                for track in self._tracks.values()
                if track.kind == observation.kind
                and not track.matched
                and distance(track.position, observation.position) <= self.match_radius_m
            ]
            if candidates:
                best = min(candidates, key=lambda track: distance(track.position, observation.position))
                self._assign(best, observation, now)
            else:
                track_id = self._next_track_id
                self._next_track_id += 1
                track = Track(
                    track_id=track_id,
                    kind=observation.kind,
                    position=observation.position,
                    pixel=observation.pixel,
                    camera_drone=observation.camera_drone,
                    timestamp_ms=observation.timestamp_ms,
                    depth=observation.depth,
                    confidence=observation.confidence,
                    bbox_width=observation.bbox_width,
                    last_seen=now,
                )
                track.updated = True
                track.matched = True
                self._tracks[track_id] = track

    def flush(self, now: float) -> list[Observation]:
        """输出到发布条件的当前 track；同一 track 按间隔/位移控制频率。"""
        output: list[Observation] = []
        for track in list(self._tracks.values()):
            if not track.updated:
                continue
            if not self._should_publish(track, now):
                continue
            output.append(self._to_observation(track))
            track.last_publish_time = now
            track.last_published_position = track.position
        return output

    def _should_publish(self, track: Track, now: float) -> bool:
        """满足发布频率或目标移动达到阈值时才发布。"""
        if track.last_publish_time is None:
            return True
        if now - track.last_publish_time >= self.min_publish_interval_sec:
            return True
        if track.last_published_position is not None:
            return distance(track.position, track.last_published_position) >= self.min_move_m
        return False

    def _assign(self, track: Track, observation: Observation, now: float) -> None:
        """把观测合并到已有 track，并保留更高置信度。"""
        track.position = observation.position
        track.pixel = observation.pixel
        track.camera_drone = observation.camera_drone
        track.timestamp_ms = observation.timestamp_ms
        track.depth = observation.depth
        track.bbox_width = observation.bbox_width
        track.confidence = max(track.confidence, observation.confidence)
        track.last_seen = now
        track.updated = True
        track.matched = True

    def _prune(self, now: float) -> None:
        """删除超过 stale_timeout 未更新的 track，避免发布旧坐标。"""
        stale_ids = [
            track_id
            for track_id, track in self._tracks.items()
            if now - track.last_seen > self.stale_timeout_sec
        ]
        for track_id in stale_ids:
            self._tracks.pop(track_id, None)

    @staticmethod
    def _to_observation(track: Track) -> Observation:
        return Observation(
            kind=track.kind,
            position=track.position,
            pixel=track.pixel,
            camera_drone=track.camera_drone,
            timestamp_ms=track.timestamp_ms,
            depth=track.depth,
            confidence=track.confidence,
            bbox_width=track.bbox_width,
        )


def assign_slot_ids(observations: Iterable[Observation]) -> list[tuple[int, Observation]]:
    """为当前去重结果分配临时输出 ID 1..N，不保证跨帧稳定。"""
    return [(index, observation) for index, observation in enumerate(observations, start=1)]
