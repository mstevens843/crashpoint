# Results

Verification record. Every number here was produced by running the command shown, on the toolchain
named below, and reading its output - the out-of-process ledger the crashed process could not forge.
Nothing is copied from an earlier run or a summary of a run. Where something is skipped,
substrate-limited, or unproven, it says so and says why.

**Frozen:** 2026-08-27
**Toolchain:** Python 3.12.13, uv 0.11.26, pytest, ruff, mypy (strict) via `uv run`.
**Runtimes:** LangGraph 1.2.11 (+ langgraph-checkpoint-sqlite 3.1.1); Temporal server 1.31.2 with
temporalio 1.32.0 (local `start-dev`, in-memory); DBOS 2.31.0 with Postgres 16 (Docker, on 5433).
**Ground truth:** the distinct side-effect count recorded by a separate ledger process the runtime
cannot read, reset, or forge - never the runtime's own report.

## Summary

| Gate | Result |
|---|---|
| Model + purity + ledger + oracle + discrimination tests (`uv run pytest`) | **68 passed** |
| ruff / mypy --strict | clean |
| Predicted outcome matrix (`python -m crashpoint.model`) | 10 runtimes x 3 barriers, pure derivation, purity-checked |
| Reflexive adversary (`python -m crashpoint.adversaries.reflexive`) | ledger out of reach (execute-only, opaque receipt) AND tamper-evident (edit -> VOID) |
| Controls, k=100 (240 trials) | DUPLICATED / LOST / EXACTLY_ONCE pinned; **0 disagreements** |
| LangGraph #8039, k=50 (150 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**; **0 disagreements** |
| Temporal, k=30 (90 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**; **0 disagreements** |
| DBOS, k=30 (90 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**; **0 disagreements** |

570 crash+recover trials in all; every observed cell equals a prediction written before any runtime
was crashed.

## The headline, stated once

Durable-execution runtimes make the journaled result exactly-once, but the external side effect inside
an activity / step / node is at-least-once unless that unit is made idempotent. Crash a naive effect
after it has crossed but before the runtime has durably recorded the step, and recovery re-runs the
step and the effect crosses twice. This reproduces on three runtimes by three different mechanisms - a
put/put_writes checkpoint race (LangGraph #8039), an activity retry after a worker timeout (Temporal),
and a step re-run on recovery (DBOS) - and in every case an idempotent dedup-by-key boundary is the
only thing that recovers exactly-once. The controls pin all three outcomes on demand, so the oracle is
proven to discriminate; the ledger is a separate process the runtime cannot reach, so the count is
observed, not self-reported.

## The matrix (`uv run python -m crashpoint.model`, then the four harness runs)

Predicted (P) equals observed (O) in every cell:

    runtime                before_effect           between     after_persist
    ------------------------------------------------------------------------
    null_baseline              ONCE/ONCE           DUP/DUP           DUP/DUP     control, k=100
    dup_control                ONCE/ONCE           DUP/DUP         ONCE/ONCE     control, k=100
    lost_control               ONCE/ONCE         LOST/LOST         ONCE/ONCE     control, k=100
    idem_reference             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     control, k=100
    langgraph_naive            ONCE/ONCE           DUP/DUP         ONCE/ONCE     #8039,   k=50
    langgraph_idem             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     fix,     k=50
    temporal_naive             ONCE/ONCE           DUP/DUP         ONCE/ONCE     k=30
    temporal_idem              ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=30
    dbos_naive                 ONCE/ONCE           DUP/DUP         ONCE/ONCE     k=30
    dbos_idem                  ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=30

Every measured cell sits at rate 1.0. Wilson 95% lower bound: 0.963 at k=100, 0.929 at k=50, 0.886
at k=30. The b1 (between) column is the finding: every naive durable runtime DUPLICATES, the
idempotent boundary is the only thing that recovers EXACTLY_ONCE.

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
- **The oracle discriminates.** Three control adapters pin DUPLICATED / LOST / EXACTLY_ONCE over 240
  trials with zero disagreements, so a real runtime reading EXACTLY_ONCE means something.
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
    uv run python -m crashpoint.harness.matrix --k 100 --runtimes r_null,r_dup,r_lost,r_idem --name controls
    uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem --name langgraph

The real-server columns need their substrate up first:

    # Temporal: a local dev server (survives the worker SIGKILL)
    temporal server start-dev --headless &
    uv run --extra temporal python -m crashpoint.harness.matrix --k 30 --runtimes r_tmp_naive,r_tmp_idem --name temporal

    # DBOS: a Postgres for the system database (5432 was taken here, so 5433)
    docker run -d --name cp-postgres -e POSTGRES_USER=cpuser -e POSTGRES_PASSWORD=dbos -e POSTGRES_DB=cpdbos -p 5433:5432 postgres:16
    export CRASHPOINT_DBOS_URL=postgresql://cpuser:dbos@localhost:5433/cpdbos
    uv run --extra dbos python -m crashpoint.harness.matrix --k 30 --runtimes r_dbos_naive,r_dbos_idem --name dbos

## What this does not prove

- **True uid-separation on macOS.** The ledger is a separate process the subject cannot reach, and the
  invoke socket exposes only `execute`; but the stronger `setpriv --reuid nobody` uid-drop and full
  container isolation are a documented Linux stretch, not the default here. The out-of-reach claim is
  the socket-privilege boundary, not an OS sandbox.
- **The b1 barrier is uncloseable without idempotency.** No naive effect closes it on any runtime -
  that is the finding, named as the floor, not a gap to be fixed by a runtime setting.
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
