"""Canonicalization must give equal meaning an equal hash, and different meaning a different one."""

from __future__ import annotations

import pytest

from crashpoint.canonical import CanonicalError, canonicalize, chain, receipt


def test_key_order_does_not_change_the_hash() -> None:
    assert receipt({"a": 1, "b": 2}) == receipt({"b": 2, "a": 1})


def test_dropped_null_member_equals_absent_member() -> None:
    assert receipt({"a": 1, "b": None}) == receipt({"a": 1})


def test_negative_zero_equals_zero() -> None:
    assert canonicalize(-0.0) == canonicalize(0.0) == "0"


def test_different_values_differ() -> None:
    assert receipt({"a": 1}) != receipt({"a": 2})
    assert receipt([1, 2]) != receipt([2, 1])


def test_non_finite_floats_are_rejected() -> None:
    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(CanonicalError):
            canonicalize(bad)


def test_cycles_are_rejected() -> None:
    d: dict[str, object] = {}
    d["self"] = d
    with pytest.raises(CanonicalError):
        canonicalize(d)


def test_receipt_is_versioned_and_stable() -> None:
    r = receipt({"channel": "c04", "obtained": True})
    assert r.startswith("cp1_") and len(r) == 4 + 64


def test_chain_breaks_on_edit() -> None:
    g = "genesis"
    h1 = chain(g, {"i": 0, "event": "read"})
    h2 = chain(h1, {"i": 1, "event": "read"})
    # editing record 0 changes h1 and therefore h2
    h1b = chain(g, {"i": 0, "event": "READ"})
    h2b = chain(h1b, {"i": 1, "event": "read"})
    assert h2 != h2b
