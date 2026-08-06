from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np

Intrinsics = tuple[float, float, float, float]
Vector3 = tuple[float, float, float]


def camera_intrinsics(width: int, height: int, fov_deg: float) -> Intrinsics:
    if width <= 0 or height <= 0:
        raise ValueError('image dimensions must be positive')
    if not 0.0 < fov_deg < 180.0:
        raise ValueError('fov_deg must be between 0 and 180')

    fy = height / (2.0 * math.tan(math.radians(fov_deg) / 2.0))
    return (fy, fy, width / 2.0, height / 2.0)


def pixel_to_ray(u: float, v: float, intrinsics: Intrinsics) -> Vector3:
    fx, fy, cx, cy = intrinsics
    if fx <= 0.0 or fy <= 0.0:
        raise ValueError('focal lengths must be positive')

    ray = np.array(((u - cx) / fx, (cy - v) / fy, 1.0), dtype=float)
    ray /= np.linalg.norm(ray)
    return tuple(float(value) for value in ray)  # type: ignore[return-value]


def ray_to_world(cam_forward: Any, cam_up: Any, ray: Any) -> Vector3:
    '''Convert an x-right, y-up, z-forward camera ray to a world unit ray.'''
    forward = _unit_vector(cam_forward, 'cam_forward')
    up = np.array(cam_up, dtype=float, copy=True)
    if up.shape != (3,) or not np.all(np.isfinite(up)):
        raise ValueError('cam_up must contain three finite numbers')
    up -= forward * np.dot(up, forward)
    up = _unit_vector(up, 'cam_up')
    right = np.cross(forward, up)

    camera_ray = np.asarray(ray, dtype=float)
    if camera_ray.shape != (3,) or not np.all(np.isfinite(camera_ray)):
        raise ValueError('ray must contain three finite numbers')
    world_ray = camera_ray[0] * right + camera_ray[1] * up + camera_ray[2] * forward
    world_ray = _unit_vector(world_ray, 'ray')
    return tuple(float(value) for value in world_ray)  # type: ignore[return-value]


def ray_to_ground(
    cam_pos: Any,
    cam_forward: Any,
    cam_up: Any,
    ray: Any,
    ground_z: float = 0.0,
) -> Vector3 | None:
    position = np.asarray(cam_pos, dtype=float)
    if position.shape != (3,) or not np.all(np.isfinite(position)):
        raise ValueError('cam_pos must contain three finite numbers')
    if not math.isfinite(ground_z):
        raise ValueError('ground_z must be finite')

    world_ray = np.asarray(ray_to_world(cam_forward, cam_up, ray))
    if world_ray[2] >= -1e-12:
        return None

    distance = (ground_z - position[2]) / world_ray[2]
    if distance < 0.0:
        return None
    intersection = position + distance * world_ray
    return tuple(float(value) for value in intersection)  # type: ignore[return-value]


def depth_from_apparent_size(pixel_width: float, real_width_m: float, fx: float) -> float:
    if not all(math.isfinite(value) and value > 0.0 for value in (pixel_width, real_width_m, fx)):
        raise ValueError('pixel_width, real_width_m and fx must be positive finite values')
    return fx * real_width_m / pixel_width


def detect_color_blob(frame_bgr: np.ndarray, hsv_range: Any) -> tuple[tuple[int, int], int, float] | None:
    if not isinstance(frame_bgr, np.ndarray) or frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
        raise ValueError('frame_bgr must be an HxWx3 image')

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for lower, upper in _hsv_ranges(hsv_range):
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lower, upper))

    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [contour for contour in contours if cv2.contourArea(contour) > 0.0]
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    moments = cv2.moments(contour)
    if moments['m00'] == 0.0:
        return None
    _, _, width, _ = cv2.boundingRect(contour)
    centroid = (
        int(round(moments['m10'] / moments['m00'])),
        int(round(moments['m01'] / moments['m00'])),
    )
    return centroid, int(width), float(cv2.contourArea(contour))


def _unit_vector(value: Any, name: str) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f'{name} must contain three finite numbers')
    length = float(np.linalg.norm(vector))
    if length <= 1e-12:
        raise ValueError(f'{name} must be non-zero')
    return vector / length


def _hsv_ranges(value: Any) -> list[tuple[np.ndarray, np.ndarray]]:
    if len(value) == 2 and _is_hsv_triplet(value[0]) and _is_hsv_triplet(value[1]):
        value = (value,)

    ranges = []
    for lower, upper in value:
        lower_array = np.asarray(lower, dtype=np.uint8)
        upper_array = np.asarray(upper, dtype=np.uint8)
        if lower_array.shape != (3,) or upper_array.shape != (3,):
            raise ValueError('each HSV range must contain lower and upper triplets')
        ranges.append((lower_array, upper_array))
    if not ranges:
        raise ValueError('hsv_range must not be empty')
    return ranges


def _is_hsv_triplet(value: Any) -> bool:
    try:
        return len(value) == 3 and all(np.isscalar(component) for component in value)
    except TypeError:
        return False
