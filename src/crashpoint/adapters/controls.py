"""The control adapters: deterministic fixtures with no real runtime, one per outcome the oracle
must be able to produce. They calibrate the oracle - if the harness cannot make it report
DUPLICATED, LOST, and EXACTLY_ONCE on demand, the oracle has no teeth - and they need no LangGraph,
Temporal, or Postgres, so they are the guaranteed-green core.

Each control writes its own completion marker (its 'persist') and, on recovery, a durable control
skips a step whose marker is set. The (effect, persist) ORDER and whether the effect is idempotent
are what distinguish them:
  dup_control   effect-then-persist, naive        -> crash between duplicates on re-run
  lost_control  persist-then-effect, naive        -> crash between skips an effect that never ran
  idem_reference effect-then-persist, idempotent   -> the ledger dedups the re-run
  null_baseline no durability                     -> re-run from the top duplicates after the effect
  diverge_control effect-then-persist, idempotent, NONDETERMINISTIC
                                                  -> the re-run redraws, so it derives a different
                                                     key, the dedup misses, and the two crossings
                                                     differ: DIVERGED, the outcome an idempotent
                                                     boundary cannot reach

Run: `python -m crashpoint.adapters.controls --kind dup --ledger <sock> --marker <path>
--intent <id> --barrier b0|b1|b2|none --recovery 0|1`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .base import crash, effect

# kind -> (durable, persist_then_effect, idempotent, nondeterministic)
_KINDS: dict[str, tuple[bool, bool, bool, bool]] = {
    "dup": (True, False, False, False),
    "lost": (True, True, False, False),
    "idem": (True, False, True, False),
    "null": (False, False, False, False),
    "diverge": (True, False, True, True),
}


def run(
    kind: str, invoke_path: str, marker: Path, intent: str, barrier: str, recovery: bool
) -> None:
    durable, persist_then_effect, idempotent, nondeterministic = _KINDS[kind]

    def do_effect() -> None:
        effect(invoke_path, intent, idempotent, nondeterministic)

    def do_persist() -> None:
        marker.write_text("done")

    if recovery:
        # A durable runtime consults its completion marker and skips a finished step.
        if durable and marker.exists():
            return
        do_effect()
        do_persist()
        return

    # The crash run.
    if persist_then_effect:
        if barrier == "b0":
            crash()
        do_persist()
        if barrier == "b1":
            crash()
        do_effect()
        if barrier == "b2":
            crash()
    else:
        if barrier == "b0":
            crash()
        do_effect()
        if barrier == "b1":
            crash()
        do_persist()
        if barrier == "b2":
            crash()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", required=True, choices=sorted(_KINDS))
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--marker", required=True)
    ap.add_argument("--intent", required=True)
    ap.add_argument("--barrier", required=True, choices=["b0", "b1", "b2", "none"])
    ap.add_argument("--recovery", type=int, default=0)
    args = ap.parse_args(argv)
    run(args.kind, args.ledger, Path(args.marker), args.intent, args.barrier, bool(args.recovery))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
