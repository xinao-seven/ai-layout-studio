"""Agent tool execution (tools L0-L3) and tool-result shapes."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.agent.contracts import (
    AddItemInput,
    AddRoomInput,
    AskUserInput,
    AutoLayoutInput,
    MoveItemInput,
    QuerySceneInput,
    RemoveItemInput,
    SearchCatalogInput,
    UpdateItemInput,
    VerifyLayoutInput,
)
from app.agent.placement import place_add, place_move, place_remove, place_update
from app.agent.refusals import dedupe_refusals, make_refusal
from app.agent.registry import validate_arguments
from app.agent.room_ops import plan_add_room
from app.capture.service import CaptureService
from app.contracts.catalog import CatalogEntry
from app.contracts.enums import ItemSource
from app.contracts.patch import ItemAddOp, ItemRemoveOp, PatchOp
from app.contracts.proposal import Proposal, Refusal
from app.contracts.rules import Rules
from app.contracts.scene import Item, SceneDocument
from app.geometry.errors import GeometryError
from app.geometry.polygon import point_in_polygon, polygon_area
from app.scene.errors import SceneError
from app.scene.registry import OverlayView, ProposalBuild, SceneRegistry
from app.solver.engine import SolveOptions, SolveSpec
from app.solver.service import SolverService
from app.solver.verify import verify_layout
from app.vlm.service import VLMService, build_legend

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
        solver: SolverService | None = None,
        capture: CaptureService | None = None,
        vlm: VLMService | None = None,
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._rules = rules
        self._scene_id = scene_id
        self._turn_id = turn_id
        self._ask_user = ask_user
        self._solver = solver
        self._capture = capture
        self._vlm = vlm

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
        if isinstance(model, AutoLayoutInput):
            return await self._auto_layout(model)
        if isinstance(model, VerifyLayoutInput):
            return await self._verify_layout(model)
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
        if isinstance(model, AddRoomInput):
            return await self._write(
                name,
                model,
                lambda document, _pending: _from_placement(
                    plan_add_room(document, model, rules=self._rules)
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

    async def _auto_layout(self, request: AutoLayoutInput) -> ToolExecution:
        if self._solver is None:
            refusal = make_refusal(
                "internal_error",
                "The layout solver is not available.",
                suggest="Restart the server or use manual placement.",
            )
            return _refused("auto_layout", refusal)
        view = await self._registry.overlay_view(self._scene_id)
        document = view.document
        base_version = document.version
        specs = [
            SolveSpec(
                kind_id=item.kind_id,
                variant_id=item.variant_id,
                required=item.required,
                constraint=item.constraint,
            )
            for item in request.items
        ]
        options = SolveOptions(
            allow_partial=request.options.allow_partial,
            seed=request.options.seed,
            time_budget_ms=min(request.options.time_budget_ms, self._rules.solver_time_budget),
            max_evals=self._rules.solver_max_evals,
            replace_existing=request.options.replace_existing,
        )
        try:
            result = await self._solver.solve(
                document,
                dict(self._catalog),
                self._rules,
                room_id=request.room_id,
                items=specs,
                options=options,
            )
        except (SceneError, GeometryError) as error:
            return _refused(
                "auto_layout",
                make_refusal(
                    error.code,
                    error.message,
                    suggest=error.suggest,
                    details=error.details,
                ),
            )
        ops: list[PatchOp] = [
            ItemRemoveOp(op="item.remove", item_id=item_id) for item_id in result.removed_item_ids
        ]
        ops.extend(
            ItemAddOp(
                op="item.add",
                item=Item(
                    id=placement.item_id,
                    kind_id=placement.kind_id,
                    variant_id=placement.variant_id,
                    transform=placement.transform,
                    constraint=placement.constraint,
                    source=ItemSource.AGENT,
                ),
            )
            for placement in result.placements
            if not placement.existing
        )

        def build(_document: SceneDocument, _pending: list[Proposal]) -> ProposalBuild:
            if _document.version != base_version:
                return ProposalBuild(
                    refusals=[
                        make_refusal(
                            "version_conflict",
                            "The scene changed while auto_layout was solving.",
                            suggest="Retry the layout against the latest scene.",
                        )
                    ]
                )
            return ProposalBuild(ops=list(ops))

        proposal, refusals = await self._registry.propose(
            self._scene_id,
            self._turn_id,
            build,
        )
        if refusals:
            return ToolExecution(
                name="auto_layout",
                payload={
                    "ok": False,
                    "refusals": [refusal.model_dump(by_alias=True) for refusal in refusals],
                },
                refusals=refusals,
            )
        assert proposal is not None
        return ToolExecution(
            name="auto_layout",
            payload={
                "ok": True,
                "proposalId": proposal.id,
                "baseVersion": proposal.base_version,
                "placements": [
                    placement.model_dump(mode="json", by_alias=True)
                    for placement in result.placements
                ],
                "unplaced": [
                    unplaced.model_dump(mode="json", by_alias=True) for unplaced in result.unplaced
                ],
                "removedItemIds": result.removed_item_ids,
                "stats": result.stats.model_dump(mode="json", by_alias=True),
                "needsConfirmation": True,
            },
            proposal=proposal,
        )

    async def _verify_layout(self, request: VerifyLayoutInput) -> ToolExecution:
        view = await self._registry.overlay_view(self._scene_id)
        document = view.document
        try:
            result = await asyncio.to_thread(
                verify_layout,
                document,
                self._catalog,
                self._rules,
                room_id=request.room_id,
            )
        except (SceneError, GeometryError) as error:
            return _refused(
                "verify_layout",
                make_refusal(
                    error.code,
                    error.message,
                    suggest=error.suggest,
                    details=error.details,
                ),
            )
        payload: dict[str, Any] = {
            "ok": result.ok,
            "issues": [issue.model_dump(mode="json", by_alias=True) for issue in result.issues],
        }
        if not request.visual:
            return ToolExecution(name="verify_layout", payload=payload)
        if not result.ok:
            payload["visual"] = {
                "status": "skipped",
                "reason": "geometry_errors",
            }
            return ToolExecution(name="verify_layout", payload=payload)
        if self._capture is None or self._vlm is None or self._solver is None:
            payload["visual"] = {
                "status": "unavailable",
                "reason": "vlm_not_configured",
            }
            return ToolExecution(name="verify_layout", payload=payload)
        room_id = request.room_id
        if room_id is None and len(document.rooms) == 1:
            room_id = document.rooms[0].id
        if room_id is None:
            payload["visual"] = {
                "status": "unavailable",
                "reason": "room_id_required",
            }
            return ToolExecution(name="verify_layout", payload=payload)
        room = next(
            (candidate for candidate in document.rooms if candidate.id == room_id),
            None,
        )
        if room is None:
            return _refused(
                "verify_layout",
                make_refusal(
                    "unknown_room",
                    f"Room {room_id!r} does not exist.",
                    details={"roomId": room_id},
                ),
            )
        legend = build_legend(document, room)
        image = await self._capture.request(
            self._scene_id,
            self._turn_id,
            purpose="review",
            legend=legend,
        )
        if image is None:
            payload["visual"] = {
                "status": "unavailable",
                "reason": "capture_unavailable",
            }
            return ToolExecution(name="verify_layout", payload=payload)
        reviewed = await self._vlm.review_and_replan(
            document,
            room,
            image,
            self._catalog,
            self._rules,
            self._solver,
            legend=legend,
        )
        payload["visual"] = reviewed.review.model_dump(mode="json", by_alias=True)
        payload["visual"]["legend"] = image.legend
        proposal: Proposal | None = None
        if reviewed.review.status == "replanned" and reviewed.placements:
            ops = reviewed.update_ops(document)
            base_version = document.version

            def build(_document: SceneDocument, _pending: list[Proposal]) -> ProposalBuild:
                if _document.version != base_version:
                    return ProposalBuild(
                        refusals=[
                            make_refusal(
                                "version_conflict",
                                "The scene changed during the visual review.",
                                suggest="Run verify_layout again on the latest scene.",
                            )
                        ]
                    )
                return ProposalBuild(ops=list(ops))

            proposal, refusals = await self._registry.propose(
                self._scene_id,
                self._turn_id,
                build,
            )
            if refusals:
                payload["visual"]["status"] = "issues_found"
                payload["visual"]["refusals"] = [
                    refusal.model_dump(by_alias=True) for refusal in refusals
                ]
            elif proposal is not None:
                payload["visual"]["proposalId"] = proposal.id
                payload["visual"]["status"] = "replanned"
                payload["proposalId"] = proposal.id
        return ToolExecution(
            name="verify_layout",
            payload=payload,
            proposal=proposal,
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
            elif op.op == "room.add":
                preview = {
                    "roomId": op.room.id,
                    "name": op.room.name,
                    "type": op.room.type.value,
                    "areaM2": round(polygon_area(op.room.polygon), 2),
                    "vertexCount": len(op.room.polygon),
                }
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
