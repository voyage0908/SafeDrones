<<<<<<< HEAD
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
=======
import json
import math
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from swarm.perception import (
    camera_intrinsics,
    depth_from_apparent_size,
    detect_color_blob,
    pixel_to_ray,
    ray_to_ground,
)
from scripts.sim_cam_perception import SimCamPerception, now_ms


class FakeClient:
    def __init__(self) -> None:
        self.published = []

    def publish(self, topic, payload, qos, retain):
        self.published.append((topic, json.loads(payload), qos, retain))


class PerceptionTest(unittest.TestCase):
    def test_intrinsics_and_center_pixel_ray(self) -> None:
        intrinsics = camera_intrinsics(640, 360, 70.0)
        expected_focal_length = 360.0 / (2.0 * math.tan(math.radians(70.0) / 2.0))

        self.assertEqual(intrinsics, (expected_focal_length, expected_focal_length, 320.0, 180.0))
        np.testing.assert_allclose(pixel_to_ray(320.0, 180.0, intrinsics), (0.0, 0.0, 1.0))

    def test_projection_and_ground_back_projection_round_trip(self) -> None:
        intrinsics = camera_intrinsics(640, 360, 70.0)
        pitch = math.radians(15.0)
        cam_pos = (1.0, -2.0, 3.0)
        forward = (math.cos(pitch), 0.0, -math.sin(pitch))
        up = (math.sin(pitch), 0.0, math.cos(pitch))
        target = np.array((7.0, -1.5, 0.0))

        relative = target - np.array(cam_pos)
        right = np.cross(forward, up)
        x_camera = float(np.dot(relative, right))
        y_camera = float(np.dot(relative, up))
        z_camera = float(np.dot(relative, forward))
        fx, fy, cx, cy = intrinsics
        u = fx * x_camera / z_camera + cx
        v = cy - fy * y_camera / z_camera

        result = ray_to_ground(cam_pos, forward, up, pixel_to_ray(u, v, intrinsics))
        self.assertIsNotNone(result)
        self.assertLess(math.dist(result, target), 1e-6)

    def test_downward_camera_recovers_front_ground_point(self) -> None:
        intrinsics = camera_intrinsics(640, 360, 70.0)
        pitch = math.radians(15.0)
        cam_pos = (0.0, 0.0, 2.0)
        forward = (math.cos(pitch), 0.0, -math.sin(pitch))
        up = (math.sin(pitch), 0.0, math.cos(pitch))
        target = np.array((5.0, 0.0, 0.0))
        relative = target - np.array(cam_pos)
        fx, fy, cx, cy = intrinsics
        u = fx * float(np.dot(relative, np.cross(forward, up))) / float(np.dot(relative, forward)) + cx
        v = cy - fy * float(np.dot(relative, up)) / float(np.dot(relative, forward))

        result = ray_to_ground(cam_pos, forward, up, pixel_to_ray(u, v, intrinsics))
        self.assertIsNotNone(result)
        self.assertLess(math.dist(result, target), 1e-6)

    def test_horizontal_and_upward_rays_do_not_hit_ground(self) -> None:
        self.assertIsNone(ray_to_ground((0, 0, 2), (1, 0, 0), (0, 0, 1), (0, 0, 1)))
        self.assertIsNone(ray_to_ground((0, 0, 2), (0, 0, 1), (1, 0, 0), (0, 0, 1)))

    def test_apparent_size_depth_round_trip(self) -> None:
        fx = 420.0
        real_width = 0.35
        expected_depth = 4.2
        pixel_width = fx * real_width / expected_depth

        self.assertLess(abs(depth_from_apparent_size(pixel_width, real_width, fx) - expected_depth), 1e-6)

    def test_detects_largest_color_blob_in_synthetic_image(self) -> None:
        frame = np.zeros((100, 120, 3), dtype=np.uint8)
        cv2.rectangle(frame, (20, 30), (50, 60), (0, 255, 0), thickness=-1)
        frame[5, 5] = (0, 255, 0)

        result = detect_color_blob(frame, ((35, 100, 100), (85, 255, 255)))

        self.assertIsNotNone(result)
        centroid, width, area = result
        self.assertEqual(centroid, (35, 45))
        self.assertEqual(width, 31)
        self.assertEqual(area, 900.0)

    def test_latest_jpeg_and_meta_publish_exact_target_schema(self) -> None:
        client = FakeClient()
        app = SimCamPerception(client, qos=0, validate=False, save_frames=None)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.rectangle(frame, (40, 40), (60, 60), (0, 0, 255), thickness=-1)
        encoded, jpeg = cv2.imencode('.jpg', frame)
        self.assertTrue(encoded)
        meta = {
            'drone_id': 1,
            'frame_id': 1,
            'timestamp_ms': now_ms(),
            'width': 100,
            'height': 100,
            'fov_deg': 70.0,
            'pitch_deg': 90.0,
            'cam_pos_xyz': [0.0, 0.0, 2.0],
            'cam_forward_xyz': [0.0, 0.0, -1.0],
            'cam_up_xyz': [0.0, 1.0, 0.0],
        }
        app.on_message(
            None,
            None,
            SimpleNamespace(topic='swarm/cam/drone/1/meta', payload=json.dumps(meta).encode()),
        )
        app.on_message(
            None,
            None,
            SimpleNamespace(topic='swarm/cam/drone/1/frame', payload=jpeg.tobytes()),
        )

        app.process_latest()

        self.assertEqual(len(client.published), 1)
        topic, payload, qos, retain = client.published[0]
        self.assertEqual(topic, 'swarm/target/1/position')
        self.assertEqual(
            set(payload),
            {'target', 'position', 'pixel', 'source', 'camera_drone', 'timestamp_ms'},
        )
        self.assertEqual(qos, 0)
        self.assertFalse(retain)


if __name__ == '__main__':
    unittest.main()
>>>>>>> origin/feature/stereo-camera-group2
