# Results

Verification record. Every number here was produced by running the command shown, on the toolchain
named below, and reading its output - the out-of-process ledger the crashed process could not forge.
Nothing is copied from an earlier run or a summary of a run. Where something is skipped,
substrate-limited, or unproven, it says so and says why.

`uv.lock` is intentionally tracked; use `uv sync --group dev --all-extras --locked` before
reproducing these checks so the dependency graph matches the recorded evidence. The full local gate
installs optional runtime packages for type checking, but Temporal, DBOS, and Restate still require
their servers only for the explicit matrix runs below.

**Frozen:** 2026-09-01
**Toolchain:** Python 3.12.13, uv 0.11.26, pytest, ruff, mypy (strict) via `uv run`.
**Runtimes:** LangGraph 1.2.11 (+ langgraph-checkpoint-sqlite 3.1.1); Temporal CLI 1.8.2 /
server 1.31.2 with temporalio 1.32.0 (local `start-dev`); DBOS 2.31.0 with Postgres 16
(Docker, on 5433); Restate server/CLI 1.7.8 with restate-sdk 1.0.4 (Docker dev server plus Python
ASGI worker). Vercel Workflow was probed with workflow@5.0.0-beta.47 but not measured.
**Ground truth:** the distinct side-effect count recorded by a separate ledger process the runtime
cannot read, reset, or forge - never the runtime's own report.

## Summary

