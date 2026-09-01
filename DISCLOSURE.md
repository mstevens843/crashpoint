# Disclosure draft - the exactly-once external-effect fixture for durable-execution runtimes

**To:** the LangGraph maintainers and the participants of `langchain-ai/langgraph#8039` (in
particular @sajjadanwar0, who opened it; @vasilisnasopoulos, whose RS1-RS3 write-up named the
receiver half of the property and asked for it to be made empirical; @safal207, who built and
published the first executable recovery-safety benchmark for this issue; and @tamilov, who pushed
the thread from discussing the property to running it); and, as shorter notes, the Temporal, DBOS,
and Restate maintainers.

**From:** Mathew Stevens. **Nature:** defensive reliability research on public MIT/Apache code, run
in a local sandbox. The workflows crash processes the author controls; the only external side effect
is a local ledger the fixture owns. No system the author does not control was touched.

**Status:** the langgraph#8039 note was posted on 2026-08-28. This draft now includes the
2026-09-01 nondeterministic/two-phase follow-up. Temporal, DBOS, and Restate notes remain unsent.

## Summary

Durable-execution runtimes make the JOURNALED RESULT exactly-once, but the EXTERNAL side effect inside
an activity / step / node is at-least-once unless that unit is made idempotent. crashpoint enumerates
three crash barriers around one external effect, crashes the real runtime at each with an uncatchable
SIGKILL, and reads the true side-effect count from an out-of-process ledger the runtime cannot forge.
The result is a per-cell outcome matrix, calibrated by control/reference rows that pin
DUPLICATED / LOST / EXACTLY_ONCE / DIVERGED on demand.

The fix that recovers exactly-once - an idempotency key derived from what the action is - is measured
with its condition attached: it holds for a step that is reproducible from durable inputs. For a step
that draws semantic content during the call, a content-derived key diverges. A two-phase shape that
records the identity before the draw and carries it through the effect recovers EXACTLY_ONCE in the
measured control, LangGraph, Temporal, and DBOS rows.

## For langgraph#8039

The thread establishes, by reading the code, that under `durability="sync"` `put_writes` and the
superseding `put` are dispatched to a shared executor with no ordering edge, so whether recovery
replays the pending writes or re-executes the node depends on a race, and differs across hosts. #8055,
the narrow ordering fix, was closed unmerged on 2026-06-12, so this is still treated as current
behavior here.

crashpoint measures the same property from outside the runtime and across four engines, and adds
four things rather than substituting for the existing thread work:

- **The side-effect count is observed, not self-reported.** The ledger is a separate process behind
  two Unix sockets. The subject holds an execute-only invoke socket and an opaque receipt - it cannot
  read, reset, or seal the count - and every record is hash-chained, so an edited record makes the
  oracle emit VOID instead of a number.
- **The receiver control is a measured row, not an assertion.** The `*_naive` and `*_idem` rows run
  against the same crash barriers. The identity alone duplicates; the identity plus a receiver that
  honors it does not, for reproducible effects.
- **The nondeterministic conditional is measured.** The `*_idem` / `*_nondet` pairs differ in one
  declared property: whether the effect is reproducible from durable inputs. At b1, the
  content-derived key goes from EXACTLY_ONCE to DIVERGED.
- **The two-phase candidate is crashed.** The `*_twophase` rows prepare the effect identity before the
  nondeterministic draw. At b1, replay redraws content but reuses the durable identity, so the ledger
  dedups the second attempt and the row reads EXACTLY_ONCE.

Reproduce the current LangGraph evidence:

```
uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem,r_lg_nondet,r_lg_twophase --name langgraph
```

The current receipt is
`cp1_bffad8a0407e66ad6f2dc5ad4a3f39a96d7528ce4e4388ec6be1f9059be13073`.

## Nondeterminism and Two-Phase

