"""The prediction. One pure function from (runtime, barrier) to an outcome, and the full predicted
matrix derived from it.

WHAT THIS IS. The model of the durable-execution lifecycle, written before any runtime is crashed.
Given a runtime's declared (durability, persist order, effect mode) and a barrier phase, ``predict``
returns EXACTLY_ONCE / DUPLICATED / LOST with the one-line reason that produced it. It reads
nothing, spawns nothing, crashes nothing; it is a total function over the enums in ``layers.py`` and
the data in ``runtimes.py`` and ``barriers.py``. That purity is enforced by the contract test.

WHY A DERIVATION AND NOT A TABLE. A hand-filled matrix is unfalsifiable. Here every cell is the
output of the rule its (order, phase) selects, reading exactly the properties that matter, so a
disagreement with a real crashed runtime is traceable to a property. The harness fills an observed
matrix beside this one and any predicted != observed cell is a finding.

THE RULE, IN ONE SENTENCE. At the BETWEEN barrier an effect-then-persist runtime re-runs the effect
(DUPLICATED, unless an idempotent boundary dedups it to EXACTLY_ONCE), a persist-then-effect runtime
skips the effect that never ran (LOST), and a non-durable runtime duplicates after the effect and is
exactly-once before it; BEFORE and AFTER are exactly-once for every durable runtime. VOID is never
predicted here - it is an integrity outcome the oracle and the adversary produce.

THE CONDITIONAL ON THE IDEMPOTENT BRANCH. The dedup above assumes the re-run derives the SAME key,
which assumes replaying the step reproduces the same effect. For a NONDETERMINISTIC step it does
not: the payload carries a value that did not exist until the step ran, so the re-run derives a
different key, the dedup misses, and the second crossing is a different action - DIVERGED, not
DUPLICATED and certainly not EXACTLY_ONCE. This is why the idempotent branch is guarded by
determinism and not by effect mode alone. Note where it does NOT bite: at BEFORE only the recovery
run's effect ever crosses, and at AFTER only the crashed run's did, so a single crossing is a
single crossing whether or not it was reproducible. Nondeterminism costs nothing except at the one
barrier where the effect happens twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from .barriers import BARRIERS, Barrier
from .layers import Determinism, Durability, EffectMode, Outcome, PersistOrder, Phase
from .runtimes import RUNTIMES, Runtime


@dataclass(frozen=True)
class Cell:
    runtime_id: str
    barrier_id: str
    outcome: Outcome
    rationale: str


def predict(runtime: Runtime, barrier: Barrier) -> Cell:
    o, why = _predict(runtime, barrier)
    return Cell(runtime.id, barrier.id, o, why)


def _predict(rt: Runtime, b: Barrier) -> tuple[Outcome, str]:
    if rt.durability is Durability.NONE:
        # No completion marker: a crash re-runs the whole fixture from the top.
        if b.phase is Phase.BEFORE:
            return (Outcome.EXACTLY_ONCE, "no durability, but the re-run does the effect once")
        return (
            Outcome.DUPLICATED,
            "no durability: the re-run repeats an effect that already crossed",
        )

    # Durable runtimes.
    if b.phase is Phase.BEFORE:
        return (Outcome.EXACTLY_ONCE,
                "nothing has crossed yet; recovery replays and the effect runs once")
    if b.phase is Phase.AFTER:
        return (Outcome.EXACTLY_ONCE,
                "the completion is durable; recovery skips the step, no re-run")

    # The BETWEEN barrier: the whole finding.
    if rt.persist_order is PersistOrder.PERSIST_THEN_EFFECT:
        return (
            Outcome.LOST,
            "the completion was persisted before the effect; recovery skips a step that never "
            "crossed",
        )
    # effect_then_persist or the LangGraph race: the effect crossed, the completion did not persist.
    if rt.determinism is Determinism.NONDETERMINISTIC:
        # The re-run redraws, so it neither reproduces the first effect nor derives its key. An
        # idempotent boundary has nothing to match on; a naive one never had one. Either way the
        # two crossings differ, which is a worse failure than a duplicate and a distinct outcome.
        return (
            Outcome.DIVERGED,
            "the step is not reproducible from its durable inputs: the re-run draws a different "
            "payload, so the derived key differs, the dedup misses, and the second crossing is a "
            "different action",
        )
    if rt.effect_mode is EffectMode.IDEMPOTENT:
        return (
            Outcome.EXACTLY_ONCE,
            "recovery re-runs the step, but the idempotent boundary dedups the second attempt",
        )
    if rt.persist_order is PersistOrder.RACE:
        return (
            Outcome.DUPLICATED,
            "recovery re-executes the node (the put/put_writes race is host-dependent); the naive "
            "effect crosses twice",
        )
    return (
        Outcome.DUPLICATED,
        "recovery re-runs the step (at-least-once); the naive effect crosses twice",
    )


PREDICTED: dict[str, dict[str, Cell]] = {
    rt.id: {b.id: predict(rt, b) for b in BARRIERS} for rt in RUNTIMES
}


def cell(runtime_id: str, barrier_id: str) -> Cell:
    return PREDICTED[runtime_id][barrier_id]


@dataclass(frozen=True)
class RuntimeSummary:
    runtime_id: str
    exactly_once: tuple[str, ...]
    duplicated: tuple[str, ...]
    lost: tuple[str, ...]
    diverged: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        """No barrier duplicates, diverges, or loses the effect - the property a durable runtime
        promises."""
        return not self.duplicated and not self.lost and not self.diverged


def summarize(runtime: Runtime) -> RuntimeSummary:
    once: list[str] = []
    dup: list[str] = []
    lost: list[str] = []
    div: list[str] = []
    for b in BARRIERS:
        o = PREDICTED[runtime.id][b.id].outcome
        if o is Outcome.EXACTLY_ONCE:
            once.append(b.id)
        elif o is Outcome.DUPLICATED:
            dup.append(b.id)
        elif o is Outcome.LOST:
            lost.append(b.id)
        elif o is Outcome.DIVERGED:
            div.append(b.id)
    return RuntimeSummary(runtime.id, tuple(once), tuple(dup), tuple(lost), tuple(div))
