"""The DBOS adapter: a durable workflow with an effect step and a sentinel step, checkpointed in
Postgres. On restart DBOS recovers pending workflows and resumes from the last committed step.

DBOS checkpoints each step's output in Postgres; a crash after the effect but before that output is
committed re-runs the whole step on recovery. The barrier is the PROCESS-level `_BARRIER` (a CLI
arg): the crash process crashes at the barrier and the recovery process (barrier "none") does not:
  b0 - crash in effect_step before the effect: recovery re-runs the step, the effect crosses once.
  b1 - crash in effect_step after the effect, before its output commits: recovery re-runs the step,
       so a naive effect DUPLICATES and an idempotent one dedups to EXACTLY_ONCE.
  b2 - the effect_step commits its output; a sentinel step then crashes. Recovery resumes at the
       sentinel and does not re-run the committed effect_step, so the effect crosses once.

The workflow id and executor id are derived from the per-trial checkpoint path, so recovery on the
reused Postgres only touches this trial's workflow. The Postgres URL comes from CRASHPOINT_DBOS_URL
(default: a local dev container on 5433).

Run: `python -m crashpoint.adapters.dbos_adapter --ledger <sock> --intent <id>
--mode naive|idem --barrier b0|b1|b2|none --recovery 0|1 --checkpoint <path>`.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import time

from dbos import DBOS, DBOSConfig, SetWorkflowID

from .base import crash, effect

# Process-level state, set from the CLI before launch. The steps read these globals; only _BARRIER
# differs between the crash process (real barrier) and the recovery process ("none").
_BARRIER = "none"
_LEDGER = ""
_INTENT = ""
_IDEMPOTENT = False

_DEFAULT_URL = "postgresql://cpuser:dbos@localhost:5433/cpdbos"


@DBOS.step()
def effect_step() -> str:
    if _BARRIER == "b0":
        crash()  # before the effect: recovery re-runs the step, the effect crosses once
    effect(_LEDGER, _INTENT, _IDEMPOTENT)
    if _BARRIER == "b1":
        crash()  # after the effect, before the step output commits: recovery re-runs the effect
    return "ok"


@DBOS.step()
def sentinel_step() -> str:
    if _BARRIER == "b2":
        crash()  # after effect_step committed: recovery resumes here, effect_step is not re-run
    return "ok"


@DBOS.workflow()
def charge_workflow() -> str:
    effect_step()
    sentinel_step()
    return "done"


def _configure(db_url: str, executor_id: str) -> None:
    cfg = DBOSConfig(
        name="crashpoint",
        system_database_url=db_url,
        log_level="ERROR",
        executor_id=executor_id,
    )
    DBOS(config=cfg)


def _crash_run(wfid: str) -> None:
    DBOS.launch()
    with SetWorkflowID(wfid):
        DBOS.start_workflow(charge_workflow)  # runs in a background thread; a step SIGKILLs us
    time.sleep(30)  # the step kills this whole process before this returns


def _recovery_run(wfid: str) -> None:
    DBOS.launch()  # recovers this executor's pending workflow and resumes it
    DBOS.retrieve_workflow(wfid).get_result()  # block until the recovered workflow completes


def main(argv: list[str] | None = None) -> int:
    global _BARRIER, _LEDGER, _INTENT, _IDEMPOTENT
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--intent", required=True)
    ap.add_argument("--mode", required=True, choices=["naive", "idem"])
    ap.add_argument("--barrier", required=True, choices=["b0", "b1", "b2", "none"])
    ap.add_argument("--recovery", type=int, default=0)
    ap.add_argument("--checkpoint", required=True)  # per-trial unique path -> workflow/executor id
    ap.add_argument("--db-url", default=os.environ.get("CRASHPOINT_DBOS_URL", _DEFAULT_URL))
    args = ap.parse_args(argv)
    _BARRIER = args.barrier
    _LEDGER = args.ledger
    _INTENT = args.intent
    _IDEMPOTENT = args.mode == "idem"
    digest = hashlib.sha256(args.checkpoint.encode()).hexdigest()[:16]
    wfid = "cp-" + digest
    _configure(args.db_url, "cp-exec-" + digest)
    if args.recovery:
        _recovery_run(wfid)
    else:
        _crash_run(wfid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
