#!/usr/bin/env python3
"""Export REST OpenAPI, WebSocket and tool JSON Schema artifacts.

The Pydantic models in ``apps/server/app/contracts`` are the only source of
truth (PRD D6 / §5.6). Output is deterministic: keys are sorted and files end
with a newline, so CI can fail on any drift with ``pnpm contracts:check``.

Usage (from the repository root):

    uv run --directory apps/server python ../../scripts/export_contracts.py

or simply ``pnpm contracts:export``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = REPO_ROOT / "apps" / "server"
OUTPUT_DIR = REPO_ROOT / "packages" / "contracts" / "generated"
DRAFT_07 = "http://json-schema.org/draft-07/schema#"

if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from pydantic import TypeAdapter  # noqa: E402

from app.agent.registry import tool_bundle  # noqa: E402
from app.contracts.tools import ToolBundle  # noqa: E402
from app.contracts.ws import WS_MODELS, WsEnvelope  # noqa: E402
from app.main import create_app  # noqa: E402


def write_json(path: Path, payload: Any) -> None:
    """Write canonical JSON (sorted keys, UTF-8, LF line endings)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{text}\n")
    print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")


def _downgrade_tuples(node: Any) -> None:
    """Convert Pydantic's 2020-12 ``prefixItems`` tuples to draft-07 arrays."""
    if isinstance(node, dict):
        prefix_items = node.pop("prefixItems", None)
        if isinstance(prefix_items, list):
            node["items"] = prefix_items
            node["minItems"] = len(prefix_items)
            node["maxItems"] = len(prefix_items)
            node["additionalItems"] = False
        for value in node.values():
            _downgrade_tuples(value)
    elif isinstance(node, list):
        for value in node:
            _downgrade_tuples(value)


def _model_schema(model: type) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = TypeAdapter(model).json_schema(
        mode="validation",
        ref_template="#/definitions/{model}",
    )
    definitions = schema.pop("$defs", {})
    return schema, definitions


def build_ws_schema() -> dict[str, Any]:
    """Bundle every WS message/payload model and its dependencies.

    Draft-07 with ``definitions`` is used because json-schema-to-typescript
    resolves that reliably. A wrapper object references every exported model
    so the TypeScript generator emits types for all of them (an envelope's
    payload is a generic dict at runtime).
    """
    definitions: dict[str, Any] = {}
    for model in WS_MODELS:
        schema, nested = _model_schema(model)
        definitions.setdefault(model.__name__, schema)
        for name, nested_schema in nested.items():
            definitions.setdefault(name, nested_schema)

    wrapper_name = "WebSocketContracts"
    definitions[wrapper_name] = {
        "type": "object",
        "title": wrapper_name,
        "description": "All M1 WebSocket message and payload shapes.",
        "properties": {
            model.__name__: {"$ref": f"#/definitions/{model.__name__}"}
            for model in WS_MODELS
        },
        "additionalProperties": False,
    }
    bundle = {
        "$schema": DRAFT_07,
        "$id": "https://ai-layout.local/contracts/ws.schema.json",
        "title": "WebSocket contracts",
        "description": (
            "M1 WebSocket protocol: envelope, state/turn payloads and the "
            "client -> server host message union."
        ),
        "$ref": f"#/definitions/{wrapper_name}",
        "definitions": definitions,
    }
    _downgrade_tuples(bundle)
    return bundle


def build_tools_schema() -> dict[str, Any]:
    """Tool contract bundle populated from the M3 ToolRegistry."""
    bundle_model = tool_bundle()
    schema, definitions = _model_schema(ToolBundle)
    bundle = {
        "$schema": DRAFT_07,
        "$id": "https://ai-layout.local/contracts/tools.schema.json",
        "title": "Agent tool contracts",
        "description": (
            "Tool names, descriptions and Pydantic-derived parameter schemas. "
            "The registry is the single source; the default value pins the "
            "nine M3/M4 tools for contract drift checks."
        ),
        "$ref": "#/definitions/ToolBundle",
        "definitions": {"ToolBundle": schema, **definitions},
        "default": bundle_model.model_dump(mode="json", by_alias=True),
    }
    _downgrade_tuples(bundle)
    return bundle


def main() -> int:
    app = create_app()
    write_json(OUTPUT_DIR / "openapi.json", app.openapi())
    write_json(OUTPUT_DIR / "ws.schema.json", build_ws_schema())
    write_json(OUTPUT_DIR / "tools.schema.json", build_tools_schema())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())