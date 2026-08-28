# 04 - Temporal, the at-least-once contrast

Working record. The second real runtime. Temporal activities are at-least-once by design, so this is
where the finding stops being a LangGraph story and starts being a property of durable execution.
Every number below was produced by running the command shown against a local Temporal dev server and
reading the out-of-process ledger.

## Substrate

`brew install temporal` provides the CLI (server 1.31.2); `temporalio 1.32.0` via `uv --extra
temporal`. The server is a local `temporal server start-dev --headless` (in-memory, no cloud). It is
a separate process, so it survives the worker's self-SIGKILL - which is the whole mechanism: the
worker is what crashes, the service is what redelivers.

## What was built

`adapters/temporal_adapter.py` - a workflow with two activities: `effect_activity` (the external
effect) and `sentinel_activity`. The barrier is the process-level `_BARRIER`, so the crash worker
crashes at the barrier and the recovery worker (a fresh process, barrier "none") does not:
- **b0** crashes in `effect_activity` before the effect: the activity times out (2s
  start-to-close) and is retried; recovery runs the effect once.
- **b1** crashes in `effect_activity` after the effect, before it reports completion: the retry
  re-runs the whole activity.
- **b2** lets `effect_activity` complete (its result is durable in history), then crashes
  `sentinel_activity`: recovery does not re-run the completed activity.

The workflow runs unsandboxed (`UnsandboxedWorkflowRunner`) - it is two sequential activities, so the
sandbox added only a module re-import that fought this package's relative imports. Noted, not hidden.

## What it measured (`uv run --extra temporal python -m crashpoint.harness.matrix --k 30 --runtimes r_tmp_naive,r_tmp_idem --name temporal`)

```
runtime                before_effect           between     after_persist
------------------------------------------------------------------------
temporal_naive             ONCE/ONCE           DUP/DUP         ONCE/ONCE
temporal_idem              ONCE/ONCE         ONCE/ONCE         ONCE/ONCE

disagreements (model wrong): []
```

30 trials per cell, every cell at rate 1.0 (Wilson 95% [0.886, 1.000]), zero disagreements. The b1
cell is the documented Temporal contract made concrete: "Activities may be executed more than once ...
A non-idempotent Activity could adversely affect the state." The naive activity's external effect
crosses twice; the idempotency-key boundary dedups the retry to exactly-once. Same finding as
LangGraph, a different mechanism (a retry after a timeout, not a checkpoint race).

## Checks

- `evidence/temporal.json` carries receipt `cp1_86b16976...`; `tests/test_discrimination.py`
  re-derives it and asserts the naive/idem b1 contrast.
- `uv run pytest` / `ruff` / `mypy` green.

Next: DBOS (checkpoint-in-Postgres), then the discrimination meta-test over the full evidence.
