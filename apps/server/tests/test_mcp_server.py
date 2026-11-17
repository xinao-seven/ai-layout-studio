"""MCP server tests (PRD FR-21): contract parity + tool execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.catalog.service import CatalogService
from app.core.config import Settings
from app.mcp.server import MCP_TOOL_NAMES, McpContext, McpServer, mcp_tool_definitions
from app.scene.factory import scene_from_fixture

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "scenes"


def load_fixture(name: str) -> dict[str, Any]:
    import json

    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
async def mcp(test_settings: Settings) -> McpContext:
    context = McpContext(settings=test_settings)
    await context.startup()
    yield context
    await context.aclose()


@pytest.fixture
async def seeded_scene(mcp: McpContext) -> str:
    document = scene_from_fixture(load_fixture("studio.json"))
    created = await mcp.registry.create_scene(document)
    return created.id


async def make_server(mcp: McpContext) -> McpServer:
    return McpServer(mcp)


def test_tools_list_matches_chat_contract() -> None:
    from app.agent.registry import TOOL_BY_NAME, TOOL_NAMES, openai_tools

    tools = mcp_tool_definitions()
    assert [tool["name"] for tool in tools] == list(MCP_TOOL_NAMES)
    assert set(MCP_TOOL_NAMES) <= set(TOOL_NAMES)
    chat_openai_payload = {entry["function"]["name"]: entry["function"] for entry in openai_tools()}
    for tool in tools:
        spec = TOOL_BY_NAME[tool["name"]]
        # 契约对等：名称/描述/schema 必须与 chat 工具完全一致。
        assert tool["description"] == spec.description
        assert tool["inputSchema"] == parameter_schema_of(spec.name)
        # 与 OpenAI tools payload 独立比对一次（同一来源、不同装配路径）。
        chat_function = chat_openai_payload[tool["name"]]
        assert chat_function["description"] == tool["description"]
        assert chat_function["parameters"] == tool["inputSchema"]


def parameter_schema_of(name: str) -> dict[str, Any]:
    from app.agent.registry import parameter_schema

    return parameter_schema(name)


async def test_malformed_params_do_not_crash(test_settings: Settings) -> None:
    context = McpContext(settings=test_settings)
    await context.startup()
    try:
        server = McpServer(context)
        response = await server.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1, 2]}
        )
        assert response is not None
        assert response["error"]["code"] == -32602

        response = await server.dispatch(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": "nonsense"}
        )
        assert response is not None
        assert response["error"]["code"] == -32602
    finally:
        await context.aclose()


async def test_initialize_and_ping(test_settings: Settings) -> None:
    context = McpContext(settings=test_settings)
    await context.startup()
    try:
        server = McpServer(context)
        response = await server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        assert response is not None
        assert response["result"]["protocolVersion"] == "2024-11-05"
        assert response["result"]["serverInfo"]["name"] == "ai-layout-workbench"

        # Notifications produce no response.
        assert (
            await server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
            is None
        )

        response = await server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        assert response is not None
        assert response["result"] == {}

        response = await server.handle_message({"jsonrpc": "2.0", "id": 3, "method": "bogus"})
        assert response is not None
        assert response["error"]["code"] == -32601
    finally:
        await context.aclose()


async def test_tools_call_query_scene(test_settings: Settings, seeded_scene: str) -> None:
    context = McpContext(settings=test_settings)
    await context.startup()
    try:
        server = McpServer(context)
        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "query_scene",
                    "arguments": {"sceneId": seeded_scene},
                },
            }
        )
        assert response is not None
        content = response["result"]["content"][0]
        payload = json.loads(content["text"])
        assert response["result"]["isError"] is False
        assert payload["ok"] is True
        assert payload["sceneId"] == seeded_scene
        assert payload["rooms"]
    finally:
        await context.aclose()


async def test_tools_call_add_item_direct_commit(
    test_settings: Settings, seeded_scene: str
) -> None:
    context = McpContext(settings=test_settings)
    await context.startup()
    try:
        document = await context.registry.get_document(seeded_scene)
        room_id = document.rooms[0].id
        catalog = CatalogService()
        kind_id = sorted(catalog.by_kind.keys())[0]
        server = McpServer(context)
        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "add_item",
                    "arguments": {
                        "sceneId": seeded_scene,
                        "kindId": kind_id,
                        "roomId": room_id,
                        "relation": {"type": "center"},
                    },
                },
            }
        )
        assert response is not None
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["ok"] is True, payload
        # MCP 直接落库（无确认 UI），与 chat 的 proposal 流不同但校验相同。
        assert payload["needsConfirmation"] is False
        assert payload["appliedDirectly"] is True
        assert payload["preview"]["kindId"] == kind_id

        document = await context.registry.get_document(seeded_scene)
        assert len(document.items) >= 1

        # L2/L3 refusals still flow through the same refusal envelope.
        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "add_item",
                    "arguments": {
                        "sceneId": seeded_scene,
                        "kindId": "does_not_exist",
                        "roomId": room_id,
                        "relation": {"type": "center"},
                    },
                },
            }
        )
        assert response is not None
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["ok"] is False
        assert payload["refusals"][0]["code"] == "unknown_kind"
        assert response["result"]["isError"] is True
    finally:
        await context.aclose()


async def test_tools_call_requires_scene_id(test_settings: Settings) -> None:
    context = McpContext(settings=test_settings)
    await context.startup()
    try:
        server = McpServer(context)
        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {"name": "query_scene", "arguments": {}},
            }
        )
        assert response is not None
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload["ok"] is False
        assert payload["refusals"][0]["code"] == "invalid_input"
    finally:
        await context.aclose()


async def test_query_scene_matches_chat_payload_shape(
    test_settings: Settings, seeded_scene: str
) -> None:
    """Same scene, same payload as the chat agent's ToolExecutor would produce."""
    context = McpContext(settings=test_settings)
    await context.startup()
    try:
        from app.agent.tools import ToolExecutor

        execution = await ToolExecutor(
            registry=context.registry,
            catalog=context.catalog.by_kind,
            rules=context.rules,
            scene_id=seeded_scene,
            turn_id="chat-test",
            ask_user=_fail_ask_user,
        ).execute("query_scene", {})
        payload = await context.execute_tool("query_scene", {"sceneId": seeded_scene})
        assert payload == execution.payload
    finally:
        await context.aclose()


