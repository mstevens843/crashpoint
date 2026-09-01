"""The LangGraph adapter: a one-node durable graph whose node performs the external effect, with a
RacingSaver checkpointer that self-SIGKILLs at a named barrier.

This is the seam langchain-ai/langgraph#8039 uses, run under durability="sync" (the mode the issue
is about, where put_writes and the superseding put race on a shared executor). The node calls the
ledger effect and sets a flag; the RacingSaver self-SIGKILLs at one enumerated barrier:
  b0 - after the entry checkpoint is durable but before the node runs (crash on the first put with
       effect not yet done): recovery replays the node and the effect crosses exactly once.
  b1 - after the effect, before the pending writes persist (crash in put_writes): recovery re-runs
       the node, so a naive effect DUPLICATES. This is the dangerous half of the #8039 race.
  b2 - after the superseding checkpoint is durable (crash in put): recovery skips the node, so the
       effect crosses once. This is the safe half of the #8039 race.
Enumerating b1 and b2 removes the production race and shows both of its outcomes deterministically.
Recovery re-invokes with the same thread and checkpoint file, and LangGraph resumes from there.

The `nondet` mode is `idem` plus a value drawn inside the node, so the b1 re-run derives a
different key: the same idempotent boundary, on a node that is not reproducible from its inputs.
The `twophase` mode inserts a durable prepare node before the draw; the charge node may redraw on
re-run, but it reuses the prepared key from checkpointed state.

Run: `python -m crashpoint.adapters.langgraph_adapter --ledger <sock> --checkpoint <db>
--intent <id> --mode naive|idem|nondet|twophase --barrier b0|b1|b2|none --recovery 0|1`.
"""

from __future__ import annotations

import argparse
import sqlite3
from typing import Any, TypedDict

from .base import crash, effect, two_phase_key

# Process-global flags shared between the node and the RacingSaver (one process per run).
_STATE = {"effect_done": False, "writes_persisted": False, "crashed": False}


class _S(TypedDict, total=False):
    started: bool
    done: bool
    effect_key: str


def _build(
    ledger: str, intent: str, mode: str, barrier: str, recovery: bool, checkpoint: str
) -> Any:
    _STATE.update({"effect_done": False, "writes_persisted": False, "crashed": False})
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph

    class RacingSaver(SqliteSaver):
        def put_writes(self, *a: Any, **k: Any) -> Any:
            if barrier == "b1" and _STATE["effect_done"] and not _STATE["crashed"]:
                _STATE["crashed"] = True
                crash()  # never returns: pending writes never persist, recovery re-runs the node
            r = super().put_writes(*a, **k)
            if _STATE["effect_done"]:
                _STATE["writes_persisted"] = True
            return r

        def put(self, *a: Any, **k: Any) -> Any:
            r = super().put(*a, **k)
            if barrier == "b0" and not _STATE["effect_done"] and not _STATE["crashed"]:
                # The entry checkpoint is durable but the node has not run: recovery replays it and
                # the effect crosses once. (A crash BEFORE this checkpoint drops the run entirely -
                # a real behavior, measured separately, not this calibration barrier.)
                _STATE["crashed"] = True
                crash()
            if (barrier == "b2" and _STATE["effect_done"] and _STATE["writes_persisted"]
                    and not _STATE["crashed"]):
                _STATE["crashed"] = True
                crash()  # the completion is durable; recovery will skip the node
            return r

    def charge_node(state: _S) -> _S:
        key = state["effect_key"] if mode == "twophase" else None
        effect(
            ledger,
            intent,
            mode in ("idem", "nondet", "twophase"),
            mode in ("nondet", "twophase"),
            key_override=key,
        )
        _STATE["effect_done"] = True
        return {"done": True}

    def prepare_node(state: _S) -> _S:
        return {"started": True, "effect_key": two_phase_key(intent)}

    g = StateGraph(_S)
    if mode == "twophase":
        g.add_node("prepare", prepare_node)
        g.add_node("charge", charge_node)
        g.add_edge(START, "prepare")
        g.add_edge("prepare", "charge")
    else:
        g.add_node("charge", charge_node)
        g.add_edge(START, "charge")
    g.add_edge("charge", END)

    conn = sqlite3.connect(checkpoint, check_same_thread=False)
    saver = RacingSaver(conn)
    saver.setup()
    return g.compile(checkpointer=saver)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--intent", required=True)
    ap.add_argument("--mode", required=True, choices=["naive", "idem", "nondet", "twophase"])
    ap.add_argument("--barrier", required=True, choices=["b0", "b1", "b2", "none"])
    ap.add_argument("--recovery", type=int, default=0)
    args = ap.parse_args(argv)
    recovery = bool(args.recovery)
    app = _build(args.ledger, args.intent, args.mode, args.barrier, recovery, args.checkpoint)
    cfg = {"configurable": {"thread_id": args.intent}}
    # durability="sync" is the #8039 mode: checkpoints are written synchronously, so the entry
    # checkpoint is durable before the node runs (b0 lands before the effect deterministically)
    # and put_writes vs the superseding put race on the shared executor (the b1 finding).
    if recovery:
        app.invoke(None, cfg, durability="sync")  # resume from the checkpoint
    else:
        app.invoke({"started": False, "done": False}, cfg, durability="sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
