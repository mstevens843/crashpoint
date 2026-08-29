# 06 - the nondeterminism conditional, and a fifth outcome

Working record. This entry exists because a reviewer named a limit the first five entries did not
measure, and the limit turned out to be real.

## Where it came from

On 2026-08-28, after crashpoint was posted to `langchain-ai/langgraph#8039`,
[@vasilisnasopoulos](https://github.com/vasilisnasopoulos) replied with the sharpest available
objection to the whole result:

> An ordinal within the step closes that - but only under a condition worth stating: **the step must
> be deterministic given its durable inputs.** [...] **A step that calls a model is not deterministic
> given its inputs**, so replay does not reproduce its effects, and no key format rescues that - the
> discriminator you need does not exist until after the non-deterministic call has already happened.
> None of the three runtimes measured here addresses it. [...] The cheap test for the first case is
> whether a process that did not run the step can recompute the identity from the durable inputs
> alone.

He is right, and it lands on this project specifically. The fixture in entries 00-05 is a
deterministic 4-step workflow - a deliberate choice, so the whole matrix costs no API dollars - which
means the `*_idem` rows measured the case that is already solvable. The interesting case was out of
frame.

## What was NOT needed to measure it

A model. The property is irreproducibility, not model-ness: an effect payload carrying a value drawn
during the step has exactly the property he describes, whether a sampler or `uuid4()` drew it. Using
a real model here would cost money, add latency and an API key to every trial, and diverge only when
the sampler happened to - where a draw diverges on every trial, deterministically. So the
nondeterministic arm is a drawn `memo` field, and the honest statement of what that is stands in
`adapters/base.py::_draw`.

The `memo` name matters. It is ordinary semantic content - the memo line an agent would have a model
write - so it is legitimately part of what the action IS, and therefore legitimately part of a
content-derived key. That is what makes it lethal rather than a strawman: the key stays
content-derived, the existing forbidden-identity-field guard stays satisfied, and the dedup still
fails.

## The model, written before the adapters existed

Two additions, written and rendered before the adapters, the oracle change, and the probe existed
- the rendered output below is the prediction as it stood then:

- **A fifth outcome, `DIVERGED`** - the effect crossed twice AND the crossings differ in content.
  Not a duplicate: two DIFFERENT charges, not one charge twice. Strictly worse, because the usual
  reconciliations do not reach it - you cannot dedup by matching, and you cannot refund "the second
  one" without first deciding which was real.
- **A `Determinism` axis**, defined by his test: is the effect reproducible from the step's durable
  inputs alone?

The rule, in `predict.py`: at the BETWEEN barrier, a nondeterministic step is `DIVERGED` regardless
of effect mode - the re-run redraws, so it neither reproduces the first effect nor derives its key.
An idempotent boundary has nothing to match on; a naive one never had one.

The prediction also says where nondeterminism costs **nothing**: at b0 only the recovery run's
effect ever crosses, and at b2 only the crashed run's did. One crossing is one crossing whether or
not it was reproducible. If b0 or b2 had also diverged, the fixture would be drawing where it should
not and the b1 result would mean less - so that is asserted, not assumed
(`test_nondeterminism_does_not_bleed_into_the_calibration_columns`).

## The recomputability probe (`uv run python -m crashpoint.harness.recomputability`)

His cheap test, implemented rather than quoted, in `harness/recomputability.py`. The harness process
never ran the step - the step ran in a subprocess that was SIGKILLed - so "a process that did not run
the step" is literal here.

```
PROBE A - deterministic step (idem_reference):
  {"distinct_effects_crossed": 1, "distinct_keys_used": 1, "outcome_at_b1": "exactly_once",
   "recomputability": "RECOMPUTABLE", "runtime": "r_idem"}
PROBE B - nondeterministic step (diverge_control):
  {"distinct_effects_crossed": 2, "distinct_keys_used": 2, "outcome_at_b1": "diverged",
   "recomputability": "NOT_RECOMPUTABLE", "runtime": "r_diverge"}
GUARD - the existing forbidden-identity-field check:
  {"guard_accepts_deterministic_payload": true, "guard_accepts_nondeterministic_payload": true,
   "guard_rejects_a_classic_per_attempt_payload": true}
```

Two things worth stating separately:

1. **The predicate is a LEADING indicator.** It decided both cases without a crash, a recovery, or k
   trials, and `NOT_RECOMPUTABLE` predicted `DIVERGED` at b1. That is the practical value of his
   test: it answers before you ship, where the matrix answers after.
2. **The existing guard does not catch this.** `assert_no_forbidden_identity_fields` is the state of
   the art for the classic key bug - it rejects `attempt`, `retry`, `epoch`, `nonce`, a delivery id.
   It **accepts** the payload that breaks the dedup here, because `memo` is not a per-attempt field.
   Passing the guard says nothing about recomputability; they are not the same check.

## What it measured

Four runs, all re-run from scratch so the evidence is coherent with the new rows.

```
uv run python -m crashpoint.harness.matrix --k 100 \
  --runtimes r_null,r_dup,r_lost,r_idem,r_diverge --name controls
uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 \
  --runtimes r_lg_naive,r_lg_idem,r_lg_nondet --name langgraph
uv run --extra temporal python -m crashpoint.harness.matrix --k 30 \
  --runtimes r_tmp_naive,r_tmp_idem,r_tmp_nondet --name temporal
uv run --extra dbos python -m crashpoint.harness.matrix --k 30 \
  --runtimes r_dbos_naive,r_dbos_idem,r_dbos_nondet --name dbos
```

```
runtime                before_effect           between     after_persist
------------------------------------------------------------------------
null_baseline              ONCE/ONCE           DUP/DUP           DUP/DUP    k=100
dup_control                ONCE/ONCE           DUP/DUP         ONCE/ONCE    k=100
lost_control               ONCE/ONCE         LOST/LOST         ONCE/ONCE    k=100
idem_reference             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE    k=100
diverge_control            ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE    k=100
langgraph_naive            ONCE/ONCE           DUP/DUP         ONCE/ONCE    k=50
langgraph_idem             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE    k=50
langgraph_nondet           ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE    k=50
temporal_naive             ONCE/ONCE           DUP/DUP         ONCE/ONCE    k=30
temporal_idem              ONCE/ONCE         ONCE/ONCE         ONCE/ONCE    k=30
temporal_nondet            ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE    k=30
dbos_naive                 ONCE/ONCE           DUP/DUP         ONCE/ONCE    k=30
dbos_idem                  ONCE/ONCE         ONCE/ONCE         ONCE/ONCE    k=30
dbos_nondet                ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE    k=30

disagreements (model wrong): []
```

2,490 crash+recover trials, every cell at rate 1.0, zero disagreements on any runtime. The model was
written and rendered before the adapters, the oracle change, and the probe existed, and it was not
corrected once - the b1 rule it predicts is the rule every measured cell landed on.

## The result, stated as narrowly as it should be

Read the three `*_idem` / `*_nondet` pairs as pairs. Same engine, same barrier, same idempotent
boundary, same content-derived key. The one declared property that changes is whether the step is
reproducible from its durable inputs, and the b1 cell goes from `EXACTLY_ONCE` to `DIVERGED` on all
three runtimes.

So the fix published in entries 03-05 - "an idempotent dedup-by-key boundary is the only thing that
recovers EXACTLY_ONCE" - is now stated with its condition attached: **it recovers exactly-once for a
step that is reproducible from its durable inputs, and does nothing for a step that is not.** That
is not a weakening of the earlier result; it is the boundary of it, and it was not measured before
because the fixture never left the reproducible case.

A second floor now sits under the first:

- **Floor 1** (entries 03-05): no naive effect closes b1 on any runtime.
- **Floor 2** (here): no idempotent boundary closes b1 either, once the step stops being
  reproducible. Nothing in any of the three runtimes addresses it, which is what
  @vasilisnasopoulos said, and it now has a number.

## What this still does not prove

- **A drawn value is not a model call.** It has the property that matters here - not reproducible
  from the durable inputs - and it isolates that property cleanly, which a model would not. But no
  frontier model was in the loop, and nothing here measures how a real sampler behaves under
  temperature, seeding, or a provider's own caching. The claim is about irreproducibility, and it
  should be read as exactly that.
- **This does not say the case is unsolvable.** It says none of the three runtimes solves it, and
  that the standard mitigation does not. A two-phase shape - durably record an intent and its
  identity BEFORE the nondeterministic call, then carry that identity through the effect - is the
  obvious candidate, and it is not implemented or measured here. Calling it a fix without crashing
  it would be exactly the thing this project exists to avoid.
- **`DIVERGED` is an observation, not a severity ranking.** The ledger reports that two crossings
  differed in content. Whether that is worse than a duplicate in a given system is a judgment about
  that system; the argument for it being worse is in `layers.py`, and it is an argument, not a
  measurement.
