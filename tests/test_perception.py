"""
感知模块单元测试。

覆盖：
- 相机内参与像素/坐标往返。
- 地面求交与单目测深。
- 文档限定颜色的空中目标检测。
- 友方过滤。
- 全局 track 去重与临时 ID 分配。
"""

import math
import unittest

import cv2
import numpy as np

from swarm.perception import (
    AIRBORNE_HSV_RANGES,
    CameraMeta,
    FriendFilter,
    Observation,
    PerceptionConfig,
    SceneBounds,
    TrackRegistry,
    assign_slot_ids,
    camera_intrinsics,
    depth_from_apparent_size,
    detect_color_blobs,
    distance,
    perceive_frame,
    pixel_to_ray,
    position_from_pixel_depth,
    project_to_pixel,
    ray_to_ground,
)


def make_meta(
    *,
    drone_id: int = 1,
    width: int = 640,
    height: int = 360,
    fov_deg: float = 70.0,
    cam_pos: tuple[float, float, float] = (0.0, 0.0, 2.0),
    pitch_deg: float = 15.0,
) -> CameraMeta:
    """构造一个下俯 15 度的标准相机 meta，便于复现测试。"""
    forward = (math.cos(math.radians(pitch_deg)), 0.0, -math.sin(math.radians(pitch_deg)))
    up = (math.sin(math.radians(pitch_deg)), 0.0, math.cos(math.radians(pitch_deg)))
    return CameraMeta(
        drone_id=drone_id,
        frame_id=1,
        timestamp_ms=1,
        width=width,
        height=height,
        fov_deg=fov_deg,
        pitch_deg=pitch_deg,
        cam_pos_xyz=cam_pos,
        cam_forward_xyz=forward,
        cam_up_xyz=up,
    )


def make_observation(
    kind: str,
    position: tuple[float, float, float],
    *,
    pixel: tuple[float, float] = (0.0, 0.0),
    depth: float = 5.0,
) -> Observation:
    """构造一个测试用 Observation，只关注位置和去重逻辑。"""
    return Observation(
        kind=kind,
        position=position,
        pixel=pixel,
        camera_drone=1,
        timestamp_ms=1,
        depth=depth,
        confidence=100.0,
        bbox_width=20.0,
    )


