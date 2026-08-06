<<<<<<< HEAD
"""
相机感知 MQTT 运行入口。

订阅 Group 1 的 frame/meta/groundtruth/telemetry，按以下流程工作：
1. 解码 JPEG 帧。
2. 使用 swarm.perception 做目标/无人机检测和坐标反解。
3. 使用己方 telemetry 过滤友方目标。
4. 使用全局 TrackRegistry 做跨帧/跨相机去重。
5. 为当前去重结果分配临时 ID 1..N 并发布。
"""

from __future__ import annotations

import argparse
from collections import deque
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

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from swarm.perception import (
    CameraMeta,
    FriendFilter,
    Observation,
    PerceptionConfig,
    SceneBounds,
    TrackRegistry,
    assign_slot_ids,
    distance,
    perceive_frame,
)


LOGGER = logging.getLogger("sim_cam_perception")


def now_ms() -> int:
    """返回当前 Unix 毫秒时间戳。"""
    return time.time_ns() // 1_000_000


def build_mqtt_client(client_id: str):
    """创建 paho MQTT 客户端，兼容不同 paho 版本。"""
    try:
        import paho.mqtt.client as mqtt
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency: paho-mqtt. Install it in your active conda env with "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def topic_drone_id(topic: str) -> int | None:
    """从 swarm/drone/{id}/... 或 swarm/cam/drone/{id}/... 中提取无人机 ID。"""
    parts = topic.split("/")
    for index, part in enumerate(parts):
        if part == "drone" and index + 1 < len(parts) and parts[index + 1].isdigit():
            return int(parts[index + 1])
    return None


def parse_friend_ids(raw: str) -> set[int]:
    """解析 --friend-drone-ids；空字符串表示使用全部 telemetry。"""
    if not raw:
        return set()
    try:
        return {int(item) for item in raw.split(",") if item.strip()}
    except ValueError as exc:
        raise SystemExit("--friend-drone-ids must be a comma-separated list of integers") from exc


