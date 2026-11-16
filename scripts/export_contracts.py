#!/usr/bin/env python3
"""Export REST OpenAPI and WebSocket JSON Schema artifacts.

The Pydantic models in ``apps/server/app/contracts`` are the only source of
truth (PRD D6 / §5.6). Output is deterministic: keys are sorted and files end
with a newline, so CI can fail on any drift with a plain ``git diff``.

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

if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from pydantic import TypeAdapter  # noqa: E402

from app.contracts.ws import WsEnvelope  # noqa: E402
from app.main import create_app  # noqa: E402


def write_json(path: Path, payload: Any) -> None:
    """Write canonical JSON (sorted keys, UTF-8, LF line endings)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{text}\n")
    print(f"wrote {path.relative_to(REPO_ROOT).as_posix()}")


def build_ws_schema() -> dict[str, Any]:
    """Bundle the WS envelope model and its dependencies into one JSON Schema.

    Draft-07 with ``definitions`` is used because the front-end generator
    (json-schema-to-typescript) resolves that reliably; M0 ships the envelope
    only. The discriminated union of turn/state/host events is frozen at the
    end of M1 (PRD §6.7).
    """
    envelope_schema = TypeAdapter(WsEnvelope).json_schema(
        mode="validation",
        ref_template="#/definitions/{model}",
    )
    definitions = envelope_schema.pop("$defs", {})
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": "https://ai-layout.local/contracts/ws.schema.json",
        "title": "WsEnvelope",
        "description": (
            "M0 envelope contract. The full turn/state/host event union is "
            "frozen at the end of M1."
        ),
        "$ref": "#/definitions/WsEnvelope",
        "definitions": {"WsEnvelope": envelope_schema, **definitions},
    }


def main() -> int:
    app = create_app()
    write_json(OUTPUT_DIR / "openapi.json", app.openapi())
    write_json(OUTPUT_DIR / "ws.schema.json", build_ws_schema())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())