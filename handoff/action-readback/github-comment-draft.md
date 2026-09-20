DRAFT ONLY - not posted. For Mathew to review, edit, and decide where (if anywhere) to post.
Likely destination: a follow-up comment on crewAIInc/crewAI#5802 (the same thread azender1's
external-evidence comparison and safal207's acknowledgment appeared in) - your call. No URL below
has been created or published by this task.

---

Built a small, bounded fixture for the piece that thread's comparison didn't cover: a caller-side
action identity, minted and durably committed *before* dispatch, linked to an independently
read-back external effect - as opposed to observing existing activity after the fact.

Setup: a fresh action ID is committed to a caller-owned SQLite store (WAL, full sync, recorded and
checked per trial) and independently re-confirmed durable through a second connection before a
worker is ever allowed to dispatch. The worker itself re-reads its own committed record rather
than trusting a command-line argument. The external effect goes through an out-of-process ledger
the worker can't forge, and a *separate* freshly spawned process - never the worker's own exit
code, never a live in-memory dump - reads back what actually landed, archives the exact bytes, and
independently re-parses the record chain and order.

Six cases, 3 trials each, 18 total, all matching a prediction frozen before the batch ran:

1. **Clean** dispatch and completion - 1 effect, matches the admitted payload.
2. **Effect recorded, then the client's own receipt is lost** (killed right after, confirmed via
   an independent check before the kill) - still 1 effect; the *client's* bookkeeping is what's
   missing, not the effect.
3. **Killed before ever dispatching** - a real, independently-verified empty receiver store, not
   an assumed one.
4. **A naive retry with no receiver-side deduplication** - 2 real recorded effects, in the correct
   raw order (the original attempt before the retry).
5. **A deliberately mismatched payload dispatched under the correct action ID** - exposed as a
   digest mismatch, not folded into "the action succeeded" just because the ID matched.
6. **Evidence deliberately denied after the effect happened** (the readback file removed before
   the observer runs) - stays classified as unverified/indeterminate; never miscoded as zero
   effects, "missing externally," or safe to blindly replay.

Before calling any of this final, switched to reviewing it adversarially: found and fixed three
real bugs in the harness/verifier themselves (a missed evidence-file write, an observer-launch
failure that was crashing a whole trial instead of degrading honestly, and a cross-trial check
whose own database query could never have caught what it claimed to check), then proved - by
disabling six of the highest-risk checks one at a time in a throwaway copy, never the real code -
that the real checks are actually what the tests depend on, not something else accidentally
passing. Full writeup if useful.

Scope/limits: same-host process separation (not a sandbox), no distributed fencing, no
host/power-loss durability claim, 18 predeclared trials (no reliability-rate claim). This is a
bounded reference fixture for comparing against something like a future SafeAgent Control
mechanism, not that comparison itself - haven't run one, and this doesn't touch CrewAI, LangGraph,
or SafeAgent's own code.

Happy to share the harness, raw trial data, or the field-mapping notes if useful for your own
comparison work.
