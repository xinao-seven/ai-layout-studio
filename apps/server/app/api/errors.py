"""Unified REST error envelope handlers (PRD §6.7)."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.contracts.api import ErrorBody, ErrorEnvelope
from app.scene.errors import SceneError

logger = structlog.get_logger(__name__)


def _response(status_code: int, body: ErrorBody) -> JSONResponse:
    envelope = ErrorEnvelope(error=body)
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json", by_alias=True),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(SceneError)
    async def _scene_error(_: Request, exc: SceneError) -> JSONResponse:
        return _response(exc.status_code, exc.to_error_body())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _response(
            422,
            ErrorBody(
                code="invalid_input",
                message="Request body or query parameters do not match the contract.",
                suggest="Fix the fields and retry.",
                details={"errors": jsonable_encoder(exc.errors())},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "invalid_input"
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _response(
            exc.status_code,
            ErrorBody(code=code, message=message),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("request.unhandled", path=request.url.path, error=str(exc))
        return _response(
            500,
            ErrorBody(
                code="internal_error",
                message="The server hit an unexpected error.",
                suggest="Retry once; if it persists, check the server logs.",
            ),
        )
