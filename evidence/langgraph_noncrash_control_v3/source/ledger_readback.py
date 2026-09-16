"""Independent, from-bytes parsing of the out-of-process ledger's raw JSONL store.

WHY THIS EXISTS SEPARATELY FROM ``ledger/core.py``. ``LedgerState`` is a live, stateful object built
by replaying ``execute()`` calls; its ``dump()`` is an in-memory summary trusted only because the
caller trusts the process that built it. This module instead takes raw bytes - already read by a
fresh handle, from disk, by a process that did not write them - and re-derives identity, attempt/
effect counts, and effect references directly from what is actually there. The fresh observer and
the offline verifier both import only this module (plus the stdlib and ``canonical``), never
LangGraph, so a bundle can be checked without installing or importing the runtime under test.

THE MISSING-PATH TRAP. ``LedgerState.verify(path)`` treats a nonexistent path as a valid, empty
chain (``(True, -1)``) - correct for its own purpose (a fresh trial's ledger legitimately may not
have been written yet), wrong for this control, where "the store file is absent" and "the store
file exists and is legitimately empty" are different findings: the former is an incomplete
observation (UNKNOWN), the latter is a valid zero-effect observation (a failed positive control).
This module never touches a path or answers that question; it only parses bytes it is handed.
Callers (the observer, the verifier) are responsible for checking existence first and keeping those
two cases apart - see ``langgraph_control_observer.observe`` and
``langgraph_control_verify.verify_bundle``.

WHAT THE RAW BYTES ACTUALLY REPRESENT. Each JSONL line stores its ``intent_id`` directly, in
plaintext, but the effect ``payload`` only as a SHA-256 ``payload_digest`` - the raw payload is
never persisted by the ledger and is not recoverable from these bytes. Identity is direct; payload
content is only ever compared by digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from ..canonical import chain
from ..ledger.core import GENESIS


class LedgerBytesCorrupt(ValueError):
    """Raised when raw ledger bytes cannot be parsed at all: malformed JSON or a record missing a
    field this module depends on. Distinct from a broken hash chain, which parses fine line-by-line
    but fails the linkage check (see ``chain_valid`` / ``first_broken_index`` below)."""


@dataclass(frozen=True)
class LedgerReadback:
    """Everything independently recomputed from one raw byte string of a ledger JSONL store."""

    raw_sha256: str
    byte_length: int
    record_count: int
    chain_valid: bool
    first_broken_index: int  # -1 when chain_valid is True
    final_head: str
    attempts: dict[str, int] = field(default_factory=dict)
    side_effects: dict[str, int] = field(default_factory=dict)
    effect_digests: dict[str, list[str]] = field(default_factory=dict)
    effect_attempt_ids: dict[str, list[str | None]] = field(default_factory=dict)

    def effect_count(self, intent_id: str) -> int:
        """Distinct side-effect crossings for one intent. 0 is a legitimate answer: the store
        parsed and its chain is intact, but this intent never crossed - not the same as a missing
        store."""
        return self.side_effects.get(intent_id, 0)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _lines(raw: bytes) -> list[str]:
    text = raw.decode("utf-8")
    return [line for line in text.split("\n") if line.strip() != ""]


def verify_chain_bytes(raw: bytes) -> tuple[bool, int]:
    """Recompute the hash chain over raw bytes, the same rule as ``LedgerState.verify`` but over an
    in-memory byte string instead of a path, so it works after the bundle has been moved or the
    original ledger process is long gone. Stops at the first broken link, mirroring the original."""
    head = GENESIS
    lines = _lines(raw)
    for idx, raw_line in enumerate(lines):
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            return (False, idx)
        if not isinstance(entry, dict):
            return (False, idx)
        if entry.get("prev") != head:
            return (False, idx)
        record = entry.get("record")
        expected = chain(head, record)
        if entry.get("hash") != expected:
            return (False, idx)
        head = expected
    return (True, -1)


def _final_head(raw: bytes) -> str:
    head = GENESIS
    for raw_line in _lines(raw):
        entry = json.loads(raw_line)
        head = str(entry.get("hash", head))
    return head


_Aggregate = tuple[
    dict[str, int], dict[str, int], dict[str, list[str]], dict[str, list[str | None]]
]


def aggregate_records(raw: bytes) -> _Aggregate:
    """Best-effort replay of every ``execute`` record into (attempts, side_effects,
    effect_digests, effect_attempt_ids), keyed by ``intent_id``, applying the ledger's own dedup
    rule (``deduped``, read directly from the record - the raw idempotency key itself is not
    persisted). Raises ``LedgerBytesCorrupt`` on a line that is not valid JSON or an execute record
    missing a field this aggregation depends on. Does NOT consult ``chain_valid`` - a caller must
    check that separately before trusting these counts (see the module docstring)."""
    attempts: dict[str, int] = {}
    side_effects: dict[str, int] = {}
    effect_digests: dict[str, list[str]] = {}
    effect_attempt_ids: dict[str, list[str | None]] = {}
    for idx, raw_line in enumerate(_lines(raw)):
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise LedgerBytesCorrupt(f"line {idx}: invalid JSON: {exc}") from exc
        if not isinstance(entry, dict):
            raise LedgerBytesCorrupt(f"line {idx}: entry is not a JSON object")
        record = entry.get("record")
        if not isinstance(record, dict):
            raise LedgerBytesCorrupt(f"line {idx}: entry has no record object")
        if record.get("op") != "execute":
            continue
        intent_id = record.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            raise LedgerBytesCorrupt(f"line {idx}: execute record has no string intent_id")
        deduped = record.get("deduped")
        if not isinstance(deduped, bool):
            raise LedgerBytesCorrupt(f"line {idx}: execute record 'deduped' is not a bool")
        digest = record.get("payload_digest")
        if not isinstance(digest, str) or not digest:
            raise LedgerBytesCorrupt(f"line {idx}: execute record has no string payload_digest")
        attempt_id = record.get("attempt_id")
        if attempt_id is not None and not isinstance(attempt_id, str):
            raise LedgerBytesCorrupt(f"line {idx}: execute record 'attempt_id' is not str/null")

        attempts[intent_id] = attempts.get(intent_id, 0) + 1
        if not deduped:
            side_effects[intent_id] = side_effects.get(intent_id, 0) + 1
            effect_digests.setdefault(intent_id, []).append(digest)
            effect_attempt_ids.setdefault(intent_id, []).append(attempt_id)
    return attempts, side_effects, effect_digests, effect_attempt_ids


def parse_ledger_bytes(raw: bytes) -> LedgerReadback:
    """The one entry point callers should use: hash the raw bytes, verify the chain, and
    aggregate per-intent counts/digests/references, all from the bytes alone. Raises
    ``LedgerBytesCorrupt`` if any record cannot be parsed at all; a broken-but-parseable chain is
    reported via ``chain_valid=False`` rather than raised, so the caller can still see where."""
    attempts, side_effects, effect_digests, effect_attempt_ids = aggregate_records(raw)
    chain_valid, first_broken = verify_chain_bytes(raw)
    return LedgerReadback(
        raw_sha256=sha256_bytes(raw),
        byte_length=len(raw),
        record_count=len(_lines(raw)),
        chain_valid=chain_valid,
        first_broken_index=first_broken,
        final_head=_final_head(raw) if raw.strip() else GENESIS,
        attempts=attempts,
        side_effects=side_effects,
        effect_digests=effect_digests,
        effect_attempt_ids=effect_attempt_ids,
    )
