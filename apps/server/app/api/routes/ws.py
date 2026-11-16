"""WebSocket endpoint (single connection, three logical channels)."""

from __future__ import annotations

from typing import Annotated, cast

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.scene.errors import SceneError
from app.sync.gateway import SyncGateway

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    scene_id: Annotated[str | None, Query(alias="sceneId")] = None,
) -> None:
    if not scene_id:
        await websocket.close(code=1008, reason="sceneId_required")
        return

    gateway = cast(SyncGateway, websocket.app.state.gateway)
    await websocket.accept()
    try:
        await gateway.connect(websocket, scene_id)
    except SceneError as error:
        logger.info("ws.connect_rejected", scene_id=scene_id, code=error.code)
        await websocket.close(code=1008, reason=error.code)
        return

    try:
        while True:
            raw = await websocket.receive_text()
            await gateway.handle_message(websocket, scene_id, raw)
    except WebSocketDisconnect:
        pass
    except Exception as error:  # pragma: no cover - defensive
        logger.exception("ws.connection_failed", error=str(error))
    finally:
        await gateway.disconnect(websocket, scene_id)
        if websocket.application_state == WebSocketState.CONNECTED:
            await websocket.close()
