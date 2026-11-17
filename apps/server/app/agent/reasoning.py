"""Provider-neutral reasoning ("thinking") levels (M7).

The UI exposes a small set of thinking levels; each OpenAI-compatible provider
spells "think harder" differently. This module keeps that mapping in one place
so the adapter, the capabilities endpoint and the contracts agree.

``OFF`` means "do not ask for anything extra": the provider default applies.
That is deliberately not a force-disable, because reasoning-only models cannot
be switched off through the chat-completions API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.contracts.enums import ReasoningStyle, ThinkingLevel

ALL_LEVELS: tuple[ThinkingLevel, ...] = (
    ThinkingLevel.OFF,
    ThinkingLevel.LOW,
    ThinkingLevel.MEDIUM,
    ThinkingLevel.HIGH,
)

# Anthropic uses a token budget instead of a named effort.
_ANTHROPIC_BUDGETS: dict[ThinkingLevel, int] = {
    ThinkingLevel.LOW: 1024,
    ThinkingLevel.MEDIUM: 4096,
    ThinkingLevel.HIGH: 16384,
}


@dataclass(frozen=True)
class StreamOptions:
    """Resolved provider options for one model call.

    Passed per request (never stored on the adapter) so concurrent turns with
    different levels cannot trample each other.
    """

    reasoning_requested: bool = False
    reasoning_effort: str | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)
    max_tokens: int | None = None


def detect_reasoning_style(base_url: str, model: str) -> ReasoningStyle:
    """Guess the provider family from its endpoint and model name."""
    haystack = f"{base_url} {model}".lower()
    if "anthropic" in haystack or "claude" in haystack:
        return ReasoningStyle.ANTHROPIC
    if "deepseek" in haystack:
        return ReasoningStyle.DEEPSEEK
    if "dashscope" in haystack or "aliyuncs" in haystack or "qwen" in haystack:
        return ReasoningStyle.QWEN
    if "bigmodel" in haystack or "zhipu" in haystack or "glm" in haystack:
        return ReasoningStyle.GLM
    if "openai" in haystack or "azure" in haystack or model.startswith(("o1", "o3", "o4", "gpt-5")):
        return ReasoningStyle.OPENAI
    return ReasoningStyle.NONE


def resolve_reasoning_style(
    base_url: str,
    model: str,
    override: str | ReasoningStyle | None,
) -> ReasoningStyle:
    """Resolve the configured style, falling back to auto-detection."""
    if override is None or override == "" or override == ReasoningStyle.AUTO:
        return detect_reasoning_style(base_url, model)
    if isinstance(override, ReasoningStyle):
        return override
    try:
        return ReasoningStyle(override)
    except ValueError:
        return ReasoningStyle.NONE


def build_stream_options(
    style: ReasoningStyle,
    level: ThinkingLevel | None,
) -> StreamOptions:
    """Translate one thinking level into provider-specific request options."""
    resolved = level or ThinkingLevel.OFF
    if style is ReasoningStyle.NONE or resolved is ThinkingLevel.OFF:
        return StreamOptions()

    if style is ReasoningStyle.OPENAI:
        return StreamOptions(reasoning_requested=True, reasoning_effort=resolved.value)

    if style is ReasoningStyle.ANTHROPIC:
        budget = _ANTHROPIC_BUDGETS.get(resolved, 4096)
        return StreamOptions(
            reasoning_requested=True,
            extra_body={"thinking": {"type": "enabled", "budget_tokens": budget}},
            # Anthropic requires max_tokens to stay above the thinking budget.
            max_tokens=budget + 8192,
        )

    if style is ReasoningStyle.DEEPSEEK or style is ReasoningStyle.GLM:
        return StreamOptions(
            reasoning_requested=True,
            extra_body={"thinking": {"type": "enabled"}},
        )

    if style is ReasoningStyle.QWEN:
        return StreamOptions(
            reasoning_requested=True,
            extra_body={"enable_thinking": True},
        )

    return StreamOptions()


def is_granular(style: ReasoningStyle) -> bool:
    """Whether the style distinguishes low/medium/high (not just on/off)."""
    return style in {ReasoningStyle.OPENAI, ReasoningStyle.ANTHROPIC}


def available_levels(style: ReasoningStyle) -> tuple[ThinkingLevel, ...]:
    if style is ReasoningStyle.NONE:
        return (ThinkingLevel.OFF,)
    return ALL_LEVELS


def supports_reasoning(style: ReasoningStyle) -> bool:
    return style is not ReasoningStyle.NONE
