"""The ledger's in-process logic: record every attempt, count distinct side effects, hash-chain the
record, and detect tampering. The socket daemon in ``daemon.py`` wraps this; this module is pure
logic over a file path and is host-testable with no sockets.

WHAT MAKES IT THE GROUND TRUTH. It records EVERY execute call as an ATTEMPT, and separately counts
DISTINCT SIDE EFFECTS - a keyless call is always a distinct effect, a keyed call is deduped by key.
So the true side-effect count is observed here, not self-reported by the runtime, and the oracle
reads exactly-once off the effect count, never off the wire. Every record is chained
`h_i = sha256(h_{i-1} || canonical(record))`, so any edit, delete, or reorder breaks the chain and
the run is VOID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..canonical import chain

GENESIS = "crashpoint-ledger-genesis-cp1"


@dataclass
class LedgerState:
    """One trial's ledger. `intent_id` groups the executes for a single logical action."""

    path: Path
    head: str = GENESIS
    count: int = 0
    attempts: dict[str, int] = field(default_factory=dict)
    side_effects: dict[str, int] = field(default_factory=dict)
    _seen_keys: dict[str, set[str]] = field(default_factory=dict)

    def execute(self, intent_id: str, key: str | None, payload: dict[str, object]) -> str:
        """Record one external-effect attempt. Returns an IMPOVERISHED receipt - identical for a
        first call and a deduped repeat, so the caller cannot read exactly-once off the wire."""
        self.attempts[intent_id] = self.attempts.get(intent_id, 0) + 1
        deduped = False
        if key:
            seen = self._seen_keys.setdefault(intent_id, set())
            if key in seen:
                deduped = True
            else:
                seen.add(key)
                self.side_effects[intent_id] = self.side_effects.get(intent_id, 0) + 1
        else:
            # A keyless (naive) call is always a distinct side effect.
            self.side_effects[intent_id] = self.side_effects.get(intent_id, 0) + 1
        record = {
            "op": "execute",
            "intent_id": intent_id,
            "keyed": bool(key),
            "attempt": self.attempts[intent_id],
            "deduped": deduped,
        }
        self._append(record)
        return "receipt-ok"  # deliberately opaque and constant

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
