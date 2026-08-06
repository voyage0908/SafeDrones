from __future__ import annotations

import argparse
from collections import defaultdict, deque
import json
import logging
import math
from pathlib import Path
import sys
import threading
import time
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.perception import (
    camera_intrinsics,
    depth_from_apparent_size,
    detect_color_blob,
    pixel_to_ray,
    ray_to_ground,
    ray_to_world,
)


LOGGER = logging.getLogger('sim_cam_perception')
RED_HSV = (((0, 100, 80), (10, 255, 255)), ((170, 100, 80), (179, 255, 255)))
FALLBACK_HSV = (
    ((95, 80, 60), (120, 255, 255)),
    RED_HSV,
    ((60, 60, 50), (90, 255, 255)),
    ((15, 80, 80), (35, 255, 255)),
)
DRONE_WIDTH_M = 0.35
FRAME_MAX_AGE_SEC = 0.5
META_MAX_AGE_SEC = 2.0
TRUTH_MAX_AGE_SEC = 2.0


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def build_mqtt_client(client_id: str):
    try:
        import paho.mqtt.client as mqtt
    except ModuleNotFoundError as exc:
        raise SystemExit('Missing dependency: paho-mqtt. Run python -m pip install -r requirements.txt.') from exc

    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


class SimCamPerception:
    def __init__(self, client: Any, qos: int, validate: bool, save_frames: Path | None):
        self.client = client
        self.qos = qos
        self.validate = validate
        self.save_frames = save_frames
        self.lock = threading.Lock()
        self.sequence = 0
        self.frames: dict[int, tuple[int, float, bytes]] = {}
        self.metas: dict[int, tuple[float, dict[str, Any]]] = {}
        self.telemetry: dict[int, tuple[float, dict[str, Any]]] = {}
        self.groundtruth: tuple[float, dict[str, Any]] | None = None
        self.processed: dict[int, int] = {}
        self.last_saved: dict[int, float] = {}
        self.errors: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=100))
        self.last_report = time.monotonic()

        if save_frames is not None:
            save_frames.mkdir(parents=True, exist_ok=True)

    def on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        LOGGER.info('connected to MQTT broker with result=%s', reason_code)
        client.subscribe(
            [
                ('swarm/cam/drone/+/frame', self.qos),
                ('swarm/cam/drone/+/meta', self.qos),
                ('swarm/target/1/groundtruth', self.qos),
                ('swarm/drone/+/telemetry', self.qos),
            ]
        )

    def on_message(self, client: Any, userdata: Any, message: Any) -> None:
        received = time.monotonic()
        parts = message.topic.split('/')

        if len(parts) == 5 and parts[:3] == ['swarm', 'cam', 'drone']:
            try:
                camera_id = int(parts[3])
            except ValueError:
                return
            if parts[4] == 'frame':
                with self.lock:
                    self.sequence += 1
                    self.frames[camera_id] = (self.sequence, received, bytes(message.payload))
                return
            if parts[4] != 'meta':
                return
        else:
            camera_id = None

        try:
            payload = json.loads(message.payload.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            LOGGER.warning('ignored invalid JSON on %s', message.topic)
            return
        if not isinstance(payload, dict):
            return

        with self.lock:
            if camera_id is not None:
                self.metas[camera_id] = (received, payload)
            elif message.topic == 'swarm/target/1/groundtruth':
                self.groundtruth = (received, payload)
            elif len(parts) == 4 and parts[:2] == ['swarm', 'drone'] and parts[3] == 'telemetry':
                try:
                    drone_id = int(parts[2])
                except ValueError:
                    return
                self.telemetry[drone_id] = (received, payload)

    def process_latest(self) -> None:
        current = time.monotonic()
        with self.lock:
            frames = list(self.frames.items())
            metas = dict(self.metas)
            telemetry = dict(self.telemetry)
            groundtruth = self.groundtruth

        for camera_id, (sequence, frame_received, jpeg) in frames:
            if self.processed.get(camera_id) == sequence:
                continue
            if current - frame_received > FRAME_MAX_AGE_SEC:
                self.processed[camera_id] = sequence
                continue

            meta_entry = metas.get(camera_id)
            if meta_entry is None:
                continue
            self.processed[camera_id] = sequence
            meta_received, meta = meta_entry
            if current - meta_received > META_MAX_AGE_SEC:
                continue
            if abs(frame_received - meta_received) > META_MAX_AGE_SEC:
                continue

            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                LOGGER.warning('failed to decode JPEG from camera %s', camera_id)
                continue

            values = self._meta_values(camera_id, meta, frame)
            if values is None:
                continue
            cam_pos, cam_forward, cam_up, intrinsics = values

            self._detect_target(
                camera_id,
                frame,
                cam_pos,
                cam_forward,
                cam_up,
                intrinsics,
                groundtruth,
            )
            self._detect_drones(
                camera_id,
                frame,
                cam_pos,
                cam_forward,
                cam_up,
                intrinsics,
                telemetry,
            )
            self._save_frame(camera_id, jpeg, current)

        self._report_validation(current)

    @staticmethod
    def _meta_values(
        camera_id: int,
        meta: dict[str, Any],
        frame: np.ndarray,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], Any] | None:
        try:
            if int(meta['drone_id']) != camera_id or int(meta['frame_id']) < 0:
                return None
            timestamp = int(meta['timestamp_ms'])
            width = int(meta['width'])
            height = int(meta['height'])
            fov_deg = float(meta['fov_deg'])
            cam_pos = _vector3(meta['cam_pos_xyz'])
            cam_forward = _vector3(meta['cam_forward_xyz'])
            cam_up = _vector3(meta['cam_up_xyz'])
        except (KeyError, TypeError, ValueError):
            return None

        if abs(now_ms() - timestamp) > 5_000:
            return None
        if width != frame.shape[1] or height != frame.shape[0]:
            return None
        try:
            intrinsics = camera_intrinsics(width, height, fov_deg)
            ray_to_world(cam_forward, cam_up, (0.0, 0.0, 1.0))
        except ValueError:
            return None
        return cam_pos, cam_forward, cam_up, intrinsics

    def _detect_target(
        self,
        camera_id: int,
        frame: np.ndarray,
        cam_pos: tuple[float, float, float],
        cam_forward: tuple[float, float, float],
        cam_up: tuple[float, float, float],
        intrinsics: Any,
        groundtruth: tuple[float, dict[str, Any]] | None,
    ) -> None:
        blob = detect_color_blob(frame, RED_HSV)
        if blob is None:
            return

        pixel, _, _ = blob
        ray = pixel_to_ray(*pixel, intrinsics)
        position = ray_to_ground(cam_pos, cam_forward, cam_up, ray)
        if position is None:
            return

        timestamp = now_ms()
        self._publish(
            'swarm/target/1/position',
            {
                'target': 1,
                'position': list(position),
                'pixel': list(pixel),
                'source': 'sim_cam',
                'camera_drone': camera_id,
                'timestamp_ms': timestamp,
            },
        )
        if self.validate and groundtruth is not None:
            received, payload = groundtruth
            truth = _payload_position(payload)
            if time.monotonic() - received <= TRUTH_MAX_AGE_SEC and truth is not None:
                self.errors['target/1'].append(math.dist(position, truth))

    def _detect_drones(
        self,
        camera_id: int,
        frame: np.ndarray,
        cam_pos: tuple[float, float, float],
        cam_forward: tuple[float, float, float],
        cam_up: tuple[float, float, float],
        intrinsics: Any,
        telemetry: dict[int, tuple[float, dict[str, Any]]],
    ) -> None:
        current = time.monotonic()
        active: dict[int, tuple[float, float, float]] = {}
        for drone_id, (received, payload) in telemetry.items():
            position = _payload_position(payload)
            if current - received <= TRUTH_MAX_AGE_SEC and position is not None:
                active[drone_id] = position

        owners: dict[int, list[int]] = defaultdict(list)
        for drone_id in active:
            if drone_id != camera_id:
                owners[abs(drone_id - 1) % len(FALLBACK_HSV)].append(drone_id)

        fx = intrinsics[0]
        for palette_index, drone_ids in owners.items():
            # ponytail: colors repeat every four IDs; add temporal association if duplicates must coexist.
            if len(drone_ids) != 1:
                continue
            drone_id = drone_ids[0]
            blob = detect_color_blob(frame, FALLBACK_HSV[palette_index])
            if blob is None:
                continue

            pixel, pixel_width, _ = blob
            camera_ray = pixel_to_ray(*pixel, intrinsics)
            depth = depth_from_apparent_size(pixel_width, DRONE_WIDTH_M, fx)
            world_ray = ray_to_world(cam_forward, cam_up, camera_ray)
            distance = depth / camera_ray[2]
            position = tuple(cam_pos[index] + distance * world_ray[index] for index in range(3))
            self._publish(
                f'swarm/drone_seen/{drone_id}/position',
                {
                    'target': drone_id,
                    'position': list(position),
                    'pixel': list(pixel),
                    'estimated_depth': depth,
                    'source': 'sim_cam',
                    'camera_drone': camera_id,
                    'timestamp_ms': now_ms(),
                },
            )
            if self.validate:
                self.errors[f'drone/{drone_id}'].append(math.dist(position, active[drone_id]))

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.client.publish(
            topic,
            payload=json.dumps(payload, separators=(',', ':')),
            qos=self.qos,
            retain=False,
        )

    def _save_frame(self, camera_id: int, jpeg: bytes, current: float) -> None:
        if self.save_frames is None or current - self.last_saved.get(camera_id, 0.0) < 1.0:
            return
        self.last_saved[camera_id] = current
        path = self.save_frames / f'drone_{camera_id}_{now_ms()}.jpg'
        try:
            path.write_bytes(jpeg)
        except OSError as exc:
            LOGGER.warning('failed to save %s: %s', path, exc)

    def _report_validation(self, current: float) -> None:
        if not self.validate or current - self.last_report < 1.0:
            return
        self.last_report = current
        for label, errors in sorted(self.errors.items()):
            if errors:
                LOGGER.info(
                    'validation %s samples=%d mean_error_m=%.3f max_error_m=%.3f',
                    label,
                    len(errors),
                    sum(errors) / len(errors),
                    max(errors),
                )


