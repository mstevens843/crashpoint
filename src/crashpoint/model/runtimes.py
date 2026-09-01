"""The runtimes under test, as data: a ladder from a no-durability baseline through the three
control adapters that pin each outcome, to the real durable-execution engines in naive and
idempotent variants.

WHAT THIS IS. Eighteen rows, each a bundle of the orthogonal properties ``predict`` reads: whether
the runtime is durable, the order in which it does the effect and the persist write, whether the
effect is made idempotent at the ledger boundary, whether the identity was prepared before a
nondeterministic draw, and whether the effect is reproducible from durable inputs. This file is
data, not behavior - the actual crashing lives in ``adapters/``. The controls exist so the oracle is
proven to discriminate: DUPLICATED, LOST, EXACTLY_ONCE, and DIVERGED must all be producible on
demand. If the harness cannot make the oracle produce them, the oracle has no teeth.

WHAT THIS IS NOT. It is not the real runtimes' documentation. Each real row carries an ``upstream``
note tying it to the shipped semantics it models; the harness grades the real adapters against these
declarations and any disagreement is a finding.
"""

from __future__ import annotations

from dataclasses import dataclass

from .layers import Determinism, Durability, EffectMode, PersistOrder


@dataclass(frozen=True)
class Runtime:
    id: str
    slug: str
    summary: str
    durability: Durability
    persist_order: PersistOrder
    effect_mode: EffectMode
    is_real: bool          # a real durable-execution engine, vs a control adapter
    # Is the effect reproducible from the step's durable inputs? Defaults to DETERMINISTIC because
    # the original runtime rows were; the nondeterministic rows declare it explicitly.
    determinism: Determinism = Determinism.DETERMINISTIC
    upstream: str = ""
    is_target: bool = False     # the live defect this project is built around (#8039)
    is_reference: bool = False  # the correct-by-construction idempotent reference


RUNTIMES: tuple[Runtime, ...] = (
    # -- controls: they pin the non-VOID outcomes so the oracle is proven to have teeth -----------
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
    Runtime(
        id="r_diverge",
        slug="diverge_control",
        summary="Durable, effect-then-persist, idempotent boundary, but the effect payload is "
        "drawn DURING the step (the model-call shape). The re-run derives a different key from a "
        "different payload, so the dedup misses and the second crossing is a different action. "
        "Pins DIVERGED: the oracle must distinguish two-different-effects from the-same-effect-"
        "twice, or the idempotent arm's EXACTLY_ONCE means nothing for a nondeterministic step.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.IDEMPOTENT,
        is_real=False,
        determinism=Determinism.NONDETERMINISTIC,
    ),
    Runtime(
        id="r_twophase",
        slug="two_phase_reference",
        summary="Durable, effect-then-persist, NONDETERMINISTIC effect, but the identity is "
        "prepared before the draw and carried through the effect. The b1 re-run redraws, but "
        "reuses the same pre-call key, so the ledger dedups it. This is the measured candidate for "
        "closing the DIVERGED floor, not a runtime-specific claim.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.TWO_PHASE,
        is_real=False,
        determinism=Determinism.NONDETERMINISTIC,
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
    # -- the nondeterministic arm: the SAME idempotent boundary, on a step whose effect is not -----
    # -- reproducible from its durable inputs. The conditional @vasilisnasopoulos named on #8039. --
    Runtime(
        id="r_lg_nondet",
        slug="langgraph_nondet",
        summary="LangGraph durability=sync, idempotent boundary, NONDETERMINISTIC effect. "
        "Identical to langgraph_idem in every respect except that the effect payload carries a "
        "value drawn "
        "during the node (the model-output shape). The b1 re-run derives a different key, so the "
        "boundary that recovers exactly-once for a deterministic node does not apply at all.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.RACE,
        effect_mode=EffectMode.IDEMPOTENT,
        is_real=True,
        upstream="langchain-ai/langgraph#8039; the nondeterministic-step conditional",
        determinism=Determinism.NONDETERMINISTIC,
    ),
    Runtime(
        id="r_lg_twophase",
        slug="langgraph_twophase",
        summary="LangGraph durability=sync, NONDETERMINISTIC effect, with the identity prepared in "
        "a durable predecessor node before the draw. The charge node may re-run and redraw at b1, "
        "but it recovers the pre-call identity from the checkpointed state.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.RACE,
        effect_mode=EffectMode.TWO_PHASE,
        is_real=True,
        upstream="langchain-ai/langgraph#8039; two-phase identity-before-draw candidate",
        determinism=Determinism.NONDETERMINISTIC,
    ),
    Runtime(
        id="r_tmp_nondet",
        slug="temporal_nondet",
        summary="Temporal activity, idempotency-key boundary, NONDETERMINISTIC effect. The "
        "activity retry re-runs the draw, so the key the retry derives is not the key the first "
        "attempt used. Temporal journals an activity's RESULT, which restores determinism for the "
        "workflow on replay; it does not restore it for a retry of the activity itself, which is "
        "the window the effect lives in.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.IDEMPOTENT,
        is_real=True,
        upstream="Temporal at-least-once activities; retries re-run the whole activity body",
        determinism=Determinism.NONDETERMINISTIC,
    ),
    Runtime(
        id="r_tmp_twophase",
        slug="temporal_twophase",
        summary="Temporal activity, NONDETERMINISTIC effect, with a deterministic identity "
        "computed by the workflow and passed as an activity argument before the draw. Activity "
        "retry re-runs the draw but reuses the same identity.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.TWO_PHASE,
        is_real=True,
        upstream="Temporal activity arguments are recorded before activity retry",
        determinism=Determinism.NONDETERMINISTIC,
    ),
    Runtime(
        id="r_dbos_nondet",
        slug="dbos_nondet",
        summary="DBOS step, idempotency-key boundary, NONDETERMINISTIC effect. A step that has not "
        "committed its output is re-run on recovery, re-running the draw with it, so the recovered "
        "step's effect is not the effect the crashed step performed.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.IDEMPOTENT,
        is_real=True,
        upstream="DBOS: steps should be idempotent - which a nondeterministic step cannot be",
        determinism=Determinism.NONDETERMINISTIC,
    ),
    Runtime(
        id="r_dbos_twophase",
        slug="dbos_twophase",
        summary="DBOS step, NONDETERMINISTIC effect, with a prepared-identity step before the "
        "effect step. Recovery replays the uncommitted effect step with the same prepared key, "
        "even though the effect payload redraws.",
        durability=Durability.DURABLE,
        persist_order=PersistOrder.EFFECT_THEN_PERSIST,
        effect_mode=EffectMode.TWO_PHASE,
        is_real=True,
        upstream="DBOS checkpointed predecessor step as two-phase identity carrier",
        determinism=Determinism.NONDETERMINISTIC,
    ),
)

RUNTIMES_BY_ID = {r.id: r for r in RUNTIMES}
RUNTIME_IDS = tuple(r.id for r in RUNTIMES)
