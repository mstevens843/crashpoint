# Results

Verification record. Every number here was produced by running the command shown, on the toolchain
named below, and reading its output - the out-of-process ledger the crashed process could not forge.
Nothing is copied from an earlier run or a summary of a run. Where something is skipped,
substrate-limited, or unproven, it says so and says why.

`uv.lock` is intentionally tracked; use `uv sync --group dev --all-extras --locked` before
reproducing these checks so the dependency graph matches the recorded evidence. The full local gate
installs optional runtime packages for type checking, but Temporal and DBOS still require their
servers only for the explicit matrix runs below.

**Frozen:** 2026-09-01
**Toolchain:** Python 3.12.13, uv 0.11.26, pytest, ruff, mypy (strict) via `uv run`.
**Runtimes:** LangGraph 1.2.11 (+ langgraph-checkpoint-sqlite 3.1.1); Temporal CLI 1.8.2 /
server 1.31.2 with temporalio 1.32.0 (local `start-dev`); DBOS 2.31.0 with Postgres 16
(Docker, on 5433).
**Ground truth:** the distinct side-effect count recorded by a separate ledger process the runtime
cannot read, reset, or forge - never the runtime's own report.

## Summary

| Gate | Result |
|---|---|
| Model + purity + ledger + oracle + determinism + discrimination tests (`uv run pytest`) | **120 passed** |
| ruff / mypy --strict | clean |
| Predicted outcome matrix (`python -m crashpoint.model`) | 18 runtime rows x 3 barriers, pure derivation, purity-checked |
| Reflexive adversary (`python -m crashpoint.adversaries.reflexive`) | ledger out of reach (execute-only, opaque receipt) AND tamper-evident (edit -> VOID) |
| Linux isolation adversary (`python -m crashpoint.adversaries.isolation`) | implemented; BLOCKED on this macOS host because `setpriv` UID isolation is Linux-only |
| Recomputability probe (`python -m crashpoint.harness.recomputability`) | deterministic/content-derived is RECOMPUTABLE, nondeterministic/content-derived is NOT_RECOMPUTABLE, two-phase prepared identity is RECOMPUTABLE |
| Controls, k=100 (1,800 trials) | DUPLICATED / LOST / EXACTLY_ONCE / **DIVERGED** pinned; two-phase recovers; **0 disagreements** |
| LangGraph #8039, k=50 (600 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**, two-phase b1 **EXACTLY_ONCE**; **0 disagreements** |
| Temporal, k=30 (360 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**, two-phase b1 **EXACTLY_ONCE**; **0 disagreements** |
| DBOS, k=30 (360 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**, two-phase b1 **EXACTLY_ONCE**; **0 disagreements** |
| Hidden-barrier inventory (`python -m crashpoint.harness.barrier_inventory`) | unmodeled internal candidates are named and kept disjoint from b0/b1/b2 |
| Deferred runtime inventory (`python -m crashpoint.harness.deferred_runtimes`) | Restate and Vercel Workflow remain unimplemented with precise blockers |

3,120 crash+recover trials in all; every observed cell equals a prediction written before any
runtime was crashed, and every cell sits at rate 1.0. The two-phase rows were modeled before the
adapters were measured and do not change the earlier claim: content-derived idempotency only works
when the effect is reproducible from durable inputs.

## The headline, stated once

Durable-execution runtimes make the journaled result exactly-once, but the external side effect inside
an activity / step / node is at-least-once unless that unit is made idempotent. Crash a naive effect
after it has crossed but before the runtime has durably recorded the step, and recovery re-runs the
step and the effect crosses twice. This reproduces on three runtimes by three different mechanisms - a
put/put_writes checkpoint race (LangGraph #8039), an activity retry after a worker timeout (Temporal),
and a step re-run on recovery (DBOS).

The standard mitigation has a measured condition. A content-derived idempotency key recovers
EXACTLY_ONCE only if replaying the step reproduces the same effect. For a step whose effect depends
on a value produced DURING the step - a model call being the motivating case - replay derives a
different key, the dedup silently stops applying, and the two crossings are different actions:
DIVERGED. If the identity is durably prepared before the nondeterministic draw and carried through
the effect, the measured two-phase rows recover EXACTLY_ONCE at b1 in the control, LangGraph,
Temporal, and DBOS adapters. That is a measured pattern in this fixture, not a claim that the
runtimes provide it automatically.

## The matrix

Predicted (P) equals observed (O) in every cell:

    runtime                before_effect           between     after_persist
    ------------------------------------------------------------------------
    null_baseline              ONCE/ONCE           DUP/DUP           DUP/DUP     control, k=100
    dup_control                ONCE/ONCE           DUP/DUP         ONCE/ONCE     control, k=100
    lost_control               ONCE/ONCE         LOST/LOST         ONCE/ONCE     control, k=100
    idem_reference             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     control, k=100
    diverge_control            ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE     control, k=100
    two_phase_reference        ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     control, k=100
    langgraph_naive            ONCE/ONCE           DUP/DUP         ONCE/ONCE     #8039,   k=50
    langgraph_idem             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     idem,    k=50
    langgraph_nondet           ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE     nondet,  k=50
    langgraph_twophase         ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     2phase,  k=50
    temporal_naive             ONCE/ONCE           DUP/DUP         ONCE/ONCE     k=30
    temporal_idem              ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=30
    temporal_nondet            ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE     k=30
    temporal_twophase          ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=30
    dbos_naive                 ONCE/ONCE           DUP/DUP         ONCE/ONCE     k=30
    dbos_idem                  ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=30
    dbos_nondet                ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE     k=30
    dbos_twophase              ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=30

Every measured cell sits at rate 1.0. Wilson 95% lower bound: 0.963 at k=100, 0.929 at k=50,
0.886 at k=30. The b1 column is the finding: naive durable units duplicate, content-derived
idempotency fixes only reproducible effects, nondeterministic content-derived effects diverge, and
pre-call identity fixes that failure in the measured two-phase rows.

## Evidence Receipts

| Evidence | Command | Receipt |
|---|---|---|
| `evidence/controls.json` | `uv run python -m crashpoint.harness.matrix --k 100 --runtimes r_null,r_dup,r_lost,r_idem,r_diverge,r_twophase --name controls` | `cp1_667ba86d7791b962cbf291518d38c0339307e4ca6b0c81a50332f077fbd20edc` |
| `evidence/langgraph.json` | `uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem,r_lg_nondet,r_lg_twophase --name langgraph` | `cp1_bffad8a0407e66ad6f2dc5ad4a3f39a96d7528ce4e4388ec6be1f9059be13073` |
| `evidence/temporal.json` | `uv run --extra temporal python -m crashpoint.harness.matrix --k 30 --runtimes r_tmp_naive,r_tmp_idem,r_tmp_nondet,r_tmp_twophase --name temporal` | `cp1_6a057f0779fb2b98c60fe71c4b8e8111328a5385ff8069b1598f4d7fa50728a5` |
| `evidence/dbos.json` | `uv run --extra dbos python -m crashpoint.harness.matrix --k 30 --runtimes r_dbos_naive,r_dbos_idem,r_dbos_nondet,r_dbos_twophase --name dbos` | `cp1_0793cb0b8ad9925a9ae547057b707355dc420ac763aec94ce1f7dd0f2334c220` |

`tests/test_discrimination.py` re-derives each receipt from the JSON body and checks that no present
evidence cell disagrees with the model.

## Packaging

`uv build` succeeds. The wheel is code-only (`src/crashpoint`), while the sdist explicitly includes
`tests/`, `evidence/`, `results/`, top-level docs, repro scripts, and `uv.lock`. That keeps installed
packages small while preserving the release artifact needed to reproduce the research evidence.

## Reproduce

    uv sync --group dev --all-extras --locked
    uv run pytest
    uv run ruff check .
    uv run mypy
    uv run python -m crashpoint.model
    uv run python -m crashpoint.adversaries.reflexive
    uv run python -m crashpoint.adversaries.isolation
    uv run python -m crashpoint.harness.recomputability
    uv run python -m crashpoint.harness.barrier_inventory
    uv run python -m crashpoint.harness.deferred_runtimes
    uv run python -m crashpoint.harness.matrix --k 100 --runtimes r_null,r_dup,r_lost,r_idem,r_diverge,r_twophase --name controls
    uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem,r_lg_nondet,r_lg_twophase --name langgraph

The real-server columns need their substrate up first:

    # Temporal: a local dev server that survives worker SIGKILL
    temporal server start-dev --headless --ip 127.0.0.1 --port 7233 --db-filename /tmp/crashpoint-temporal.db
    uv run --extra temporal python -m crashpoint.harness.matrix --k 30 --runtimes r_tmp_naive,r_tmp_idem,r_tmp_nondet,r_tmp_twophase --name temporal

    # DBOS: a Postgres system database on the adapter's default port
    docker run -d --name cp-postgres -e POSTGRES_USER=cpuser -e POSTGRES_PASSWORD=dbos -e POSTGRES_DB=cpdbos -p 5433:5432 postgres:16
    export CRASHPOINT_DBOS_URL=postgresql://cpuser:dbos@localhost:5433/cpdbos
    uv run --extra dbos python -m crashpoint.harness.matrix --k 30 --runtimes r_dbos_naive,r_dbos_idem,r_dbos_nondet,r_dbos_twophase --name dbos

If `cp-postgres` already exists, use `docker start cp-postgres` instead of creating a second
container.

## Optional Real-Model Arm

The default nondeterministic source is `uuid`, because the property under test is irreproducibility
from durable inputs. A real sampler can be injected without hardcoding secrets:

    export CRASHPOINT_NONDET_SOURCE=model
    export CRASHPOINT_MODEL_SAMPLER_CMD='your-sampler-command'
    export CRASHPOINT_MODEL_SAMPLER_TIMEOUT=30

The command receives the prompt on stdin and must write the sampled memo on stdout. Evidence should
only be checked in after a real configured sampler run. This repository currently contains no
model-backed evidence.

## What this does not prove

- **Strong isolation on macOS.** The default proof is the socket-privilege boundary. The Linux
  `setpriv --reuid nobody` proof is implemented, but this macOS run reports BLOCKED. A PASS from that
  adversary proves execute-only access for a UID-dropped subject on that Linux host; it is not a full
  container escape audit.
- **All hidden framework crash points.** The matrix measures b0/b1/b2. `barrier_inventory.py` names
  additional candidates around LangGraph pending writes, Temporal activity scheduling/replay, and
  DBOS step/status commits, but those are not evidence until each receives a model rule and a measured
  adapter barrier.
- **A real model sampler.** The optional command interface exists, but no real sampler was configured
  for this evidence set. Do not cite these UUID-backed nondeterministic rows as model-provider
  measurements.
- **Restate or Vercel Workflow.** They remain unimplemented. Restate needs a validated local
  service-journal adapter. Vercel Workflow needs a faithful Node worker/development-server harness.
- **A runtime defect where the runtime documents at-least-once behavior.** Temporal and DBOS are not
  defect reports. They demonstrate the gap between exactly-once workflow language and external
  side-effect behavior under the documented contracts.
- **Large-k statistical claims on real servers.** Temporal and DBOS ran at k=30, so the Wilson lower
  bound is 0.886. The observed cells are deterministic here; larger k would tighten the interval, not
  change the measured claim.
