"""The oracle classifies a ledger dump into the five outcomes, fail-closed."""

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


def _run_varying(tmp_path: Path, keyed: bool) -> tuple[dict[str, object], Path]:
    """Two crossings whose PAYLOADS differ - the nondeterministic-step shape. When keyed, the key
    is derived from the payload, so it differs too and the dedup never fires."""
    p = tmp_path / "c.jsonl"
    led = LedgerState(path=p)
    for n in (1, 2):
        led.execute("i", f"cp1key_{n}" if keyed else None, {"memo": f"draw-{n}"})
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


def test_two_differing_crossings_are_diverged_not_duplicated(tmp_path: Path) -> None:
    # The distinction the count alone cannot make: two DIFFERENT charges, not one charge twice.
    dump, p = _run_varying(tmp_path, keyed=False)
    assert classify("i", dump, p) is Outcome.DIVERGED


def test_a_keyed_nondeterministic_step_diverges(tmp_path: Path) -> None:
    # The idempotent boundary is present and correctly content-derived; it simply never matches,
    # because the content is not reproducible. This is the finding, at the oracle level.
    dump, p = _run_varying(tmp_path, keyed=True)
    assert classify("i", dump, p) is Outcome.DIVERGED


def test_multiple_crossings_without_digests_are_void_not_guessed(tmp_path: Path) -> None:
    # Fail-closed: with two crossings and no per-crossing digest, DUPLICATED and DIVERGED are
    # indistinguishable, so the oracle refuses to pick one.
    _, p = _run(tmp_path, None, 2)
    assert classify("i", {"side_effects": {"i": 2}}, p) is Outcome.VOID
