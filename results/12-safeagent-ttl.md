# 12 - SafeAgent SQLite claim/sweep TTL boundary

Working record for 2026-09-19. **Corrections (same day, two passes):** a first independent review
found that the original harness and offline verifier could claim success without the evidence
their claims required - a snapshot/observer failure could leak a live worker process and silently
report a missing ledger file as a confirmed empty one, and the verifier did not check per-cell
trial counts, protocol fields, the summary, provenance hashes, or confinement of fixed filenames
against symlink escape. A second independent review then found that several of those same fixes,
and several receipt fields the schema's own docstrings described as required, were not actually
enforced when tested individually: `reset_confirmed`, `ttl_boundary_confirmed`,
`measured_age_at_decision`, `pending_ttl_seconds`, `worker_a_exit_status`, and `sweep_invoked`
were all unchecked structural fields; the manifest's self-reported `trial_count` was never
compared against its own trial list; the embedded-source inventory was only checked against
whatever the manifest itself claimed to embed, never an independent required list; a missing
`retry_claim` event went unrequired; and the raw ledger's attempt order (not just its counts and
digests) was never verified, so a duplicated effect could be recorded with worker-B's retry
appearing to precede worker-A's original attempt (full findings and fixes, both passes:
`handoff/safeagent-ttl/CORRECTIONS.md` and `handoff/safeagent-ttl/SUMMARY.md`). The measured
central result was independently reproduced against the actual production code in both passes and
is unchanged in either; this is a twice-corrected repeat of already-known results, not a newly
blinded discovery. The original 30-trial bundle (`evidence/safeagent_ttl/safeagent_ttl/`), the
first pass's corrected bundle (`evidence/safeagent_ttl/safeagent_ttl_corrected/`), and the frozen
prediction are all preserved byte-for-byte as historical evidence. This report, and the command
below, now describe the twice-corrected harness and the new, current, authoritative bundle
(`evidence/safeagent_ttl/safeagent_ttl_corrected_review2/`), produced with the same frozen
prediction (sha256 `28a8d9ae822e927dca8e7ad21b281096da030870171737c5a2f88fc328212b73`, unchanged).

This experiment is independent of entries 00-11: it asks whether
`safeagent-exec-guard`'s `SQLiteExecutionStore` - a durable-claim guard for agent tool actions,
unrelated to CrewAI, LangGraph, or any runtime measured elsewhere in this repo - lets an unsettled
logical action become executable again after a real process death, a real elapsed TTL, and an
explicit sweep, and what changed about that boundary between the released `0.1.23` and `0.1.24`
distributions.

## Question

