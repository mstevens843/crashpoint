# crashpoint

**Durable-execution runtimes guarantee exactly-once for the JOURNALED RESULT, but at-least-once for
the actual EXTERNAL side effect inside an activity/step/run unless that unit is made idempotent.
crashpoint crashes them at named barriers and measures the exactly-once property through an
out-of-process ledger the runtime cannot forge, publishing a per-cell pass-rate matrix with error
bars.**

The target is real and active. `langchain-ai/langgraph#8039` (open): under `durability="sync"`, the
pending-writes persist and the superseding checkpoint race on a shared executor, so whether recovery
replays the writes or re-executes the node - and therefore whether a naive side effect duplicates -
depends on the host. Temporal activities are at-least-once ("Activities may be executed more than
once"); DBOS steps "should be idempotent"; all of them push the exactly-once burden for the external
effect onto the developer. crashpoint measures where that burden is unmet, on three runtimes, at
three enumerated crash barriers, against a side-effect count the runtime cannot self-report.

This is defensive reliability research on public MIT/Apache code, run in the author's own sandbox,
crashing runtimes the author controls on a fixture whose only side effect is its own ledger. No
production system is touched.

> [!WARNING]
> **Pre-1.0, nothing published.** What is true is stated with the command that reproduces it; what is
> not done is listed as not done. Full record in [RESULTS.md](./RESULTS.md).
>
> **What runs today, measured rather than remembered:**
>
> - **The model, before any crash.** `src/crashpoint/model/` derives, as a pure total function over
>   declared (durability, persist order, effect mode) and a crash barrier, the predicted outcome for
>   10 runtimes x 3 barriers: `uv run python -m crashpoint.model`. Purity is checked, not asserted;
>   zero third-party dependencies in the model.
> - **The ledger is out of process and forgery-proof.** A separate daemon behind two Unix sockets
>   records every attempt, counts distinct side effects, and hash-chains every record. The subject
>   holds only an execute-only invoke socket and an opaque receipt; it cannot read, reset, or seal the
>   count. Editing any record breaks the chain and the oracle emits VOID:
>   `uv run python -m crashpoint.adversaries.reflexive`.
> - **The controls prove the oracle discriminates.** Three control adapters pin DUPLICATED / LOST /
>   EXACTLY_ONCE on demand over 240 crash+recover trials, zero disagreements with the model:
>   `uv run python -m crashpoint.harness.matrix --k 100 --runtimes r_null,r_dup,r_lost,r_idem --name controls`.
> - **LangGraph #8039, reproduced against current behavior.** The naive effect DUPLICATES at the
>   after-effect-before-persist barrier; the idempotent boundary recovers EXACTLY_ONCE at every
>   barrier; zero disagreements with the model:
>   `uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem --name langgraph`.
> - **Temporal and DBOS, the same contrast on real engines.** Temporal (at-least-once activities, a
>   local `start-dev` server) and DBOS (checkpoint-in-Postgres steps, a Docker Postgres) both
>   DUPLICATE the naive effect at the lethal barrier and recover EXACTLY_ONCE with the idempotent
>   boundary: `uv run --extra temporal python -m crashpoint.harness.matrix --runtimes r_tmp_naive,r_tmp_idem,r_tmp_nondet --name temporal`
>   and `uv run --extra dbos python -m crashpoint.harness.matrix --runtimes r_dbos_naive,r_dbos_idem,r_dbos_nondet --name dbos`.
> - **And the fix has a measured condition.** An idempotency key derived from what the action IS only
>   survives a crash if replaying the step reproduces that action. On a step whose effect depends on a
>   value drawn DURING it - the model-call shape - the re-run derives a different key, the dedup
>   silently stops applying, and the two crossings are not the same action: **DIVERGED**, on all three
>   runtimes, at the same barrier the idempotent boundary closes for a reproducible step. The
>   `*_idem` / `*_nondet` pairs differ in exactly one declared property. The reviewer's own leading
>   indicator is implemented too - can a process that did not run the step recompute the identity from
>   the durable inputs alone? - as `uv run python -m crashpoint.harness.recomputability`.
>
> **Do not cite a number from this repo that does not name the command that produced it.**

## Layout

```
src/crashpoint/model/     the prediction: layers, runtimes, barriers, predict (pure, purity-tested)
src/crashpoint/canonical.py  canonical JSON + SHA-256 receipts + hash chain (reused from a sibling)
src/crashpoint/ledger/    the out-of-process ledger daemon + Outcome oracle + idempotency keys
src/crashpoint/adapters/  minimal durable workflows per runtime, naive vs idempotent effect
src/crashpoint/harness/   k crash+recover trials per cell, Wilson intervals, the observed matrix
src/crashpoint/adversaries/  the reflexive adversary (out-of-reach + tamper-evident VOID)
src/crashpoint/harness/recomputability.py  the leading indicator: is the identity recomputable?
evidence/                 receipted observed matrices (controls, langgraph, temporal, dbos)
results/                  numbered, append-only lab notebook (00 substrate .. 06 nondeterminism)
DISCLOSURE.md             the drafted upstream note for langgraph#8039, crediting the thread by name
```

## The five outcomes and the three barriers

`Outcome {EXACTLY_ONCE, DUPLICATED, DIVERGED, LOST, VOID}`, where DIVERGED is "it crossed twice and
the crossings were not the same action" (two different charges, not one charge twice) and VOID is the
fail-closed "cannot certify". The barriers are named relative to the effect and the runtime's persist
write: **b0** before the effect (recovery replays, effect once), **b1** after the effect but before
the completion is durable (the lethal barrier: recovery re-runs the step, a naive effect duplicates
and a nondeterministic one diverges), **b2** after the completion is durable (recovery skips the step,
effect once). The whole finding is the b1 column.

## What it does not do

It does not achieve true uid-separation on macOS: the ledger is a separate process the subject cannot
reach, but the `setpriv` uid-drop and full container isolation are a documented Linux stretch, not the
default. It does not close b1 without idempotency - that barrier is uncloseable by any runtime without
an idempotent boundary, and that is the finding, named as the honest floor, not a gap. It does not
close b1 WITH idempotency either, once the step stops being reproducible from its durable inputs -
that is the second floor, and it is measured, not fixed; a two-phase intent-before-the-call shape is
the obvious candidate and is deliberately not claimed because it was not crashed. The nondeterministic
arm draws a value rather than calling a model: that isolates irreproducibility cleanly, but nothing
here measures a real sampler. The crash is placed at the adapter's enumerated barriers, not at every
hidden persistence point inside each framework. Restate and the Vercel Workflow DevKit are deferred.
