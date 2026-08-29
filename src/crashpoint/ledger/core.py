"""The ledger's in-process logic: record every attempt, count distinct side effects, hash-chain the
record, and detect tampering. The socket daemon in ``daemon.py`` wraps this; this module is pure
logic over a file path and is host-testable with no sockets.

WHAT MAKES IT THE GROUND TRUTH. It records EVERY execute call as an ATTEMPT, and separately counts
DISTINCT SIDE EFFECTS - a keyless call is always a distinct effect, a keyed call is deduped by key.
So the true side-effect count is observed here, not self-reported by the runtime, and the oracle
reads exactly-once off the effect count, never off the wire. Every record is chained
`h_i = sha256(h_{i-1} || canonical(record))`, so any edit, delete, or reorder breaks the chain and
the run is VOID.

IT ALSO DIGESTS THE PAYLOAD. Counting crossings alone cannot tell "the same charge twice" from "two
DIFFERENT charges", and those are different failures - the second is worse, because you cannot
reconcile it by matching. So the ledger digests each distinct effect's payload and keeps the digests
per intent; the oracle reads DUPLICATED vs DIVERGED off whether they agree. The digest is computed
HERE, by the ledger, from the payload it was handed - the subject never reports it, and cannot see
it. The invoke socket still returns only the opaque constant receipt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..canonical import canonicalize, chain, sha256_hex

GENESIS = "crashpoint-ledger-genesis-cp1"


@dataclass
class LedgerState:
    """One trial's ledger. `intent_id` groups the executes for a single logical action."""

    path: Path
    head: str = GENESIS
    count: int = 0
    attempts: dict[str, int] = field(default_factory=dict)
    side_effects: dict[str, int] = field(default_factory=dict)
    # One entry per DISTINCT side effect, in crossing order: what crossed, and under which key.
    effect_digests: dict[str, list[str]] = field(default_factory=dict)
    effect_keys: dict[str, list[str | None]] = field(default_factory=dict)
    _seen_keys: dict[str, set[str]] = field(default_factory=dict)

    def execute(self, intent_id: str, key: str | None, payload: dict[str, object]) -> str:
        """Record one external-effect attempt. Returns an IMPOVERISHED receipt - identical for a
        first call and a deduped repeat, so the caller cannot read exactly-once off the wire."""
        self.attempts[intent_id] = self.attempts.get(intent_id, 0) + 1
        digest = sha256_hex(canonicalize(payload))
        deduped = False
        if key:
            seen = self._seen_keys.setdefault(intent_id, set())
            if key in seen:
                deduped = True
            else:
                seen.add(key)
                self._crossed(intent_id, key, digest)
        else:
            # A keyless (naive) call is always a distinct side effect.
            self._crossed(intent_id, None, digest)
        record = {
            "op": "execute",
            "intent_id": intent_id,
            "keyed": bool(key),
            "attempt": self.attempts[intent_id],
            "deduped": deduped,
            # Chained too, so a redraw between attempts is itself tamper-evident.
            "payload_digest": digest,
        }
        self._append(record)
        return "receipt-ok"  # deliberately opaque and constant

    def _crossed(self, intent_id: str, key: str | None, digest: str) -> None:
        """Record one DISTINCT side effect: the count, what crossed, and its key."""
        self.side_effects[intent_id] = self.side_effects.get(intent_id, 0) + 1
        self.effect_digests.setdefault(intent_id, []).append(digest)
        self.effect_keys.setdefault(intent_id, []).append(key)

    def _append(self, record: dict[str, object]) -> None:
        new_head = chain(self.head, record)
        line = json.dumps(
            {"i": self.count, "prev": self.head, "record": record, "hash": new_head},
            sort_keys=True,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self.head = new_head
        self.count += 1

    def dump(self) -> dict[str, object]:
        return {
            "attempts": dict(self.attempts),
            "side_effects": dict(self.side_effects),
            "effect_digests": {k: list(v) for k, v in self.effect_digests.items()},
            "effect_keys": {k: list(v) for k, v in self.effect_keys.items()},
            "head": self.head,
            "count": self.count,
        }

    @staticmethod
    def verify(path: Path) -> tuple[bool, int]:
        """Recompute the chain from disk. Returns (ok, first_broken_index or -1)."""
        head = GENESIS
        idx = -1
        if not path.exists():
            return (True, -1)
        with path.open(encoding="utf-8") as fh:
            for idx, raw in enumerate(fh):
                entry = json.loads(raw)
                if entry.get("prev") != head:
                    return (False, idx)
                expect = chain(head, entry["record"])
                if entry.get("hash") != expect:
                    return (False, idx)
                head = expect
        return (True, -1)
