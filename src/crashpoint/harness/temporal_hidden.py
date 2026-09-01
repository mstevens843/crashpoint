"""Temporal crash points outside the shared b0/b1/b2 matrix.

The main matrix crashes inside the activity body (b0/b1) or inside a later activity (b2). This
module measures two Temporal-only edges that sit on the service's own persistence boundaries. Each
has its own predicted rule and receipted evidence, and stays disjoint from b0/b1/b2:

  tmp_activity_scheduled_before_worker_poll
      The workflow task that schedules the activity has completed, so ActivityTaskScheduled is
      durable in history, but no worker has started the activity body. The subject process runs a
      workflows-only worker (no activity poller), starts the workflow, watches the history until
      the schedule event exists, and SIGKILLs itself. Predicted EXACTLY_ONCE: the scheduled task
      waits in matching and the recovery worker's first attempt runs the body once.

  tmp_workflow_task_replay
      The activity completed and ActivityTaskCompleted is durable, but the workflow task that
      consumes that completion dies before it is reported. The subject runs a full worker; the
      workflow code SIGKILLs the process right after the activity result is handed back to it on
      the live (non-replay) workflow task. Predicted EXACTLY_ONCE: the workflow task times out and
      is rescheduled, and the recovery worker's replay reads the activity result from history
      instead of re-running the activity.

Both edges use a NAIVE effect: the question is whether the framework re-runs the unit at that
edge, not whether an idempotent boundary would hide it. Beside the ledger outcome, each trial
records the event history the recovery worker observed, so the mechanism (attempt number,
timed-out workflow task, single schedule event) is evidence too.

Run against a local dev server:
    temporal server start-dev --headless --ip 127.0.0.1 --port 7233 --db-filename /tmp/cp.db
    uv run --extra temporal python -m crashpoint.harness.temporal_hidden --k 30 \\
        --barrier tmp_workflow_task_replay --name temporal_hidden_replay
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ..canonical import receipt
from ..ledger.oracle import classify
from ..model.layers import Outcome
from .ledger_process import LedgerDaemon, LedgerHandle
from .wilson import wilson

HiddenBarrierId = Literal[
    "tmp_activity_scheduled_before_worker_poll",
    "tmp_workflow_task_replay",
]
HIDDEN_BARRIERS: tuple[HiddenBarrierId, ...] = (
    "tmp_activity_scheduled_before_worker_poll",
    "tmp_workflow_task_replay",
)

_PAYLOAD = {"amount": 100, "to": "acct-attacker"}
_DEFAULT_ADDRESS = "127.0.0.1:7233"
_EXPECTED_CRASH = -int(signal.SIGKILL)
_PREDICTED: dict[HiddenBarrierId, Outcome] = {
    "tmp_activity_scheduled_before_worker_poll": Outcome.EXACTLY_ONCE,
    "tmp_workflow_task_replay": Outcome.EXACTLY_ONCE,
}
_PREDICTION_RULES: dict[HiddenBarrierId, str] = {
    "tmp_activity_scheduled_before_worker_poll": (
        "ActivityTaskScheduled is durable but no attempt has started; the task waits in matching "
        "and the recovery worker's first attempt runs the body once, so the effect crosses once"
    ),
    "tmp_workflow_task_replay": (
        "ActivityTaskCompleted is durable and the crashed workflow task only consumed it; the "
        "service times the task out, reschedules it, and replay reads the activity result from "
        "history without re-running the activity, so the effect crosses once"
    ),
}
# What the recovered history should show if the mechanism is the one the rule names.
_HISTORY_RULES: dict[HiddenBarrierId, str] = {
    "tmp_activity_scheduled_before_worker_poll": (
        "exactly one ActivityTaskScheduled, the ActivityTaskStarted attempt is 1, and no "
        "WorkflowTaskTimedOut"
    ),
    "tmp_workflow_task_replay": (
        "exactly one ActivityTaskScheduled, the ActivityTaskStarted attempt is 1, and at least "
        "one WorkflowTaskTimedOut for the crashed workflow task"
    ),
}

@dataclass(frozen=True)
class HistoryRow:
    event_type: str  # e.g. "activity_task_started"
    attempt: int | None = None
    failure: str = ""


@dataclass(frozen=True)
class HiddenTrial:
    barrier: HiddenBarrierId
    predicted: Outcome
    observed: Outcome
    subject_returncode: int
    recovery_returncode: int
    effect_count: int
    recovery_result: str
    history: dict[str, object]
    history_agrees: bool


def summarize_rows(rows: list[HistoryRow]) -> dict[str, object]:
    """Pure summary of a recovered history: event-type counts, the attempt number the activity
    finally started with, and the failure Temporal attached to a retried attempt."""
    counts: Counter[str] = Counter(r.event_type for r in rows)
    started = [r for r in rows if r.event_type == "activity_task_started"]
    return {
        "event_counts": dict(sorted(counts.items())),
        "activity_task_scheduled": counts.get("activity_task_scheduled", 0),
        "activity_task_started": counts.get("activity_task_started", 0),
        "activity_started_attempt": started[0].attempt if started else None,
        "activity_last_failure": started[0].failure if started else "",
        "workflow_task_timed_out": counts.get("workflow_task_timed_out", 0),
        "workflow_task_failed": counts.get("workflow_task_failed", 0),
    }


def history_agrees(barrier: HiddenBarrierId, summary: dict[str, object]) -> bool:
    """Does the recovered history show the mechanism the barrier's rule names?"""
    one_schedule = summary.get("activity_task_scheduled") == 1
    first_attempt = summary.get("activity_started_attempt") == 1
    timed_out = int(cast(int, summary.get("workflow_task_timed_out", 0)))
    if barrier == "tmp_activity_scheduled_before_worker_poll":
        return one_schedule and first_attempt and timed_out == 0
    return one_schedule and first_attempt and timed_out >= 1


