# 05 - DBOS, checkpoint-in-Postgres, and the discrimination meta-test

Working record. The third real runtime, and the meta-test that ties the evidence together. DBOS
checkpoints each step's output in Postgres and recovers pending workflows on restart. Every number
below was produced by running the command shown against a local Postgres and reading the
out-of-process ledger.

## Substrate

`dbos 2.31.0` via `uv --extra dbos`. The system database is a Docker Postgres
(`postgres:16`) on port 5433 - a native Postgres already held 5432, caught by the Day-0-style check
and worked around, not ignored. The URL is passed via `CRASHPOINT_DBOS_URL`.

## What was built

`adapters/dbos_adapter.py` - a `@DBOS.workflow` with an `effect_step` and a `sentinel_step`. The
barrier is the process-level `_BARRIER`; the workflow and executor ids are derived from the per-trial
checkpoint path, so recovery on the reused Postgres only touches this trial's workflow. Recovery is
`DBOS.launch()` on the recovery process, which recovers the pending workflow and resumes it from the
last committed step - no timeout wait, unlike Temporal.
- **b0** crashes in `effect_step` before the effect: recovery re-runs the step, effect once.
- **b1** crashes in `effect_step` after the effect, before its output commits: recovery re-runs it.
- **b2** lets `effect_step` commit, then crashes `sentinel_step`: recovery resumes at the sentinel
  and does not re-run the committed effect step.

## What it measured (`uv run --extra dbos python -m crashpoint.harness.matrix --k 30 --runtimes r_dbos_naive,r_dbos_idem --name dbos`)

```
runtime                before_effect           between     after_persist
------------------------------------------------------------------------
dbos_naive                 ONCE/ONCE           DUP/DUP         ONCE/ONCE
dbos_idem                  ONCE/ONCE         ONCE/ONCE         ONCE/ONCE

disagreements (model wrong): []
```

30 trials per cell, every cell at rate 1.0 (Wilson 95% [0.886, 1.000]), zero disagreements. DBOS's
documented "steps should be idempotent" is the b1 cell: the naive step's effect crosses twice on the
recovery re-run; the idempotent boundary dedups it. Three runtimes, three mechanisms - a checkpoint
race, a timeout retry, a step re-run - one property.

## The discrimination meta-test (`uv run pytest tests/test_discrimination.py`)

Over the four receipted evidence files, all skip-if-absent, now all present:
- every evidence receipt re-derives from its body (no number was edited after the run);
- the three controls pin DUPLICATED / LOST / EXACTLY_ONCE (teeth);
- both failure modes - a doubled effect and a lost one - appear in the evidence;
- for each real runtime the naive effect DUPLICATES at b1 and the idempotent boundary recovers
  EXACTLY_ONCE (the fix);
- the honest floor: no naive effect closes b1;
- no observed cell disagrees with the model, on any runtime.

## Full-run summary

| evidence | k | trials | result |
|---|---|---|---|
| controls | 100 | 240 | DUPLICATED / LOST / EXACTLY_ONCE pinned; 0 disagreements |
| langgraph | 50 | 150 | naive b1 DUPLICATED, idem b1 EXACTLY_ONCE; 0 disagreements |
| temporal | 30 | 90 | naive b1 DUPLICATED, idem b1 EXACTLY_ONCE; 0 disagreements |
| dbos | 30 | 90 | naive b1 DUPLICATED, idem b1 EXACTLY_ONCE; 0 disagreements |

570 crash+recover trials, every observed cell equal to a prediction written before any runtime was
crashed. `uv run pytest` / `ruff` / `mypy --strict` green.