class CameraMathTest(unittest.TestCase):
    """相机内参、坐标反解与测深参数测试。"""

    def test_camera_intrinsics_are_square_and_centered(self) -> None:
        """验证 fx=fy 且主点位于图像中心。"""
        intrinsics = camera_intrinsics(640, 360, 70.0)

        self.assertAlmostEqual(intrinsics.fx, intrinsics.fy, places=9)
        self.assertAlmostEqual(intrinsics.fx, 457.0, delta=1.0)
        self.assertEqual(intrinsics.cx, 319.5)
        self.assertEqual(intrinsics.cy, 179.5)

    def test_known_camera_pose_ground_reverse(self) -> None:
        """验证下俯 15 度相机对地面点的反解精度。"""
        intrinsics = camera_intrinsics(640, 360, 70.0)
        meta = make_meta()
        target = (5.0, 0.0, 0.0)

        pixel = project_to_pixel(
            target,
            meta.cam_pos_xyz,
            meta.cam_forward_xyz,
            meta.cam_up_xyz,
            intrinsics,
        )
        self.assertIsNotNone(pixel)

        ray = pixel_to_ray(pixel[0], pixel[1], intrinsics)
        inverse = ray_to_ground(
            meta.cam_pos_xyz,
            meta.cam_forward_xyz,
            meta.cam_up_xyz,
            ray,
        )
        self.assertIsNotNone(inverse)
        self.assertLess(distance(inverse, target), 1e-6)

    def test_airborne_position_from_apparent_depth(self) -> None:
        """验证离轴像素使用光轴距离反解三维位置。"""
        intrinsics = camera_intrinsics(640, 360, 70.0)
        meta = make_meta()
        point = (2.0, 1.0, 1.5)

        pixel = project_to_pixel(
            point,
            meta.cam_pos_xyz,
            meta.cam_forward_xyz,
            meta.cam_up_xyz,
            intrinsics,
        )
        self.assertIsNotNone(pixel)
        ray = pixel_to_ray(pixel[0], pixel[1], intrinsics)
        true_depth = sum(
            (point[i] - meta.cam_pos_xyz[i]) * meta.cam_forward_xyz[i]
            for i in range(3)
        )
        inverse = position_from_pixel_depth(
            meta.cam_pos_xyz,
            meta.cam_forward_xyz,
            meta.cam_up_xyz,
            ray,
            true_depth,
        )
        self.assertIsNotNone(inverse)
        self.assertLess(distance(inverse, point), 1e-6)

    def test_horizontal_or_upward_ray_has_no_ground_hit(self) -> None:
        """水平或向上射线不应产生地面交点。"""
        forward = (1.0, 0.0, 0.0)
        up = (0.0, 0.0, 1.0)
        cam_pos = (0.0, 0.0, 2.0)

        horizontal = ray_to_ground(cam_pos, forward, up, (0.0, 0.0, 1.0))
        upward = ray_to_ground(cam_pos, forward, up, (0.0, 1.0, 1.0))

        self.assertIsNone(horizontal)
        self.assertIsNone(upward)

    def test_depth_from_apparent_size_and_parameters(self) -> None:
        """验证默认测深和 depth_scale/depth_offset 修正。"""
        depth = depth_from_apparent_size(100.0, 0.35, 500.0)
        self.assertIsNotNone(depth)
        self.assertAlmostEqual(depth, 1.75, places=9)

        adjusted = depth_from_apparent_size(100.0, 0.35, 500.0, scale=2.0, offset_m=0.1)
        self.assertAlmostEqual(adjusted, 3.6, places=9)
        self.assertIsNone(depth_from_apparent_size(0.0, 0.35, 500.0))


