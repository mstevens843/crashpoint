# 00 - Day-0 substrate gate

Working record. Every line below was produced by running the command shown on this machine and
reading its output. Nothing is remembered. Where a runtime cannot run it says so and says why.

**Host:** macOS (Darwin arm64). **Python:** 3.12.13 (uv). **uv:** 0.11.26. **Docker:** 29.3.1, up.
**No model API key needed:** the fixture has no frontier model in the loop; cost is wall-clock only.

## Gate 1 - LangGraph (the guaranteed core, and the live defect)

`uv sync --extra langgraph` installs `langgraph 1.2.11` + `langgraph-checkpoint-sqlite 3.1.1`. The
crash seam is confirmed working: a `RacingSaver(SqliteSaver)` subclass that overrides `put` and
`put_writes` is accepted as the checkpointer, and both fire during a run (observed call order on a
one-node graph: `put, put_writes, put_writes, put, put`). This is the exact seam `langgraph#8039`
uses to force the put/put_writes interleaving and self-SIGKILL. **LangGraph: RUN.**

## Gate 2 - Temporal (the at-least-once contrast)

`temporal` CLI was not installed; `brew install temporal` (a local dev server, no cloud) and the
`temporalio` SDK are being provisioned. The crash seam is `ActivityInboundCallsInterceptor.execute`
(kill the worker post-effect) with `temporal server start-dev`. Status recorded in results/04; if the
dev server will not run locally the Temporal columns are reported GATED, never faked.

## Gate 3 - DBOS (checkpoint-in-Postgres reference)

`dbos` via uv + a Docker Postgres for the system database. The crash seam is a wrapped `@DBOS.step`
crashed between the effect and the Postgres checkpoint write. Status recorded in results/05; GATED if
Postgres/DBOS will not come up locally.

## Gate outcome

The guaranteed core clears on Gate 1 alone: the model, the out-of-process ledger, the LangGraph
adapter, and the three control adapters need no external servers. Temporal and DBOS are best-effort
real columns, gated on their smoke tests. Proceed: model first (done), then the ledger daemon.
