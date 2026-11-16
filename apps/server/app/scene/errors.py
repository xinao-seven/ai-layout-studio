"""Scene-level errors mapped to refusal codes and HTTP statuses."""

from __future__ import annotations

from typing import Any

from app.contracts.api import ErrorBody

DEFAULT_STATUS = 400
STATUS_BY_CODE = {
    "not_found": 404,
    "unknown_room": 404,
    "unknown_kind": 404,
    "variant_missing": 404,
    "version_conflict": 409,
    "proposal_expired": 409,
    "turn_in_progress": 409,
    "turn_cancelled": 409,
    "scene_changed": 409,
}


class SceneError(Exception):
    """A domain error that serialises to the unified REST error envelope."""

    def __init__(
        self,
        code: str,
        message: str,
        suggest: str = "Inspect the scene and retry.",
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggest = suggest
        self.details = details or {}
        self.status_code = status_code or STATUS_BY_CODE.get(code, DEFAULT_STATUS)

    def to_error_body(self) -> ErrorBody:
        return ErrorBody(
            code=self.code,
            message=self.message,
            suggest=self.suggest,
            details=self.details or None,
        )