| Gate | Result |
|---|---|
| Model + purity + ledger + oracle + determinism + discrimination tests (`uv run pytest`) | pass |
| ruff / mypy --strict | clean |
| Predicted outcome matrix (`python -m crashpoint.model`) | 22 runtime rows x 3 barriers, pure derivation, purity-checked |
| Reflexive adversary (`python -m crashpoint.adversaries.reflexive`) | ledger out of reach (execute-only, opaque receipt) AND tamper-evident (edit -> VOID) |
| Linux isolation adversary (`python -m crashpoint.adversaries.isolation`) | direct macOS command BLOCKED; Dockerized Linux `setpriv` proof PASS in `evidence/isolation_linux.json` |
| Recomputability probe (`python -m crashpoint.harness.recomputability`) | deterministic/content-derived is RECOMPUTABLE, nondeterministic/content-derived is NOT_RECOMPUTABLE, two-phase prepared identity is RECOMPUTABLE |
| Controls, k=100 (1,800 trials) | DUPLICATED / LOST / EXACTLY_ONCE / **DIVERGED** pinned; two-phase recovers; **0 disagreements** |
| LangGraph #8039, k=50 (600 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**, two-phase b1 **EXACTLY_ONCE**; **0 disagreements** |
| Temporal, k=30 (360 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**, two-phase b1 **EXACTLY_ONCE**; **0 disagreements** |
| DBOS, k=30 (360 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**, two-phase b1 **EXACTLY_ONCE**; **0 disagreements** |
| Restate, k=10 (120 trials) | naive b1 **DUPLICATED**, idem b1 **EXACTLY_ONCE**, nondet b1 **DIVERGED**, two-phase b1 **EXACTLY_ONCE**; **0 disagreements** |
| Real model sampler, LangGraph k=5 (30 trials) | Anthropic Haiku 4.5: nondet b1 **DIVERGED**, two-phase b1 **EXACTLY_ONCE**; **0 disagreements** |
| LangGraph hidden barriers (`python -m crashpoint.harness.langgraph_hidden ...`) | `lg_pre_first_checkpoint` LOST at k=50; `lg_pending_writes_after_persist` EXACTLY_ONCE at k=50; **0 disagreements** |
| Hidden-barrier inventory (`python -m crashpoint.harness.barrier_inventory`) | two LangGraph candidates measured; remaining internal candidates named and kept disjoint from b0/b1/b2 |
| Deferred runtime inventory (`python -m crashpoint.harness.deferred_runtimes`) | Vercel Workflow fixture exists but remains unmeasured with a precise Local World blocker |

3,370 crash+recover trials in all: 3,240 in the default shared b0/b1/b2 matrices, 30 in the
real-model LangGraph submatrix, and 100 in the separate LangGraph hidden-barrier runs. Every observed
cell equals a prediction written before any runtime was crashed, and every cell sits at rate 1.0.
The two-phase rows were modeled before the adapters were measured and do not change the earlier
claim: content-derived idempotency only works when the effect is reproducible from durable inputs.

## The headline, stated once

Durable-execution runtimes make the journaled result exactly-once, but the external side effect inside
an activity / step / node is at-least-once unless that unit is made idempotent. Crash a naive effect
after it has crossed but before the runtime has durably recorded the step, and recovery re-runs the
step and the effect crosses twice. This reproduces on four runtimes by four different mechanisms - a
put/put_writes checkpoint race (LangGraph #8039), an activity retry after a worker timeout (Temporal),
step re-run on recovery (DBOS), and a Restate durable operation retry before the action result is
journaled.

The standard mitigation has a measured condition. A content-derived idempotency key recovers
EXACTLY_ONCE only if replaying the step reproduces the same effect. For a step whose effect depends
on a value produced DURING the step - a model call being the motivating case - replay derives a
different key, the dedup silently stops applying, and the two crossings are different actions:
DIVERGED. If the identity is durably prepared before the nondeterministic draw and carried through
the effect, the measured two-phase rows recover EXACTLY_ONCE at b1 in the control, LangGraph,
Temporal, DBOS, and Restate adapters. That is a measured pattern in this fixture, not a claim that the
runtimes provide it automatically.

The real-model arm is measured narrowly: Anthropic Haiku 4.5 produced the same b1 shape on LangGraph
as the UUID/draw control at k=5. That closes the "no real sampler at all" gap for this fixture, but it
does not generalize to every model/provider/cache setting.

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
    restate_naive              ONCE/ONCE           DUP/DUP         ONCE/ONCE     k=10
    restate_idem               ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=10
    restate_nondet             ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE     k=10
    restate_twophase           ONCE/ONCE         ONCE/ONCE         ONCE/ONCE     k=10

Every measured cell sits at rate 1.0. Wilson 95% lower bound: 0.963 at k=100, 0.929 at k=50,
0.886 at k=30, 0.722 at k=10. The b1 column is the finding: naive durable units duplicate, content-derived
idempotency fixes only reproducible effects, nondeterministic content-derived effects diverge, and
pre-call identity fixes that failure in the measured two-phase rows.

## Evidence Receipts

| Evidence | Command | Receipt |
|---|---|---|
| `evidence/controls.json` | `uv run python -m crashpoint.harness.matrix --k 100 --runtimes r_null,r_dup,r_lost,r_idem,r_diverge,r_twophase --name controls` | `cp1_667ba86d7791b962cbf291518d38c0339307e4ca6b0c81a50332f077fbd20edc` |
| `evidence/langgraph.json` | `uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem,r_lg_nondet,r_lg_twophase --name langgraph` | `cp1_bffad8a0407e66ad6f2dc5ad4a3f39a96d7528ce4e4388ec6be1f9059be13073` |
| `evidence/temporal.json` | `uv run --extra temporal python -m crashpoint.harness.matrix --k 30 --runtimes r_tmp_naive,r_tmp_idem,r_tmp_nondet,r_tmp_twophase --name temporal` | `cp1_6a057f0779fb2b98c60fe71c4b8e8111328a5385ff8069b1598f4d7fa50728a5` |
| `evidence/dbos.json` | `uv run --extra dbos python -m crashpoint.harness.matrix --k 30 --runtimes r_dbos_naive,r_dbos_idem,r_dbos_nondet,r_dbos_twophase --name dbos` | `cp1_0793cb0b8ad9925a9ae547057b707355dc420ac763aec94ce1f7dd0f2334c220` |
| `evidence/restate.json` | `uv run --extra restate python -m crashpoint.harness.restate_matrix --k 10 --name restate --timeout 45` | `cp1_69b85c39d1257ac1307088ef5718b854ef5cfae490ffea9e4f9493870e85bfb7` |
| `evidence/langgraph_model.json` | `ANTHROPIC_MODEL=claude-haiku-4-5-20251001 CRASHPOINT_NONDET_SOURCE=model CRASHPOINT_MODEL_SAMPLER_CMD='python scripts/anthropic_sampler.py' CRASHPOINT_MODEL_SAMPLER_TIMEOUT=90 CRASHPOINT_MODEL_PROMPT='Return only a fresh random-looking 12-character lowercase hexadecimal payment memo. Choose a different value each time.' uv run --extra langgraph python -m crashpoint.harness.matrix --k 5 --runtimes r_lg_nondet,r_lg_twophase --name langgraph_model` | `cp1_9c99ccf1c57336a7c1ca84bc4b08dad1a7c3519b8e17976b5bf806ebda47cb9d` |
| `evidence/isolation_linux.json` | `docker run --rm -v "$PWD:/work:ro" -v "$PWD/evidence:/evidence" -w /work -e PYTHONPATH=src python:3.12-slim python -m crashpoint.adversaries.isolation --require --evidence-path /evidence/isolation_linux.json` | `cp1_9d16e122023c860eafdf320ed69d8241a57f74c790975d883ffd7d77a6bd496d` |
| `evidence/langgraph_hidden.json` | `uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 --name langgraph_hidden` | `cp1_993c57f79dae43a92e42013a8403adf93c0f38088ad6b7864816e7885cc76ff8` |
| `evidence/langgraph_hidden_pending.json` | `uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 --name langgraph_hidden_pending --barrier lg_pending_writes_after_persist` | `cp1_d6c738d55b00a2cf4510bfb12e1b7497e7555de567e7b56e0406d366747d2553` |

`tests/test_discrimination.py` re-derives each receipt from the JSON body and checks that no present
evidence cell disagrees with the model. Dedicated tests re-derive the isolation and hidden-barrier
receipts.

## Packaging

`uv build` succeeds. The wheel is code-only (`src/crashpoint`), while the sdist explicitly includes
`tests/`, `evidence/`, `results/`, top-level docs, repro scripts, `uv.lock`, and the optional
`runtime/` probes. That keeps installed packages small while preserving the release artifact needed
to reproduce the research evidence and the current Vercel blocker.

## Reproduce

    uv sync --group dev --all-extras --locked
    uv run pytest
    uv run ruff check .
    uv run mypy
    uv run python -m crashpoint.model
    uv run python -m crashpoint.adversaries.reflexive
    uv run python -m crashpoint.adversaries.isolation
    docker run --rm -v "$PWD:/work:ro" -v "$PWD/evidence:/evidence" -w /work -e PYTHONPATH=src python:3.12-slim python -m crashpoint.adversaries.isolation --require --evidence-path /evidence/isolation_linux.json
    uv run python -m crashpoint.harness.recomputability
    uv run python -m crashpoint.harness.barrier_inventory
    uv run python -m crashpoint.harness.deferred_runtimes
    uv run python -m crashpoint.harness.matrix --k 100 --runtimes r_null,r_dup,r_lost,r_idem,r_diverge,r_twophase --name controls
    uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem,r_lg_nondet,r_lg_twophase --name langgraph
    uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 --name langgraph_hidden
    uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 --name langgraph_hidden_pending --barrier lg_pending_writes_after_persist

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

    # Restate: a local dev server plus the Python ASGI adapter registered by the harness
    docker run -d --name crashpoint-restate -p 8080:8080 -p 9070:9070 -p 9071:9071 --add-host=host.docker.internal:host-gateway docker.restate.dev/restatedev/restate:latest
    uv run --extra restate python -m crashpoint.harness.restate_matrix --k 10 --name restate --timeout 45

## Real-Model Arm

The default nondeterministic source is `uuid`, because the property under test is irreproducibility
from durable inputs. A real sampler can be injected without hardcoding secrets:

    export CRASHPOINT_NONDET_SOURCE=model
    export CRASHPOINT_MODEL_SAMPLER_CMD='your-sampler-command'
    export CRASHPOINT_MODEL_SAMPLER_TIMEOUT=30

The command receives the prompt on stdin and must write the sampled memo on stdout. For Anthropic's
Messages API, the repo includes a stdlib-only helper:

    export ANTHROPIC_API_KEY=...
    export ANTHROPIC_MODEL=claude-haiku-4-5-20251001
    export CRASHPOINT_NONDET_SOURCE=model
    export CRASHPOINT_MODEL_SAMPLER_CMD='python scripts/anthropic_sampler.py'

The helper also loads a local `.env` file, which is ignored by git; `.env.example` lists the expected
variable names. The checked-in `evidence/langgraph_model.json` was produced with Anthropic Haiku 4.5
and records only non-secret sampler metadata: model name, sampler command, workspace-id presence as a
boolean, and a prompt hash.

## What this does not prove

- **Strong isolation on macOS.** The default proof is the socket-privilege boundary. The Linux
  `setpriv --reuid nobody` proof passed in Docker, but the direct macOS command still reports
  BLOCKED. The PASS proves execute-only access for a UID-dropped subject in that Linux container; it
  is not a full container escape audit.
- **All hidden framework crash points.** The matrix measures b0/b1/b2. LangGraph
  `lg_pre_first_checkpoint` and `lg_pending_writes_after_persist` are measured separately.
  `barrier_inventory.py` still names additional candidates around Temporal activity
  scheduling/replay and DBOS step/status commits, but those are not evidence until each receives a
  model rule and a measured adapter barrier.
- **A broad model-sampler claim.** `evidence/langgraph_model.json` measures one provider/model
  configuration on two LangGraph rows at k=5. It does not characterize provider caching,
  temperature/seed behavior, local samplers, or every model.
- **Vercel Workflow.** An optional JS/TS Nitro fixture exists in `runtime/vercel-workflow/`, but it
  remains unmeasured. On 2026-09-01, workflow@5.0.0-beta.47 compiled under Nitro, but the Local
  World failed before any run with `Invalid version string: "bundled"`, so no faithful
  worker/backend crash harness was validated and no rows were modeled.
- **A runtime defect where the runtime documents at-least-once behavior.** Temporal, DBOS, and
  Restate are not defect reports. They demonstrate the gap between exactly-once workflow language and external
  side-effect behavior under the documented contracts.
- **Large-k statistical claims on real servers.** Temporal and DBOS ran at k=30, and Restate ran at
  k=10. The observed cells are deterministic here; larger k would tighten the interval, not change the
  measured claim.
