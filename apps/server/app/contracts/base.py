"""Shared Pydantic contract pieces.

Pydantic v2 is the single source of truth for REST/WS/tool schemas
(PRD D6). JSON is camelCase via the alias generator; Python stays snake_case.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    """Base model that serialises field names as camelCase."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        serialize_by_alias=True,
    )
