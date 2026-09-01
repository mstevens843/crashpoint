# 00 - Day-0 substrate gate

Working record. Every line below was produced by running the command shown on this machine and
reading its output. Nothing is remembered. Where a runtime cannot run it says so and says why.

**Host:** macOS (Darwin arm64). **Python:** 3.12.13 (uv). **uv:** 0.11.26. **Docker:** 29.3.1, up.
**Default path needs no model API key:** the checked-in nondeterministic evidence has no frontier
model in the loop; cost is wall-clock only.

Current note (2026-09-01): Temporal CLI 1.8.2 / server 1.31.2, Docker Postgres, and Restate
server/CLI Docker images were available and were used for fresh evidence in `results/07`. The
default nondeterministic evidence still uses a local draw, not a model API.

## Gate 1 - LangGraph (the guaranteed core, and the live defect)

`uv sync --extra langgraph` installs `langgraph 1.2.11` + `langgraph-checkpoint-sqlite 3.1.1`. The
crash seam is confirmed working: a `RacingSaver(SqliteSaver)` subclass that overrides `put` and
`put_writes` is accepted as the checkpointer, and both fire during a run (observed call order on a
one-node graph: `put, put_writes, put_writes, put, put`). This is the exact seam `langgraph#8039`
uses to force the put/put_writes interleaving and self-SIGKILL. **LangGraph: RUN.**

## Gate 2 - Temporal (the at-least-once contrast)

Historical note: the first Day-0 pass had to provision `temporal`. The current evidence uses a local
`temporal server start-dev` server. The crash boundary is the worker process around
`effect_activity`; status recorded in results/04 and results/07.

## Gate 3 - DBOS (checkpoint-in-Postgres reference)

`dbos` via uv + a Docker Postgres for the system database. The crash boundary is a wrapped
`@DBOS.step` crashed between the effect and the Postgres checkpoint write. Status recorded in
results/05 and results/07; GATED if Postgres/DBOS will not come up locally.

## Gate 4 - Restate (durable-operation retry contrast)

Restate server/CLI Docker images were available locally. The later adapter serves a Python ASGI
workflow and registers it with the Docker dev server; the harness kills and restarts the ASGI worker
around `ctx.run_typed`. Status recorded in `results/07`.

## Gate outcome

The guaranteed core clears on Gate 1 alone: the model, the out-of-process ledger, the LangGraph
adapter, and the control/reference adapters need no external servers. Temporal, DBOS, and Restate are
real-service columns, gated on their smoke tests. Proceed: model first (done), then the ledger daemon.