def _run_process(args: list[str], expected_returncode: int, timeout: float) -> (
    subprocess.CompletedProcess[str]
):
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != expected_returncode:
        raise RuntimeError(
            "\n".join(
                [
                    "hidden Temporal subprocess failed",
                    f"expected={expected_returncode} actual={proc.returncode}",
                    f"argv={' '.join(args)}",
                    f"stdout={proc.stdout.strip() or '<empty>'}",
                    f"stderr={proc.stderr.strip()[-2000:] or '<empty>'}",
                ]
            )
        )
    return proc


def run_trial(
    ledger: LedgerHandle,
    index: int,
    barrier: HiddenBarrierId,
    address: str = _DEFAULT_ADDRESS,
    timeout: float = 60.0,
) -> HiddenTrial:
    predicted = _PREDICTED[barrier]
    digest = hashlib.sha256(
        f"{barrier}:{index}:{os.getpid()}:{time.time_ns()}".encode()
    ).hexdigest()[:16]
    intent = f"{barrier}-{index}"
    wfid = f"cp-hidden-{digest}"
    task_queue = f"cp-hidden-tq-{digest}"
    ledger.reset()
    common = [
        sys.executable,
        "-m",
        "crashpoint.harness.temporal_hidden",
        "--barrier",
        barrier,
        "--address",
        address,
        "--ledger",
        ledger.invoke_path,
        "--intent",
        intent,
        "--wfid",
        wfid,
        "--task-queue",
        task_queue,
    ]
    subject_proc = _run_process([*common, "--subject"], _EXPECTED_CRASH, timeout)
    recovery_proc = _run_process([*common, "--recovery"], 0, timeout)
    try:
        report = json.loads(recovery_proc.stdout)
    except json.JSONDecodeError:
        report = {"result": "<bad recovery output>", "history": {}}
    history = report.get("history", {}) if isinstance(report, dict) else {}
    history = history if isinstance(history, dict) else {}

    ledger.seal()
    dump = ledger.dump()
    observed = classify(intent, dump, Path(ledger.store_path))
    side_effects = dump.get("side_effects", {})
    effect_count = int(side_effects.get(intent, 0)) if isinstance(side_effects, dict) else -1
    return HiddenTrial(
        barrier=barrier,
        predicted=predicted,
        observed=observed,
        subject_returncode=subject_proc.returncode,
        recovery_returncode=recovery_proc.returncode,
        effect_count=effect_count,
        recovery_result=str(report.get("result", "")) if isinstance(report, dict) else "",
        history=history,
        history_agrees=history_agrees(barrier, history),
    )


