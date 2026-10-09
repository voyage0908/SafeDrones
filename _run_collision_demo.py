"""临时演示：双向安全协议端到端（2 机对撞 → Safety Gate 否决 → override 事件回流 → 经 MCP list_safety_events 上报）。

走 AGH 同款路径：起一个独立 mcp_bridge（stdio + 独立 MQTT client_id），
用 MCP 工具 publish_waypoint 下发对撞航点，再用 list_safety_events 收到
safety_override 事件（上行反馈），同时 safety_gate 会下发 safety 命令给两机（下行否决）。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = r"C:\Users\Lenovo\Desktop\新建文件夹\SafeDrones"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")


def _wp(drone: int, target: list[float], rationale: str) -> dict:
    return {
        "drone": drone,
        "action": "move_to",
        "target": target,
        "waypoint": target,
        "priority": "normal",
        "confidence": 0.8,
        "ttl_sec": 10.0,
        "command_id": f"demo-{drone}-{int(time.time() * 1000)}",
        "rationale": rationale,
        "timestamp_ms": int(time.time() * 1000),
    }


def _extract(result) -> str:
    if getattr(result, "structured_content", None):
        return json.dumps(result.structured_content, ensure_ascii=False)
    return str(result.content)


async def main() -> int:
    drone2 = subprocess.Popen(
        [PY, os.path.join(ROOT, "mock_drone.py"), "--drone-id", "2", "--start-position", "2,0,1", "--speed", "1.0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    gate = subprocess.Popen(
        [PY, os.path.join(ROOT, "safety_gate.py"), "--log-level", "INFO"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(1.5)  # 让 drone2 / safety_gate 连上 broker

        env = dict(os.environ)
        env["SAFEDRONES_MQTT_CLIENT_ID"] = "bridge-collision-demo"
        params = StdioServerParameters(command=PY, args=["mcp_bridge.py"], cwd=ROOT, env=env)

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("tools:", [t.name for t in (await session.list_tools()).tools])

                # 下行：经 MCP 下发对撞航点（AGH 指挥官同款动作）
                r1 = await session.call_tool("publish_waypoint", {"drone": 1, "waypoint": [2, 0, 1], "rationale": "对撞演示：1 号机东行"})
                r2 = await session.call_tool("publish_waypoint", {"drone": 2, "waypoint": [0, 0, 1], "rationale": "对撞演示：2 号机西行"})
                print("下发 1 ->", _extract(r1))
                print("下发 2 ->", _extract(r2))

                # 上行：轮询 list_safety_events 直到捕获 safety_override
                overrides = []
                deadline = time.time() + 20.0
                while time.time() < deadline:
                    ev = await session.call_tool("list_safety_events", {"since_ms": 0, "limit": 100})
                    sc = ev.structured_content or {}
                    # list 返回被 MCPServer 包成 {"result": [...]}
                    events = sc.get("result", []) if isinstance(sc, dict) else []
                    for e in events:
                        p = e.get("payload", {}) if isinstance(e, dict) else {}
                        if p.get("event") == "safety_override":
                            overrides.append(p)
                    if overrides:
                        break
                    await asyncio.sleep(0.4)

                print("\n" + "=" * 64)
                print("双向安全协议 · 对撞演示结果")
                print("=" * 64)
                if not overrides:
                    print("✗ 未捕获 safety_override（超时）")
                    t1 = await session.call_tool("read_telemetry", {"drone": 1})
                    t2 = await session.call_tool("read_telemetry", {"drone": 2})
                    print("telemetry 1 ->", _extract(t1))
                    print("telemetry 2 ->", _extract(t2))
                    return 1

                for o in overrides:
                    print(f"✓ 上行 safety_override: drone={o.get('drone')} "
                          f"reason={o.get('reason')} risk={o.get('risk_level')} "
                          f"target_drone={o.get('target_drone')} "
                          f"diverted_from={o.get('diverted_from')}")

                # 下行否决已在 safety_gate 内部完成：safety 优先级命令下发到两机
                t1 = await session.call_tool("read_telemetry", {"drone": 1})
                t2 = await session.call_tool("read_telemetry", {"drone": 2})
                print("✓ 下行否决后 telemetry 1 ->", _extract(t1))
                print("✓ 下行否决后 telemetry 2 ->", _extract(t2))
                print("\n结论：Safety Gate 独立检出碰撞风险 → 下行 safety 命令接管两机 + 上行 override 事件经 MCP 回流 AGH。闭环成立。")
                return 0
    finally:
        for p in (drone2, gate):
            try:
                p.terminate()
            except Exception:
                pass
        time.sleep(0.5)
        for p in (drone2, gate):
            try:
                p.kill()
            except Exception:
                pass


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(main()))
