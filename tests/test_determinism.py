"""THE NONDETERMINISM CONDITIONAL, asserted over the model and the recomputability predicate.

The finding this file guards: an idempotency key derived from what the action IS only survives a
crash if replaying the step reproduces that action. Where it does not - a step whose effect depends
on a value drawn during the step, of which a model call is the motivating case - the key differs on
the re-run, the dedup silently stops applying, and the two crossings are not even the same action.

The isolation test is the important one: langgraph_idem and langgraph_nondet differ in EXACTLY one
declared property. Same engine, same barrier, same idempotent boundary. Only determinism changes,
and the outcome goes from EXACTLY_ONCE to DIVERGED. If that pair ever agrees, the axis is not
carrying the finding and this file should fail.
"""

from __future__ import annotations

from crashpoint.adapters.base import _PAYLOAD
from crashpoint.harness.recomputability import (
    NO_IDENTITY,
    NOT_RECOMPUTABLE,
    RECOMPUTABLE,
    recompute_from_durable_inputs,
    verdict,
)
from crashpoint.ledger.idempotency import (
    ForbiddenIdentityField,
    assert_no_forbidden_identity_fields,
)
from crashpoint.model.layers import Determinism, EffectMode, Outcome
from crashpoint.model.predict import PREDICTED
from crashpoint.model.runtimes import RUNTIMES

_NONDET_PAIRS = (("r_lg_idem", "r_lg_nondet"), ("r_tmp_idem", "r_tmp_nondet"),
                 ("r_dbos_idem", "r_dbos_nondet"), ("r_idem", "r_diverge"))


def _nondeterministic() -> list[str]:
    return [r.id for r in RUNTIMES if r.determinism is Determinism.NONDETERMINISTIC]


def test_the_diverge_control_pins_the_new_outcome() -> None:
    # A fourth control, so DIVERGED is a value the oracle is proven to produce on demand - the same
    # discipline that makes the other three outcomes mean something.
    assert PREDICTED["r_diverge"]["b1"].outcome is Outcome.DIVERGED


def test_every_nondeterministic_runtime_diverges_at_the_lethal_barrier() -> None:
    rows = _nondeterministic()
    assert rows
    for rid in rows:
        assert PREDICTED[rid]["b1"].outcome is Outcome.DIVERGED, rid


def test_nondeterminism_costs_nothing_where_the_effect_crosses_once() -> None:
    # At b0 only the recovery run's effect crosses and at b2 only the crashed run's did. A single
    # crossing is a single crossing whether or not it was reproducible - so the axis must NOT bleed
    # into the calibration columns.
    for rid in _nondeterministic():
        assert PREDICTED[rid]["b0"].outcome is Outcome.EXACTLY_ONCE, rid
        assert PREDICTED[rid]["b2"].outcome is Outcome.EXACTLY_ONCE, rid


def test_determinism_is_the_only_difference_that_breaks_the_idempotent_boundary() -> None:
    from crashpoint.model.runtimes import RUNTIMES_BY_ID

    for det_id, nondet_id in _NONDET_PAIRS:
        det, nondet = RUNTIMES_BY_ID[det_id], RUNTIMES_BY_ID[nondet_id]
        # Both arms carry the SAME idempotent boundary...
        assert det.effect_mode is EffectMode.IDEMPOTENT
        assert nondet.effect_mode is EffectMode.IDEMPOTENT
        assert det.durability is nondet.durability
        assert det.persist_order is nondet.persist_order
        # ...and differ only in whether the step is reproducible from its durable inputs.
        assert det.determinism is Determinism.DETERMINISTIC
        assert nondet.determinism is Determinism.NONDETERMINISTIC
        # Which is enough to take the boundary from working to not applying at all.
        assert PREDICTED[det_id]["b1"].outcome is Outcome.EXACTLY_ONCE
        assert PREDICTED[nondet_id]["b1"].outcome is Outcome.DIVERGED


def test_the_second_floor_no_idempotent_boundary_closes_b1_for_a_nondeterministic_step() -> None:
    # The first floor was "no naive effect closes b1". This is the second, and it is the one that
    # matters for agent runtimes: the documented fix does not close b1 either, once the step stops
    # being reproducible. Named, not hidden.
    for rid in _nondeterministic():
        assert PREDICTED[rid]["b1"].outcome is not Outcome.EXACTLY_ONCE, rid


def test_recomputability_verdicts() -> None:
    expected = recompute_from_durable_inputs("order-1")
    assert verdict([expected], "order-1") == RECOMPUTABLE
    assert verdict([expected, expected], "order-1") == RECOMPUTABLE
    assert verdict([expected, "cp1key_drawn-at-step-time"], "order-1") == NOT_RECOMPUTABLE
    assert verdict([None, None], "order-1") == NO_IDENTITY  # the naive arm has no identity at all
    assert verdict([], "order-1") == NO_IDENTITY


def test_recompute_uses_only_durable_inputs() -> None:
    # Deriving twice, in a process that never ran the step, must give the same key - otherwise the
    # predicate could not decide anything.
    assert recompute_from_durable_inputs("order-1") == recompute_from_durable_inputs("order-1")
    assert recompute_from_durable_inputs("order-1") != recompute_from_durable_inputs("order-2")


def test_the_forbidden_field_guard_does_not_catch_this() -> None:
    """The state-of-the-art guard against the CLASSIC key bug is satisfied by the payload that
    breaks the dedup here. That is precisely why the recomputability predicate is needed: the two
    checks are not the same check, and passing the first says nothing about the second."""
    # It rejects identity that varies per attempt...
    for bad in ({**_PAYLOAD, "attempt": 2}, {**_PAYLOAD, "nonce": "x"}):
        try:
            assert_no_forbidden_identity_fields(bad)
            raise AssertionError(f"guard should have rejected {bad}")
        except ForbiddenIdentityField:
            pass
    # ...but accepts a semantically-named field carrying an irreproducible value.
    assert_no_forbidden_identity_fields({**_PAYLOAD, "memo": "whatever the model wrote"})
