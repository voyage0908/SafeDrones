from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from swarm.llm_provider import CommanderLLM


async def run() -> None:
    parser = argparse.ArgumentParser(description="Check the configured LLM provider.")
    parser.add_argument("text", nargs="?", default="让 1 号无人机去左前方侦察点")
    parser.add_argument("--drone", type=int, default=1)
    args = parser.parse_args()

    plan = await CommanderLLM().plan(args.text, drone=args.drone, context=[])
    print(
        json.dumps(
            {
                "drone": plan.drone,
                "waypoint": list(plan.waypoint),
                "priority": plan.priority,
                "confidence": plan.confidence,
                "rationale": plan.rationale,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