The model-call concern is not that a UUID is a model. The measured property is that the value used to
form the action is not reproducible from durable inputs. A UUID draw is the cheap deterministic
property control. A narrow real-sampler arm was also measured through
`CRASHPOINT_NONDET_SOURCE=model` and `CRASHPOINT_MODEL_SAMPLER_CMD`: Anthropic Haiku 4.5 on the
LangGraph nondeterministic/two-phase rows at k=5.

Current b1 table:

| runtime | content-derived deterministic (`*_idem`) | content-derived nondeterministic (`*_nondet`) | identity-before-draw (`*_twophase`) |
|---|---|---|---|
| LangGraph 1.2.11 | EXACTLY_ONCE | **DIVERGED** | EXACTLY_ONCE |
| Temporal | EXACTLY_ONCE | **DIVERGED** | EXACTLY_ONCE |
| DBOS | EXACTLY_ONCE | **DIVERGED** | EXACTLY_ONCE |
| Restate | EXACTLY_ONCE | **DIVERGED** | EXACTLY_ONCE |

The recomputability probe is the leading indicator:

```
uv run python -m crashpoint.harness.recomputability
```

It reports RECOMPUTABLE for deterministic/content-derived identity, NOT_RECOMPUTABLE for
nondeterministic/content-derived identity, and RECOMPUTABLE for the two-phase prepared identity.

## For Temporal, DBOS, and Restate

The same fixture, unchanged except for the adapter, reproduces the same b1 contrast on three
additional runtimes:

- **Temporal** - activities are at-least-once by design. A worker SIGKILLed after the effect but
  before the activity reports completion is retried, and the naive effect DUPLICATES at b1; an
  idempotency-key boundary recovers EXACTLY_ONCE for reproducible effects; a content-derived
  nondeterministic effect DIVERGES; a prepared identity recovers EXACTLY_ONCE.
- **DBOS** - steps checkpoint their output in Postgres. A step SIGKILLed after the effect but before
  its output commits is re-run on recovery, with the same observed outcomes.
- **Restate** - the Python adapter runs the effect inside `ctx.run_typed`. Killing the ASGI worker
  after the effect but before the durable operation result reaches Restate causes the operation to be
  retried. The measured k=10 row has the same naive/idempotent/nondeterministic/two-phase contrast.

These are not defect reports where the runtimes document at-least-once/idempotency responsibilities.
The note is that the gap between "exactly-once workflow" and "exactly-once external effect" is easy
to miss, and a crash-tested fixture makes it concrete.

## Limits

- Strong UID isolation is Linux-only. The direct macOS command reports BLOCKED, while the Dockerized
  Linux `setpriv --reuid nobody` adversary passed and is receipted in `evidence/isolation_linux.json`.
  That proves execute-only access for the UID-dropped subject in that Linux container, not a full
  container escape audit.
- Hidden framework crash points beyond b0/b1/b2 are mostly inventoried, not measured. The one current
  exception is LangGraph `lg_pre_first_checkpoint`, measured separately at k=50 as LOST in
  `evidence/langgraph_hidden.json`.
- Vercel Workflow is not implemented. Current Vercel docs include JS/TS and Python Workflow support,
  but this repo has not validated a faithful worker/backend crash harness before any row can be
  modeled or measured.
- Real model-sampler evidence is narrow. `evidence/langgraph_model.json` covers Anthropic Haiku 4.5
  on the LangGraph nondeterministic/two-phase rows at k=5; it is not a broad model/provider claim.

## What we are offering

- The fixture, the out-of-process ledger, the control calibration, and the receipts, runnable from
  `uv run`, so the check can be re-run against any future LangGraph release.
- The naive-vs-idempotent-vs-nondeterministic-vs-two-phase contrast as a ready-made regression test
  for whichever ordering guarantee #8039 lands on.
- Credit in the writeup to everyone named above. The property is the thread's, not mine:
  @vasilisnasopoulos stated it, @safal207 first made it executable, and this is the same check run
  against an oracle the runtime cannot write and two runtimes beyond LangGraph.
