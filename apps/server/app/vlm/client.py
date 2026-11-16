"""VLM adapters (PRD §6.6, M4-04): OpenAI-compatible plus scripted replay."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol, cast

import structlog
from openai import AsyncOpenAI

from app.core.config import Settings
from app.core.paths import project_roots

logger = structlog.get_logger(__name__)


class VLMClient(Protocol):
    model: str

    async def review(self, png: bytes, prompt: str, *, timeout: float) -> str: ...


class OpenAIVLMClient:
    """Vision model behind an OpenAI-compatible endpoint (GLM/Qwen/OpenAI)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
    ) -> None:
        self.model = model
        self._default_timeout = timeout
        self._client = AsyncOpenAI(
            base_url=base_url or None,
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
        )

    async def review(self, png: bytes, prompt: str, *, timeout: float) -> str:
        image_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ]
        create = cast(Any, self._client.chat.completions.create)
        try:
            response = await create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                response_format={"type": "json_object"},
                timeout=timeout,
            )
        except Exception as error:  # providers differ on response_format support
            if "response_format" not in str(error).lower():
                raise
            response = await create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                timeout=timeout,
            )
        content = response.choices[0].message.content if response.choices else None
        return content or ""

    async def aclose(self) -> None:
        await self._client.close()


ScriptedResponse = str | dict[str, Any] | Callable[[str], str | dict[str, Any]]


class ScriptedVLMClient:
    """Deterministic replay used by CI and offline demos.

    A response may be a callable receiving the prompt (which contains the
    authoritative legend); tests use it to reference runtime-minted item labels.
    """

    def __init__(self, responses: Iterable[ScriptedResponse], *, model: str = "scripted-vlm"):
        self.model = model
        self._responses = list(responses)
        self.calls = 0

    async def review(self, png: bytes, prompt: str, *, timeout: float) -> str:
        del png, timeout
        if not self._responses:
            return '{"verdict":"pass","issues":[]}'
        index = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        response = self._responses[index]
        if callable(response):
            response = response(prompt)
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)


def load_script(path: Path) -> list[str | dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        payload = json.loads(stripped)
        return list(payload)
    lines = [line for line in stripped.splitlines() if line.strip()]
    if len(lines) == 1:
        return [json.loads(lines[0])]
    return [json.loads(line) for line in lines]


def _resolve_script_path(raw: str) -> Path:
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.is_file():
        return candidate
    for root in project_roots():
        path = root / candidate
        if path.is_file():
            return path
    return candidate


def build_vlm_client(settings: Settings) -> VLMClient | None:
    if not settings.vlm_enabled:
        return None
    if settings.vlm_script_path:
        path = _resolve_script_path(settings.vlm_script_path)
        if path.is_file():
            return ScriptedVLMClient(load_script(path))
        logger.warning("vlm.script_missing", path=str(path))
    if not (settings.vlm_base_url and settings.vlm_model):
        return None
    return OpenAIVLMClient(
        base_url=settings.vlm_base_url,
        api_key=settings.vlm_api_key.get_secret_value(),
        model=settings.vlm_model,
        timeout=settings.vlm_timeout_seconds,
    )


__all__ = [
    "OpenAIVLMClient",
    "ScriptedVLMClient",
    "VLMClient",
    "build_vlm_client",
    "load_script",
]
