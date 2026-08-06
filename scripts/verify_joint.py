"""Joint acceptance test: Python subscribes to Group 1 camera frames and
decodes JPEG, validates image content, and optionally saves a sample."""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import paho.mqtt.client as mqtt

BROKER = "127.0.0.1"
PORT = 1883
WAIT_SECS = 6

frames: dict[int, list[tuple[float, bytes]]] = defaultdict(list)
metas: dict[int, dict] = {}


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[OK] Connected to broker, subscribing...")
    client.subscribe("swarm/cam/drone/+/frame", qos=0)
    client.subscribe("swarm/cam/drone/+/meta", qos=0)


def on_message(client, userdata, msg):
    topic = msg.topic
    drone_id = int(topic.split("/")[3]) if "/drone/" in topic else 0

    if "/frame" in topic:
        frames[drone_id].append((time.time(), msg.payload))
    elif "/meta" in topic:
        metas[drone_id] = json.loads(msg.payload.decode())


def main():
    print("=" * 60)
    print("Group 1 + Group 2 Joint Acceptance: JPEG Decode Test")
    print(f"Listening for {WAIT_SECS}s on {BROKER}:{PORT}...")
    print("=" * 60)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="joint-verify")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.loop_start()
    time.sleep(WAIT_SECS)
    client.loop_stop()
    client.disconnect()

    if not frames:
        print("\n[FAIL] No frames received — is Unity in Play mode?")
        return 1

    print(f"\nReceived frames from {len(frames)} drone(s)\n")
    all_ok = True

    for drone_id in sorted(frames.keys()):
        frame_list = frames[drone_id]
        print(f"--- Drone {drone_id} ---")
        print(f"  Frames received: {len(frame_list)}")

        if not frame_list:
            print(f"  [FAIL] No frames")
            all_ok = False
            continue

        # Decode first frame
        jpeg_bytes = frame_list[0][1]
        print(f"  JPEG size: {len(jpeg_bytes)} bytes")

        # Check JPEG header
        is_jpeg = jpeg_bytes[:2] == b'\xff\xd8' and jpeg_bytes[-2:] == b'\xff\xd9'
        print(f"  JPEG magic: {'YES' if is_jpeg else 'NO'}  (FF D8 ... FF D9)")

        # Decode with OpenCV
        np_arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if img is None:
            print(f"  [FAIL] cv2.imdecode returned None — not a valid image")
            all_ok = False
            continue

        h, w, c = img.shape
        print(f"  Decoded: {w}x{h}  channels={c}  dtype={img.dtype}")

        # Check it's a reasonable image (not all black, not all noise)
        mean_val = float(np.mean(img))
        std_val = float(np.std(img))
        print(f"  Pixel mean={mean_val:.1f}  std={std_val:.1f}")

        if mean_val < 5 or std_val < 5:
            print(f"  [WARN] Image looks blank or uniform")
        else:
            print(f"  [PASS] Image has visible content")

        # Check meta exists
        meta = metas.get(drone_id, {})
        if meta:
            expected_w = meta.get("width", 0)
            expected_h = meta.get("height", 0)
            match = (w == expected_w and h == expected_h)
            print(f"  Meta: {expected_w}x{expected_h}  Frame: {w}x{h}  match={'YES' if match else 'NO'}")
            if not match:
                print(f"  [WARN] Resolution mismatch")
            print(f"  FOV: {meta.get('fov_deg')}°  Pitch: {meta.get('pitch_deg')}°")
            print(f"  Cam pos: {[round(v,2) for v in meta.get('cam_pos_xyz', [])]}")

        # Save sample
        out_dir = Path("logs/joint_verify")
        out_dir.mkdir(parents=True, exist_ok=True)
        sample_path = out_dir / f"drone{drone_id}_sample.jpg"
        cv2.imwrite(str(sample_path), img)
        print(f"  Sample saved: {sample_path}")

        print()

    if all_ok:
        print("=" * 60)
        print("JOINT ACCEPTANCE: PASSED")
        print("Python can decode Group 1 camera frames successfully.")
        print("=" * 60)
        return 0
    else:
        print("=" * 60)
        print("JOINT ACCEPTANCE: FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
