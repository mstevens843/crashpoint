"""The crash barriers, as data. Three phases, named so a column is comparable across runtimes whose
(effect, persist) order differs.

The whole finding lives in the BETWEEN barrier: the effect has crossed but its completion is not yet
durable (or, for the inverse-order control, it is durable but the effect has not crossed).
BEFORE and AFTER are the calibration columns every durable runtime should pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layers import Phase


@dataclass(frozen=True)
class Barrier:
    id: str
    slug: str
    phase: Phase
    summary: str


BARRIERS: tuple[Barrier, ...] = (
    Barrier(
        id="b0",
        slug="before_effect",
        phase=Phase.BEFORE,
        summary="Crash before anything durable-relevant: no completion marker, effect not done. "
        "Recovery replays and the effect happens once.",
    ),
    Barrier(
        id="b1",
        slug="between",
        phase=Phase.BETWEEN,
        summary="THE LETHAL BARRIER. Exactly one of {effect crossed, completion persisted} has "
        "happened. For effect-then-persist a re-run duplicates; for persist-then-effect a skip "
        "loses; only an idempotent boundary survives it.",
    ),
    Barrier(
        id="b2",
        slug="after_persist",
        phase=Phase.AFTER,
        summary="Crash after both the effect and its completion marker. Recovery skips the "
        "completed step and the effect is not re-run.",
    ),
)

BARRIERS_BY_ID = {b.id: b for b in BARRIERS}
BARRIER_IDS = tuple(b.id for b in BARRIERS)
