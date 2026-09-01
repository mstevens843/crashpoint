# 03 - The controls, the LangGraph adapter, and two corrections the model forced

Working record. The first crashed runtimes. Every number below was produced by running the command
shown and reading the ledger the crashed process could not forge. The two corrections are kept in
full because they are the point of building the model first: held fixed, it disciplined the harness.

## What was built

- `adapters/base.py` - the shared effect (`execute` on the ledger's invoke socket) and the crash
  (`os.kill(getpid, SIGKILL)`), so every adapter runs in its own subprocess and the ledger survives.
- `adapters/controls.py` - the original controls plus the null baseline, each pinning one outcome.
- `adapters/langgraph_adapter.py` - a one-node durable graph whose node performs the effect, with a
  `RacingSaver(SqliteSaver)` that self-SIGKILLs at an enumerated barrier, run under
  `durability="sync"` (the #8039 mode).
- `harness/trial.py`, `harness/matrix.py`, `harness/wilson.py` - one crash+recover trial, k trials
  per cell with a Wilson interval on the modal rate, the observed matrix beside the predicted one.

## The controls (`uv run python -m crashpoint.harness.matrix --k 100 --runtimes r_null,r_dup,r_lost,r_idem,r_diverge,r_twophase --name controls`)

Historical note: this entry originally predated the `r_diverge` and `r_twophase` controls and used
older trial-count arithmetic. The current controls evidence is k=100 over 18 cells, 1,800 trials,
and pins DUPLICATED / LOST / EXACTLY_ONCE / DIVERGED plus the two-phase recovery shape with zero
disagreements; see `RESULTS.md`.

```
runtime                before_effect           between     after_persist
------------------------------------------------------------------------
null_baseline              ONCE/ONCE           DUP/DUP           DUP/DUP
dup_control                ONCE/ONCE           DUP/DUP         ONCE/ONCE
lost_control               ONCE/ONCE         LOST/LOST         ONCE/ONCE
idem_reference             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE
diverge_control            ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE
two_phase_reference        ONCE/ONCE         ONCE/ONCE         ONCE/ONCE

disagreements (model wrong): []
```

The current run covers the three barriers over six rows at k=100, with zero disagreements. The
controls pin DUPLICATED, LOST, EXACTLY_ONCE, DIVERGED, and two-phase recovery on demand, so the
oracle is proven to have teeth: it is not reporting one outcome for everything.

## LangGraph (`uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 --runtimes r_lg_naive,r_lg_idem,r_lg_nondet,r_lg_twophase --name langgraph`)

```
runtime                before_effect           between     after_persist
------------------------------------------------------------------------
langgraph_naive            ONCE/ONCE           DUP/DUP         ONCE/ONCE
langgraph_idem             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE
langgraph_nondet           ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE
langgraph_twophase         ONCE/ONCE         ONCE/ONCE         ONCE/ONCE
```

The money cell is `langgraph_naive / between`: DUPLICATED, 50/50. This is `langchain-ai/langgraph#8039`
reproduced against current behavior - a crash after the effect but before the completion is durable
re-runs the node, and the naive external effect crosses twice. The `langgraph_idem` row is the fix:
the same barrier, but the idempotent boundary dedups the re-run, so the effect is exactly-once at
every barrier for a reproducible node. The `langgraph_nondet` row shows the content-derived key's
limit at b1: DIVERGED. The `langgraph_twophase` row prepares the key in a durable predecessor node
and recovers EXACTLY_ONCE at b1. Zero disagreements with the model.

## Correction 1 - the model caught a harness bug

When the LangGraph adapter was first wired, `langgraph_idem` read LOST at every barrier, contradicting
the model's EXACTLY_ONCE. Because the model is the fixed reference, that contradiction was treated as
a bug to find, not a new fact. The trial harness chose the control-vs-real argv form by the adapter's
`kind` string - but a real runtime's `idem` MODE collides with a control's `idem` KIND, so `r_lg_idem`
was launched with the control adapter's flags, argparse rejected them (rc 2), the adapter never ran,
and the ledger honestly recorded zero effects - which the oracle correctly reads as LOST. The fix was
to dispatch on the adapter MODULE, not the kind. The model was right; the harness was wrong; the
disagreement is exactly what surfaced it.

## Correction 2 - b0 refined, and a real behavior fell out

The first b0 crashed inside the node, before the effect. Observed, naive b0 was EXACTLY_ONCE only
~80% of the time, the rest LOST. Cause: a crash before LangGraph's FIRST durable checkpoint leaves
recovery nothing to resume, so the whole workflow is silently dropped. That is a real behavior worth
naming - a crash in the opening instant of a run loses it - but it is not the before-effect barrier
the calibration column is meant to be. b0 was redefined to crash after the entry checkpoint is durable
(in the checkpointer's first `put`) but before the node runs. b0 is then deterministically
EXACTLY_ONCE, and the naive-vs-idem b1 contrast is untouched. The refinement is in the adapter
docstring; the dropped-run behavior is noted here, not hidden in the calibration.

## Checks

- `uv run pytest` green; `uv run ruff check .` clean; `uv run mypy` clean.
- `evidence/controls.json` and `evidence/langgraph.json` each carry a canonical-JSON SHA-256 receipt;
  `tests/test_discrimination.py` re-derives both.

Current note: Temporal and DBOS now have the same naive/idempotent/nondeterministic/two-phase row
family; see `RESULTS.md`, `results/04-temporal.md`, and `results/05-dbos.md`.
