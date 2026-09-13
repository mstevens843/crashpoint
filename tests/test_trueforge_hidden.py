from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from crashpoint.canonical import receipt
from crashpoint.harness import trueforge_hidden

_ROOT = Path(__file__).resolve().parents[1]
_EVIDENCE = _ROOT / "evidence" / "trueforge_hidden.json"


def test_trueforge_receiver_modes_are_explicit() -> None:
    assert trueforge_hidden.RECEIVER_MODES == ("naive", "idempotent")


def test_decode_body_handles_json_text_and_empty() -> None:
    assert trueforge_hidden._decode_body(b'{"ok":true}') == {"ok": True}
    assert trueforge_hidden._decode_body(b"plain") == "plain"
    assert trueforge_hidden._decode_body(b"") is None


def test_turn_state_is_fail_closed() -> None:
    assert trueforge_hidden._turn_state({"data": {"state": {"status": "running"}}}) == (
        "running",
        None,
    )
    cancelled = {"data": {"state": {"status": "cancelled", "reason": "abandoned"}}}
    assert trueforge_hidden._turn_state(cancelled) == (
        "cancelled",
        "abandoned",
    )
    assert trueforge_hidden._turn_state({}) == ("<invalid>", None)


def test_trueforge_single_trial() -> None:
    executable = (
        trueforge_hidden.DEFAULT_FIXTURE_DIR / "node_modules" / ".bin" / "trueforge"
    )
    if not executable.exists():
        pytest.skip("TrueForge fixture dependencies are not installed")
    record = trueforge_hidden.run(1, "test_trueforge_hidden")
    arms = cast(dict[str, dict[str, Any]], record["arms"])
    assert record["all_agree"] is True
    assert arms["naive"]["outcomes"] == {"duplicated": 1}
    assert arms["idempotent"]["outcomes"] == {"exactly_once": 1}


def test_checked_in_trueforge_evidence_receipt() -> None:
    if not _EVIDENCE.exists():
        pytest.skip("TrueForge evidence absent")
    record = json.loads(_EVIDENCE.read_text())
    body = dict(record)
    body.pop("receipt")
    assert record["receipt"] == receipt(body)
    assert record["runtime"] == "trueforge"
    assert record["experiment_family"] == "mcp_effect_before_tool_response_persist"
    assert record["all_agree"] is True
