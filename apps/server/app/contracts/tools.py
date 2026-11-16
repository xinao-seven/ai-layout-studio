"""Tool contract envelope.

The agent tools land in M3 and later milestones; this model fixes the shape of
the exported ``tools.schema.json`` so the contract pipeline and CI drift check
exist now.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.contracts.base import CamelModel


class ToolDefinition(CamelModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(description="JSON Schema for the tool input.")


class ToolBundle(CamelModel):
    version: Literal[1] = 1
    tools: list[ToolDefinition] = Field(default_factory=list)
