# Results

Verification record. Every number here was produced by running the command shown, on the toolchain
named below, and reading its output - the out-of-process ledger the crashed process could not forge.
Nothing is copied from an earlier run or a summary of a run. Where something is skipped,
substrate-limited, or unproven, it says so and says why.

**Frozen:** 2026-08-28
**Toolchain:** Python 3.12.13, uv 0.11.26, pytest, ruff, mypy (strict) via `uv run`.
**Runtimes:** LangGraph 1.2.11 (+ langgraph-checkpoint-sqlite 3.1.1); Temporal server 1.31.2 with
temporalio 1.32.0 (local `start-dev`, in-memory); DBOS 2.31.0 with Postgres 16 (Docker, on 5433).
**Ground truth:** the distinct side-effect count recorded by a separate ledger process the runtime
cannot read, reset, or forge - never the runtime's own report.

## Summary

| Gate | Result |
|---|---|
| Model + purity + ledger + oracle + determinism + discrimination tests (`uv run pytest`) | **93 passed** |
| ruff / mypy --strict | clean |
| Predicted outcome matrix (`python -m crashpoint.model`) | 14 runtimes x 3 barriers, pure derivation, purity-checked |
| Reflexive adversary (`python -m crashpoint.adversaries.reflexive`) | ledger out of reach (execute-only, opaque receipt) AND tamper-evident (edit -> VOID) |
| Recomputability probe (`python -m crashpoint.harness.recomputability`) | the identity is RECOMPUTABLE for a deterministic step and NOT_RECOMPUTABLE for a nondeterministic one, decided before any crash |
| Controls, k=100 (1,500 trials) | DUPLICATED / LOST / EXACTLY_ONCE / **DIVERGED** pinned; **0 disagreements** |
| LangGraph #8039, k=50 (450 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**; **0 disagreements** |
| Temporal, k=30 (270 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**; **0 disagreements** |
| DBOS, k=30 (270 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**; **0 disagreements** |

2,490 crash+recover trials in all; every observed cell equals a prediction written before any
runtime was crashed, and every cell sits at rate 1.0. The nondeterministic rows were predicted and
rendered before their adapters, the oracle's DIVERGED branch, and the probe were written.

> **Correction (2026-08-28).** Earlier versions of this file reported 240 / 150 / 90 / 90 trials and
> a 570 total. Those were undercounts: the harness runs k trials per CELL, and a run has
> (runtimes x barriers) cells, so the counts should have been 1,200 / 300 / 180 / 180 = 1,860 for
> that evidence. The numbers above are recomputed directly from `len(cells) x k` in each evidence
> file. No outcome, rate, or disagreement count was affected - only the trial arithmetic was wrong.

## The headline, stated once

Durable-execution runtimes make the journaled result exactly-once, but the external side effect inside
an activity / step / node is at-least-once unless that unit is made idempotent. Crash a naive effect
after it has crossed but before the runtime has durably recorded the step, and recovery re-runs the
step and the effect crosses twice. This reproduces on three runtimes by three different mechanisms - a
put/put_writes checkpoint race (LangGraph #8039), an activity retry after a worker timeout (Temporal),
and a step re-run on recovery (DBOS) - and in every case an idempotent dedup-by-key boundary is the
only thing that recovers exactly-once. The controls pin all four outcomes on demand, so the oracle is
proven to discriminate; the ledger is a separate process the runtime cannot reach, so the count is
observed, not self-reported.

**And that fix has a condition, which is measured here rather than assumed.** A content-derived
idempotency key only survives a crash if replaying the step reproduces the same effect. For a step
whose effect depends on a value produced DURING the step - a model call being the motivating case -
it does not: the re-run derives a different key, the dedup silently stops applying, and the two
crossings are not even the same action. That is a fifth outcome, DIVERGED, and it is what all three
runtimes do at b1 once the step stops being reproducible. So the honest statement of the fix is
"an idempotent boundary recovers exactly-once for a step that is reproducible from its durable
inputs", and the second floor is that nothing in any of these runtimes closes b1 for a step that is
not. Full record in [results/06](./results/06-nondeterminism.md).

## The matrix (`uv run python -m crashpoint.model`, then the four harness runs)

Predicted (P) equals observed (O) in every cell:

    runtime                before_effect           between     after_persist
    ------------------------------------------------------------------------
    null_baseline              ONCE/ONCE           DUP/DUP           DUP/DUP     control, k=100
    dup_control                ONCE/ONCE           DUP/DUP         ONCE/ONCE     control, k=100
    lost_control               ONCE/ONCE         LOST/LOST         ONCE/ONCE     control, k=100
    idem_reference             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     control, k=100
    diverge_control            ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE     control, k=100
    langgraph_naive            ONCE/ONCE           DUP/DUP         ONCE/ONCE     #8039,   k=50
    langgraph_idem             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     fix,     k=50
    langgraph_nondet           ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE     cond.,   k=50
    temporal_naive             ONCE/ONCE           DUP/DUP         ONCE/ONCE     k=30
    temporal_idem              ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=30
    temporal_nondet            ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE     k=30
    dbos_naive                 ONCE/ONCE           DUP/DUP         ONCE/ONCE     k=30
    dbos_idem                  ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=30
    dbos_nondet                ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE     k=30

Every measured cell sits at rate 1.0. Wilson 95% lower bound: 0.963 at k=100, 0.929 at k=50, 0.886
at k=30. The b1 (between) column is the finding: every naive durable runtime DUPLICATES; the
idempotent boundary is the only thing that recovers EXACTLY_ONCE; and it recovers nothing once the
step stops being reproducible from its durable inputs, which is the `*_nondet` row on each engine.
Read the three idem/nondet pairs as pairs - one declared property changes between them.

## What each claim rests on

- **The model is a pure derivation, purity-checked.** `tests/test_contract.py` parses the model
  modules with `ast` and fails on a clock, a random draw, a filesystem, or a network call. Zero
  third-party dependencies in the model; it was written and rendered before any runtime was crashed.
- **The ledger is out of process and the count cannot be forged.** A separate daemon behind two Unix
  sockets records every attempt and counts distinct side effects; the subject holds only an
  execute-only invoke socket and an opaque, constant receipt. The reflexive adversary shows all three
  control verbs refused on the invoke socket and the harness reading the true count on the control
  socket.
- **Tampering fails closed.** Every ledger record is hash-chained; editing one breaks the chain, and
  the oracle emits VOID rather than the DUPLICATED the untampered record read. Demonstrated, not
  asserted.
- **The oracle discriminates.** Four control adapters pin DUPLICATED / LOST / EXACTLY_ONCE /
  DIVERGED over 1,500 trials with zero disagreements, so a real runtime reading EXACTLY_ONCE means
  something - and so does a real runtime reading DIVERGED.
- **DUPLICATED and DIVERGED are told apart by observation, not inference.** The ledger digests each
  distinct crossing's payload and keeps the digests per intent, so "the same charge twice" and "two
  different charges" are different readings of different facts. Where the digests are missing the
  oracle returns VOID rather than guessing between them - fail-closed, as everywhere else.
- **The reviewer's own test is implemented, not quoted.** `harness/recomputability.py` asks whether a
  process that did not run the step can recompute the effect's identity from the durable inputs
  alone. It decides both cases with no crash and no trials, and NOT_RECOMPUTABLE predicted DIVERGED
  at b1. It also shows that the existing forbidden-identity-field guard ACCEPTS the payload that
  breaks the dedup: passing that guard says nothing about recomputability.
- **The model disciplined the harness, in the open.** When `langgraph_idem` first read LOST against
  the model's EXACTLY_ONCE, the contradiction was treated as a bug to find - and it was a harness
  argv-dispatch collision, not a model error (`results/03`). A second correction refined b0 and
  surfaced a real secondary failure mode (a crash before the first checkpoint drops the run). Both are
  recorded, not smoothed over.
- **Every observed run is receipted.** `evidence/{controls,langgraph,temporal,dbos}.json` each carry a
  canonical-JSON SHA-256 receipt, and `tests/test_discrimination.py` re-derives each from its body, so
  a hand-edited number fails the suite.

## Reproduce

    uv sync --group dev
    uv run pytest && uv run ruff check . && uv run mypy
    uv run python -m crashpoint.model
    uv run python -m crashpoint.adversaries.reflexive
    uv run python -m crashpoint.harness.recomputability
    uv run python -m crashpoint.harness.matrix --k 100 --runtimes r_null,r_dup,r_lost,r_idem,r_diverge --name controls
    uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem,r_lg_nondet --name langgraph

The real-server columns need their substrate up first:

    # Temporal: a local dev server (survives the worker SIGKILL)
    temporal server start-dev --headless &
    uv run --extra temporal python -m crashpoint.harness.matrix --k 30 --runtimes r_tmp_naive,r_tmp_idem,r_tmp_nondet --name temporal

    # DBOS: a Postgres for the system database (5432 was taken here, so 5433)
    docker run -d --name cp-postgres -e POSTGRES_USER=cpuser -e POSTGRES_PASSWORD=dbos -e POSTGRES_DB=cpdbos -p 5433:5432 postgres:16
    export CRASHPOINT_DBOS_URL=postgresql://cpuser:dbos@localhost:5433/cpdbos
    uv run --extra dbos python -m crashpoint.harness.matrix --k 30 --runtimes r_dbos_naive,r_dbos_idem,r_dbos_nondet --name dbos

## What this does not prove

- **True uid-separation on macOS.** The ledger is a separate process the subject cannot reach, and the
  invoke socket exposes only `execute`; but the stronger `setpriv --reuid nobody` uid-drop and full
  container isolation are a documented Linux stretch, not the default here. The out-of-reach claim is
  the socket-privilege boundary, not an OS sandbox.
- **The b1 barrier is uncloseable without idempotency.** No naive effect closes it on any runtime -
  that is the finding, named as the floor, not a gap to be fixed by a runtime setting. And a second
  floor sits under it: no idempotent boundary closes b1 either once the step stops being
  reproducible from its durable inputs. Neither floor is a gap this project intends to fix.
- **The nondeterministic arm draws a value; it does not call a model.** The drawn value has the
  property that matters - not reproducible from the durable inputs - and isolates it cleanly, which
  a real sampler would not. But nothing here measures how a real model behaves under temperature,
  seeding, or provider-side caching. The claim is about irreproducibility and should be read as
  exactly that.
- **DIVERGED is not shown to be unsolvable.** It is shown that none of these three runtimes solves
  it and that the standard mitigation does not. A two-phase shape - durably record an intent and its
  identity before the nondeterministic call, then carry that identity through the effect - is the
  obvious candidate and is neither implemented nor crashed here, so it is not claimed.
- **Not every internal barrier.** The crash is placed at the adapter's enumerated barriers (named
  precisely), not at every hidden persistence point inside each framework. A framework could have
  other barriers with other outcomes; this measures the three that bracket the external effect.
- **The #8039 race is enumerated, not raced.** The harness forces b1 and b2 deterministically to show
  both outcomes of the race; it does not reproduce the host-dependent timing itself (that would be the
  timed-crash approach this project deliberately avoids). The claim is "at b1 the effect duplicates,"
  plus "the race decides, per production crash, whether you land at b1 or b2."
- **k is modest on the real servers.** Temporal and DBOS ran at k=30 (wall-clock cost per trial is
  seconds), so the Wilson floor is 0.886. The cells are deterministic here; a larger k would only
  tighten an interval that already excludes the alternative outcomes.
