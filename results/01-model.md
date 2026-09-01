# 01 - The model, before any crash

Working record. The model layer is complete and self-verifying. It was written and tested with no
runtime running and nothing crashed: it is a pure prediction, and the harness exists to try to prove
it wrong.

Current note: the model later added the nondeterminism conditional, the fifth outcome `DIVERGED`,
and the two-phase prepared-identity rows. The current matrix is 18 runtime rows x 3 barriers; this
entry is kept as the model-stage record, with the current vocabulary named below.

## What was built

`src/crashpoint/model/` - four modules, zero third-party dependencies:
- `layers.py` - the vocabulary:
  `Outcome {EXACTLY_ONCE, DUPLICATED, DIVERGED, LOST, VOID}` and the orthogonal property enums
  `Durability`, `PersistOrder {EFFECT_THEN_PERSIST, PERSIST_THEN_EFFECT, RACE}`,
  `EffectMode {NAIVE, IDEMPOTENT, TWO_PHASE}`,
  `Determinism {DETERMINISTIC, NONDETERMINISTIC}`, and `Phase {BEFORE, BETWEEN, AFTER}`.
- `runtimes.py` - 18 rows: a null baseline, controls/reference rows that pin the non-VOID outcomes
  and the two-phase recovery shape, and the three real engines (LangGraph, Temporal, DBOS) in naive,
  idempotent, nondeterministic, and two-phase variants where applicable, each with the shipped
  semantics it models.
- `barriers.py` - the three crash barriers named by phase so a column is comparable across runtimes.
- `predict.py` - one pure total function from (runtime, barrier) to an outcome-with-rationale.

## What it predicts (`uv run python -m crashpoint.model`)

```
runtime                  before_effect         between   after_persist
null_baseline                     ONCE            DUP             DUP
dup_control                       ONCE            DUP             ONCE
lost_control                      ONCE            LOST            ONCE
idem_reference                    ONCE            ONCE            ONCE
diverge_control                   ONCE        DIVERGED            ONCE
two_phase_reference               ONCE            ONCE            ONCE
langgraph_naive                   ONCE            DUP             ONCE   [TARGET]
langgraph_idem                    ONCE            ONCE            ONCE
temporal_naive                    ONCE            DUP             ONCE
temporal_idem                     ONCE            ONCE            ONCE
dbos_naive                        ONCE            DUP             ONCE
dbos_idem                         ONCE            ONCE            ONCE
langgraph_nondet                  ONCE        DIVERGED            ONCE
langgraph_twophase                ONCE            ONCE            ONCE
temporal_nondet                   ONCE        DIVERGED            ONCE
temporal_twophase                 ONCE            ONCE            ONCE
dbos_nondet                       ONCE        DIVERGED            ONCE
dbos_twophase                     ONCE            ONCE            ONCE
```

The finding is the BETWEEN column: every naive durable runtime DUPLICATES the external effect at the
lethal barrier, and the content-derived idempotent boundary recovers it only when the step is
reproducible from its durable inputs. The two-phase rows recover by preparing the identity before the
nondeterministic draw. The controls pin DUPLICATED (dup_control), LOST (lost_control),
EXACTLY_ONCE (idem_reference), DIVERGED (diverge_control), and two-phase EXACTLY_ONCE
(two_phase_reference), so the oracle is proven to discriminate. BEFORE and AFTER are exactly-once for
every durable runtime - the calibration columns.

## Checks (`uv run pytest`, `ruff`, `mypy --strict`)

- Historical model-stage count: 44 tests passed. The current full-suite count is in `RESULTS.md`.
  `test_contract.py` parses the four model modules with `ast` and fails if the
  predictor imports or calls anything impure. `test_predict.py` asserts the calibration columns hold,
  the controls pin their outcomes, every naive durable runtime duplicates at b1, the idempotent
  boundary recovers it for reproducible steps, two-phase recovers the nondeterministic case when the
  identity is prepared before the draw, and the honest floor - no naive durable runtime closes b1 -
  is named.
- ruff clean, mypy --strict clean, zero third-party dependencies in the model.

Next: the out-of-process ledger daemon (two sockets, records every attempt, hash-chained, the SUT
cannot forge it) + the Outcome oracle + the idempotency-key derivation, then the LangGraph adapter and
the crash injector.