def _vector3(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('expected a three-value vector')
    vector = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in vector):
        raise ValueError('vector components must be finite')
    return vector


def _payload_position(payload: dict[str, Any]) -> tuple[float, float, float] | None:
    value = payload.get('position')
    if value is None and all(axis in payload for axis in ('x', 'y', 'z')):
        value = [payload['x'], payload['y'], payload['z']]
    try:
        return _vector3(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description='Detect simulated camera targets and publish world positions.')
    parser.add_argument('--host', default='localhost')
    parser.add_argument('--port', type=int, default=1883)
    parser.add_argument('--qos', type=int, choices=[0, 1, 2], default=0)
    parser.add_argument('--validate', action='store_true')
    parser.add_argument(
        '--save-frames',
        nargs='?',
        const=Path('logs/sim_cam'),
        type=Path,
        metavar='DIR',
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    client = build_mqtt_client(f'sim-cam-perception-{time.time_ns()}')
    app = SimCamPerception(client, args.qos, args.validate, args.save_frames)
    client.on_connect = app.on_connect
    client.on_message = app.on_message
    client.connect(args.host, args.port, keepalive=30)
    client.loop_start()

    try:
        while True:
            app.process_latest()
            time.sleep(0.005)
    except KeyboardInterrupt:
        LOGGER.info('stopping simulated camera perception')
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == '__main__':
    main()
