from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

import httpx


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str | None
    base_url: str
    model: str
    timeout_sec: float = 30.0


@dataclass(frozen=True)
class WaypointPlan:
    drone: int
    waypoint: tuple[float, float, float]
    priority: str = "normal"
    confidence: float = 0.6
    rationale: str = ""


class LLMProviderError(RuntimeError):
    pass


def load_api_key(provider: str) -> str | None:
    generic = os.getenv("LLM_API_KEY")
    if generic:
        return generic

    if provider == "deepseek":
        key = os.getenv("DEEPSEEK_API_KEY")
        if key:
            return key

        key_file = Path(os.getenv("DEEPSEEK_API_KEY_FILE", "deepseek_api_key"))
        if key_file.exists():
            value = key_file.read_text(encoding="utf-8").strip()
            return value or None

    return None


def load_llm_settings() -> LLMSettings:
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()

    if provider == "heuristic":
        return LLMSettings(
            provider=provider,
            api_key=None,
            base_url="",
            model="heuristic",
            timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC", "30")),
        )

    if provider == "deepseek":
        return LLMSettings(
            provider=provider,
            api_key=load_api_key(provider),
            base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
            model=os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
            timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC", "30")),
        )

    if provider in {"openai_compatible", "compatible"}:
        return LLMSettings(
            provider="openai_compatible",
            api_key=load_api_key(provider),
            base_url=os.getenv("LLM_BASE_URL", "").strip(),
            model=os.getenv("LLM_MODEL", "").strip(),
            timeout_sec=float(os.getenv("LLM_TIMEOUT_SEC", "30")),
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

        if not self.settings.api_key:
            raise LLMProviderError(
                f"missing API key for provider {self.settings.provider}; set DEEPSEEK_API_KEY, "
                "DEEPSEEK_API_KEY_FILE, or LLM_API_KEY"
            )
        if not self.settings.base_url or not self.settings.model:
            raise LLMProviderError("LLM_BASE_URL and LLM_MODEL are required for openai_compatible provider")

        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "text": text,
                            "default_drone": drone,
                            "recent_feedback": context or [],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.1,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
        }
        if self.settings.provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}

        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.settings.timeout_sec) as client:
            response = await client.post(url, headers=headers, json=payload)

        if response.status_code >= 400:
            raise LLMProviderError(f"LLM API error {response.status_code}: {response.text[:500]}")

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError("LLM API response missing choices[0].message.content") from exc

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
