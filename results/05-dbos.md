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
- The two-phase row adds a committed `prepare_identity_step` before `effect_step`; recovery reuses
  that prepared key when the uncommitted effect step is replayed.
- **b0** crashes in `effect_step` before the effect: recovery re-runs the step, effect once.
- **b1** crashes in `effect_step` after the effect, before its output commits: recovery re-runs it.
- **b2** lets `effect_step` commit, then crashes `sentinel_step`: recovery resumes at the sentinel
  and does not re-run the committed effect step.

## What it measured (`uv run --extra dbos python -m crashpoint.harness.matrix --k 30 --runtimes r_dbos_naive,r_dbos_idem,r_dbos_nondet,r_dbos_twophase --name dbos`)

```
runtime                before_effect           between     after_persist
------------------------------------------------------------------------
dbos_naive                 ONCE/ONCE           DUP/DUP         ONCE/ONCE
dbos_idem                  ONCE/ONCE         ONCE/ONCE         ONCE/ONCE
dbos_nondet                ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE
dbos_twophase              ONCE/ONCE         ONCE/ONCE         ONCE/ONCE

disagreements (model wrong): []
```

30 trials per cell over four rows, every cell at rate 1.0 (Wilson 95% [0.886, 1.000]), zero
disagreements. DBOS's documented "steps should be idempotent" is the b1 cell: the naive step's
effect crosses twice on the recovery re-run; the idempotent boundary dedups it for a reproducible
payload; the nondeterministic content-derived key diverges; the two-phase prepared key recovers
exactly-once.

## The discrimination meta-test (`uv run pytest tests/test_discrimination.py`)

Over the four receipted evidence files, all skip-if-absent, now all present:
- every evidence receipt re-derives from its body (no number was edited after the run);
- the controls pin DUPLICATED / LOST / EXACTLY_ONCE / DIVERGED (teeth);
- both failure modes - a doubled effect and a lost one - appear in the evidence;
- for each real runtime the naive effect DUPLICATES at b1 and the idempotent boundary recovers
  EXACTLY_ONCE (the fix);
- the honest floor: no naive effect closes b1;
- no observed cell disagrees with the model, on any runtime.
- `evidence/dbos.json` carries receipt
  `cp1_0793cb0b8ad9925a9ae547057b707355dc420ac763aec94ce1f7dd0f2334c220`.

## Full-run summary

Historical note: this entry originally predated the nondeterministic and two-phase rows and used the
older trial-count arithmetic. The current checked-in evidence is summarized in `RESULTS.md`.

| evidence | k | trials | result |
|---|---|---|---|
| controls | 100 | 1,800 | outcomes pinned; two-phase recovers; 0 disagreements |
| langgraph | 50 | 600 | naive b1 DUPLICATED, idem b1 EXACTLY_ONCE, nondet b1 DIVERGED, two-phase b1 EXACTLY_ONCE; 0 disagreements |
| temporal | 30 | 360 | naive b1 DUPLICATED, idem b1 EXACTLY_ONCE, nondet b1 DIVERGED, two-phase b1 EXACTLY_ONCE; 0 disagreements |
| dbos | 30 | 360 | naive b1 DUPLICATED, idem b1 EXACTLY_ONCE, nondet b1 DIVERGED, two-phase b1 EXACTLY_ONCE; 0 disagreements |

3,120 crash+recover trials in the current checked-in evidence, every observed cell equal to a
prediction written before any runtime was crashed. `uv run pytest` / `ruff` / `mypy --strict` green.
