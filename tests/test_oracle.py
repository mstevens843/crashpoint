"""The oracle classifies a ledger dump into the four outcomes, fail-closed."""

from __future__ import annotations

from pathlib import Path

from crashpoint.ledger.core import LedgerState
from crashpoint.ledger.oracle import classify
from crashpoint.model.layers import Outcome


def _run(tmp_path: Path, key: str | None, n: int) -> tuple[dict[str, object], Path]:
    p = tmp_path / "c.jsonl"
    led = LedgerState(path=p)
    for _ in range(n):
        led.execute("i", key, {})
    return led.dump(), p


def test_one_side_effect_is_exactly_once(tmp_path: Path) -> None:
    dump, p = _run(tmp_path, None, 1)
    assert classify("i", dump, p) is Outcome.EXACTLY_ONCE


def test_two_keyless_side_effects_is_duplicated(tmp_path: Path) -> None:
    dump, p = _run(tmp_path, None, 2)
    assert classify("i", dump, p) is Outcome.DUPLICATED


def test_two_keyed_calls_dedupe_to_exactly_once(tmp_path: Path) -> None:
    dump, p = _run(tmp_path, "cp1key_x", 2)
    assert classify("i", dump, p) is Outcome.EXACTLY_ONCE


def test_zero_required_is_lost(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    LedgerState(path=p)  # nothing executed
    assert classify("i", {"side_effects": {}}, p, required=True) is Outcome.LOST


def test_tampered_chain_is_void(tmp_path: Path) -> None:
    import json

    dump, p = _run(tmp_path, None, 1)
    lines = p.read_text().splitlines()
    e = json.loads(lines[0])
    e["record"]["intent_id"] = "other"
    lines[0] = json.dumps(e, sort_keys=True)
    p.write_text("\n".join(lines) + "\n")
    assert classify("i", dump, p) is Outcome.VOID
