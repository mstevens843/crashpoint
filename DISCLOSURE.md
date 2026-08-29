# Disclosure draft - the exactly-once external-effect fixture for durable-execution runtimes

**To:** the LangGraph maintainers and the participants of `langchain-ai/langgraph#8039` (in
particular @sajjadanwar0, who opened it; @vasilisnasopoulos, whose RS1-RS3 write-up named the receiver
half of the property and asked for it to be made empirical; @safal207, who built and published the
first executable recovery-safety benchmark for this issue; and @tamilov, who pushed the thread from
discussing the property to running it); and, as shorter notes, the Temporal and DBOS maintainers.
**From:** Mathew Stevens. **Nature:** defensive reliability research on public MIT/Apache code, run in
a local sandbox. The workflows crash processes the author controls; the only external side effect is a
local ledger the fixture owns. No system the author does not control was touched.
**Status:** the langgraph#8039 note was posted on 2026-08-28. The section below on the
nondeterministic conditional is the drafted follow-up, answering the reply it drew; the Temporal and
DBOS notes remain unsent.

## Summary

Durable-execution runtimes make the JOURNALED RESULT exactly-once, but the EXTERNAL side effect inside
an activity / step / node is at-least-once unless that unit is made idempotent. crashpoint enumerates
three crash barriers around one external effect, crashes the real runtime at each with an uncatchable
SIGKILL, and reads the true side-effect count from an out-of-process ledger the runtime cannot forge.
The result is a per-cell outcome matrix, calibrated by four control adapters that pin
DUPLICATED / LOST / EXACTLY_ONCE / DIVERGED on demand.

The fix that recovers exactly-once - an idempotency key derived from what the action is - is measured
here with its condition attached: it holds for a step that is reproducible from its durable inputs,
and does nothing for a step that is not.

## For langgraph#8039

The thread establishes, by reading the code, that under `durability="sync"` `put_writes` and the
superseding `put` are dispatched to a shared executor with no ordering edge, so whether recovery
replays the pending writes or re-executes the node depends on a race, and differs across hosts. #8055,
the narrow ordering fix, was closed unmerged on 2026-06-12, so this is still current behavior; the
measurements below were taken on `langgraph 1.2.11`, the current release.

