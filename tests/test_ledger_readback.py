from __future__ import annotations

import json
from pathlib import Path

import pytest

from crashpoint.harness import ledger_readback as lr
from crashpoint.ledger.core import GENESIS, LedgerState


def _make_ledger_bytes(
    tmp_path: Path, records: list[tuple[str, str | None, dict[str, object], str | None]]
) -> bytes:
    """Build real, correctly hash-chained ledger bytes using the actual ledger's own write path,
    rather than hand-crafted JSON, so these tests exercise the real on-disk record shape."""
    path = tmp_path / "ledger.jsonl"
    state = LedgerState(path=path)
    for intent_id, key, payload, attempt_id in records:
        state.execute(intent_id, key, payload, attempt_id=attempt_id)
    return path.read_bytes()


def test_empty_bytes_is_a_valid_empty_chain() -> None:
    readback = lr.parse_ledger_bytes(b"")
    assert readback.chain_valid is True
    assert readback.first_broken_index == -1
    assert readback.record_count == 0
    assert readback.final_head == GENESIS
    assert readback.effect_count("anything") == 0


def test_single_execute_record_is_recovered_from_bytes(tmp_path: Path) -> None:
    raw = _make_ledger_bytes(tmp_path, [("intent-a", None, {"x": 1}, "intent-a:attempt-1")])
    readback = lr.parse_ledger_bytes(raw)
    assert readback.chain_valid is True
    assert readback.record_count == 1
    assert readback.effect_count("intent-a") == 1
    assert readback.effect_count("intent-b") == 0  # never appears: a legitimate zero, not missing
    assert len(readback.effect_digests["intent-a"]) == 1
    assert readback.effect_attempt_ids["intent-a"] == ["intent-a:attempt-1"]
    assert readback.raw_sha256 == lr.sha256_bytes(raw)
    assert readback.byte_length == len(raw)


def test_keyed_duplicate_call_is_deduped_and_not_a_second_distinct_effect(tmp_path: Path) -> None:
    raw = _make_ledger_bytes(
        tmp_path,
        [
            ("intent-a", "idem-key", {"x": 1}, "attempt-1"),
            ("intent-a", "idem-key", {"x": 1}, "attempt-2"),
        ],
    )
    readback = lr.parse_ledger_bytes(raw)
    assert readback.chain_valid is True
    assert readback.attempts["intent-a"] == 2
    assert readback.effect_count("intent-a") == 1  # the ledger's own dedup rule, read from bytes


def test_two_keyless_calls_are_two_distinct_crossings(tmp_path: Path) -> None:
    raw = _make_ledger_bytes(
        tmp_path,
        [
            ("intent-a", None, {"x": 1}, "attempt-1"),
            ("intent-a", None, {"x": 1}, "attempt-2"),
        ],
    )
    readback = lr.parse_ledger_bytes(raw)
    assert readback.effect_count("intent-a") == 2
    assert len(set(readback.effect_digests["intent-a"])) == 1  # same payload: DUPLICATED shape


def test_two_keyless_calls_with_different_payloads_diverge(tmp_path: Path) -> None:
    raw = _make_ledger_bytes(
        tmp_path,
        [
            ("intent-a", None, {"x": 1}, "attempt-1"),
            ("intent-a", None, {"x": 2}, "attempt-2"),
        ],
    )
    readback = lr.parse_ledger_bytes(raw)
    assert readback.effect_count("intent-a") == 2
    assert len(set(readback.effect_digests["intent-a"])) == 2  # different payloads: DIVERGED shape


def test_malformed_json_line_raises_ledger_bytes_corrupt() -> None:
    with pytest.raises(lr.LedgerBytesCorrupt, match="invalid JSON"):
        lr.aggregate_records(b"not json at all\n")
    with pytest.raises(lr.LedgerBytesCorrupt, match="invalid JSON"):
        lr.parse_ledger_bytes(b"not json at all\n")


def test_execute_record_missing_intent_id_raises(tmp_path: Path) -> None:
    raw = _make_ledger_bytes(tmp_path, [("intent-a", None, {"x": 1}, None)])
    entry = json.loads(raw.decode().strip())
    del entry["record"]["intent_id"]
    mutated = (json.dumps(entry) + "\n").encode()
    with pytest.raises(lr.LedgerBytesCorrupt, match="intent_id"):
        lr.aggregate_records(mutated)


def test_broken_chain_is_reported_not_raised(tmp_path: Path) -> None:
    raw = _make_ledger_bytes(
        tmp_path,
        [
            ("intent-a", None, {"x": 1}, None),
            ("intent-a", None, {"x": 2}, None),
        ],
    )
    lines = raw.decode().splitlines()
    entry = json.loads(lines[1])
    entry["record"]["payload_digest"] = "0" * 64  # tamper with the second record's content
    lines[1] = json.dumps(entry)
    tampered = ("\n".join(lines) + "\n").encode()

    valid, broken_idx = lr.verify_chain_bytes(tampered)
    assert valid is False
    assert broken_idx == 1

    readback = lr.parse_ledger_bytes(tampered)
    assert readback.chain_valid is False
    assert readback.first_broken_index == 1
    # aggregation is still best-effort/diagnostic even though the chain is broken; the caller (the
    # observer/verifier), not this module, decides whether to trust these numbers.
    assert readback.effect_count("intent-a") == 2


def test_truncating_the_trailing_record_yields_a_valid_shorter_chain(tmp_path: Path) -> None:
    """A hash chain proves nothing EARLIER was edited or reordered once something later exists on
    top of it; it does not by itself prove nothing was ever appended after the last retained line.
    Dropping the trailing record leaves a chain that still verifies - shorter, and honestly
    reporting fewer effects, not flagged CORRUPT. This is a property of the chain construction
    itself (shared with ``LedgerState.verify``), not a gap introduced by this module."""
    raw = _make_ledger_bytes(
        tmp_path,
        [
            ("intent-a", None, {"x": 1}, None),
            ("intent-a", None, {"x": 2}, None),
        ],
    )
    lines = raw.decode().splitlines()
    truncated = (lines[0] + "\n").encode()  # drop the second record entirely
    valid, _ = lr.verify_chain_bytes(truncated)
    assert valid is True
    readback = lr.parse_ledger_bytes(truncated)
    assert readback.effect_count("intent-a") == 1
