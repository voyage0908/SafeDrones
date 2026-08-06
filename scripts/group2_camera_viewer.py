#!/usr/bin/env python
"""
Group 2 — Python MQTT camera subscriber (联合验收用)

Supports mono + stereo (left/right) drone camera feeds.

Usage:
  conda activate eai-swarm
  python scripts/group2_camera_viewer.py
  python scripts/group2_camera_viewer.py --drone-id 1
  python scripts/group2_camera_viewer.py --save-frames
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import paho.mqtt.client as mqtt

BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1883


class Group2Viewer:
    def __init__(self, drone_id: int = 0, save_frames: bool = False):
        self.target_drone = drone_id
        self.save_frames = save_frames
        # Per-drone state: mono jpeg, left jpeg, right jpeg, meta, counters
        self.views: dict = defaultdict(lambda: {
            "jpeg": None,          # mono/center frame
            "jpeg_left": None,     # stereo left eye
            "jpeg_right": None,    # stereo right eye
            "meta": {},
            "count": 0,
        })
        self.groundtruth: list = []
        self.running = True
        self.connected = False
        self.msg_total = 0
        self.maincam_jpeg: Optional[bytes] = None
        self.maincam_count: int = 0

    # ── MQTT callbacks ──────────────────────────────────────────────────

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self.connected = True
            print("[Group2] Connected to broker!")
            # Mono / center camera
            client.subscribe("swarm/cam/drone/+/frame", qos=0)
            # Stereo left/right
            client.subscribe("swarm/cam/drone/+/left/frame", qos=0)
            client.subscribe("swarm/cam/drone/+/right/frame", qos=0)
            # Metadata
            client.subscribe("swarm/cam/drone/+/meta", qos=0)
            # Main camera
            client.subscribe("swarm/cam/main/frame", qos=0)
            # Ground truth
            client.subscribe("swarm/target/+/groundtruth", qos=0)
            print("[Group2] Subscribed to mono + stereo camera, meta, main, groundtruth topics.")
        else:
            print(f"[Group2] CONNECTION FAILED! rc={reason_code}")

    def on_message(self, client, userdata, msg):
        self.msg_total += 1
        topic = msg.topic

        # Handle main camera separately (no drone ID in topic)
        if "/main/frame" in topic:
            self.maincam_jpeg = msg.payload
            self.maincam_count += 1
            return

        drone_id = self._drone_id(topic)
        if drone_id <= 0:
            return

        try:
            if "/left/frame" in topic:
                self.views[drone_id]["jpeg_left"] = msg.payload
            elif "/right/frame" in topic:
                self.views[drone_id]["jpeg_right"] = msg.payload
            elif "/frame" in topic:
                # Matches both swarm/cam/drone/{id}/frame and stereo-specific topics
                # (the more specific /left/ and /right/ are handled above)
                self.views[drone_id]["jpeg"] = msg.payload
                self.views[drone_id]["count"] += 1
            elif "/meta" in topic:
                self.views[drone_id]["meta"] = json.loads(msg.payload.decode())
            elif "/groundtruth" in topic:
                self.groundtruth.append(json.loads(msg.payload.decode()))
                if len(self.groundtruth) > 200:
                    self.groundtruth = self.groundtruth[-100:]
        except Exception:
            pass

    # ── Main loop ───────────────────────────────────────────────────────

    def run(self):
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="group2-viewer-" + str(int(time.time()))
        )
        client.on_connect = self.on_connect
        client.on_message = self.on_message
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
        client.loop_start()
        print(f"[Group2] Connecting to {BROKER_HOST}:{BROKER_PORT}...")

        save_dir = Path("logs/frames") if self.save_frames else None
        if save_dir:
            save_dir.mkdir(parents=True, exist_ok=True)

        try:
            while self.running:
                self._render(save_dir)
                key = cv2.waitKey(1) & 0xFF
                if key == 27:  # ESC
                    self.running = False
        finally:
            client.loop_stop()
            client.disconnect()
            cv2.destroyAllWindows()
            print(f"[Group2] Stopped. Total messages received: {self.msg_total}")

    # ── Render ──────────────────────────────────────────────────────────

    DISPLAY_WIDTH = 1280  # target pixel width for the stacked view

    def _render(self, save_dir):
        # Collect all panels: Main Camera + drone views (mono or stereo)
        panels = []  # list of (label, ndarray)

        # --- 1. Main Camera ---
        if self.maincam_jpeg is not None:
            arr = np.frombuffer(self.maincam_jpeg, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                cv2.putText(frame, f"MAIN CAMERA  |  Frames: {self.maincam_count}",
                            (8, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
                if self.groundtruth:
                    gt = self.groundtruth[-1]
                    gt_pos = gt.get("position", [0, 0, 0])
                    cv2.putText(frame, f"Target GT: ({gt_pos[0]:.1f}, {gt_pos[1]:.1f}, {gt_pos[2]:.1f})",
                                (8, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                panels.append(("MAIN CAMERA", frame))

        # --- 2. Drone FPVs (mono or stereo) ---
        for did in sorted(self.views.keys()):
            v = self.views[did]
            meta = v["meta"]
            is_stereo = meta.get("is_stereo", False)

            # Try stereo first, then mono
            if is_stereo and v["jpeg_left"] is not None and v["jpeg_right"] is not None:
                arr_l = np.frombuffer(v["jpeg_left"], dtype=np.uint8)
                arr_r = np.frombuffer(v["jpeg_right"], dtype=np.uint8)
                frame_l = cv2.imdecode(arr_l, cv2.IMREAD_COLOR)
                frame_r = cv2.imdecode(arr_r, cv2.IMREAD_COLOR)

                if frame_l is None or frame_r is None:
                    continue

                # Equalise heights
                h = min(frame_l.shape[0], frame_r.shape[0])
                frame_l = frame_l[:h, :, :]
                frame_r = frame_r[:h, :, :]

                frame = np.hstack([frame_l, frame_r])

                # Divider + L/R labels
                mid_x = frame_l.shape[1]
                cv2.line(frame, (mid_x, 0), (mid_x, h), (0, 255, 255), 1)
                cv2.putText(frame, "L", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                cv2.putText(frame, "R", (mid_x + 6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            elif v["jpeg"] is not None:
                arr = np.frombuffer(v["jpeg"], dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if frame is None:
                    continue
            else:
                continue

            # Overlay info
            pos = meta.get("cam_pos_xyz", [0, 0, 0])
            fov = meta.get("fov_deg", "?")
            baseline = meta.get("baseline_m", 0)

            if is_stereo:
                label = f"Drone {did}  STEREO  FOV:{fov}  BL:{baseline:.3f}m"
            else:
                label = f"Drone {did}  FOV:{fov}"

            h = frame.shape[0]
            cv2.putText(frame, label,
                        (8, h - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
            cv2.putText(frame, f"Pos: ({pos[0]:.1f},{pos[1]:.1f},{pos[2]:.1f})  |  Frames: {v['count']}",
                        (8, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)

            if is_stereo:
                label = f"DRONE {did} (STEREO L|R)"
            else:
                label = f"DRONE {did}"
            panels.append((label, frame))

            # Save periodically
            if save_dir and v["count"] % 30 == 0:
                ts = int(time.time() * 1000)
                path = save_dir / f"drone{did}_{ts:013d}.jpg"
                cv2.imwrite(str(path), frame)

        # --- 3. Waiting / no frames ---
        if not panels:
            canvas = np.zeros((360, 640, 3), dtype=np.uint8)
            lines = [
                "Waiting for frames...",
                f"MQTT: {'CONNECTED' if self.connected else 'NOT CONNECTED'}",
                f"Messages received: {self.msg_total}",
                f"Active drones: {sum(1 for d in self.views.values() if d['jpeg'] or d['jpeg_left'])}",
                "",
                "Check: Unity Play mode? Broker running?",
                f"Broker: {BROKER_HOST}:{BROKER_PORT}",
            ]
            for i, line in enumerate(lines):
                cv2.putText(canvas, line, (20, 40 + i * 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.imshow("Group2 Viewer", canvas)
            return

        # --- 4. Single-drone pop-out mode ---
        if self.target_drone > 0:
            for label, frame in panels:
                if f"Drone {self.target_drone}" in label or str(self.target_drone) in label:
                    cv2.imshow(f"Group2 Viewer", frame)
                    return

        # --- 5. Scale all panels to uniform width, stack vertically ---
        rows = []
        for label, frame in panels:
            h, w = frame.shape[:2]
            if w != self.DISPLAY_WIDTH:
                new_h = int(h * self.DISPLAY_WIDTH / w)
                frame = cv2.resize(frame, (self.DISPLAY_WIDTH, new_h))
            # Add a thin separator bar at the top of each panel with its label
            header_h = 22
            header = np.zeros((header_h, self.DISPLAY_WIDTH, 3), dtype=np.uint8)
            header[:] = (40, 40, 40)
            cv2.putText(header, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            rows.append(header)
            rows.append(frame)

        final = np.vstack(rows)
        cv2.imshow("Group2 Viewer", final)

    @staticmethod
    def _drone_id(topic):
        parts = topic.split("/")
        for i, p in enumerate(parts):
            if p == "drone" and i + 1 < len(parts):
                try:
                    return int(parts[i + 1])
                except ValueError:
                    pass
        return -1


def main():
    parser = argparse.ArgumentParser(description="Group 2 MQTT Camera Viewer (mono + stereo)")
    parser.add_argument("--drone-id", type=int, default=0)
    parser.add_argument("--save-frames", action="store_true")
    parser.add_argument("--broker-host", default=BROKER_HOST)
    parser.add_argument("--broker-port", type=int, default=BROKER_PORT)
    args = parser.parse_args()

    print("=" * 55)
    print("  Group 2 — Camera Frame Subscriber")
    print(f"  Broker: {args.broker_host}:{args.broker_port}")
    print(f"  Supports: Mono + Stereo (left/right) cameras")
    print(f"  Press ESC to quit")
    print("=" * 55)

    viewer = Group2Viewer(drone_id=args.drone_id, save_frames=args.save_frames)
    viewer.run()


if __name__ == "__main__":
    main()
