"""THE MODEL LAYER'S MOST IMPORTANT TEST.

A conformance kit that reported every runtime clean, or every runtime broken, would be useless and
look exactly like a green run. So this asserts, over the PREDICTION: the BEFORE and AFTER are
exactly-once for every durable runtime (the calibration columns hold); the three controls pin the
three non-VOID outcomes (the oracle has teeth); every NAIVE durable runtime duplicates at the lethal
barrier and the idempotent boundary recovers it (the finding and its fix); and the honest floor -
no naive durable runtime closes the lethal barrier - is named. The harness asserts the same shape
over OBSERVED runs later.
"""

from __future__ import annotations

from crashpoint.model.layers import Durability, EffectMode, Outcome, PersistOrder
from crashpoint.model.predict import PREDICTED
from crashpoint.model.runtimes import RUNTIMES, RUNTIMES_BY_ID


def _durable() -> list[str]:
    return [r.id for r in RUNTIMES if r.durability is Durability.DURABLE]


def test_before_and_after_are_exactly_once_for_every_durable_runtime() -> None:
    for rid in _durable():
        assert PREDICTED[rid]["b0"].outcome is Outcome.EXACTLY_ONCE
        assert PREDICTED[rid]["b2"].outcome is Outcome.EXACTLY_ONCE


def test_the_three_controls_pin_the_three_outcomes() -> None:
    # dup_control -> DUPLICATED, lost_control -> LOST, idem_reference -> EXACTLY_ONCE, all at b1.
    assert PREDICTED["r_dup"]["b1"].outcome is Outcome.DUPLICATED
    assert PREDICTED["r_lost"]["b1"].outcome is Outcome.LOST
    assert PREDICTED["r_idem"]["b1"].outcome is Outcome.EXACTLY_ONCE


def test_both_failure_modes_appear_somewhere() -> None:
    seen = {PREDICTED[rid][bid].outcome for rid in RUNTIMES_BY_ID for bid in ("b0", "b1", "b2")}
    assert Outcome.DUPLICATED in seen
    assert Outcome.LOST in seen


def test_every_naive_durable_runtime_duplicates_at_the_lethal_barrier() -> None:
    naive_durable = [
        r for r in RUNTIMES
        if r.durability is Durability.DURABLE
        and r.effect_mode is EffectMode.NAIVE
        and r.persist_order is not PersistOrder.PERSIST_THEN_EFFECT
    ]
    assert naive_durable
    for r in naive_durable:
        assert PREDICTED[r.id]["b1"].outcome is Outcome.DUPLICATED, r.id


def test_the_idempotent_boundary_recovers_the_lethal_barrier() -> None:
    # For every real runtime, the idempotent variant is exactly-once at b1 where the naive is not.
    for base in ("lg", "tmp", "dbos"):
        naive = PREDICTED[f"r_{base}_naive"]["b1"].outcome
        idem = PREDICTED[f"r_{base}_idem"]["b1"].outcome
        assert naive is Outcome.DUPLICATED
        assert idem is Outcome.EXACTLY_ONCE


def test_the_honest_floor_no_naive_durable_runtime_closes_the_lethal_barrier() -> None:
    # The finding: at b1 not one naive durable runtime achieves exactly-once. The floor is named.
    floor = [
        r.id for r in RUNTIMES
        if r.durability is Durability.DURABLE and r.effect_mode is EffectMode.NAIVE
    ]
    assert floor
    for rid in floor:
        assert PREDICTED[rid]["b1"].outcome is not Outcome.EXACTLY_ONCE
