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
> - **LangGraph hidden edges are measured separately.** `lg_pre_first_checkpoint` is LOST at k=50,
>   and `lg_pending_writes_after_persist` is EXACTLY_ONCE at k=50. These are not folded into the
>   shared b0/b1/b2 matrix:
>   `uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 --name langgraph_hidden`
>   and
>   `uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 --name langgraph_hidden_pending --barrier lg_pending_writes_after_persist`.
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
> - **A real model sampler is measured narrowly.** The UUID/draw arm remains the default
>   irreproducibility control, and an Anthropic Haiku 4.5 run measures the real model-backed shape on
>   the LangGraph nondeterministic/two-phase rows at k=5:
>   `CRASHPOINT_NONDET_SOURCE=model CRASHPOINT_MODEL_SAMPLER_CMD='python scripts/anthropic_sampler.py' uv run --extra langgraph python -m crashpoint.harness.matrix --k 5 --runtimes r_lg_nondet,r_lg_twophase --name langgraph_model`.
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
runtime/                  optional runtime-specific probes that are not Python package code
scripts/                  optional local helpers for non-baseline evidence runs
results/                  numbered, append-only lab notebook (00 substrate .. 08 current phase)
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
- **No broad real-model provider claim.** `evidence/langgraph_model.json` measures Anthropic Haiku
  4.5 on the LangGraph nondeterministic/two-phase rows at k=5. It is real model-sampler evidence,
  not a claim about every model, provider cache, temperature, or seeded/local sampler.
  `scripts/anthropic_sampler.py` reads secrets from the shell or a local ignored `.env`.
- **No hidden-barrier overclaim.** The measured cross-runtime barriers are b0/b1/b2. LangGraph's
  pre-first-checkpoint edge is measured separately as `lg_pre_first_checkpoint` in
  `evidence/langgraph_hidden.json`, and the pending-writes-after-persist edge is measured separately
  as `lg_pending_writes_after_persist` in `evidence/langgraph_hidden_pending.json`. Remaining
  framework-internal candidates stay inventoried by
  `uv run python -m crashpoint.harness.barrier_inventory` until each has its own model rule and run.
- **No Vercel Workflow claim yet.** An optional JS/TS Nitro fixture exists under
  `runtime/vercel-workflow/`, but it is not a modeled or measured adapter. On 2026-09-01, both the
  built server and `nitro dev` failed before any workflow run because the bundled Local World raised
  `Invalid version string: "bundled"` during data-dir initialization.
  `uv run python -m crashpoint.harness.deferred_runtimes` records the remaining Vercel blocker: it
  still needs a faithful Workflow worker/backend crash harness before any row can be modeled or
  measured.
