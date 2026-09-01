"""Inventory of crash points that are not part of the shared b0/b1/b2 matrix.

This is not the main evidence matrix. The shared matrix crashes at three named barriers that bracket
the external effect. Hidden framework persistence points need their own prediction and evidence
before they can be cited. This module keeps those candidates visible and prevents them from being
silently folded into the current barrier ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..model.barriers import BARRIER_IDS

Status = Literal["measured", "blocked"]


@dataclass(frozen=True)
class BarrierCandidate:
    runtime: str
    candidate_id: str
    where: str
    status: Status
    blocker: str
    evidence: str = ""


CANDIDATES: tuple[BarrierCandidate, ...] = (
    BarrierCandidate(
        "langgraph",
        "lg_pre_first_checkpoint",
        "process death before the first durable checkpoint exists",
        "measured",
        "kept separate because it precedes the b0 entry-checkpoint boundary",
        "evidence/langgraph_hidden.json",
    ),
    BarrierCandidate(
        "langgraph",
        "lg_pending_writes_after_persist",
        "after pending writes persist but before the superseding checkpoint wins the race",
        "measured",
        "kept separate because it is after the main b1 crash-before-pending-writes boundary",
        "evidence/langgraph_hidden_pending.json",
    ),
    BarrierCandidate(
        "temporal",
        "tmp_activity_scheduled_before_worker_poll",
        "after ActivityTaskScheduled is durable but before any worker attempt starts the body",
        "measured",
        "kept separate because it precedes the b0 in-body crash: the scheduled task is delivered "
        "to the recovery worker's first attempt",
        "evidence/temporal_hidden_scheduled.json",
    ),
    BarrierCandidate(
        "temporal",
        "tmp_workflow_task_replay",
        "after ActivityTaskCompleted is durable but before the workflow task consuming it "
        "completes",
        "measured",
        "kept separate because the crash is in a workflow task, not an activity body; replay "
        "reads the activity result from history",
        "evidence/temporal_hidden_replay.json",
    ),
    BarrierCandidate(
        "dbos",
        "dbos_step_output_uncommitted",
        "after the step output INSERT executes but before its transaction commits",
        "measured",
        "kept separate because it is inside the system-database transaction the b1 crash never "
        "reaches; Postgres atomicity makes it read like b1",
        "evidence/dbos_hidden_uncommitted.json",
    ),
    BarrierCandidate(
        "dbos",
        "dbos_step_output_committed_before_resume",
        "after the step output commits but before the workflow function resumes",
        "measured",
        "kept separate because it sits between b1 and b2: the output is durable but no later "
        "step has started",
        "evidence/dbos_hidden_committed.json",
    ),
    BarrierCandidate(
        "dbos",
        "dbos_workflow_outcome_uncommitted",
        "after the SUCCESS status UPDATE executes but before its transaction commits",
        "measured",
        "kept separate because it is after b2: every step output is durable and only the "
        "terminal status is not",
        "evidence/dbos_hidden_outcome.json",
    ),
    BarrierCandidate(
        "dbos",
        "dbos_duplicate_workflow_name_recovery",
        "recovery dispatch when two modules register the same workflow function name",
        "measured",
        "kept separate because the crash is the b1 point but the recovery process resolves the "
        "stored name to a different function body",
        "evidence/dbos_hidden_dupname.json",
    ),
    BarrierCandidate(
        "vercel_workflow",
        "vwf_step_create_claim_before_event",
        "after world-local links the lazy step-create claim file but before the step entity and "
        "step_created event are written",
        "blocked",
        "observed as a recovery wedge in the shared-matrix crash trials (the re-enqueued run's "
        "lazy step start hits the stale claim, is mapped to skipped, and the run never completes; "
        "scored VOID there); needs a deterministic injection point inside world-local's event "
        "storage before it can be measured on its own",
    ),
)


def unmodeled_candidate_ids() -> tuple[str, ...]:
    return tuple(c.candidate_id for c in CANDIDATES)


def render() -> str:
    lines = ["hidden crash-point inventory"]
    for c in CANDIDATES:
        suffix = f"; evidence: {c.evidence}" if c.evidence else f"; blocker: {c.blocker}"
        lines.append(f"- {c.runtime}:{c.candidate_id} [{c.status}] - {c.where}{suffix}")
    return "\n".join(lines)


def main() -> int:
    overlap = set(unmodeled_candidate_ids()) & set(BARRIER_IDS)
    if overlap:
        print(f"INVALID inventory overlaps modeled barrier ids: {sorted(overlap)}")
        return 1
    print(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
