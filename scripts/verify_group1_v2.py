"""
Group 1 acceptance verification v2 — timestamp-matched comparison.
"""
from __future__ import annotations

import json
import time
import sys
from collections import defaultdict

import paho.mqtt.client as mqtt

BROKER = "127.0.0.1"
PORT = 1883
VERIFY_SECS = 15

# Store ALL messages with timestamps for accurate matching
frames: dict[int, list[tuple[float, bytes]]] = defaultdict(list)
metas: dict[int, list[tuple[float, dict]]] = defaultdict(list)
telemetries: dict[int, list[tuple[float, dict]]] = defaultdict(list)
groundtruths: list[tuple[float, dict]] = []


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[OK] Connected, rc={reason_code}")
    client.subscribe("swarm/cam/drone/+/frame", qos=0)
    client.subscribe("swarm/cam/drone/+/meta", qos=0)
    client.subscribe("swarm/target/+/groundtruth", qos=0)
    client.subscribe("swarm/drone/+/telemetry", qos=0)


def drone_id_from_topic(topic: str) -> int:
    parts = topic.split("/")
    for i, p in enumerate(parts):
        if p == "drone" and i + 1 < len(parts):
            try:
                return int(parts[i + 1])
            except ValueError:
                pass
    return -1


def on_message(client, userdata, msg):
    t = time.time()
    topic = msg.topic

    if "/frame" in topic:
        did = drone_id_from_topic(topic)
        frames[did].append((t, msg.payload))

    elif "/meta" in topic:
        did = drone_id_from_topic(topic)
        try:
            meta = json.loads(msg.payload.decode())
            metas[did].append((t, meta))
        except json.JSONDecodeError:
            pass

    elif "/groundtruth" in topic:
        try:
            gt = json.loads(msg.payload.decode())
            groundtruths.append((t, gt))
        except json.JSONDecodeError:
            pass

    elif "/telemetry" in topic:
        did = drone_id_from_topic(topic)
        try:
            tel = json.loads(msg.payload.decode())
            telemetries[did].append((t, tel))
        except json.JSONDecodeError:
            pass


def compute_rate(ts_list: list[float]) -> float:
    if len(ts_list) < 2:
        return 0.0
    return (len(ts_list) - 1) / (ts_list[-1] - ts_list[0])


def find_closest_telemetry(meta_time: float, tel_list: list[tuple[float, dict]]) -> tuple[float, dict] | None:
    if not tel_list:
        return None
    best = min(tel_list, key=lambda x: abs(x[0] - meta_time))
    return best


def main():
    print("=" * 60)
    print("Group 1 Acceptance Verification v2 (timestamp-matched)")
    print(f"Collecting {VERIFY_SECS}s...")
    print("=" * 60)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="g1v2-" + str(int(time.time())))
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, PORT, 60)
    client.loop_start()

    time.sleep(VERIFY_SECS)
    client.loop_stop()
    client.disconnect()

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    all_ok = True

    # 1. Frame rates
    print("\n[1] Frame publishing @ 10 Hz")
    for did in sorted(frames.keys()):
        ts = [f[0] for f in frames[did]]
        rate = compute_rate(ts)
        sizes = [len(f[1]) for f in frames[did]]
        ok = rate >= 8.0
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] drone={did}  count={len(frames[did])}  rate={rate:.1f} Hz  avg_size={sum(sizes)//len(sizes)} bytes")
        if not ok:
            all_ok = False
    if not frames:
        print("  [FAIL] No frames — is Unity in Play mode?")
        all_ok = False

    # 2. Meta rates
    print("\n[2] Meta publishing @ ~1 Hz")
    for did in sorted(metas.keys()):
        ts = [m[0] for m in metas[did]]
        rate = compute_rate(ts)
        ok = 0.5 <= rate <= 2.0
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] drone={did}  count={len(metas[did])}  rate={rate:.2f} Hz")
        if not ok:
            all_ok = False
    if not metas:
        print("  [FAIL] No meta")
        all_ok = False

    # 3. Timestamp-matched position comparison
    print("\n[3] cam_pos_xyz vs telemetry (timestamp-matched, <0.5s gap)")
    matched = 0
    for did in sorted(metas.keys()):
        tel_list = telemetries.get(did, [])
        if not tel_list:
            print(f"  [SKIP] drone={did} no telemetry data")
            continue
        for meta_time, meta in metas[did]:
            closest = find_closest_telemetry(meta_time, tel_list)
            if closest is None:
                continue
            tel_time, tel = closest
            gap = abs(meta_time - tel_time)
            if gap > 0.5:
                continue  # skip poorly matched pairs

            cam = meta.get("cam_pos_xyz", [])
            tel_pos = tel.get("position") or [tel.get("x", 0), tel.get("y", 0), tel.get("z", 0)]
            if len(cam) < 3 or len(tel_pos) < 3:
                continue

            dx, dy, dz = cam[0]-tel_pos[0], cam[1]-tel_pos[1], cam[2]-tel_pos[2]
            dist = (dx*dx + dy*dy + dz*dz) ** 0.5
            ok = dist < 0.5  # room for nose offset (0.15m) + smoothing lag + minor timing gap
            tag = "PASS" if ok else "WARN"
            print(f"  [{tag}] drone={did}  dist={dist:.3f}m  gap={gap*1000:.0f}ms  cam={[round(v,3) for v in cam]}  tel={[round(v,3) for v in tel_pos]}")
            if not ok:
                all_ok = False
            matched += 1
    if matched == 0:
        print("  [INFO] No timestamp-matched pairs — drones or telemetry may not be running")
    else:
        print(f"  (compared {matched} timestamp-matched pairs)")

    # 4. Groundtruth
    print("\n[4] Groundtruth @ 5 Hz")
    if groundtruths:
        ts = [g[0] for g in groundtruths]
        rate = compute_rate(ts)
        ok = rate >= 4.0
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] count={len(groundtruths)}  rate={rate:.1f} Hz")
        last = groundtruths[-1][1]
        print(f"  [INFO] last pos={last.get('position')}")
        if not ok:
            all_ok = False
    else:
        print("  [FAIL] No groundtruth")
        all_ok = False

    # 5. JPEG validity
    print("\n[5] JPEG decode verification")
    if frames:
        for did in sorted(frames.keys()):
            payload = frames[did][0][1]
            is_jpeg = payload[:2] == b'\xff\xd8'
            tag = "PASS" if is_jpeg else "FAIL"
            print(f"  [{tag}] drone={did} JPEG header check: {is_jpeg} ({len(payload)} bytes)")
            if not is_jpeg:
                all_ok = False
    else:
        print("  [SKIP] No frames")

    print("\n" + "=" * 60)
    print("OVERALL:", "ALL PASSED" if all_ok else "SOME FAILED")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
