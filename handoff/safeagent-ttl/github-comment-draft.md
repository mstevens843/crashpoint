DRAFT ONLY - not posted. For Mathew to review, edit, and decide where (if anywhere) to post.
Likely destination: a comment on crewAIInc/crewAI#5802 (the thread the correction referencing
SafeAgent appeared in), or on the SafeAgent repository directly if that seems more appropriate -
your call. No URL below has been created or published by this task.

---

Ran an independent check of `safeagent-exec-guard`'s `SQLiteExecutionStore` claim/sweep TTL
boundary against both released PyPI versions (`0.1.23` and `0.1.24`), scoped narrowly to that one
store class - not the HTTP `/sweep` route, not `PostgresExecutionStore`, not payment/x402 gating.

Setup: two isolated virtualenvs, one release each, real process `SIGKILL` (not a caught
exception) after independently verifying claim state and effect count, a real elapsed TTL, an
explicit `sweep_stale_pending()` call, and a fresh OS process for the retry. External effects are
counted by an out-of-process ledger the worker can only write to, never read, so the count isn't
self-reported. 5 cases x 2 releases x 3 trials = 30 trials, matched a prediction frozen before the
batch ran, 30/30.

**Confirmed the fix works as intended for the reported shape.** In `0.1.23`,
`sweep_stale_pending()` deletes a stale `PENDING` row, so a claim whose worker died *after*
recording its effect can be re-claimed and re-executed by a fresh client - the ledger shows 2
effects for 1 logical action (3/3 trials). In `0.1.24`, `sweep_stale_pending()` is an
unconditional no-op (`return 0`, no matter how stale the row is) - the row is never released
through that method, so the retry is always denied and the duplicate never happens (3/3 trials).

**One bounded finding worth flagging, and one small doc/behavior mismatch:**

1. In the bounded case where the worker died *before* ever attempting the effect (never even
   reached it), `0.1.23` correctly recovers: sweep frees the row, the retry performs the one
   effect that was always needed, `COMMITTED` (3/3 trials). `0.1.24` denies that retry too, so the
   row stayed `PENDING` with zero effects recorded in every trial (3/3 trials, `0.1.24` only - not
   both releases). The tested claim/sweep path does not restore liveness for that claim; nothing
   in `SQLiteExecutionStore`'s public API (`claim`/`get`/`settle`/`sweep_stale_pending`/
   `count_stale_pending`/`audit_claims`) releases it automatically. That doesn't mean the claim is
   unrecoverable in general - `settle()` remains available for a caller that has independently
   reconciled with the actual provider (confirmed the action did or didn't happen) to record a
   truthful result - only that this store's own methods don't do that reconciliation for you, and
   your `0.1.24` docstring already says as much ("Reconcile with the provider before an explicit
   recovery decision"). Flagging mainly because it's easy to miss that this is the deliberate
   trade for closing the `0.1.23` duplication, not a separate new gap.
2. `sweep_stale_pending()`'s own docstring in `0.1.24` says it now *reports* stale rows - but the
   reporting is entirely in the separate, new `count_stale_pending()` method.
   `sweep_stale_pending()` itself reports nothing back to the caller (still returns an `int`, just
   always `0`); a caller upgrading and still only calling `sweep_stale_pending()`, as `0.1.23`
   code would, gets no signal that anything changed - the stale claim just quietly never comes
   back.

Scope/limits: SQLite store only, same-host/same-user process separation (not a sandbox), no
distributed fencing or stale-owner-overlap test, 30 predeclared trials (no reliability-rate
claim). Full matrix, receipts, and an offline-verifiable evidence bundle (re-parses raw ledger
bytes and raw SQLite snapshots independently of the summary, survives relocation) are in a
branch I can share if useful - not linked here since it isn't published yet.

Happy to share the harness or raw trial data if it's useful for a regression test on your end.
