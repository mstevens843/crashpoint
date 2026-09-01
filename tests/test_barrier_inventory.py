"""Unmodeled crash points must stay visibly separate from modeled barriers."""

from __future__ import annotations

from crashpoint.harness.barrier_inventory import CANDIDATES, unmodeled_candidate_ids
from crashpoint.model.barriers import BARRIER_IDS


def test_hidden_barrier_candidates_are_not_modeled_barriers() -> None:
    assert set(unmodeled_candidate_ids()).isdisjoint(BARRIER_IDS)


def test_hidden_barrier_candidates_have_blockers() -> None:
    assert CANDIDATES
    for candidate in CANDIDATES:
        assert candidate.runtime
        assert candidate.where
        if candidate.status == "blocked":
            assert len(candidate.blocker) > 20
            assert not candidate.evidence
        else:
            assert candidate.evidence
