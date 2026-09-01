"""Inventory of crash points that are not yet modeled as b0/b1/b2 barriers.

This is deliberately not evidence. The existing matrix crashes at three named barriers that
bracket the external effect. Hidden framework persistence points need their own model rows before
they can be measured. This module keeps those candidates visible and prevents them from being
silently folded into the current barrier ids.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model.barriers import BARRIER_IDS


@dataclass(frozen=True)
class BarrierCandidate:
    runtime: str
    candidate_id: str
    where: str
    blocker: str


CANDIDATES: tuple[BarrierCandidate, ...] = (
    BarrierCandidate(
        "langgraph",
        "lg_pre_first_checkpoint",
        "process death before the first durable checkpoint exists",
        "measured separately as a repro script, but not part of the b0/b1/b2 side-effect matrix",
    ),
    BarrierCandidate(
        "langgraph",
        "lg_pending_writes_after_persist",
        "after pending writes persist but before the superseding checkpoint wins the race",
        "needs a model rule for replaying pending writes vs re-executing the node on the active "
        "LangGraph checkpointer version",
    ),
    BarrierCandidate(
        "temporal",
        "tmp_activity_scheduled_before_worker_poll",
        "after activity scheduling is durable but before a worker starts the activity body",
        "requires event-history instrumentation against a live Temporal dev server",
    ),
    BarrierCandidate(
        "temporal",
        "tmp_workflow_task_replay",
        "workflow-task replay around activity completion history",
        "requires distinguishing workflow replay from activity retry without moving the external "
        "effect boundary",
    ),
    BarrierCandidate(
        "dbos",
        "dbos_step_output_commit_edge",
        "inside DBOS step output commit and workflow-status update",
        "requires DBOS internal schema/transaction instrumentation against a live system database",
    ),
    BarrierCandidate(
        "dbos",
        "dbos_duplicate_workflow_name_recovery",
        "recovery dispatch when two modules register the same workflow function name",
        "has a root repro probe, but it is a separate recovery-dispatch question, not yet a "
        "modeled external-effect barrier",
    ),
)


def unmodeled_candidate_ids() -> tuple[str, ...]:
    return tuple(c.candidate_id for c in CANDIDATES)


def render() -> str:
    lines = ["unmodeled crash-point inventory"]
    for c in CANDIDATES:
        lines.append(f"- {c.runtime}:{c.candidate_id} - {c.where}; blocker: {c.blocker}")
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
