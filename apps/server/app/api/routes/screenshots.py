"""Before/after screenshot serving (FR-16, M4-05)."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.capture.service import CaptureService

router = APIRouter(tags=["screenshots"])


def _get_capture(request: Request) -> CaptureService:
    return cast(CaptureService, request.app.state.capture)


@router.get(
    "/screenshots/{scene_id}/{name}",
    response_class=FileResponse,
    include_in_schema=False,
)
async def get_screenshot(
    scene_id: str,
    name: str,
    capture: Annotated[CaptureService, Depends(_get_capture)],
) -> FileResponse:
    path = capture.path_for(scene_id, name)
    if path is None:
        raise HTTPException(status_code=404, detail="screenshot_not_found")
    return FileResponse(
        path,
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


__all__ = ["router"]
