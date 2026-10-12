#!/usr/bin/env python3
"""SafeDrones · AGH 闭环演示场景（保持蜂群存活，供 AGH 经 MCP 桥监控并闭环）。

分工（升级 A：让 AGH / Agnes 模型自己走 MCP 工具闭环，而非 thin one-shot）：
  - 本脚本：起 broker + 2 架 mock drone + safety_gate，下发对撞航点制造一次
    safety_override 事件，然后保持存活 --hold-sec 秒，期间不自己做任何 MCP 调用。
  - 外部 AGH：另开终端跑 `agh` TUI，敲 `/yolo` 授信后让 Agnes 模型自己调 MCP 工具
    （list_safety_events → read_telemetry → publish_waypoint）完成「上行反馈 + 重规划」。

为什么本脚本不下发避让航点：闭环的「重规划」那一步必须由 AGH 完成，否则演示不出
「Agnes Harness 与模型执行闭环」这一评分维度（20%）。

用法：
  ./.venv/Scripts/python.exe scripts/agh_mcp_scenario.py --delay 20 --hold-sec 60

  本脚本先起 broker + 2 机 + safety_gate 就位，打印「蜂群已就位」后进入 --delay 秒等待；
  这段时间去另开终端跑 AGH TUI，敲 /yolo 授信并下发监控指令。--delay 结束后脚本自动注入对撞。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
_VENV_SCRIPTS = Path(PY).parent
if str(_VENV_SCRIPTS) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(_VENV_SCRIPTS) + os.pathsep + os.environ.get("PATH", "")

HOST = "127.0.0.1"
PORT = 1883


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="启动蜂群对撞场景并保持存活，供 AGH 经 MCP 闭环。")
    p.add_argument("--hold-sec", type=float, default=60.0, help="对撞注入后保持蜂群存活的秒数（给 AGH 反应时间）。")
    p.add_argument("--delay", type=float, default=20.0, help="蜂群就位后、首次注入对撞前的等待秒数（给你时间去 AGH TUI 敲 /yolo 并下发监控指令）。")
    p.add_argument("--rearm-sec", type=float, default=12.0, help="首次注入后每隔多少秒重新注入一次对撞航点（多次触发 override，便于 AGH 随时接入）。")
    p.add_argument("--no-broker", action="store_true", help="broker 已由外部启动，本脚本不再拉起。")
    return p.parse_args()


def _waypoint(drone: int, target: list[float], rationale: str) -> dict:
    return {
        "drone": drone,
        "action": "move_to",
        "target": target,
        "waypoint": target,
        "priority": "normal",
        "confidence": 0.8,
        "ttl_sec": 30.0,
        "command_id": f"setup-{drone}-{int(time.time() * 1000)}",
        "rationale": rationale,
        "timestamp_ms": int(time.time() * 1000),
    }


def _publish_commands(commands: list[dict]) -> None:
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="agh-scenario-setup")
    client.connect(HOST, PORT, keepalive=30)
    client.loop_start()
    time.sleep(0.3)
    for cmd in commands:
        topic = f"swarm/drone/{cmd['drone']}/command"
        client.publish(topic, payload=json.dumps(cmd, ensure_ascii=False), qos=0, retain=False)
        print(f"[scenario] 下发 {topic} -> {cmd['target']} ({cmd['rationale']})")
    time.sleep(0.3)
    client.loop_stop()
    client.disconnect()


def main() -> int:
    args = parse_args()

    procs: list[subprocess.Popen] = []

    def spawn(*argv: str) -> subprocess.Popen:
        p = subprocess.Popen([PY, *argv], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)
        return p

    try:
        if not args.no_broker:
            print("[scenario] 启动 MQTT broker (amqtt)...")
            spawn(str(ROOT / "scripts" / "dev_broker.py"))
            time.sleep(2.0)  # 等 broker 就绪

        # 两架无人机相向而行：1 号从原点东行到 x=4，2 号从 x=4 西行回原点，在 x=2 交汇。
        # 注意 start-position 用非负值，避免 argparse 把 "-3,0,1" 误判成 option。
        print("[scenario] 启动 mock_drone 1/2 与 safety_gate...")
        spawn(str(ROOT / "mock_drone.py"), "--drone-id", "1", "--start-position", "0,0,1", "--speed", "1.0")
        spawn(str(ROOT / "mock_drone.py"), "--drone-id", "2", "--start-position", "4,0,1", "--speed", "1.0")
        spawn(
            str(ROOT / "safety_gate.py"),
            "--host", HOST, "--port", str(PORT),
            "--vertical-escape-gain", "0.0",  # 纯水平避障，便于观察
            "--log-level", "INFO",
        )
        time.sleep(2.0)  # 等 drone / gate 连上 broker

        print("[scenario] ================================================")
        print(f"[scenario] 蜂群已就位。请在 AGH TUI 敲 /yolo 并下发监控指令，{args.delay:.0f}s 后自动注入对撞。")
        print("[scenario] ================================================")
        time.sleep(args.delay)

        deadline = time.time() + args.hold_sec
        while time.time() < deadline:
            print(f"[scenario] 注入对撞航点（1 号东行 / 2 号西行）...")
            _publish_commands([
                _waypoint(1, [4, 0, 1], "对撞演示：1 号机东行"),
                _waypoint(2, [0, 0, 1], "对撞演示：2 号机西行"),
            ])
            time.sleep(min(args.rearm_sec, max(1.0, deadline - time.time())))

        print("[scenario] 演示结束，清理进程。")
        return 0
    except KeyboardInterrupt:
        print("[scenario] 收到中断，清理进程。")
        return 130
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(0.5)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass
    sys.exit(main())
