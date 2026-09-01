"""@vasilisnasopoulos's cheap test, made executable: can a process that did NOT run the step
recompute the effect's identity from the step's durable inputs alone?

He proposed it on langchain-ai/langgraph#8039 as the discriminator for when a content-derived
idempotency key actually survives a crash:

    "The cheap test for the first case is whether a process that did not run the step can recompute
     the identity from the durable inputs alone."

WHY IT IS WORTH IMPLEMENTING RATHER THAN QUOTING. It is a LEADING indicator. The outcome matrix
needs a crash, a recovery, and k trials to tell you an idempotent boundary failed; this predicate
needs neither, and it answers before you ship. If the content-derived identity is not recomputable,
the dedup has nothing stable to match on and the boundary cannot hold - so NOT_RECOMPUTABLE predicts
DIVERGED at the b1 barrier. A prepared pre-call identity is the measured counter-shape: it is
recomputable before the draw and can close the same b1 window.

WHAT IT SHOWS THAT THE EXISTING GUARD DOES NOT. `idempotency.assert_no_forbidden_identity_fields`
is the state of the art for this bug class: it rejects a key built from `attempt`, `retry`, `epoch`,
`nonce`, a delivery id - identity that varies per try. It PASSES the nondeterministic payload here,
because `memo` is not a per-attempt field; it is ordinary semantic content that happens not to be
reproducible. So the guard that prevents the classic key bug does not prevent this one, and the
recomputability predicate is what separates them.

The probe runs in the harness process, which never executed the step - the step ran in a subprocess
that was SIGKILLed - so "a process that did not run the step" is literal here, not simulated.

Impure (spawns the ledger daemon and the adapters); run with
`python -m crashpoint.harness.recomputability`.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..adapters.base import _PAYLOAD, two_phase_key
from ..ledger.idempotency import (
    ForbiddenIdentityField,
    assert_no_forbidden_identity_fields,
    derive_idempotency_key,
)
from ..model.layers import Outcome
from .ledger_process import LedgerDaemon, LedgerHandle
from .trial import run_trial

RECOMPUTABLE = "RECOMPUTABLE"
NOT_RECOMPUTABLE = "NOT_RECOMPUTABLE"
NO_IDENTITY = "NO_IDENTITY"

_INTENT = "order-1"


def recompute_from_durable_inputs(intent_id: str) -> str:
    """The key a process that never ran the step would derive, knowing only the durable inputs: the
    namespace, the subject, the intent version, and the payload declared before the step runs."""
    return derive_idempotency_key("charge", intent_id, 1, dict(_PAYLOAD))


def recompute_prepared_identity(intent_id: str) -> str:
    """The key prepared before the nondeterministic draw in the two-phase arm."""
    return two_phase_key(intent_id)


def verdict(
    observed_keys: list[str | None],
    intent_id: str = _INTENT,
    expected_key: str | None = None,
) -> str:
    """Compare what the step actually used against what an outsider can derive."""
    if not observed_keys or all(k is None for k in observed_keys):
        return NO_IDENTITY  # the naive arm: there is no identity to recompute
    expected = expected_key or recompute_from_durable_inputs(intent_id)
    return RECOMPUTABLE if all(k == expected for k in observed_keys) else NOT_RECOMPUTABLE


def _guard_passes(payload: dict[str, object]) -> bool:
    """Does the existing forbidden-identity-field guard accept this payload? (It does.)"""
    try:
        assert_no_forbidden_identity_fields(payload)
    except ForbiddenIdentityField:
        return False
    return True


def probe(
    runtime_id: str,
    ledger: LedgerHandle,
    expected_key: str | None = None,
) -> dict[str, object]:
    """Crash one runtime at the lethal barrier, then - from this process, which did not run the
    step - try to recompute the identity of whatever crossed."""
    outcome = run_trial(runtime_id, "b1", ledger)
    dump = ledger.dump()
    keys_map = dump.get("effect_keys", {})
    keys = list(keys_map.get(_INTENT, [])) if isinstance(keys_map, dict) else []
    digests_map = dump.get("effect_digests", {})
    digests = list(digests_map.get(_INTENT, [])) if isinstance(digests_map, dict) else []
    return {
        "runtime": runtime_id,
        "recomputability": verdict(keys, expected_key=expected_key),
        "distinct_keys_used": len(set(keys)),
        "distinct_effects_crossed": len(set(digests)),
        "outcome_at_b1": outcome.value,
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as d, LedgerDaemon(Path(d) / "led") as ledger:
        deterministic = probe("r_idem", ledger)
        nondeterministic = probe("r_diverge", ledger)
        two_phase = probe("r_twophase", ledger, recompute_prepared_identity(_INTENT))

    nondet_payload = {**_PAYLOAD, "memo": "whatever-the-model-wrote"}
    guard = {
        "guard_accepts_deterministic_payload": _guard_passes(dict(_PAYLOAD)),
        "guard_accepts_nondeterministic_payload": _guard_passes(nondet_payload),
        "guard_rejects_a_classic_per_attempt_payload":
            not _guard_passes({**_PAYLOAD, "attempt": 2}),
    }

    print("PROBE A - deterministic step (idem_reference):")
    print("  " + json.dumps(deterministic, sort_keys=True))
    print("PROBE B - nondeterministic step (diverge_control):")
    print("  " + json.dumps(nondeterministic, sort_keys=True))
    print("PROBE C - two-phase nondeterministic step (two_phase_reference):")
    print("  " + json.dumps(two_phase, sort_keys=True))
    print("GUARD - the existing forbidden-identity-field check:")
    print("  " + json.dumps(guard, sort_keys=True))

    ok = (
        deterministic["recomputability"] == RECOMPUTABLE
        and deterministic["outcome_at_b1"] == Outcome.EXACTLY_ONCE.value
        and nondeterministic["recomputability"] == NOT_RECOMPUTABLE
        and nondeterministic["outcome_at_b1"] == Outcome.DIVERGED.value
        and two_phase["recomputability"] == RECOMPUTABLE
        and two_phase["outcome_at_b1"] == Outcome.EXACTLY_ONCE.value
        # The guard is satisfied by the payload that breaks the dedup: it is not the same check.
        and bool(guard["guard_accepts_nondeterministic_payload"])
        and bool(guard["guard_rejects_a_classic_per_attempt_payload"])
    )
    print(
        f"\nRECOMPUTABILITY PROBE {'PASS' if ok else 'FAIL'}: the predicate decided both cases "
        f"before any crash, NOT_RECOMPUTABLE predicted DIVERGED at b1, and the prepared identity "
        f"recovered EXACTLY_ONCE - while the forbidden-field guard accepted the payload that broke "
        f"the content-derived dedup."
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
