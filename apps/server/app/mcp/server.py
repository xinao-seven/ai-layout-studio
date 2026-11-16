"""Minimal MCP server over stdio (PRD FR-21).

Five tools share the chat agent's Pydantic contracts:

- ``tools/list`` serves the exact ``ToolSpec`` name/description/schema the chat
  agent exposes (via ``app.agent.registry``), filtered to the MCP subset.
- Read tools (``query_scene`` / ``search_catalog``) reuse ``ToolExecutor`` so
  their payloads are identical with chat.
- Write tools (``add_item`` / ``move_item`` / ``remove_item``) run the same
  L1-L3 placement rules via ``app.agent.placement`` and commit **directly**
  instead of creating a proposal: MCP has no confirmation UI, so the proposal
  chain is intentionally bypassed (documented in the README and ADR-0010).

Protocol: JSON-RPC 2.0 over stdio, one message per line (MCP stdio transport).
Only ``initialize``, ``notifications/initialized``, ``ping``, ``tools/list``
and ``tools/call`` are implemented — the minimal subset FR-21 asks for.

Tool arguments are exactly the chat tool schemas; a single reserved key
``sceneId`` addresses the scene (chat tools are always scoped to the current
scene, so the schema does not carry it — ``tools/call`` strips it before L0
validation).

Run with ``uv run --directory apps/server python -m app.mcp`` or ``pnpm mcp``.
Everything written to stdout is protocol; diagnostics go to stderr only.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from app.agent.contracts import AddItemInput, MoveItemInput, RemoveItemInput
from app.agent.placement import place_add, place_move, place_remove
from app.agent.refusals import dedupe_refusals, make_refusal
from app.agent.registry import TOOL_BY_NAME, parameter_schema, validate_arguments
from app.agent.tools import ToolExecutor
from app.catalog.service import CatalogService
from app.contracts.enums import Actor
from app.contracts.rules import get_rules
from app.core.config import Settings
from app.db import models  # noqa: F401 - register ORM tables on Base.metadata
from app.db.base import Base
from app.db.repository import SceneRepository
from app.db.session import create_engine, create_session_factory
from app.geometry.errors import GeometryError
from app.scene.errors import SceneError
from app.scene.registry import SceneRegistry

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ai-layout-workbench"
SERVER_VERSION = "0.1.0"

MCP_TOOL_NAMES: tuple[str, ...] = (
    "query_scene",
    "search_catalog",
    "add_item",
    "move_item",
    "remove_item",
)
WRITE_TOOL_NAMES: frozenset[str] = frozenset({"add_item", "move_item", "remove_item"})
RESERVED_ARGUMENT_KEYS: frozenset[str] = frozenset({"sceneId"})


def mcp_tool_definitions() -> list[dict[str, Any]]:
    """Tool list served by ``tools/list`` — same contract as the chat agent."""
    definitions: list[dict[str, Any]] = []
    for name in MCP_TOOL_NAMES:
        spec = TOOL_BY_NAME[name]
        definitions.append(
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": parameter_schema(spec.name),
            }
        )
    return definitions


def _strip_reserved_keys(arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Split the reserved ``sceneId`` routing key from tool arguments."""
    remaining = {
        key: value for key, value in arguments.items() if key not in RESERVED_ARGUMENT_KEYS
    }
    scene_id = arguments.get("sceneId")
    return remaining, str(scene_id) if isinstance(scene_id, str) and scene_id else None


def _refusal_payload(code: str, message: str, *, suggest: str | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "refusals": [make_refusal(code, message, suggest=suggest).model_dump(by_alias=True)],
    }


def _missing_scene_id(name: str) -> dict[str, Any]:
    return _refusal_payload(
        "invalid_input",
        f"{name} requires a sceneId argument.",
        suggest='Pass {"sceneId": "scene_..."} alongside the tool arguments.',
    )


