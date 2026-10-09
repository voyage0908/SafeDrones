"""临时测试：通过 stdio 连接 mcp_bridge，验证 MCP 协议层（AGH 将用同样方式接入）。"""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PY = r"C:\Users\Lenovo\Desktop\新建文件夹\SafeDrones\.venv\Scripts\python.exe"
CWD = r"C:\Users\Lenovo\Desktop\新建文件夹\SafeDrones"


async def main() -> None:
    params = StdioServerParameters(command=PY, args=["mcp_bridge.py"], cwd=CWD)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", [t.name for t in tools.tools])

            r = await session.call_tool("publish_waypoint", {"drone": 1, "waypoint": [0, 0, 1], "rationale": "回基地"})
            print("publish_waypoint ->", r.structured_content or r.content)

            await asyncio.sleep(1.5)
            t = await session.call_tool("read_telemetry", {"drone": 1})
            print("read_telemetry ->", t.structured_content or t.content)

            d = await session.call_tool("list_drones", {})
            print("list_drones ->", d.structured_content or d.content)

            s = await session.call_tool("list_safety_events", {"since_ms": 0, "limit": 5})
            print("list_safety_events ->", s.structured_content or s.content)

            a = await session.call_tool("emergency_abort", {"drone": 1})
            print("emergency_abort ->", a.structured_content or a.content)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
