"""Temporal-side definitions for ``crashpoint.harness.temporal_hidden``.

Only the subject and recovery subprocesses import this module, so the harness itself stays
importable without temporalio. The workflow class has to live at module level: the Temporal SDK
rejects ``@workflow.run`` on a local class.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from ..ledger.daemon import execute
from .temporal_hidden import HistoryRow, summarize_rows

_PAYLOAD = {"amount": 100, "to": "acct-attacker"}

# Process-level barrier: the subject process carries the real barrier; the recovery process
# carries None so nothing crashes twice.
BARRIER: str | None = None


def _die() -> None:
    """SIGKILL this process and never return to the caller.

    The kill is issued from a worker thread. Process termination is not instantaneous for the
    thread that requested it: on macOS the calling thread can run a little further while the
    process is torn down, which is enough to send a COMMIT to Postgres or report a task to a
    server. Blocking here keeps the crash exactly where the barrier says it is.
    """
    os.kill(os.getpid(), signal.SIGKILL)
    while True:  # pragma: no cover - the process is dead before this loop matters
        time.sleep(1)


@activity.defn
async def hidden_effect_activity(ledger: str, intent: str) -> str:
    execute(ledger, intent, None, dict(_PAYLOAD))
    return "ok"


@workflow.defn
class HiddenWorkflow:
    @workflow.run
    async def run(self, ledger: str, intent: str) -> str:
        rp = RetryPolicy(maximum_attempts=100, initial_interval=timedelta(milliseconds=200))
        await workflow.execute_activity(
            hidden_effect_activity,
            args=[ledger, intent],
            start_to_close_timeout=timedelta(seconds=2),
            retry_policy=rp,
        )
        if BARRIER == "tmp_workflow_task_replay" and not workflow.unsafe.is_replaying():
            # ActivityTaskCompleted is durable; this live workflow task has not completed.
            _die()
        return "done"


def _worker(client: Client, task_queue: str, with_activities: bool) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[HiddenWorkflow],
        activities=[hidden_effect_activity] if with_activities else [],
        max_cached_workflows=0,
        workflow_runner=UnsandboxedWorkflowRunner(),
    )


def history_rows(events: Any) -> list[HistoryRow]:
    """Flatten protobuf history events into rows the pure summarizer can read."""
    rows: list[HistoryRow] = []
    for event in events:
        name = EventType.Name(event.event_type).removeprefix("EVENT_TYPE_").lower()
        attempt: int | None = None
        failure = ""
        if name == "activity_task_started":
            attrs = event.activity_task_started_event_attributes
            attempt = int(attrs.attempt)
            failure = str(attrs.last_failure.message) if attrs.HasField("last_failure") else ""
        rows.append(HistoryRow(name, attempt, failure))
    return rows


async def subject(address: str, barrier: str, task_queue: str, wfid: str, ledger: str,
                  intent: str) -> None:
    client = await Client.connect(address)
    full_worker = barrier == "tmp_workflow_task_replay"
    async with _worker(client, task_queue, with_activities=full_worker):
        handle = await client.start_workflow(
            HiddenWorkflow.run,
            args=[ledger, intent],
            id=wfid,
            task_queue=task_queue,
            task_timeout=timedelta(seconds=2),
        )
        if barrier == "tmp_activity_scheduled_before_worker_poll":
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                history = await handle.fetch_history()
                if any(
                    e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                    for e in history.events
                ):
                    # The schedule is durable and this process owns no activity poller.
                    _die()
                await asyncio.sleep(0.05)
            raise RuntimeError("activity was never scheduled")
        await asyncio.sleep(30)  # the workflow code SIGKILLs this process first
    raise RuntimeError("subject did not crash at the barrier")


async def recovery(address: str, task_queue: str, wfid: str) -> dict[str, object]:
    client = await Client.connect(address)
    async with _worker(client, task_queue, with_activities=True):
        handle = client.get_workflow_handle(wfid)
        result = await handle.result()
        history = await handle.fetch_history()
    return {"result": str(result), "history": summarize_rows(history_rows(history.events))}