class McpContext:
    """Standalone process context: engine, repository, registry, catalog."""

    def __init__(
        self,
        *,
        settings: Settings,
        registry: SceneRegistry | None = None,
        catalog: CatalogService | None = None,
    ) -> None:
        self.settings = settings
        self._owns_engine = registry is None
        self.catalog = catalog or CatalogService()
        self.rules = get_rules()
        if registry is not None:
            self.registry = registry
            self._engine = None
            return
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(settings=settings)
        session_factory = create_session_factory(self._engine)
        self.repository = SceneRepository(session_factory)
        self.registry = SceneRegistry(self.repository, catalog=self.catalog.by_kind)

    async def startup(self) -> None:
        """Create tables outside production (mirrors create_app's dev convenience)."""
        if self._engine is None or self.settings.environment == "production":
            return
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def aclose(self) -> None:
        if self._owns_engine and self._engine is not None:
            await self._engine.dispose()

    async def execute_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_arguments, scene_id = _strip_reserved_keys(arguments)
        if not scene_id:
            return _missing_scene_id(name)
        try:
            if name in {"query_scene", "search_catalog"}:
                return await self._execute_read(name, scene_id, tool_arguments)
            if name in WRITE_TOOL_NAMES:
                return await self._execute_write(name, scene_id, tool_arguments)
        except SceneError as error:
            return _refusal_payload(error.code, error.message, suggest=error.suggest)
        except GeometryError as error:
            return _refusal_payload(error.code, error.message, suggest=error.suggest)
        return _refusal_payload(
            "invalid_input",
            f"Tool {name!r} is not part of the MCP tool set.",
            suggest="Use one of: " + ", ".join(MCP_TOOL_NAMES) + ".",
        )

    async def _execute_read(
        self,
        name: str,
        scene_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        execution = await self._executor(scene_id).execute(name, arguments)
        return execution.payload

    def _executor(self, scene_id: str) -> ToolExecutor:
        async def _no_ask_user(*_args: Any) -> dict[str, list[str]]:
            raise RuntimeError("ask_user is not available over MCP")

        return ToolExecutor(
            registry=self.registry,
            catalog=self.catalog.by_kind,
            rules=self.rules,
            scene_id=scene_id,
            turn_id="mcp",
            ask_user=_no_ask_user,
        )

    async def _execute_write(
        self,
        name: str,
        scene_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            model = validate_arguments(name, arguments)
        except SceneError as error:
            return _refusal_payload(error.code, error.message, suggest=error.suggest)

        view = await self.registry.overlay_view(scene_id)
        document = view.document
        try:
            if isinstance(model, AddItemInput):
                result = place_add(
                    document,
                    model,
                    catalog=self.catalog.by_kind,
                    rules=self.rules,
                    strict=True,
                )
            elif isinstance(model, MoveItemInput):
                result = place_move(
                    document,
                    model,
                    catalog=self.catalog.by_kind,
                    rules=self.rules,
                    strict=True,
                )
            elif isinstance(model, RemoveItemInput):
                result = place_remove(document, model.item_id)
            else:  # pragma: no cover - write tools are covered by the three above
                return _refusal_payload(
                    "invalid_input",
                    f"Tool {name!r} is not a supported write tool.",
                    suggest="Use one of: " + ", ".join(sorted(WRITE_TOOL_NAMES)) + ".",
                )
        except (SceneError, GeometryError) as error:
            return _refusal_payload(error.code, error.message, suggest=error.suggest)
        if result.refusals:
            return {
                "ok": False,
                "refusals": [
                    refusal.model_dump(by_alias=True)
                    for refusal in dedupe_refusals(list(result.refusals))
                ],
            }

        outcome = await self.registry.commit(
            scene_id,
            list(result.ops),
            base_version=document.version,
            actor=Actor.AGENT,
        )
        return {
            "ok": True,
            "version": outcome.document.version,
            "ops": [op.model_dump(mode="json", by_alias=True) for op in outcome.patch.ops],
            "preview": _ops_preview(outcome.patch.ops),
            "needsConfirmation": False,
            "appliedDirectly": True,
        }


def _ops_preview(ops: list[Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    for op in ops:
        if op.op == "item.add":
            preview = {
                "itemId": op.item.id,
                "kindId": op.item.kind_id,
                "variantId": op.item.variant_id,
                "position": list(op.item.transform.position),
                "rotationY": op.item.transform.rotation_y,
            }
        elif op.op in {"item.update", "item.remove"}:
            preview = {"itemId": op.item_id}
    return preview


class McpServer:
    """JSON-RPC dispatch shared by stdio serving and tests."""

    def __init__(self, context: McpContext) -> None:
        self.context = context

    async def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        message_id = message.get("id")
        if not isinstance(method, str):
            return _error_response(message_id, -32600, "Invalid Request: missing method.")
        if method == "initialize":
            return _result_response(
                message_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            )
        if method == "ping":
            return _result_response(message_id, {})
        if method == "tools/list":
            return _result_response(message_id, {"tools": mcp_tool_definitions()})
        if method == "tools/call":
            return await self._handle_tools_call(message_id, message.get("params") or {})
        # Notifications have no id and expect no response at all.
        if message_id is None:
            return None
        return _error_response(message_id, -32601, f"Method not found: {method}")

    async def _handle_tools_call(
        self,
        message_id: Any,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error_response(message_id, -32602, "Invalid params for tools/call.")
        try:
            payload = await self.context.execute_tool(name, arguments)
        except Exception as error:  # noqa: BLE001 - report as tool error, never crash
            payload = _refusal_payload("internal_error", f"MCP tool crashed: {error}")
        text = json.dumps(payload, ensure_ascii=False)
        return _result_response(
            message_id,
            {
                "content": [{"type": "text", "text": text}],
                "isError": payload.get("ok") is not True,
            },
        )


def _result_response(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error_response(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _write_line(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


async def serve(context: McpContext) -> None:
    """Read newline-delimited JSON-RPC from stdin until EOF."""
    server = McpServer(context)
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write_line(_error_response(None, -32700, "Parse error."))
            continue
        if not isinstance(message, dict):
            _write_line(_error_response(None, -32600, "Invalid Request."))
            continue
        response = await server.handle_message(message)
        if response is not None:
            _write_line(response)


async def run(settings: Settings | None = None) -> None:
    """Entry point: build the context and serve until stdin closes."""
    context = McpContext(settings=settings or Settings())
    await context.startup()
    try:
        await serve(context)
    finally:
        await context.aclose()