def run(k: int, name: str, barrier: HiddenBarrierId, address: str = _DEFAULT_ADDRESS,
        timeout: float = 60.0) -> dict[str, object]:
    trials: list[HiddenTrial] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerDaemon(Path(tmp) / "ledger") as ledger:
        for i in range(k):
            trials.append(run_trial(ledger, i, barrier, address, timeout))

    counts: Counter[str] = Counter(t.observed.value for t in trials)
    modal, modal_n = counts.most_common(1)[0]
    predicted = _PREDICTED[barrier].value
    record: dict[str, object] = {
        "name": name,
        "runtime": "temporal",
        "barrier": barrier,
        "barrier_family": "hidden",
        "prediction_rule": _PREDICTION_RULES[barrier],
        "history_rule": _HISTORY_RULES[barrier],
        "k": k,
        "counts": dict(counts),
        "modal": modal,
        "modal_rate": round(modal_n / k, 4),
        "wilson95": list(wilson(modal_n, k)),
        "predicted": predicted,
        "agrees": modal == predicted,
        "history_agrees_count": sum(1 for t in trials if t.history_agrees),
        "trials": [
            {
                "barrier": t.barrier,
                "predicted": t.predicted.value,
                "observed": t.observed.value,
                "subject_returncode": t.subject_returncode,
                "recovery_returncode": t.recovery_returncode,
                "effect_count": t.effect_count,
                "recovery_result": t.recovery_result,
                "history": t.history,
                "history_agrees": t.history_agrees,
            }
            for t in trials
        ],
    }
    record["receipt"] = receipt(record)
    return record


def render(record: dict[str, object]) -> str:
    return "\n".join(
        [
            f"temporal hidden crash-point evidence - k={record['k']}",
            f"barrier: {record['barrier']}",
            f"predicted: {record['predicted']}",
            f"observed modal: {record['modal']} @ {record['modal_rate']}",
            f"counts: {record['counts']}",
            f"history agrees: {record['history_agrees_count']}/{record['k']}",
            f"disagreement: {not record['agrees']}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--name", default="temporal_hidden")
    ap.add_argument("--barrier", choices=HIDDEN_BARRIERS, default=HIDDEN_BARRIERS[0])
    ap.add_argument("--address", default=_DEFAULT_ADDRESS)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--subject", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--recovery", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--ledger", help=argparse.SUPPRESS)
    ap.add_argument("--intent", help=argparse.SUPPRESS)
    ap.add_argument("--wfid", help=argparse.SUPPRESS)
    ap.add_argument("--task-queue", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    barrier = cast(HiddenBarrierId, args.barrier)

    if args.subject or args.recovery:
        if args.wfid is None or args.task_queue is None:
            ap.error("--subject/--recovery require --wfid and --task-queue")
        from . import temporal_hidden_runtime as rt

        if args.subject:
            if args.ledger is None or args.intent is None:
                ap.error("--subject requires --ledger and --intent")
            rt.BARRIER = barrier
            asyncio.run(
                rt.subject(args.address, barrier, args.task_queue, args.wfid, args.ledger,
                           args.intent)
            )
            return 1  # pragma: no cover - the subject SIGKILLs itself before this
        rt.BARRIER = None
        print(json.dumps(asyncio.run(rt.recovery(args.address, args.task_queue, args.wfid)),
                         sort_keys=True))
        return 0

    record = run(args.k, args.name, barrier, args.address, args.timeout)
    print(render(record))
    out = Path(__file__).resolve().parents[3] / "evidence" / f"{args.name}.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True))
    print(f"\nreceipt: {record['receipt']}\nwrote {out}")
    return 0 if record["agrees"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
