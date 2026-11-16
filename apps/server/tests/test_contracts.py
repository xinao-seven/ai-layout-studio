"""Contract model and shared rules tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts.ws import WsChannel, WsEnvelope

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_PATH = REPO_ROOT / "packages" / "contracts" / "rules.json"


def test_ws_envelope_serialises_camel_case() -> None:
    envelope = WsEnvelope(
        ch=WsChannel.TURN,
        type="turn.started",
        seq=1,
        ts=1_763_000_000_000,
        turn_id="turn_1",
    )

    payload = json.loads(envelope.model_dump_json())

    assert payload["turnId"] == "turn_1"
    assert payload["ch"] == "turn"
    assert payload["v"] == 1
    assert "turn_id" not in payload


def test_ws_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError):
        WsEnvelope.model_validate(
            {
                "ch": "turn",
                "type": "turn.started",
                "seq": 0,
                "ts": 0,
                "unexpected": True,
            }
        )


def test_rules_json_has_consistent_derived_inset() -> None:
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))

    assert rules["ROOM_INSET"] == pytest.approx(rules["WALL_THICKNESS"] / 2 + 0.02)
    assert rules["WALL_HEIGHT"] > 0
    assert rules["SOLVER_BEAM_WIDTH"] > 0
    assert rules["SOLVER_ROTATIONS"] == [0, 90, 180, 270]
