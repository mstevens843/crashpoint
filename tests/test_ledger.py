"""The ledger must count attempts and DISTINCT side effects correctly, chain tamper-evidently, and
the idempotency-key derivation must be stable and refuse per-attempt fields."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crashpoint.ledger.core import LedgerState
from crashpoint.ledger.idempotency import (
    ForbiddenIdentityField,
    derive_idempotency_key,
)


def test_keyless_calls_are_each_a_distinct_side_effect(tmp_path: Path) -> None:
    led = LedgerState(path=tmp_path / "c.jsonl")
    led.execute("i", None, {})
    led.execute("i", None, {})
    assert led.attempts["i"] == 2
    assert led.side_effects["i"] == 2  # naive: two crossings


def test_keyed_calls_dedupe_to_one_side_effect(tmp_path: Path) -> None:
    led = LedgerState(path=tmp_path / "c.jsonl")
    k = "cp1key_abc"
    led.execute("i", k, {})
    led.execute("i", k, {})  # a re-run with the same key
    assert led.attempts["i"] == 2  # both attempts recorded
    assert led.side_effects["i"] == 1  # but one distinct effect


def test_receipt_is_opaque_and_constant(tmp_path: Path) -> None:
    led = LedgerState(path=tmp_path / "c.jsonl")
    r1 = led.execute("i", "k", {})
    r2 = led.execute("i", "k", {})  # deduped repeat
    assert r1 == r2  # the caller cannot read exactly-once off the wire


def test_chain_verifies_and_detects_tampering(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    led = LedgerState(path=p)
    led.execute("i", None, {})
    led.execute("i", None, {})
    assert LedgerState.verify(p) == (True, -1)
    lines = p.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["record"]["attempt"] = 99
    lines[0] = json.dumps(entry, sort_keys=True)
    p.write_text("\n".join(lines) + "\n")
    ok, broken = LedgerState.verify(p)
    assert not ok and broken == 0


def test_idempotency_key_is_stable_over_content() -> None:
    a = derive_idempotency_key("charges", "order-7", 1, {"amount": 100, "to": "acct-9"})
    b = derive_idempotency_key("charges", "order-7", 1, {"to": "acct-9", "amount": 100})
    assert a == b and a.startswith("cp1key_")


def test_idempotency_key_refuses_per_attempt_fields() -> None:
    for bad in ({"attempt": 2}, {"retryCount": 1}, {"nested": {"epoch": 5}}, {"delivery_id": "x"}):
        with pytest.raises(ForbiddenIdentityField):
            derive_idempotency_key("n", "s", 1, bad)
