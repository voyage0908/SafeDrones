from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import re
import shutil
import tempfile
from typing import Any

# 唯一模型后端 = AGH 智能体（Agnes 模型）。Python 侧不直接调用任何 LLM API：
# 这里通过 AGH CLI 的一次 one-shot 会话触发 Agnes 完成「自然语言 → JSON 航点」。
# 比赛红线：Python 代码不得出现任何第三方模型名，所有模型调用只发生在 AGH 内部。
DEFAULT_AGH_CLI = ""  # 由 AGH_CLI 环境变量指定 AGH CLI 路径（agnes-harness 的 packages/cli/dist/local/agnes.mjs）
DEFAULT_AGH_PROFILE = "local-dev"


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str | None
    base_url: str
    model: str
    timeout_sec: float = 30.0
    agh_cli: str = DEFAULT_AGH_CLI
    agh_node: str = "node"
    agh_profile: str = DEFAULT_AGH_PROFILE


@dataclass(frozen=True)
class WaypointPlan:
    drone: int
    waypoint: tuple[float, float, float]
    priority: str = "normal"
    confidence: float = 0.6
    rationale: str = ""


class LLMProviderError(RuntimeError):
    pass


def load_llm_settings() -> LLMSettings:
    provider = os.getenv("LLM_PROVIDER", "agnes").strip().lower()

    if provider == "heuristic":
        return LLMSettings(
            provider=provider,
            api_key=None,
            base_url="",
            model="heuristic",
            timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC", "30")),
        )

    if provider in {"agnes", "agh"}:
        agh_cli = os.getenv("AGH_CLI", DEFAULT_AGH_CLI)
        if not agh_cli:
            raise LLMProviderError(
                "AGH_CLI not set; point it at agnes-harness's packages/cli/dist/local/agnes.mjs"
            )
        return LLMSettings(
            provider="agnes",
            api_key=None,
            base_url="",
            model=os.getenv("AGNES_MODEL", "agnes-3.0-flash"),
            timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC", "60")),
            agh_cli=agh_cli,
            agh_node=os.getenv("AGH_NODE", "node"),
            agh_profile=os.getenv("AGH_PROFILE", DEFAULT_AGH_PROFILE),
        )

    raise LLMProviderError(f"unsupported LLM_PROVIDER: {provider}")


def system_prompt() -> str:
    return (
        "你是无人机蜂群的高层指挥官。你的任务是把自然语言命令转换为一个 json 对象。"
        "坐标系定义：X 表示前方，Y 表示左方，Z 表示高度，单位为米。"
        "预设点：侦察点=[10,5,2]，基地=[0,0,1]，左前方=[5,5,2]，右前方=[5,-5,2]。"
        "只输出 JSON，不输出 Markdown。JSON 字段必须包含："
        "drone:int, waypoint:[x,y,z], priority:normal|high|emergency, confidence:0到1, rationale:string。"
        "示例 JSON：{\"drone\":1,\"waypoint\":[3.0,0.0,1.0],"
        "\"priority\":\"normal\",\"confidence\":0.8,\"rationale\":\"恢复原任务航点\"}。"
        "rationale 只给简短可审计理由，不输出完整推理过程。"
    )


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    decoder = json.JSONDecoder()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        search_start = stripped.find("{")
        while search_start >= 0:
            try:
                value, _ = decoder.raw_decode(stripped[search_start:])
                break
            except json.JSONDecodeError:
                search_start = stripped.find("{", search_start + 1)
        else:
            raise LLMProviderError("LLM response did not contain a JSON object")

    if not isinstance(value, dict):
        raise LLMProviderError("LLM response JSON must be an object")
    return value


