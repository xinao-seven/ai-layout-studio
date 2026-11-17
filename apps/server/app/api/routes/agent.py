"""Agent capability discovery (M7).

The chat UI needs to know whether the configured model accepts a reasoning
level before it renders the selector. Keeping this server-side means the
frontend never guesses provider behaviour.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.agent.reasoning import (
    ALL_LEVELS,
    available_levels,
    is_granular,
    resolve_reasoning_style,
    supports_reasoning,
)
from app.api.deps import get_app_settings
from app.contracts.api import AgentCapabilitiesResponse
from app.contracts.enums import ReasoningStyle, ThinkingLevel
from app.core.config import Settings

router = APIRouter(tags=["agent"])


@router.get("/agent/capabilities", response_model=AgentCapabilitiesResponse)
async def get_capabilities(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AgentCapabilitiesResponse:
    """Report the model name and the reasoning levels it can honour."""
    if settings.llm_script_path:
        # Scripted demos stream canned reasoning but send no provider options.
        return AgentCapabilitiesResponse(
            model="scripted",
            reasoning=True,
            reasoning_style=ReasoningStyle.AUTO,
            thinking_levels=list(ALL_LEVELS),
            default_thinking_level=ThinkingLevel(settings.llm_thinking_level),
            granular=False,
        )
    style = resolve_reasoning_style(
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_reasoning_style,
    )
    return AgentCapabilitiesResponse(
        model=settings.llm_model,
        reasoning=supports_reasoning(style),
        reasoning_style=style,
        thinking_levels=list(available_levels(style)),
        default_thinking_level=ThinkingLevel(settings.llm_thinking_level),
        granular=is_granular(style),
    )
