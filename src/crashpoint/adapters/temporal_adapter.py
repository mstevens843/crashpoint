"""The Temporal adapter: a workflow with an effect activity and a sentinel activity, run against a
local `temporal server start-dev` that survives the worker's self-SIGKILL.

Temporal activities are at-least-once: a crash after the effect but before the activity's completion
is reported to the service triggers a retry that re-runs the whole activity. The barrier is set by
the PROCESS-level `_BARRIER` (a CLI arg), so the crash worker crashes at the barrier and the
recovery worker (a fresh process with barrier "none") does not:
  b0 - crash in effect_activity before the effect: the activity times out and is retried; recovery
       runs the effect once.
  b1 - crash in effect_activity after the effect, before it reports completion: the retry re-runs
       the activity, so a naive effect DUPLICATES and an idempotent one dedups to EXACTLY_ONCE.
  b2 - the effect_activity completes (its result is durable in history); a sentinel activity then
       crashes. Recovery does not re-run the completed effect_activity, so the effect crosses once.

The workflow id is derived from the per-trial checkpoint path so trials never collide on the reused
server; the ledger intent is separate and drives the out-of-process side-effect count.

Run: `python -m crashpoint.adapters.temporal_adapter --ledger <sock> --intent <id>
--mode naive|idem --barrier b0|b1|b2|none --recovery 0|1 --checkpoint <path> [--address host:port]`.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

with workflow.unsafe.imports_passed_through():
    from .base import crash, effect

# Process-level barrier: set from the CLI. The crash worker carries the real barrier; the recovery
# worker carries "none", so the re-run of an activity in recovery never crashes again.
_BARRIER = "none"

_TASK_QUEUE_PREFIX = "cp-tq-"


@activity.defn
async def effect_activity(ledger: str, intent: str, idempotent: bool) -> str:
    if _BARRIER == "b0":
        crash()  # before the effect: the activity is retried, the effect crosses once
    effect(ledger, intent, idempotent)
    if _BARRIER == "b1":
        crash()  # after the effect, before completion is reported: the retry re-runs the effect
    return "ok"


@activity.defn
async def sentinel_activity() -> str:
    if _BARRIER == "b2":
        crash()  # after effect_activity's completion is durable: its result is not re-run
    return "ok"


@workflow.defn
class CrashpointWorkflow:
    @workflow.run
    async def run(self, ledger: str, intent: str, idempotent: bool) -> str:
        rp = RetryPolicy(maximum_attempts=100, initial_interval=timedelta(milliseconds=200))
        await workflow.execute_activity(
            effect_activity,
            args=[ledger, intent, idempotent],
            start_to_close_timeout=timedelta(seconds=2),
            retry_policy=rp,
        )
        await workflow.execute_activity(
            sentinel_activity,
            start_to_close_timeout=timedelta(seconds=2),
            retry_policy=rp,
        )
        return "done"


def _worker(client: Client, task_queue: str) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[CrashpointWorkflow],
        activities=[effect_activity, sentinel_activity],
        max_cached_workflows=0,  # disable sticky execution so a fresh worker resumes without delay
        # The workflow is trivially deterministic (two sequential activities); the sandbox only adds
        # a module re-import that fights this package's relative imports, so run unsandboxed.
        workflow_runner=UnsandboxedWorkflowRunner(),
    )


async def _crash_run(client: Client, tq: str, wfid: str, ledger: str, intent: str,
                     idempotent: bool) -> None:
    async with _worker(client, tq):
        await client.start_workflow(
            CrashpointWorkflow.run, args=[ledger, intent, idempotent], id=wfid, task_queue=tq
        )
        await asyncio.sleep(30)  # an activity SIGKILLs this whole process before this returns


async def _recovery_run(client: Client, tq: str, wfid: str) -> None:
    async with _worker(client, tq):
        handle = client.get_workflow_handle(wfid)
        await handle.result()  # drive the retry to completion, then return


async def _run(address: str, recovery: bool, tq: str, wfid: str, ledger: str, intent: str,
               idempotent: bool) -> None:
    client = await Client.connect(address)
    if recovery:
        await _recovery_run(client, tq, wfid)
    else:
        await _crash_run(client, tq, wfid, ledger, intent, idempotent)


def main(argv: list[str] | None = None) -> int:
    global _BARRIER
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--intent", required=True)
    ap.add_argument("--mode", required=True, choices=["naive", "idem"])
    ap.add_argument("--barrier", required=True, choices=["b0", "b1", "b2", "none"])
    ap.add_argument("--recovery", type=int, default=0)
    ap.add_argument("--checkpoint", required=True)  # per-trial unique path -> workflow id
    ap.add_argument("--address", default="localhost:7233")
    args = ap.parse_args(argv)
    _BARRIER = args.barrier
    idempotent = args.mode == "idem"
    wfid = "cp-" + hashlib.sha256(args.checkpoint.encode()).hexdigest()[:16]
    tq = _TASK_QUEUE_PREFIX + hashlib.sha256(args.checkpoint.encode()).hexdigest()[:16]
    asyncio.run(_run(args.address, bool(args.recovery), tq, wfid, args.ledger, args.intent,
                     idempotent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
