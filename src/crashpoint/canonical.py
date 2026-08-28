"""Canonical JSON and SHA-256 receipts, so a result can be re-derived and checked by someone who
does not trust the process that produced it.

Ported from the durable-agent-outbox idempotency discipline: sort keys, drop null members, reject
non-finite floats, collapse -0 to 0, refuse types that could serialize two different values the
same way, cap depth, and detect cycles. The point is that two inputs hash equal iff they mean the
same thing, so a receipt over a run is a stable name for that run and nothing else - not its
timestamp, not its key order, not how it was spelled.

This module is allowed the standard library (hashlib, json only for scalars); it is NOT part of the
pure model and is not covered by the model purity contract. It has no third-party dependency.
"""

from __future__ import annotations

import hashlib
from typing import Any

MAX_DEPTH = 64
_SCHEME = "cp1"  # crashpoint receipts, version 1


class CanonicalError(ValueError):
    """Raised when a value cannot be canonicalized without ambiguity."""


def canonicalize(value: Any) -> str:
    """Return the canonical string form of a JSON-shaped value. Total or it raises."""
    out: list[str] = []
    _emit(value, out, depth=0, seen=set())
    return "".join(out)


def _emit(value: Any, out: list[str], depth: int, seen: set[int]) -> None:
    if depth > MAX_DEPTH:
        raise CanonicalError("value nested deeper than MAX_DEPTH")
    if value is None:
        out.append("null")
        return
    if value is True:
        out.append("true")
        return
    if value is False:
        out.append("false")
        return
    if isinstance(value, str):
        out.append(_str(value))
        return
    if isinstance(value, bool):  # pragma: no cover - handled above, kept for clarity
        raise CanonicalError("bool handled earlier")
    if isinstance(value, int):
        out.append(str(value))
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalError("non-finite float is not canonicalizable")
        # -0.0 and 0.0 are the same value for every purpose here.
        out.append("0" if value == 0 else repr(value))
        return
    if isinstance(value, dict):
        oid = id(value)
        if oid in seen:
            raise CanonicalError("cycle detected")
        seen.add(oid)
        try:
            out.append("{")
            first = True
            for key in sorted(value):
                if not isinstance(key, str):
                    raise CanonicalError(f"non-string object key: {key!r}")
                member = value[key]
                if member is None:
                    # A dropped null member: {a: None} and {} mean the same intent, same hash.
                    continue
                if not first:
                    out.append(",")
                first = False
                out.append(_str(key))
                out.append(":")
                _emit(member, out, depth + 1, seen)
            out.append("}")
        finally:
            seen.discard(oid)
        return
    if isinstance(value, (list, tuple)):
        oid = id(value)
        if oid in seen:
            raise CanonicalError("cycle detected")
        seen.add(oid)
        try:
            out.append("[")
            for i, item in enumerate(value):
                if i:
                    out.append(",")
                _emit(item, out, depth + 1, seen)
            out.append("]")
        finally:
            seen.discard(oid)
        return
    raise CanonicalError(f"type {type(value).__name__} is not canonicalizable")


def _str(s: str) -> str:
    # Minimal, deterministic string escaping. json.dumps with sort/ensure_ascii is stable for a
    # single scalar and gives the standard escapes.
    import json

    return json.dumps(s, ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def receipt(value: Any) -> str:
    """A versioned content hash over the canonical form: co1_<64 hex>."""
    return f"{_SCHEME}_{sha256_hex(_SCHEME + ':' + canonicalize(value))}"


def chain(prev_hash: str, record: Any) -> str:
    """Hash-chain step: the new head hashes the previous head with the canonical record, so any
    deletion, reorder, or edit of an earlier record breaks the chain from that point on."""
    return sha256_hex(prev_hash + "|" + canonicalize(record))