def parse_waypoint_plan(raw: dict[str, Any], default_drone: int) -> WaypointPlan:
    drone = int(raw.get("drone", default_drone))
    waypoint_value = raw.get("waypoint", raw.get("target"))
    if not isinstance(waypoint_value, list | tuple) or len(waypoint_value) != 3:
        raise LLMProviderError("waypoint must be a list of three numbers")

    try:
        waypoint = tuple(float(value) for value in waypoint_value)
    except (TypeError, ValueError) as exc:
        raise LLMProviderError("waypoint must contain only numbers") from exc

    priority = str(raw.get("priority", "normal"))
    if priority not in {"normal", "high", "emergency"}:
        priority = "normal"

    try:
        confidence = float(raw.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    confidence = max(0.0, min(1.0, confidence))

    return WaypointPlan(
        drone=drone,
        waypoint=waypoint,  # type: ignore[arg-type]
        priority=priority,
        confidence=confidence,
        rationale=str(raw.get("rationale", "")),
    )


class CommanderLLM:
    def __init__(self, settings: LLMSettings | None = None):
        self.settings = settings or load_llm_settings()

    async def plan(self, text: str, drone: int = 1, context: list[dict[str, Any]] | None = None) -> WaypointPlan:
        if self.settings.provider == "heuristic":
            return heuristic_plan(text, drone)
        if self.settings.provider == "agnes":
            return await self._plan_via_agh(text, drone, context)
        raise LLMProviderError(f"unsupported provider: {self.settings.provider}")

    async def _plan_via_agh(
        self,
        text: str,
        drone: int,
        context: list[dict[str, Any]] | None,
    ) -> WaypointPlan:
        prompt = (
            system_prompt()
            + "\n\n只输出一个 JSON 对象，不要调用任何工具，不要输出任何多余文字。\n"
            + "用户输入："
            + json.dumps(
                {"text": text, "default_drone": drone, "recent_feedback": context or []},
                ensure_ascii=False,
            )
        )
        # 每个 replan 用独立的临时工作区（--cwd），避免复用 AGH 的持久化 workspace 会话：
        # 持久化会话在超时/孤儿 turn 后会卡死（one-shot 返回空 text、credits.used=0），
        # 换新工作区即可绕过。用完即删。
        workspace = tempfile.mkdtemp(prefix="safedrones-replan-")
        cmd = [
            self.settings.agh_node,
            self.settings.agh_cli,
            "-p",
            prompt,
            "--mode",
            "json",
            "--cwd",
            workspace,
        ]
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.settings.timeout_sec)
            except asyncio.TimeoutError as exc:
                # 超时必须杀掉子进程，否则 node 进程变孤儿、把该会话的 turn 卡死
                if proc is not None and proc.returncode is None:
                    proc.kill()
                    try:
                        await proc.wait()
                    except (ProcessLookupError, asyncio.TimeoutError):
                        pass
                raise LLMProviderError("AGH replan timed out") from exc
        except FileNotFoundError as exc:
            raise LLMProviderError(
                f"AGH CLI not found; set AGH_NODE/AGH_CLI "
                f"(node={self.settings.agh_node}, cli={self.settings.agh_cli})"
            ) from exc
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

        if proc.returncode != 0:
            detail = stderr.decode("utf-8", "replace")[-500:] if stderr else ""
            raise LLMProviderError(f"AGH replan failed (exit {proc.returncode}): {detail}")

        try:
            outer = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise LLMProviderError("AGH replan returned non-JSON output") from exc

        content = str(outer.get("text") or "")
        return parse_waypoint_plan(extract_json_object(content), default_drone=drone)


def heuristic_plan(text: str, drone: int = 1) -> WaypointPlan:
    lowered = text.lower()
    if "基地" in text or "base" in lowered or "home" in lowered:
        waypoint = (0.0, 0.0, 1.0)
        rationale = "返回基地预设点"
    elif "右" in text or "right" in lowered:
        waypoint = (5.0, -5.0, 2.0)
        rationale = "选择右前方预设点"
    elif "左" in text or "left" in lowered:
        waypoint = (5.0, 5.0, 2.0)
        rationale = "选择左前方预设点"
    else:
        waypoint = (10.0, 5.0, 2.0)
        rationale = "默认执行侦察点航点"

    return WaypointPlan(
        drone=drone,
        waypoint=waypoint,
        priority="normal",
        confidence=0.5,
        rationale=rationale,
    )
