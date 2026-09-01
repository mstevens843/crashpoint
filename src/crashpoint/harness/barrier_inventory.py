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
        "after activity scheduling is durable but before a worker starts the activity body",
        "blocked",
        "requires event-history instrumentation against a live Temporal dev server",
    ),
    BarrierCandidate(
        "temporal",
        "tmp_workflow_task_replay",
        "workflow-task replay around activity completion history",
        "blocked",
        "requires distinguishing workflow replay from activity retry without moving the external "
        "effect boundary",
    ),
    BarrierCandidate(
        "dbos",
        "dbos_step_output_commit_edge",
        "inside DBOS step output commit and workflow-status update",
        "blocked",
        "requires DBOS internal schema/transaction instrumentation against a live system database",
    ),
    BarrierCandidate(
        "dbos",
        "dbos_duplicate_workflow_name_recovery",
        "recovery dispatch when two modules register the same workflow function name",
        "blocked",
        "has a root repro probe, but it is a separate recovery-dispatch question, not yet a "
        "modeled external-effect barrier",
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