`SQLiteExecutionStore(db_path, pending_ttl_seconds=...)` implements a two-phase claim: `claim()`
inserts a `PENDING` row keyed by `request_id`, `settle()` transitions it to `COMMITTED`, and a
crash between the two leaves it `PENDING`, with only an explicit sweep able to release it. The commit
[`a0218fe`](https://github.com/azender1/SafeAgent/commit/a0218fe3626886c887db8902ac21829b0dd208f4)
changed what that sweep does, and shipped in the `0.1.24` release. The central scenario:

`durable claim -> independently recorded harmless effect -> worker death before settle -> TTL
expires -> sweep -> fresh client retries the same logical action -> independent effect readback`

Three questions are kept separate throughout, on purpose: did another claim succeed; how many
external effects actually occurred (read from crashpoint's own out-of-process ledger, never from
a claim return value); and did the logical task reach a settled result. Blocking another execution
is not, by itself, completed reconciliation.

## Experiment

Two isolated virtualenvs (`.local/safeagent-envs/0.1.23/`, `.local/safeagent-envs/0.1.24/`),
each with exactly one released `safeagent-exec-guard` version installed from PyPI and crashpoint
installed editable for its ledger client - never both versions in one environment, never an
edited or vendored copy of SafeAgent. A parent harness
(`crashpoint.harness.safeagent_ttl`) spawns a worker (`crashpoint.harness.safeagent_ttl_runtime`)
as a real OS process in the release's venv, waits for a structured barrier event on its stdout,
independently checks the claim state and ledger effect count, and only then sends it a real
`SIGKILL` - never a caught exception standing in for a crash. A fresh client (a new OS process,
same venv) then sweeps the same durable claim database, and a second fresh client (Worker B)
attempts the identical logical claim. After both are done, a freshly spawned observer process
re-parses the raw, retained ledger bytes independently of any live daemon dump.

Five cases, informally: settle normally and let time pass anyway (control); kill before settle and
retry before the TTL expires; kill and let the TTL expire but never sweep; kill, let the TTL
expire, and sweep (the central scenario); and kill *before the effect is even attempted*, let the
TTL expire, and sweep. Full definitions and both releases' predicted rows are in
[`12-safeagent-ttl-prediction.json`](./12-safeagent-ttl-prediction.json), frozen (sha256
`28a8d9ae822e927dca8e7ad21b281096da030870171737c5a2f88fc328212b73`) before the confirmatory batch
ran. One exploratory, unrecorded smoke trial per release against `pending_expired_swept` was run
before that prediction was written, to sanity-check the harness mechanics; it is not part of, and
is not substituted for, the batch below.

The recorded external effect is a single fixed, harmless, local payload
(`{"operation": "safeagent_ttl_local_action", "marker": "harmless"}`) acknowledged only by
crashpoint's own out-of-process ledger daemon - a separate process reached over a Unix socket the
worker can invoke but never read, reset, or seal - never a real payment, email, trade, blockchain
write, or any other externally consequential action.

TTL was `1.5s` for the four cases that need the claim to actually expire, and `30.0s` (never waited
out) for the one case that must stay comfortably inside the window; both are recorded per-trial,
and a before-TTL trial that measures its own age as too close to expiry is marked INVALID rather
than silently counted as a pass. Every trial uses a fresh file-backed SQLite database (never
`:memory:`, never reused across trials) and a fresh ledger with deduplication disabled
(`key=None`), so two authorized crossings of one logical action are actually counted as two. All
30 confirmatory trials were valid; none were discarded or retried.

Command:

```bash
./.venv/bin/python -m crashpoint.harness.safeagent_ttl --name safeagent_ttl_corrected_review2
```

### The actual installed behavior, read from both isolated venvs, not GitHub main

`SQLiteExecutionStore.claim()` always fails (`sqlite3.IntegrityError` on the `request_id` primary
key) against any existing row, `PENDING` or `COMMITTED`, regardless of its age - claim() itself
never consults the clock. Both releases' `sweep_stale_pending()` and (`0.1.24`-only)
`count_stale_pending()` were extracted directly from each installed `site-packages` copy and
confirmed byte-identical to the corresponding PyPI wheel by sha256 (wheel digests
`ef28d06b4e32f6bff88fcf86f21556a5de7b0a40f8f4da754656e23c900b773b` for `0.1.23` and
`45d2e238afe1e0e8773954849e2ae53a742d7275f72d337ed4af7f64c36c2708` for `0.1.24`, matching PyPI's
own reported digests):

- **`0.1.23`**: `sweep_stale_pending()` executes `DELETE FROM execution_requests WHERE
  status='PENDING' AND claimed_at < cutoff` and returns the deleted row count. A swept
  `request_id` becomes claimable again.
- **`0.1.24`**: `sweep_stale_pending()` is `return 0` - unconditionally, deleting nothing, no
  matter how stale the row is. Its own docstring: *"Deprecated fail-closed compatibility method;
  modifies no rows. Older releases deleted stale PENDING rows here. That could permit an
  externally accepted action to execute again after a timeout or crash. Reconcile with the
  provider before an explicit recovery decision."* The new `count_stale_pending()` runs the same
  cutoff query as the old delete but only counts, never mutating.

One correction to the upstream commit's own description worth recording precisely: the commit
message frames the change as `sweep` now *"reports stale PENDING rows and requires
reconciliation"* - but that reporting capability lives entirely in the new, separate
`count_stale_pending()` method. `sweep_stale_pending()` itself reports nothing; it is a pure
no-op. A caller that keeps calling only `sweep_stale_pending()`, exactly as `0.1.23` code would
have, observes no error and no behavior change signal - the stale claim simply, silently, never
comes back.

## Result

30/30 trials (3 per cell, 5 cases x 2 releases) matched the pre-registered prediction exactly, and
every trial's receipt independently passes the strict schema and cross-field checks in
`crashpoint.harness.safeagent_ttl_receipt.validate_receipt`. The offline verifier
(`crashpoint.harness.safeagent_ttl_verify`) independently re-parses every trial's raw ledger bytes
and every retained SQLite claim snapshot (pre-kill, post-kill, pre-sweep, post-sweep, settled,
final - whichever a case actually produces), cross-checks protocol claims (kill, barrier, sweep,
retry) against each trial's own retained raw worker stdout, recomputes the observed result, label,
`passed`, and the whole cells summary from those independent reads rather than the receipted
summary, verifies embedded provenance (prediction bytes, source hashes, release identity) against
what is actually in the bundle, and confines every file it opens - not only the manifest-declared
trial directories - against a symlink escaping the bundle. It reported zero contradictions on the
current bundle, both in place and after the entire bundle was copied to an unrelated path and
re-verified there with no SafeAgent installation available.

| Case | `0.1.23` observed | `0.1.24` observed |
|---|---|---|
| `settled_control` | 1 effect, `COMMITTED`, retry denied - `settled_once_retry_blocked` | same |
| `pending_before_ttl` | 1 effect, `PENDING`, retry denied - `blocked_pending_effect_already_recorded` | same |
| `pending_expired_no_sweep` | 1 effect, `PENDING`, retry denied - `blocked_pending_effect_already_recorded` | same |
| `pending_expired_swept` | **2 effects**, `COMMITTED`, retry admitted - `duplicated_effect_after_reclaim` | 1 effect, `PENDING`, retry denied - `blocked_pending_effect_already_recorded` |
| `pre_effect_expired_swept` | 1 effect, `COMMITTED`, retry admitted - `reclaimed_and_settled_once` | **0 effects**, `PENDING`, retry denied - `blocked_pending_task_unperformed` |

Toolchain: `safeagent-exec-guard` 0.1.23 and 0.1.24 (both from PyPI, sha256-verified, and the
installed version independently confirmed against `importlib.metadata` at run time), Python
3.12.13, crashpoint commit `606893ebb353df5dab3ac68738051eb5fbb7286e` (the base this experiment
branched from; the harness/runtime/receipt/verifier/ledger source files that actually executed
are embedded byte-for-byte in the bundle under `sources/`, since they were untracked relative to
that commit for the entire run). Receipt:
`cp1_c6bad483c9cd68ca95940457ae443eccb3cb4963a893652f51ce9079c51fc431`.

### The central finding

`pending_expired_swept` reproduces the reported class of bug in `0.1.23`: a worker that recorded
its effect and then died before settling leaves a `PENDING` row; once the TTL passes and something
calls `sweep_stale_pending()`, the row is deleted, a fresh client re-claims the same logical
action, and performs the same effect again - the independent ledger counts two crossings of one
logical action, `DUPLICATED`, not two different actions. `0.1.24`'s inert sweep prevents exactly
this: the row is never released through this API, the retry is denied every time, and the
already-recorded effect is never repeated.

### The bounded pre-effect finding, flagged in the prediction in advance

`pre_effect_expired_swept` was named in the frozen prediction specifically so it could not later
be mistaken for a discovery made only after seeing the data. When the original worker dies
*before* ever attempting the effect, `0.1.23`'s sweep-and-reclaim path is what makes the task
complete at all - the retry performs the one effect that was always going to be needed, and gets
a clean `EXACTLY_ONCE`/`COMMITTED` result. `0.1.24` prevents the `0.1.23`-side duplication risk
in the cases measured here, at a cost: in the bounded pre-effect case, `0.1.24` retained `PENDING`
and denied the retry with zero effects. The tested claim/sweep path does not restore liveness for
that claim; authoritative reconciliation, or another explicitly designed recovery policy, is
outside this experiment's scope, and SafeAgent's own `0.1.24` docstring already states that this
is the intended, deliberate tradeoff ("Reconcile with the provider before an explicit recovery
decision"), not an undisclosed side effect.

This is not evidence that recovery is impossible in general, only that the specific tested path
- `claim`/`get`/`settle`/`sweep_stale_pending`/`count_stale_pending`/`audit_claims`, called the way
this experiment calls them - does not release this claim. `settle()` remains available to a caller
at any time: nothing here shows it "must" be called with a fabricated success result. A caller
that has independently reconciled with the actual provider (or decided the action should be
treated as cancelled) can call `settle()` with a truthful failure/cancellation result; doing so
transitions the row to `COMMITTED` without asserting the external effect ever occurred, and this
experiment neither measures nor recommends a specific reconciliation policy. What the measured
data supports is narrower: zero effects were recorded, the retry was denied, and no automatic,
same-API path moved the claim out of `PENDING` - which is exactly why
`crashpoint.harness.safeagent_ttl_receipt.derive_label` refuses to call zero effects
`EXACTLY_ONCE` and instead labels it `blocked_pending_task_unperformed`, not
`blocked_pending_permanently` or any other stronger claim.

## Claim boundary

This is a `SQLiteExecutionStore` experiment, not a `/sweep` HTTP route test, not a
`PostgresExecutionStore` test, not an MCP/HTTP server test, and not a payment/x402/`agent_id`
gating test (`agent_id=None` throughout - payment gating was never engaged). No CrewAI, LangGraph,
n8n, or SABLE integration is exercised or claimed. All process separation is same-host, same-user
subprocess separation (real `SIGKILL` across isolated venvs on one macOS host) - not a sandboxed
or independently-operated environment, and this experiment does not test stale-owner overlap
(Worker A is confirmed dead, by exit code, before Worker B starts). Thirty trials is a small,
deterministic, predeclared count; no statistical reliability-rate claim is made from it. "Retry
denied" is reported as exactly that - never equated with "the provider-side action was
reconciled" or "the task will eventually complete." This is not a claim about provider-side
idempotency (no external provider is involved at all - the only effect is the local ledger) and
not a claim of global exactly-once execution: it measures one store's claim/sweep boundary on one
host, nothing about coordination across multiple stores, hosts, or organizations.
`crashpoint.harness.safeagent_ttl_receipt.LIMITATIONS` states these boundaries in every receipt,
not only here.

## Independent context

This follows the same public discussion this repository's `11-crewai-retry.md` entry follows -
[crewAIInc/crewAI#5802](https://github.com/crewAIInc/crewAI/issues/5802) - specifically the later
correction pointing at SafeAgent's own fix
([comment](https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5721452476),
[`a0218fe`](https://github.com/azender1/SafeAgent/commit/a0218fe3626886c887db8902ac21829b0dd208f4),
[`b6e9daf`](https://github.com/azender1/SafeAgent/commit/b6e9daf589e80b98793f3bab7d10c18f5de82984)).
This entry independently verifies that fix against the actual released PyPI distributions (not
GitHub main), and measures a boundary condition - `pre_effect_expired_swept` - not addressed in
that thread as of this writing.
