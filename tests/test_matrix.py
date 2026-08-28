"""Integrity of the model itself: ids are clean, the matrix is complete, and predict is a total
deterministic function. Guards the data the harness grades against so a rename or dropped row cannot
silently make the observed-vs-predicted diff meaningless.
"""

from __future__ import annotations

import pytest

from crashpoint.model.barriers import BARRIER_IDS, BARRIERS
from crashpoint.model.layers import Outcome
from crashpoint.model.predict import PREDICTED, predict
from crashpoint.model.runtimes import RUNTIME_IDS, RUNTIMES


def test_ids_unique_and_well_formed() -> None:
    assert len(RUNTIME_IDS) == len(set(RUNTIME_IDS))
    assert all(i.startswith("r_") for i in RUNTIME_IDS)
    assert len(BARRIER_IDS) == len(set(BARRIER_IDS))
    assert all(i[0] == "b" and i[1:].isdigit() for i in BARRIER_IDS)


def test_matrix_is_complete() -> None:
    assert set(PREDICTED) == set(RUNTIME_IDS)
    for rid in RUNTIME_IDS:
        assert set(PREDICTED[rid]) == set(BARRIER_IDS), f"{rid} missing barrier columns"


def test_every_cell_has_an_outcome_and_nonempty_rationale() -> None:
    for rid in RUNTIME_IDS:
        for bid in BARRIER_IDS:
            c = PREDICTED[rid][bid]
            assert isinstance(c.outcome, Outcome)
            assert c.rationale.strip()
            assert c.runtime_id == rid and c.barrier_id == bid


def test_predict_never_predicts_void() -> None:
    # VOID is an integrity outcome the oracle and the adversary produce, never a normal crash
    # prediction. If the model ever predicts VOID, the semantics have drifted.
    for rid in RUNTIME_IDS:
        for bid in BARRIER_IDS:
            assert PREDICTED[rid][bid].outcome is not Outcome.VOID


def test_predict_is_deterministic() -> None:
    for rt in RUNTIMES:
        for b in BARRIERS:
            assert predict(rt, b) == predict(rt, b)


def test_exactly_one_target_and_one_reference() -> None:
    assert sum(r.is_target for r in RUNTIMES) == 1
    assert sum(r.is_reference for r in RUNTIMES) == 1


@pytest.mark.parametrize("rid", RUNTIME_IDS)
def test_runtime_has_a_summary(rid: str) -> None:
    from crashpoint.model.runtimes import RUNTIMES_BY_ID

    assert len(RUNTIMES_BY_ID[rid].summary) > 30
