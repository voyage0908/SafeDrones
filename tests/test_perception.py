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