async def _fail_ask_user(*args: Any) -> dict[str, list[str]]:
    raise AssertionError("ask_user should not be called")


async def test_tools_call_move_and_remove_item(test_settings: Settings, seeded_scene: str) -> None:
    context = McpContext(settings=test_settings)
    await context.startup()
    try:
        document = await context.registry.get_document(seeded_scene)
        room_id = document.rooms[0].id
        catalog = CatalogService()
        kind_id = sorted(catalog.by_kind.keys())[0]

        # execute_tool returns the payload directly (not the MCP envelope).
        payload = await context.execute_tool(
            "add_item",
            {
                "sceneId": seeded_scene,
                "kindId": kind_id,
                "roomId": room_id,
                "relation": {"type": "center"},
            },
        )
        assert payload["ok"] is True, payload
        item_id = payload["preview"]["itemId"]
        document = await context.registry.get_document(seeded_scene)
        added_item = next(candidate for candidate in document.items if candidate.id == item_id)
        before_x = added_item.transform.position[0]
        before_z = added_item.transform.position[2]

        moved = await context.execute_tool(
            "move_item",
            {"sceneId": seeded_scene, "itemId": item_id, "offset": {"dx": 0.5, "dz": -0.3}},
        )
        assert moved["ok"] is True, moved
        assert moved["appliedDirectly"] is True

        document = await context.registry.get_document(seeded_scene)
        item = next(candidate for candidate in document.items if candidate.id == item_id)
        assert item.transform.position[0] == pytest.approx(before_x + 0.5)
        assert item.transform.position[2] == pytest.approx(before_z - 0.3)

        removed = await context.execute_tool(
            "remove_item", {"sceneId": seeded_scene, "itemId": item_id}
        )
        assert removed["ok"] is True, removed
        document = await context.registry.get_document(seeded_scene)
        assert all(candidate.id != item_id for candidate in document.items)
    finally:
        await context.aclose()


async def test_tools_call_unknown_item_refuses(test_settings: Settings, seeded_scene: str) -> None:
    context = McpContext(settings=test_settings)
    await context.startup()
    try:
        payload = await context.execute_tool(
            "remove_item", {"sceneId": seeded_scene, "itemId": "it_missing"}
        )
        assert payload["ok"] is False
        assert payload["refusals"][0]["code"] == "not_found"
    finally:
        await context.aclose()


async def test_notifications_are_silent_and_do_not_execute(
    test_settings: Settings, seeded_scene: str
) -> None:
    context = McpContext(settings=test_settings)
    await context.startup()
    try:
        server = McpServer(context)
        # A request-only method sent as a notification must neither reply nor
        # run side effects.
        assert (
            await server.dispatch(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "query_scene", "arguments": {"sceneId": seeded_scene}},
                }
            )
            is None
        )
        document = await context.registry.get_document(seeded_scene)
        assert document.version == 1  # nothing executed
    finally:
        await context.aclose()