The thread already has an executable check for this issue: @safal207's
[`langgraph-recovery-safety-v0.1`](https://github.com/safal207/ContractGraph-QA/tree/main/benchmarks/langgraph-recovery-safety-v0.1)
(merged 2026-08-27), adapting @vasilisnasopoulos's
[RS1-RS3](https://github.com/vasilisnasopoulos/recovery-safety-property). This is not a second copy of
it. crashpoint measures the same property from outside the runtime and across three engines, and adds
three things to that work rather than substituting for it:

- **The side-effect count is observed, not self-reported.** The ledger is a separate process behind two
  Unix sockets. The subject holds an execute-only invoke socket and an opaque receipt - it cannot read,
  reset, or seal the count - and every record is hash-chained, so an edited record makes the oracle emit
  VOID instead of a number. The answer to "did the effect cross twice" comes from something the crashed
  process could not have written, which is the form of the answer a vendor rebuttal does not reach.
- **The receiver control is a measured row, not an assertion.** @vasilisnasopoulos asked for exactly
  this on 2026-08-27: running append and dedup receivers "against the same crash makes the boundary
  empirical instead of asserted." The `langgraph_naive` and `langgraph_idem` rows are that pair, at all
  three barriers, k=50 - the identity alone duplicates, the identity plus a receiver that honours it
  does not.
- **The oracle is calibrated before it is trusted.** Three control adapters pin DUPLICATED / LOST /
  EXACTLY_ONCE on demand over 240 trials with zero disagreements, so a real runtime reading
  EXACTLY_ONCE means the harness discriminates rather than painting every cell green.

### What it measures

- The adapter is a one-node durable graph whose node performs the external effect, with a
  `RacingSaver(SqliteSaver)` that self-SIGKILLs at an enumerated barrier, run under `durability="sync"`
  (`src/crashpoint/adapters/langgraph_adapter.py`). Enumerating the barrier removes the host race and
  shows both of its outcomes deterministically:
  - **b1** (crash in `put_writes`, after the effect, before the pending writes persist): recovery
    re-runs the node, and the naive external effect crosses **twice - DUPLICATED**, 50/50 trials.
  - **b2** (crash in the superseding `put`, after the completion is durable): recovery skips the node,
    the effect crosses **once - EXACTLY_ONCE**.
  The #8039 race is precisely which of these a production crash lands on. On the duplicating half, the
  side effect is doubled.
- **b0** (crash after the entry checkpoint is durable, before the node): EXACTLY_ONCE. Separately
  measured and worth a maintainer's eye: a crash BEFORE the first checkpoint leaves recovery nothing
  to resume and the workflow is silently dropped (LOST) - a second, quieter failure mode.
- The fix is the `langgraph_idem` row: the same barriers, but an idempotent boundary (a dedup-by-key
  ledger) makes the external effect exactly-once at every barrier. This is what a naive node lacks.
- Reproduce:
  `uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem --name langgraph`.
  Every cell agrees with a model written before the runtime was crashed; the evidence carries a
  canonical-JSON SHA-256 receipt.

## The nondeterministic conditional (answering @vasilisnasopoulos, 2026-08-28)

The reply to the note above named the sharpest limit of it:

> An ordinal within the step closes that - but only under a condition worth stating: the step must be
> deterministic given its durable inputs. [...] A step that calls a model is not deterministic given
> its inputs, so replay does not reproduce its effects, and no key format rescues that [...] None of
> the three runtimes measured here addresses it. [...] The cheap test for the first case is whether a
> process that did not run the step can recompute the identity from the durable inputs alone.

That is correct, and it landed on a deliberate choice in the fixture: the workflow in the first
matrix is deterministic, so the `*_idem` rows measured the case that is already solvable. It has now
been measured, and the objection holds on all three runtimes.

- **No model was needed.** The property is irreproducibility, not model-ness. An effect payload
  carrying a value drawn during the step has exactly that property; a draw also diverges on every
  trial rather than only when a sampler happens to, so it isolates the property more cleanly than a
  real model would, at no cost. The nondeterministic field is a `memo` - ordinary semantic content,
  the memo line an agent would have a model write - so it is legitimately part of what the action IS
  and therefore legitimately part of a content-derived key. That is what makes it lethal rather than
  a strawman.
- **A fifth outcome was needed.** Counting crossings cannot separate "the same charge twice" from
  "two different charges", and they are different failures - the second is worse, because you cannot
  reconcile it by matching. The ledger now digests each distinct crossing's payload, so the oracle
  reads DUPLICATED vs DIVERGED off observed facts; where the digests are missing it returns VOID
  rather than guessing. A fourth control pins DIVERGED on demand, so the new outcome has the same
  teeth as the other three.
- **The result, on real engines** (k=50 LangGraph, k=30 Temporal and DBOS, zero disagreements with a
  model written and rendered before the adapters existed):

  | runtime | b1, deterministic (`*_idem`) | b1, nondeterministic (`*_nondet`) |
  |---|---|---|
  | LangGraph 1.2.11 | EXACTLY_ONCE | **DIVERGED** |
  | Temporal | EXACTLY_ONCE | **DIVERGED** |
  | DBOS | EXACTLY_ONCE | **DIVERGED** |

  Each pair differs in exactly one declared property. Same engine, same barrier, same idempotent
  boundary, same content-derived key.
- **The cheap test is implemented, not quoted.** `harness/recomputability.py` asks whether a process
  that did not run the step can recompute the identity from the durable inputs alone - literally, in
  the harness process, while the step ran in a subprocess that was SIGKILLed. It decides both cases
  with no crash and no trials, and NOT_RECOMPUTABLE predicted DIVERGED at b1. As a leading indicator
  it answers before you ship, where the matrix answers after.
- **One thing it turned up that was not in the thread.** The existing guard against the classic key
  bug - reject `attempt`, `retry`, `epoch`, `nonce`, a delivery id - ACCEPTS the payload that breaks
  the dedup here, because `memo` is not a per-attempt field. Passing that guard says nothing about
  recomputability. They are not the same check, and a codebase that has the first one may believe it
  is covered.
- **One correction offered carefully.** "None of the three runtimes addresses it" is right about the
  window that matters, and slightly understates Temporal's machinery: Temporal journals an activity's
  RESULT, which does restore determinism for the workflow on replay. What it does not restore is
  determinism across a RETRY of the activity itself, and the retry is what re-runs the draw - which
  is the window the external effect lives in. The measured cell is the retry window.

What is deliberately NOT claimed: that this is unsolvable. It is shown that none of these three
runtimes solves it and that the standard mitigation does not. A two-phase shape - durably record an
intent and its identity BEFORE the nondeterministic call, then carry that identity through the
effect - is the obvious candidate. It is not implemented or crashed here, so it is not offered as a
fix.

Reproduce:
`uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_idem,r_lg_nondet --name langgraph`
and `uv run python -m crashpoint.harness.recomputability`.

## For Temporal and DBOS (shorter notes)

The same fixture, unchanged except for the adapter, reproduces the same b1 contrast on two more
runtimes, confirming this is a property of durable execution and not of one library:

- **Temporal** - activities are at-least-once by design. A worker SIGKILLed after the effect but
  before the activity reports completion is retried, and the naive effect DUPLICATES at b1; an
  idempotency-key boundary recovers EXACTLY_ONCE. This is the documented contract ("A non-idempotent
  Activity could adversely affect the state"), demonstrated end to end with a crash.
- **DBOS** - steps checkpoint their output in Postgres. A step SIGKILLed after the effect but before
  its output commits is re-run on recovery, and the naive effect DUPLICATES at b1; the idempotent
  boundary recovers EXACTLY_ONCE. This is the documented "steps should be idempotent", demonstrated.

Neither is a defect report: both runtimes document at-least-once execution. The note is that the gap
between "exactly-once workflow" and "exactly-once external effect" is easy to miss, and a crash-tested
fixture makes it concrete.

## What we are offering

- The fixture, the out-of-process ledger, the control calibration, and the receipts, runnable from
  `uv run`, so the check can be re-run against any future LangGraph release to confirm a fix.
- The naive-vs-idempotent contrast as a ready-made regression test for whichever ordering guarantee
  #8039 lands on.
- Credit in the writeup to everyone named above. The property is the thread's, not mine:
  @vasilisnasopoulos stated it, @safal207 first made it executable, and this is the same check run
  against an oracle the runtime cannot write and two runtimes beyond LangGraph.
