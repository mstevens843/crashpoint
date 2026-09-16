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
durable steps journal operation results after the action returns; a Vercel Workflow inline step
journals its completion only after the body returns. All of them push the exactly-once burden for
the external effect onto the developer. crashpoint measures where that burden is unmet across five
engines, three enumerated crash barriers, eight hidden framework edges, and a side-effect count the
runtime cannot self-report.

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
>   outcome for 26 runtime rows x 3 barriers: `uv run python -m crashpoint.model`. Purity is checked,
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
> - **Eight hidden framework edges are measured separately.** A crash point inside a runtime's own
>   persistence machinery gets its own predicted rule and its own evidence file, and is never folded
>   into the shared b0/b1/b2 matrix. LangGraph: `lg_pre_first_checkpoint` LOST,
>   `lg_pending_writes_after_persist` EXACTLY_ONCE, both at k=50. Temporal: a durable activity
>   schedule with no worker attempt, and a workflow task that dies consuming a durable activity
>   completion, both EXACTLY_ONCE at k=30. DBOS: an uncommitted step-output INSERT DUPLICATES, a
>   committed one recovers EXACTLY_ONCE, an uncommitted terminal-status UPDATE recovers
>   EXACTLY_ONCE, and two modules registering the same workflow name make recovery DIVERGE, all at
>   k=30. The Temporal and DBOS commands are in [RESULTS.md](./RESULTS.md); the LangGraph pair is:
>   `uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 --name langgraph_hidden`
>   and
>   `uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 --name langgraph_hidden_pending --barrier lg_pending_writes_after_persist`.
> - **The pre-first-checkpoint admission gap has a measured containment arm.** In 30 paired
>   runtime-only and caller-ledger trials, both arms died with zero checkpoints, zero effects, and
>   `EmptyInputError`. The runtime-only arm correctly remained `UNVERIFIED`; an external acceptance
>   event committed with the original input before dispatch let recovery identify the admitted run,
>   replay it explicitly, and finish with one effect and three checkpoints in all 30 trials. This is
>   an application pattern, not a LangGraph fix:
>   `uv run --extra langgraph python -m crashpoint.harness.langgraph_admission --k 30 --name langgraph_admission`.
> - **A non-crash positive control binds admission to an independently observed effect.** Separate
>   from the admission gap above and from any crash: one ordinary, uncrashed LangGraph execution is
>   bound end to end (admission_id -> thread_id -> execution result -> external effect reference ->
>   independently rereadable state hash -> receipt), and a freshly spawned observer process - never
>   the worker, never `ledger.dump()` - independently reads the on-disk ledger and confirms one
>   matching effect. An offline verifier rechecks the whole chain from retained bytes without
>   installing or importing LangGraph. Recorded once, `results/12-langgraph-noncrash-control.md`:
>   `uv run --extra langgraph python -m crashpoint.harness.langgraph_control --output evidence/langgraph_noncrash_control_v4 --name langgraph_noncrash_control_v4`.
> - **Vercel Workflow, the fifth engine.** The JS/TS Workflow DevKit fixture in
>   `runtime/vercel-workflow/` runs on the Local World and shows the same b1 contrast: naive
>   DUPLICATES, the idempotent boundary recovers EXACTLY_ONCE, the nondeterministic twin DIVERGES,
>   and the two-phase row recovers EXACTLY_ONCE at rate 0.933 with two fail-closed VOID trials.
>   Zero disagreements:
>   `uv run python -m crashpoint.harness.vercel_matrix --k 30 --name vercel --timeout 60`.
>   Reaching a running Local World needed one environment variable and no patching; the root cause
>   is in `runtime/vercel-workflow/README.md`.
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
> - **CrewAI's own tool retry can duplicate a committed effect - no crash involved.**
>   [crewAIInc/crewAI#5802](https://github.com/crewAIInc/crewAI/issues/5802), reproduced narrowly: a
>   same-process `ToolUsage._use` retry (confirmed as a tool retry, not a task retry, an agent retry,
>   or an external re-trigger) re-invokes a tool after an injected failure, and when the first
>   invocation's effect had already committed, the retry performs it again. 90/90 trials (k=30 per
>   case) match the pre-registered prediction - `clean`/`pre_effect` EXACTLY_ONCE, `post_effect`
>   DUPLICATED:
>   `uv run --extra crewai python -m crashpoint.harness.crewai_retry --k 30 --name crewai_retry`.
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
results/                  numbered, append-only lab notebook (00 substrate .. 10 current phase)
DISCLOSURE.md             drafted upstream note, with conservative claims and limitations
```

## The five outcomes and the three barriers

`Outcome {EXACTLY_ONCE, DUPLICATED, DIVERGED, LOST, VOID}`, where DIVERGED is "it crossed twice and
the crossings were not the same action" (two different charges, not one charge twice) and VOID is the
fail-closed "cannot certify". The barriers are named relative to the effect and the runtime's persist
write: **b0** before the effect, **b1** after the effect but before the completion is durable, and
**b2** after the completion is durable. The whole finding is the b1 column.

## What it does not do

- **No native macOS isolation receipt.** The default fixture proves the socket-privilege boundary:
  the subject has only execute/invoke capability. The UID-drop proof now runs natively on macOS as
  well as Linux (`setpriv` there, Python's own uid drop here), but it needs root, so the direct
  command reports BLOCKED for a normal shell. The Dockerized Linux run passed and is receipted in
  `evidence/isolation_linux.json`; this repo carries no native macOS receipt.
- **No broad real-model provider claim.** `evidence/langgraph_model.json` measures Anthropic Haiku
  4.5 on the LangGraph nondeterministic/two-phase rows at k=5. It is real model-sampler evidence,
  not a claim about every model, provider cache, temperature, or seeded/local sampler.
  `scripts/anthropic_sampler.py` reads secrets from the shell or a local ignored `.env`.
- **No hidden-barrier overclaim.** The cross-runtime barriers are b0/b1/b2. The eight
  framework-internal edges are each measured on their own, with their own predicted rule and
  evidence file, and are deliberately kept out of the shared matrix. One candidate remains
  inventoried and unmeasured: a Vercel Workflow crash between world-local's step-create claim and
  the step entity, seen as a recovery wedge in the shared matrix and scored VOID there. The full
  list is `uv run python -m crashpoint.harness.barrier_inventory`.
- **No admission-ledger overclaim.** `evidence/langgraph_admission.json` shows a caller-owned
  acceptance record preserving enough authority and input to replay the pre-first-checkpoint loss.
  It is not a LangGraph guarantee and does not make external effects safe to replay without stable
  idempotency, attempt records, authorization, and destination reconciliation.
- **No non-crash-control overclaim.** `evidence/langgraph_noncrash_control_v4/` is one recorded,
  non-crashed run (n=1), not a statistical rate, a LangGraph fix, a global exactly-once guarantee,
  admission/dispatch fencing, or SABLE certification. Its offline verifier checks internal
  consistency of retained evidence, not an independent rerun or an authenticated real-world effect.
  `evidence/langgraph_noncrash_control/` (v1), `evidence/langgraph_noncrash_control_v2/`, and
  `evidence/langgraph_noncrash_control_v3/` are earlier, superseded recordings kept byte-for-byte
  for the record (schema mismatch is rejected by the current verifier, not silently reinterpreted);
  see `results/12-langgraph-noncrash-control.md`.
- **No managed Vercel World claim.** The measured Vercel Workflow rows run on the Local World, a
  single-process filesystem backend. The managed world (Vercel Queues plus Vercel Functions) cannot
  be SIGKILLed at a named barrier from this sandbox, so it stays deferred and unmodeled:
  `uv run python -m crashpoint.harness.deferred_runtimes`.
- **No CrewAI idempotency-guard, crash, or fresh-process recovery claim.** `evidence/crewai_retry.json`
  measures only CrewAI's existing, same-process `ToolUsage` retry with no crash involved; it does not
  evaluate the opt-in idempotency guard proposed in the open, unmerged
  [crewAIInc/crewAI#5822](https://github.com/crewAIInc/crewAI/pull/5822), and it says nothing about
  process-crash or durable recovery for CrewAI.
