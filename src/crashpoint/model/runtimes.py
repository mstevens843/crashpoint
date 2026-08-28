"""The runtimes under test, as data: a ladder from a no-durability baseline through the three
control adapters that pin each outcome, to the real durable-execution engines in naive and
idempotent variants.

WHAT THIS IS. Ten rows, each a bundle of the orthogonal properties ``predict`` reads: whether the
runtime is durable, the order in which it does the effect and the persist write, and whether the
effect is made idempotent at the ledger boundary. This file is data, not behavior - the actual
crashing lives in ``adapters/``. The controls exist so the oracle is proven to discriminate: one
must DUPLICATED, one must LOST, one must EXACTLY_ONCE. If the harness cannot make the oracle produce
all three on demand, the oracle has no teeth.

WHAT THIS IS NOT. It is not the real runtimes' documentation. Each real row carries an ``upstream``
note tying it to the shipped semantics it models; the harness grades the real adapters against these
declarations and any disagreement is a finding.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layers import Durability, EffectMode, PersistOrder


@dataclass(frozen=True)
class Runtime:
    id: str
    slug: str
    summary: str
    durability: Durability
    persist_order: PersistOrder
    effect_mode: EffectMode
    is_real: bool          # a real durable-execution engine, vs a control adapter
    upstream: str = ""
    is_target: bool = False     # the live defect this project is built around (#8039)
    is_reference: bool = False  # the correct-by-construction idempotent reference


RUNTIMES: tuple[Runtime, ...] = (
    # -- controls: they pin the three non-VOID outcomes so the oracle is proven to have teeth -----
    Runtime(
        id="r_null",
        slug="null_baseline",
        summary="No durability. On a crash the whole fixture is re-run from the top. The baseline: "
        "shows what 'no durable execution' costs - a re-run after the effect duplicates it.",
        durability=Durability.NONE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.NAIVE,
        is_real=False,
    ),
    Runtime(
        id="r_dup",
        slug="dup_control",
        summary="Durable, effect-then-persist, naive. A crash in the middle re-runs the effect. "
        "Pins the DUPLICATED outcome: the oracle must report a doubled side effect.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.NAIVE,
        is_real=False,
    ),
    Runtime(
        id="r_lost",
        slug="lost_control",
        summary="Durable, PERSIST-then-effect (the inverse bug), naive. A crash in the middle "
        "skips the effect that never ran. Pins LOST: the oracle must report a missing effect.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.PERSIST_THEN_EFFECT,
        effect_mode=EffectMode.NAIVE,
        is_real=False,
    ),
    Runtime(
        id="r_idem",
        slug="idem_reference",
        summary="Durable, effect-then-persist, idempotent boundary (the outbox pattern inline). "
        "Pins the EXACTLY_ONCE outcome under the lethal barrier: the ledger dedups the re-run.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.IDEMPOTENT,
        is_real=False,
        is_reference=True,
    ),
    # -- LangGraph: the live defect (#8039), naive vs idempotent --------------------------------
    Runtime(
        id="r_lg_naive",
        slug="langgraph_naive",
        summary="LangGraph durability=sync, naive effect. put_writes and the superseding put race "
        "on a shared executor, so a production crash lands nondeterministically at the b1 barrier "
        "(pending writes persisted -> re-run -> DUPLICATED) or b2 (completion persisted -> skip -> "
        "EXACTLY_ONCE). The harness enumerates both, removing the race (#8039).",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.RACE,
        effect_mode=EffectMode.NAIVE,
        is_real=True,
        upstream="langchain-ai/langgraph#8039 (open)",
        is_target=True,
    ),
    Runtime(
        id="r_lg_idem",
        slug="langgraph_idem",
        summary="LangGraph durability=sync, idempotent boundary. The same b1/b2 race happens, but "
        "the ledger dedups the re-executed effect, so the external effect is exactly-once at every "
        "barrier - the fix that closes #8039's duplicate.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.RACE,
        effect_mode=EffectMode.IDEMPOTENT,
        is_real=True,
        upstream="langchain-ai/langgraph#8039 (open)",
    ),
    # -- Temporal: at-least-once activities, naive vs idempotent --------------------------------
    Runtime(
        id="r_tmp_naive",
        slug="temporal_naive",
        summary="Temporal activity, naive effect. Activities are at-least-once: a crash after the "
        "effect but before the completion is reported to the service triggers a retry that re-runs "
        "the effect.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.NAIVE,
        is_real=True,
        upstream="Temporal at-least-once activities; idempotency is the caller's job",
    ),
    Runtime(
        id="r_tmp_idem",
        slug="temporal_idem",
        summary="Temporal activity, idempotent boundary. The retry re-runs the activity, but the "
        "ledger dedups by key, so the external effect is exactly-once.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.IDEMPOTENT,
        is_real=True,
        upstream="Temporal recommended mitigation: idempotency key",
    ),
    # -- DBOS: checkpoint-in-Postgres steps, naive vs idempotent --------------------------------
    Runtime(
        id="r_dbos_naive",
        slug="dbos_naive",
        summary="DBOS step, naive effect. A crash after the effect but before the step output is "
        "committed to Postgres re-runs the step on recovery.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.NAIVE,
        is_real=True,
        upstream="DBOS: steps should be idempotent",
    ),
    Runtime(
        id="r_dbos_idem",
        slug="dbos_idem",
        summary="DBOS step, idempotent boundary. The recovery re-run is deduped at the ledger, so "
        "the external effect is exactly-once.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.IDEMPOTENT,
        is_real=True,
        upstream="DBOS: steps should be idempotent",
    ),
)

RUNTIMES_BY_ID = {r.id: r for r in RUNTIMES}
RUNTIME_IDS = tuple(r.id for r in RUNTIMES)
