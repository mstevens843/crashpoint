# crashpoint

**Durable-execution runtimes guarantee exactly-once for the JOURNALED RESULT, but at-least-once for
the actual EXTERNAL side effect inside an activity/step/run unless that unit is made idempotent.
crashpoint crashes them at named barriers and measures the exactly-once property through an
out-of-process ledger the runtime cannot forge, publishing a per-cell pass-rate matrix with error
bars.**

The target is real and active. `langchain-ai/langgraph#8039` (open): under `durability="sync"`, the
pending-writes persist and the superseding checkpoint race on a shared executor, so whether recovery
replays the writes or re-executes the node - and therefore whether a naive side effect duplicates -
depends on the host. Temporal activities are at-least-once; DBOS steps should be idempotent; Restate
durable steps journal operation results after the action returns. All of them push the exactly-once
burden for the external effect onto the developer. crashpoint measures where that burden is unmet
across four engines, three enumerated crash barriers, and a side-effect count the runtime cannot
self-report.

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
>   declared (durability, persist order, effect mode, determinism) and a crash barrier, the predicted
>   outcome for 22 runtime rows x 3 barriers: `uv run python -m crashpoint.model`. Purity is checked,
>   not asserted; zero third-party dependencies in the model.
> - **The ledger is out of process and forgery-proof.** A separate daemon behind two Unix sockets
>   records every attempt, counts distinct side effects, and hash-chains every record. The subject
>   holds only an execute-only invoke socket and an opaque receipt; it cannot read, reset, or seal the
>   count. Editing any record breaks the chain and the oracle emits VOID:
>   `uv run python -m crashpoint.adversaries.reflexive`.
> - **The controls prove the oracle discriminates.** Control/reference rows pin DUPLICATED / LOST /
>   EXACTLY_ONCE / DIVERGED and the two-phase recovery shape over 1,800 crash+recover trials, zero
>   disagreements with the model:
>   `uv run python -m crashpoint.harness.matrix --k 100 --runtimes r_null,r_dup,r_lost,r_idem,r_diverge,r_twophase --name controls`.
> - **LangGraph #8039, reproduced against current behavior.** The naive effect DUPLICATES at the
>   after-effect-before-persist barrier; the idempotent boundary recovers EXACTLY_ONCE for a
>   reproducible node; the nondeterministic twin DIVERGES at the same barrier; a two-phase
>   identity-before-draw row recovers EXACTLY_ONCE. Zero disagreements:
>   `uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem,r_lg_nondet,r_lg_twophase --name langgraph`.
> - **Temporal, DBOS, and Restate, the same contrast on real engines.** Temporal (local
>   `start-dev`), DBOS (Docker Postgres), and Restate (Docker dev server plus Python ASGI service)
>   DUPLICATE the naive effect at the lethal barrier, recover EXACTLY_ONCE with the idempotent
>   boundary for a reproducible step, DIVERGE for the nondeterministic content-derived key, and
>   recover EXACTLY_ONCE with the two-phase identity-before-draw shape:
>   `uv run --extra temporal python -m crashpoint.harness.matrix --k 30 --runtimes r_tmp_naive,r_tmp_idem,r_tmp_nondet,r_tmp_twophase --name temporal`
>   and `uv run --extra dbos python -m crashpoint.harness.matrix --k 30 --runtimes r_dbos_naive,r_dbos_idem,r_dbos_nondet,r_dbos_twophase --name dbos`.
>   Restate was measured at k=10 with
>   `uv run --extra restate python -m crashpoint.harness.restate_matrix --k 10 --name restate`.
> - **The nondeterministic condition is measured.** A content-derived idempotency key only survives a
>   crash if replay reproduces the same action. When the step draws a value DURING the call - the
>   model-call shape - replay derives a different key and the cell reads DIVERGED. If the identity is
>   durably prepared before that draw and carried through the effect, the measured two-phase rows read
>   EXACTLY_ONCE. The recomputability probe is `uv run python -m crashpoint.harness.recomputability`.
>
> **Do not cite a number from this repo that does not name the command that produced it.**

## Reproduce

`uv.lock` is part of the evidence record and is intentionally tracked. Start with a locked sync for
the full local checks; this installs the optional runtime packages for type checking, but does not
start Temporal or Postgres:

```
uv sync --group dev --all-extras --locked
uv run pytest
uv run ruff check .
uv run mypy
```

The full evidence commands, including the Temporal, DBOS, and Restate substrate setup, are in
[RESULTS.md](./RESULTS.md).

## Layout

```
src/crashpoint/model/     the prediction: layers, runtimes, barriers, predict (pure, purity-tested)
src/crashpoint/canonical.py  canonical JSON + SHA-256 receipts + hash chain
src/crashpoint/ledger/    the out-of-process ledger daemon + Outcome oracle + idempotency keys
src/crashpoint/adapters/  minimal durable workflows per runtime and two-phase variants
src/crashpoint/harness/   k crash+recover trials, Wilson intervals, inventories, recomputability
src/crashpoint/adversaries/  reflexive adversary + Linux UID-drop isolation probe
evidence/                 receipted observed matrices and adversary proofs
scripts/                  optional local helpers for non-baseline evidence runs
results/                  numbered, append-only lab notebook (00 substrate .. 07 current phase)
DISCLOSURE.md             drafted upstream note, with conservative claims and limitations
```

## The five outcomes and the three barriers

`Outcome {EXACTLY_ONCE, DUPLICATED, DIVERGED, LOST, VOID}`, where DIVERGED is "it crossed twice and
the crossings were not the same action" (two different charges, not one charge twice) and VOID is the
fail-closed "cannot certify". The barriers are named relative to the effect and the runtime's persist
write: **b0** before the effect, **b1** after the effect but before the completion is durable, and
**b2** after the completion is durable. The whole finding is the b1 column.

## What it does not do

- **No strong macOS isolation claim.** The default macOS fixture proves the socket-privilege boundary:
  the subject has only execute/invoke capability. The stronger Linux UID-drop proof is Linux-only;
  the direct macOS command reports BLOCKED, while the Dockerized Linux run passed and is receipted in
  `evidence/isolation_linux.json`.
- **No real-model evidence yet.** `CRASHPOINT_NONDET_SOURCE=model` and
  `CRASHPOINT_MODEL_SAMPLER_CMD` provide an optional real-sampler arm, but this repo has no checked-in
  model-backed evidence because no safe sampler/provider credentials were configured for this run.
  `scripts/anthropic_sampler.py` is a helper for operators who set `ANTHROPIC_API_KEY` and
  `ANTHROPIC_MODEL` in their shell. The UUID/draw arm remains the cheap irreproducibility control.
- **No hidden-barrier overclaim.** The measured cross-runtime barriers are b0/b1/b2. LangGraph's
  pre-first-checkpoint edge is measured separately as `lg_pre_first_checkpoint` in
  `evidence/langgraph_hidden.json`; the remaining framework-internal candidates stay inventoried by
  `uv run python -m crashpoint.harness.barrier_inventory` until each has its own model rule and run.
- **No Vercel Workflow adapter yet.** Restate now has optional model rows, a Python ASGI adapter, and
  k=10 receipted evidence against a Docker dev server.
  `uv run python -m crashpoint.harness.deferred_runtimes` records the remaining Vercel blocker: it
  needs a faithful Workflow worker/backend crash harness before any row can be modeled or measured.
