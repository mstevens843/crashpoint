"""The Outcome oracle: classify one intent's ledger record into the model's four-value outcome.

Fail-closed. Any integrity doubt is VOID, never EXACTLY_ONCE. This is the crashpoint analogue of the
durable-agent-outbox audit-legality checker (DUPLICATE_EXECUTION when an intent crosses the boundary
more than once); here the boundary crossing is measured by the out-of-process ledger's distinct
side-effect count, which the runtime cannot forge.
"""

from __future__ import annotations

from pathlib import Path

from ..model.layers import Outcome
from .core import LedgerState


def classify(
    intent_id: str, dump: dict[str, object], ledger_path: Path, required: bool = True
) -> Outcome:
    """Classify the outcome for one intent from a ledger dump.

    `required` = the intent was supposed to cross exactly once (the normal case). A trial where the
    workflow legitimately never reached the step would pass `required=False`.
    """
    ok, _ = LedgerState.verify(ledger_path)
    if not ok:
        return Outcome.VOID  # the chain is broken: the ledger was tampered with
    side_effects = dump.get("side_effects", {})
    if not isinstance(side_effects, dict):
        return Outcome.VOID
    n = int(side_effects.get(intent_id, 0))
    if n == 0:
        return Outcome.LOST if required else Outcome.EXACTLY_ONCE
    if n == 1:
        return Outcome.EXACTLY_ONCE
    return Outcome.DUPLICATED
