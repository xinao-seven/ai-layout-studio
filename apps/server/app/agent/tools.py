"""Agent tool execution (7 tools, L0-L3) and tool-result shapes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.agent.contracts import (
    AddItemInput,
    AskUserInput,
    MoveItemInput,
    QuerySceneInput,
    RemoveItemInput,
    SearchCatalogInput,
    UpdateItemInput,
)
from app.agent.placement import place_add, place_move, place_remove, place_update
from app.agent.refusals import dedupe_refusals, make_refusal
from app.agent.registry import validate_arguments
from app.contracts.catalog import CatalogEntry
from app.contracts.proposal import Proposal, Refusal
from app.contracts.rules import Rules
from app.contracts.scene import SceneDocument
from app.geometry.polygon import point_in_polygon
from app.scene.errors import SceneError
from app.scene.registry import OverlayView, ProposalBuild, SceneRegistry

AskUserHandler = Callable[[AskUserInput], Awaitable[dict[str, list[str]]]]


@dataclass
class ToolExecution:
    name: str
    payload: dict[str, Any]
    refusals: list[Refusal] = field(default_factory=list)
    proposal: Proposal | None = None

    @property
    def failed(self) -> bool:
        return bool(self.refusals)


class ToolExecutor:
    """Executes one tool call against the scene overlay."""

    def __init__(
        self,
        *,
        registry: SceneRegistry,
        catalog: Mapping[str, CatalogEntry],
        rules: Rules,
        scene_id: str,
        turn_id: str,
        ask_user: AskUserHandler,
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._rules = rules
        self._scene_id = scene_id
        self._turn_id = turn_id
        self._ask_user = ask_user

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecution:
        try:
            model = validate_arguments(name, arguments)
        except SceneError as error:
            refusal = make_refusal(
                error.code, error.message, suggest=error.suggest, details=error.details
            )
            return ToolExecution(
                name=name,
                payload={"ok": False, "refusals": [refusal.model_dump(by_alias=True)]},
                refusals=[refusal],
            )

        if isinstance(model, QuerySceneInput):
            return await self._query_scene(model)
        if isinstance(model, SearchCatalogInput):
            return self._search_catalog(model)
        if isinstance(model, AddItemInput):
            return await self._write(
                name,
                model,
                lambda document, _pending: _from_placement(
                    place_add(
                        document,
                        model,
                        catalog=self._catalog,
                        rules=self._rules,
                        strict=True,
                    )
                ),
            )
        if isinstance(model, MoveItemInput):
            return await self._write(
                name,
                model,
                lambda document, _pending: _from_placement(
                    place_move(
                        document,
                        model,
                        catalog=self._catalog,
                        rules=self._rules,
                        strict=True,
                    )
                ),
            )
        if isinstance(model, UpdateItemInput):
            return await self._write(
                name,
                model,
                lambda document, _pending: _from_placement(
                    place_update(
                        document,
                        model,
                        catalog=self._catalog,
                        rules=self._rules,
                        strict=True,
                    )
                ),
            )
        if isinstance(model, RemoveItemInput):
            return await self._write(
                name,
                model,
                lambda document, _pending: _from_placement(place_remove(document, model.item_id)),
            )
        if isinstance(model, AskUserInput):
            answers = await self._ask_user(model)
            return ToolExecution(
                name=name,
                payload={
                    "ok": True,
                    "question": model.question,
                    "answers": answers,
                },
            )
        return ToolExecution(
            name=name,
            payload={
                "ok": False,
                "refusals": [
                    make_refusal("invalid_input", f"Tool {name!r} is not implemented.").model_dump(
                        by_alias=True
                    )
                ],
            },
            refusals=[make_refusal("invalid_input", f"Tool {name!r} is not implemented.")],
        )

    async def _query_scene(self, request: QuerySceneInput) -> ToolExecution:
        view = await self._registry.overlay_view(self._scene_id)
        document = view.document
        if not request.include_pending:
            confirmed = await self._registry.get_document(self._scene_id)
            view = OverlayView(
                document=confirmed,
                pending=[],
                pending_item_ids=set(),
                pending_proposal_ids=set(),
            )
            document = confirmed
        if request.room_id is not None and all(
            room.id != request.room_id for room in document.rooms
        ):
            refusal = make_refusal(
                "unknown_room",
                f"Room {request.room_id!r} does not exist.",
                details={"roomId": request.room_id},
            )
            return _refused("query_scene", refusal)
        selected_ids = set(request.item_ids) if request.item_ids else None
        if selected_ids is not None:
            missing = selected_ids - {item.id for item in document.items}
            if missing:
                refusal = make_refusal(
                    "not_found",
                    f"Items not found: {', '.join(sorted(missing))}.",
                    details={"itemIds": sorted(missing)},
                )
                return _refused("query_scene", refusal)
        items = [
            item
            for item in document.items
            if (selected_ids is None or item.id in selected_ids)
            and (request.room_id is None or _room_id_for(document, item.id) == request.room_id)
        ]
        payload = {
            "ok": True,
            "sceneId": document.id,
            "version": document.version,
            "north": document.north,
            "rooms": [
                {
                    "id": room.id,
                    "name": room.name,
                    "type": room.type.value,
                    "polygon": room.polygon,
                    "wallLabels": room.wall_labels or {},
                }
                for room in document.rooms
                if request.room_id is None or room.id == request.room_id
            ],
            "openings": [
                {
                    "id": opening.id,
                    "type": opening.type.value,
                    "roomId": opening.room_id,
                    "edgeIndex": opening.edge_index,
                    "offset": opening.offset,
                    "width": opening.width,
                    "height": opening.height,
                    "sillHeight": opening.sill_height,
                }
                for opening in document.openings
                if request.room_id is None or opening.room_id == request.room_id
            ],
            "items": [
                {
                    "id": item.id,
                    "kindId": item.kind_id,
                    "variantId": item.variant_id,
                    "position": list(item.transform.position),
                    "rotationY": item.transform.rotation_y,
                    "roomId": _room_id_for(document, item.id),
                    "constraint": item.constraint.model_dump(mode="json", by_alias=True)
                    if item.constraint
                    else None,
                    "pending": item.id in view.pending_item_ids,
                }
                for item in items
            ],
            "pendingProposals": [
                {
                    "id": proposal.id,
                    "turnId": proposal.turn_id,
                    "dependsOn": proposal.depends_on,
                    "status": proposal.status.value,
                    "ops": [op.model_dump(mode="json", by_alias=True) for op in proposal.ops],
                }
                for proposal in view.pending
            ],
        }
        return ToolExecution(name="query_scene", payload=payload)

    def _search_catalog(self, request: SearchCatalogInput) -> ToolExecution:
        query = (request.query or "").strip().lower()
        matches: list[CatalogEntry] = []
        for entry in self._catalog.values():
            if request.category is not None and entry.category != request.category:
                continue
            if query and not (
                query in entry.kind_id.lower()
                or query in entry.name.lower()
                or any(query in tag.lower() for tag in entry.tags)
            ):
                continue
            variant = next(
                (
                    candidate
                    for candidate in entry.variants
                    if candidate.id == entry.default_variant_id
                ),
                entry.variants[0],
            )
            width, depth, height = variant.size_m
            if request.min_width_m is not None and width < request.min_width_m:
                continue
            if request.max_width_m is not None and width > request.max_width_m:
                continue
            if request.min_depth_m is not None and depth < request.min_depth_m:
                continue
            if request.max_depth_m is not None and depth > request.max_depth_m:
                continue
            if request.min_height_m is not None and height < request.min_height_m:
                continue
            if request.max_height_m is not None and height > request.max_height_m:
                continue
            matches.append(entry)
        matches.sort(key=lambda entry: entry.kind_id)
        payload = {
            "ok": True,
            "entries": [
                {
                    "kindId": entry.kind_id,
                    "name": entry.name,
                    "category": entry.category.value,
                    "defaultVariantId": entry.default_variant_id,
                    "variants": [
                        {
                            "id": variant.id,
                            "sizeM": variant.size_m,
                            "modelPath": variant.model_path,
                            "thumbnailPath": variant.thumbnail_path,
                        }
                        for variant in entry.variants
                    ],
                    "rules": entry.rules.model_dump(mode="json", by_alias=True)
                    if entry.rules
                    else None,
                }
                for entry in matches[: request.limit]
            ],
        }
        return ToolExecution(name="search_catalog", payload=payload)

    async def _write(
        self,
        name: str,
        model: Any,
        builder: Callable[[SceneDocument, list[Proposal]], ProposalBuild],
    ) -> ToolExecution:
        proposal, refusals = await self._registry.propose(self._scene_id, self._turn_id, builder)
        if refusals:
            return ToolExecution(
                name=name,
                payload={
                    "ok": False,
                    "refusals": [refusal.model_dump(by_alias=True) for refusal in refusals],
                },
                refusals=refusals,
            )
        assert proposal is not None
        preview: dict[str, Any] = {}
        for op in proposal.ops:
            if op.op == "item.add":
                preview = {
                    "itemId": op.item.id,
                    "kindId": op.item.kind_id,
                    "variantId": op.item.variant_id,
                    "position": list(op.item.transform.position),
                    "rotationY": op.item.transform.rotation_y,
                }
            elif op.op == "item.update":
                preview = {
                    "itemId": op.item_id,
                    "after": op.after.model_dump(mode="json", by_alias=True, exclude_unset=True),
                }
            elif op.op == "item.remove":
                preview = {"itemId": op.item_id}
        return ToolExecution(
            name=name,
            payload={
                "ok": True,
                "proposalId": proposal.id,
                "baseVersion": proposal.base_version,
                "dependsOn": proposal.depends_on,
                "ops": [op.model_dump(mode="json", by_alias=True) for op in proposal.ops],
                "preview": preview,
                "needsConfirmation": True,
            },
            proposal=proposal,
        )


def _from_placement(result: Any) -> ProposalBuild:
    if result.refusals:
        return ProposalBuild(refusals=dedupe_refusals(list(result.refusals)))
    return ProposalBuild(
        ops=list(result.ops),
        document_after=result.document_after,
    )


def _refused(name: str, refusal: Refusal) -> ToolExecution:
    return ToolExecution(
        name=name,
        payload={"ok": False, "refusals": [refusal.model_dump(by_alias=True)]},
        refusals=[refusal],
    )


def _room_id_for(document: SceneDocument, item_id: str) -> str | None:
    item = next((candidate for candidate in document.items if candidate.id == item_id), None)
    if item is None:
        return None
    position = (item.transform.position[0], item.transform.position[2])
    for room in document.rooms:
        if point_in_polygon(position, room.polygon):
            return room.id
    return item.constraint.room_id if item.constraint is not None else None