def as_position(payload: dict[str, Any]) -> tuple[float, float, float] | None:
    """从 telemetry/groundtruth JSON 中兼容提取 position 或 x/y/z。"""
    raw = payload.get("position")
    if isinstance(raw, list | tuple) and len(raw) == 3:
        return (float(raw[0]), float(raw[1]), float(raw[2]))
    try:
        return (
            float(payload["x"]),
            float(payload["y"]),
            float(payload["z"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


class ValidationStats:
    """滚动误差统计，仅用于 --validate 模式。"""

    def __init__(self) -> None:
        self.samples: deque[tuple[str, float]] = deque(maxlen=2000)
        self.last_print = 0.0

    def add(self, kind: str, error_m: float) -> None:
        self.samples.append((kind, error_m))

    def maybe_print(self, now: float, interval_sec: float = 5.0) -> None:
        if now - self.last_print < interval_sec:
            return
        self.last_print = now
        for kind in ("target", "drone"):
            errors = [error for sample_kind, error in self.samples if sample_kind == kind]
            if not errors:
                continue
            mean_error = sum(errors) / len(errors)
            max_error = max(errors)
            print(
                f"[validate] {kind} count={len(errors)} "
                f"mean_error_m={mean_error:.4f} max_error_m={max_error:.4f}"
            )


class SimCamPerceptionApp:
    """MQTT 订阅、检测、去重、发布和抽帧保存的完整运行器。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        qos: int,
        config: PerceptionConfig,
        friend_ids: set[int],
        friend_exclusion_radius_m: float,
        match_radius_m: float,
        min_publish_interval_sec: float,
        min_move_m: float,
        stale_timeout_sec: float,
        validate: bool,
        save_frames: str | None,
        save_every: int,
    ):
        self.host = host
        self.port = port
        self.qos = qos
        self.config = config
        self.friend_ids = friend_ids
        self.validate = validate
        self.save_frames = Path(save_frames) if save_frames else None
        self.save_every = max(1, save_every)

        # 以下状态均为运行期缓存，不持久化到磁盘。
        self.connected = threading.Event()
        self.meta: dict[int, CameraMeta] = {}
        self.telemetry: dict[int, dict[str, Any]] = {}
        self.groundtruth: dict[int, dict[str, Any]] = {}
        self.frame_counts: dict[int, int] = {}
        self.friend_filter = FriendFilter(friend_exclusion_radius_m)
        self.registry = TrackRegistry(
            match_radius_m=match_radius_m,
            min_publish_interval_sec=min_publish_interval_sec,
            min_move_m=min_move_m,
            stale_timeout_sec=stale_timeout_sec,
        )
        self.validation = ValidationStats()
        self.client = build_mqtt_client("sim-cam-perception")

    def start(self) -> None:
        """连接 MQTT broker 并启动后台 loop。"""
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.host, self.port, keepalive=30)
        self.client.loop_start()
        if not self.connected.wait(timeout=5):
            raise SystemExit("failed to connect to MQTT broker")

    def stop(self) -> None:
        """停止 MQTT loop 并断开连接。"""
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        """连接成功后订阅 Group 1 输入 topic 与遥测 topic。"""
        LOGGER.info("connected to MQTT broker %s:%s with result=%s", self.host, self.port, reason_code)
        client.subscribe("swarm/cam/drone/+/frame", qos=self.qos)
        client.subscribe("swarm/cam/drone/+/meta", qos=self.qos)
        client.subscribe("swarm/target/+/groundtruth", qos=self.qos)
        client.subscribe("swarm/drone/+/telemetry", qos=self.qos)
        self.connected.set()

    def _on_message(self, client: Any, userdata: Any, message: Any) -> None:
        """按 topic 分发 frame/meta/groundtruth/telemetry 消息。"""
        topic = message.topic
        if topic.endswith("/frame"):
            self._handle_frame(topic, message.payload)
            return

        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOGGER.warning("ignored invalid JSON on %s: %s", topic, exc)
            return

        if topic.endswith("/meta"):
            self._handle_meta(payload)
        elif topic.endswith("/groundtruth"):
            self._handle_groundtruth(payload)
        elif topic.endswith("/telemetry"):
            self._handle_telemetry(payload)

    def _handle_meta(self, payload: dict[str, Any]) -> None:
        """缓存每台相机的最新 meta；frame 到来时使用该 meta。"""
        try:
            meta = CameraMeta.from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("ignored invalid camera meta: %s", exc)
            return
        self.meta[meta.drone_id] = meta

    def _handle_groundtruth(self, payload: dict[str, Any]) -> None:
        """缓存地面目标真值，只用于 --validate 误差统计。"""
        try:
            target_id = int(payload["target"])
            position = as_position(payload)
            if position is None:
                raise ValueError("position is invalid")
            self.groundtruth[target_id] = {
                "target": target_id,
                "position": position,
                "timestamp_ms": int(payload.get("timestamp_ms") or 0),
            }
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("ignored invalid groundtruth: %s", exc)

    def _handle_telemetry(self, payload: dict[str, Any]) -> None:
        """缓存 telemetry，并刷新己方/友方过滤坐标。"""
        try:
            drone_id = int(payload["drone"])
            position = as_position(payload)
            if position is None:
                raise ValueError("position is invalid")
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("ignored invalid telemetry: %s", exc)
            return

        self.telemetry[drone_id] = payload
        self._refresh_friend_positions()

    def _refresh_friend_positions(self) -> None:
        """根据 friend_ids 决定哪些 telemetry 坐标用于友方过滤。"""
        positions = []
        for drone_id, payload in self.telemetry.items():
            if not self.friend_ids or drone_id in self.friend_ids:
                position = as_position(payload)
                if position is not None:
                    positions.append(position)
        self.friend_filter.set_positions(positions)

    def _handle_frame(self, topic: str, payload: bytes) -> None:
        """处理一帧 JPEG：检测、过滤、去重、发布、可选落盘。"""
        camera_drone = topic_drone_id(topic)
        if camera_drone is None:
            LOGGER.warning("ignored frame with missing drone id: %s", topic)
            return
        meta = self.meta.get(camera_drone)
        if meta is None:
            LOGGER.debug("skipping frame before camera meta arrives: %s", topic)
            return

        # JPEG 原始字节先解码为 BGR 帧。
        frame = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            LOGGER.warning("failed to decode JPEG frame from %s", topic)
            return

        # 检测结果先做己方过滤，再进入全局去重。
        observations = perceive_frame(frame, meta, self.config)
        filtered = [
            observation
            for observation in observations
            if not self.friend_filter.is_friend(observation.position)
        ]

        now = time.monotonic()
        self.registry.update(filtered, now)
        published = self.registry.flush(now)
        self._publish_observations(published)
        self._save_sampled_frame(frame, meta, filtered, published)
        self.validation.maybe_print(now)

    def _publish_observations(self, observations: list[Observation]) -> None:
        """按当前槽位 ID 发布目标/无人机位置。"""
        for slot_id, observation in assign_slot_ids(observations):
            # 目标与非友方无人机共用临时槽位 ID，ID 不表示持久身份。
            common = {
                "target": slot_id,
                "position": [round(value, 4) for value in observation.position],
                "pixel": [round(observation.pixel[0], 2), round(observation.pixel[1], 2)],
                "source": "sim_cam",
                "camera_drone": observation.camera_drone,
                "timestamp_ms": observation.timestamp_ms,
            }
            if observation.kind == "target":
                topic = f"swarm/target/{slot_id}/position"
                payload = json.dumps(common, separators=(",", ":"))
            else:
                # 无人机额外带 estimated_depth，供消费端评估估深误差。
                topic = f"swarm/drone_seen/{slot_id}/position"
                payload = json.dumps(
                    {
                        **common,
                        "estimated_depth": round(observation.depth, 4),
                    },
                    separators=(",", ":"),
                )
            self.client.publish(topic, payload=payload, qos=self.qos, retain=False)
            LOGGER.info(
                "published %s slot=%s position=%s camera=%s",
                observation.kind,
                slot_id,
                [round(value, 3) for value in observation.position],
                observation.camera_drone,
            )
            if self.validate:
                self._record_validation(observation)

    def _record_validation(self, observation: Observation) -> None:
        """按最近邻真值计算目标平面误差或无人机三维误差。"""
        if observation.kind == "target":
            true_positions = [
                payload["position"]
                for payload in self.groundtruth.values()
                if payload.get("position") is not None
            ]
            if not true_positions:
                return
            true_position = min(true_positions, key=lambda position: distance(observation.position, position))
            # 地面目标按文档只统计 x/y 平面误差。
            plane_error = math.hypot(
                observation.position[0] - true_position[0],
                observation.position[1] - true_position[1],
            )
            self.validation.add("target", plane_error)
            return

        true_positions = [
            as_position(payload)
            for payload in self.telemetry.values()
        ]
        true_positions = [position for position in true_positions if position is not None]
        if not true_positions:
            return
        true_position = min(true_positions, key=lambda position: distance(observation.position, position))
        # 空中无人机使用完整三维误差。
        self.validation.add("drone", distance(observation.position, true_position))

    def _save_sampled_frame(
        self,
        frame: np.ndarray,
        meta: CameraMeta,
        filtered: list[Observation],
        published: list[Observation],
    ) -> None:
        """抽样保存带检测框的画面，供人工检查识别效果。"""
        if self.save_frames is None:
            return
        self.frame_counts[meta.drone_id] = self.frame_counts.get(meta.drone_id, 0) + 1
        if self.frame_counts[meta.drone_id] % self.save_every != 0:
            return

        published_by_key = {
            (candidate.kind, candidate.pixel): slot_id
            for slot_id, candidate in assign_slot_ids(published)
        }
        # 绘制检测框和当前临时槽位 ID，便于人工检查。
        annotated = frame.copy()
        for observation in filtered:
            slot = published_by_key.get((observation.kind, observation.pixel))
            # 绘制颜色仅用于人工查看，不代表 HSV 判定结果。
            color = (0, 255, 255) if observation.kind == "target" else (255, 0, 255)
            label = f"{'T' if observation.kind == 'target' else 'D'}{slot or '?'}"
            u, v = int(observation.pixel[0]), int(observation.pixel[1])
            width = max(8, int(observation.bbox_width))
            cv2.rectangle(
                annotated,
                (u - width // 2, v - width // 2),
                (u + width // 2, v + width // 2),
                color,
                2,
            )
            cv2.putText(annotated, label, (u - width // 2, v - width // 2 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        output_dir = self.save_frames
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"cam_{meta.drone_id}_frame_{self.frame_counts[meta.drone_id]:06d}.jpg"
        cv2.imwrite(str(output_path), annotated)
        LOGGER.info("saved annotated frame: %s", output_path)


def parse_args() -> argparse.Namespace:
    """解析感知运行脚本参数。"""
    parser = argparse.ArgumentParser(description="Run the camera perception MQTT bridge.")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=0)
    parser.add_argument("--validate", action="store_true", help="Print rolling errors against groundtruth/telemetry.")
    parser.add_argument("--save-frames", default=None, help="Directory for sampled annotated frames.")
    parser.add_argument("--save-every", type=int, default=20)
    parser.add_argument(
        "--object-width",
        type=float,
        default=0.35,
        help="解坐标前对所有目标统一使用的物理宽度，不区分目标类型。",
    )
    parser.add_argument("--depth-scale", type=float, default=1.0)
    parser.add_argument("--depth-offset", type=float, default=0.0)
    parser.add_argument("--ground-z", type=float, default=0.0)
    parser.add_argument(
        "--ground-tolerance",
        type=float,
        default=0.35,
        help="解出位置后的地面高度判定容差。",
    )
    parser.add_argument("--min-blob-area", type=float, default=20.0)
    parser.add_argument("--min-drone-altitude", type=float, default=0.2)
    parser.add_argument("--max-depth", type=float, default=20.0)
    parser.add_argument("--friend-drone-ids", default="", help="Comma-separated friend IDs; empty means all telemetry.")
    parser.add_argument("--friend-exclusion-radius", type=float, default=0.5)
    parser.add_argument("--match-radius", type=float, default=0.75)
    parser.add_argument("--min-publish-interval", type=float, default=0.2)
    parser.add_argument("--min-move", type=float, default=0.05)
    parser.add_argument("--stale-timeout", type=float, default=1.0)
    parser.add_argument("--scene-min", nargs=3, type=float, default=[-12.0, -12.0, 0.0])
    parser.add_argument("--scene-max", nargs=3, type=float, default=[12.0, 12.0, 5.0])
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    """启动感知 MQTT 服务。"""
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.min_blob_area <= 0:
        raise SystemExit("--min-blob-area must be positive")
    if args.max_depth <= 0:
        raise SystemExit("--max-depth must be positive")
    if args.object_width <= 0:
        raise SystemExit("--object-width must be positive")
    if args.min_publish_interval <= 0:
        raise SystemExit("--min-publish-interval must be positive")

    bounds = SceneBounds(
        min_x=args.scene_min[0],
        max_x=args.scene_max[0],
        min_y=args.scene_min[1],
        max_y=args.scene_max[1],
        min_z=args.scene_min[2],
        max_z=args.scene_max[2],
    )
    config = PerceptionConfig(
        object_width_m=args.object_width,
        depth_scale=args.depth_scale,
        depth_offset_m=args.depth_offset,
        ground_z=args.ground_z,
        ground_tolerance_m=args.ground_tolerance,
        min_blob_area=args.min_blob_area,
        min_drone_altitude=args.min_drone_altitude,
        max_depth_m=args.max_depth,
        scene_bounds=bounds,
    )
    app = SimCamPerceptionApp(
        host=args.host,
        port=args.port,
        qos=args.qos,
        config=config,
        friend_ids=parse_friend_ids(args.friend_drone_ids),
        friend_exclusion_radius_m=args.friend_exclusion_radius,
        match_radius_m=args.match_radius,
        min_publish_interval_sec=args.min_publish_interval,
        min_move_m=args.min_move,
        stale_timeout_sec=args.stale_timeout,
        validate=args.validate,
        save_frames=args.save_frames,
        save_every=args.save_every,
    )

    try:
        app.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        LOGGER.info("stopping sim camera perception")
    finally:
        app.stop()


if __name__ == "__main__":
    main()
=======
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
>>>>>>> origin/feature/stereo-camera-group2
