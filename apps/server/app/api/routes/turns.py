"""Turn and proposal REST endpoints (PRD §6.7, M3)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.agent.manager import TurnManager
from app.api.deps import get_gateway, get_registry, get_turn_manager
from app.contracts.api import (
    CreateTurnRequest,
    ProposalListResponse,
    TurnCancelResponse,
    TurnListResponse,
    TurnResponse,
)
from app.contracts.enums import ProposalStatus
from app.scene.errors import SceneError
from app.scene.registry import SceneRegistry
from app.sync.gateway import SyncGateway

router = APIRouter(tags=["turns"])


@router.get("/scenes/{scene_id}/turns", response_model=TurnListResponse)
async def list_turns(
    scene_id: str,
    manager: Annotated[TurnManager, Depends(get_turn_manager)],
) -> TurnListResponse:
    return TurnListResponse(turns=await manager.list_turns(scene_id))


@router.post(
    "/scenes/{scene_id}/turns",
    response_model=TurnResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_turn(
    scene_id: str,
    request: CreateTurnRequest,
    manager: Annotated[TurnManager, Depends(get_turn_manager)],
) -> TurnResponse:
    turn = await manager.create_turn(
        scene_id,
        request.message,
        point_context=request.point_context,
        client_turn_id=request.client_turn_id,
    )
    return TurnResponse(turn=turn)


@router.get("/scenes/{scene_id}/turns/{turn_id}", response_model=TurnResponse)
async def get_turn(
    scene_id: str,
    turn_id: str,
    manager: Annotated[TurnManager, Depends(get_turn_manager)],
) -> TurnResponse:
    turn = await manager.get_turn(turn_id)
    if turn is None or turn.scene_id != scene_id:
        raise SceneError(
            "not_found",
            f"Turn {turn_id!r} does not exist for scene {scene_id!r}.",
            "List turns for the scene.",
            {"sceneId": scene_id, "turnId": turn_id},
        )
    return TurnResponse(turn=turn)


@router.get("/scenes/{scene_id}/proposals", response_model=ProposalListResponse)
async def list_proposals(
    scene_id: str,
    registry: Annotated[SceneRegistry, Depends(get_registry)],
    gateway: Annotated[SyncGateway, Depends(get_gateway)],
    proposal_status: Annotated[ProposalStatus | None, Query(alias="status")] = None,
) -> ProposalListResponse:
    expired = await registry.expire_due(scene_id)
    if expired:
        await gateway.emit_expired(scene_id, expired)
    proposals = await registry.list_proposals(scene_id)
    if proposal_status is not None:
        proposals = [proposal for proposal in proposals if proposal.status == proposal_status]
    return ProposalListResponse(proposals=proposals)


@router.post("/turns/{turn_id}/cancel", response_model=TurnCancelResponse)
async def cancel_turn(
    turn_id: str,
    manager: Annotated[TurnManager, Depends(get_turn_manager)],
) -> TurnCancelResponse:
    return TurnCancelResponse(status=await manager.cancel_turn(turn_id))