class TargetColorDetectionTest(unittest.TestCase):
    """地面红色目标与文档颜色空中目标检测测试。"""

    def test_airborne_detection_uses_documented_colors(self) -> None:
        """蓝/绿/黄三种空中目标颜色应被识别，红色不参与空中检测。"""
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.rectangle(frame, (20, 30), (80, 90), (255, 0, 0), -1)     # 蓝
        cv2.rectangle(frame, (120, 30), (180, 90), (0, 255, 0), -1)   # 绿
        cv2.rectangle(frame, (220, 30), (280, 90), (0, 255, 255), -1) # 黄

        blobs = detect_color_blobs(frame, AIRBORNE_HSV_RANGES, min_area=100.0)

        self.assertGreaterEqual(len(blobs), 3)

    def test_perceive_frame_returns_ground_target_and_drone(self) -> None:
        """红色地面目标与蓝色空中无人机应被分别识别。"""
        meta = make_meta()
        intrinsics = camera_intrinsics(meta.width, meta.height, meta.fov_deg)
        target = (5.0, 0.0, 0.0)
        drone = (3.0, 1.5, 1.0)

        target_pixel = project_to_pixel(
            target,
            meta.cam_pos_xyz,
            meta.cam_forward_xyz,
            meta.cam_up_xyz,
            intrinsics,
        )
        drone_pixel = project_to_pixel(
            drone,
            meta.cam_pos_xyz,
            meta.cam_forward_xyz,
            meta.cam_up_xyz,
            intrinsics,
        )
        self.assertIsNotNone(target_pixel)
        self.assertIsNotNone(drone_pixel)

        target_depth = sum(
            (target[i] - meta.cam_pos_xyz[i]) * meta.cam_forward_xyz[i]
            for i in range(3)
        )
        drone_depth = sum(
            (drone[i] - meta.cam_pos_xyz[i]) * meta.cam_forward_xyz[i]
            for i in range(3)
        )
        target_width = max(8, int(intrinsics.fx * 0.30 / target_depth))
        drone_width = max(8, int(intrinsics.fx * 0.35 / drone_depth))

        frame = np.zeros((meta.height, meta.width, 3), dtype=np.uint8)
        cv2.circle(
            frame,
            (int(target_pixel[0]), int(target_pixel[1])),
            max(6, target_width // 2),
            (0, 0, 255),
            -1,
        )
        cv2.rectangle(
            frame,
            (int(drone_pixel[0]) - drone_width // 2, int(drone_pixel[1]) - drone_width // 2),
            (int(drone_pixel[0]) + drone_width // 2, int(drone_pixel[1]) + drone_width // 2),
            (255, 0, 0),
            -1,
        )

        observations = perceive_frame(frame, meta, PerceptionConfig(min_blob_area=20.0))
        kinds = {observation.kind for observation in observations}

        self.assertIn("target", kinds)
        self.assertIn("drone", kinds)
        target_observation = next(observation for observation in observations if observation.kind == "target")
        drone_observation = next(observation for observation in observations if observation.kind == "drone")
        self.assertLess(distance(target_observation.position, target), 0.3)
        self.assertLess(distance(drone_observation.position, drone), 0.5)


class FriendFilterTest(unittest.TestCase):
    """友方过滤测试。"""

    def test_friend_filter_removes_close_positions(self) -> None:
        """靠近友方 telemetry 的候选应被判定为己方。"""
        friend_filter = FriendFilter(radius_m=0.5)
        friend_filter.set_positions([(0.0, 0.0, 1.0)])

        self.assertTrue(friend_filter.is_friend((0.4, 0.0, 1.0)))
        self.assertFalse(friend_filter.is_friend((1.0, 0.0, 1.0)))


class TrackRegistryTest(unittest.TestCase):
    """全局去重与临时 ID 测试。"""

    def test_same_target_publishes_once_per_interval(self) -> None:
        """同一目标在发布间隔内不应重复输出。"""
        registry = TrackRegistry(
            match_radius_m=0.5,
            min_publish_interval_sec=0.2,
            min_move_m=0.05,
            stale_timeout_sec=1.0,
        )
        observation = make_observation("drone", (1.0, 0.0, 1.0))

        registry.update([observation], 0.0)
        self.assertEqual(len(registry.flush(0.0)), 1)

        registry.update([observation], 0.05)
        self.assertEqual(len(registry.flush(0.05)), 0)

        registry.update([observation], 0.25)
        self.assertEqual(len(registry.flush(0.25)), 1)

    def test_movement_publishes_immediately(self) -> None:
        """目标移动超过阈值时应立即输出新坐标。"""
        registry = TrackRegistry(
            match_radius_m=0.5,
            min_publish_interval_sec=0.2,
            min_move_m=0.05,
            stale_timeout_sec=1.0,
        )
        registry.update([make_observation("drone", (1.0, 0.0, 1.0))], 0.0)
        registry.flush(0.0)

        registry.update([make_observation("drone", (1.2, 0.0, 1.0))], 0.05)
        published = registry.flush(0.05)

        self.assertEqual(len(published), 1)
        self.assertAlmostEqual(published[0].position[0], 1.2)

    def test_two_close_non_friendly_objects_are_not_merged(self) -> None:
        """贴得很近的两个非友方对象应保留为两个独立 track。"""
        registry = TrackRegistry(
            match_radius_m=0.5,
            min_publish_interval_sec=0.2,
            min_move_m=0.05,
            stale_timeout_sec=1.0,
        )
        registry.update(
            [
                make_observation("drone", (1.0, 0.0, 1.0)),
                make_observation("drone", (1.3, 0.0, 1.0)),
            ],
            0.0,
        )

        published = registry.flush(0.0)

        self.assertEqual(len(published), 2)

    def test_assign_slot_ids_is_ordered_per_output(self) -> None:
        """临时 ID 按当前输出顺序分配，不保证跨帧稳定。"""
        observations = [
            make_observation("target", (5.0, 0.0, 0.0)),
            make_observation("drone", (1.0, 0.0, 1.0)),
        ]

        slots = assign_slot_ids(observations)

        self.assertEqual([slot for slot, _ in slots], [1, 2])


if __name__ == "__main__":
    unittest.main()
