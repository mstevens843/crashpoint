# 13 - Pre-Dispatch Action Identity and External Readback

Working record for 2026-09-20. Built end to end in one pass, including its own mandatory
adversarial self-review (full findings: `handoff/action-readback/SELF-REVIEW.md`), before any
evidence was reported as final.

Context: [crewAIInc/crewAI#5802](https://github.com/crewAIInc/crewAI/issues/5802) is where
safal207 acknowledged azender1's external-evidence comparison, itself built on the earlier
Ethereum/USDC observation bundles in this repo. Those bundles observe existing on-chain activity;
they are not a caller-created payment intent recorded before dispatch. This experiment builds that
missing bounded fixture: a fresh, caller-minted logical-action identity, committed durably
*before* any dispatch attempt, linked to a harmless external effect and read back by a fresh
observer process. It does **not** create a payment experiment, does not integrate a provider, and
does not assume anyone has agreed to run an integration - see `handoff/action-readback/FIELD-MAPPING.md`
for how it is meant to compare against a possible future "SafeAgent Control" without pretending
that comparison has been run.

## Question

Given a caller that mints an opaque action ID, canonicalizes its intended payload, and commits
both to its own store *before* letting a worker dispatch - can a reviewer, after a real process
crash, a real fresh retry, or a deliberately injected fault, independently reconstruct: what was
intended, what the worker claims happened, what the receiver actually retains, and whether the
retained evidence is even complete enough to compare the first three? And specifically: does an
effect recorded under the right action ID but a different payload get exposed as a mismatch,
rather than silently counted as the intended action succeeding?

Four questions are kept separate throughout, never collapsed into one verdict: the intended
action and its expected payload digest; the client/worker's own claim and exit state; what the
receiver's retained bytes actually record; and whether the observation was complete enough to
compare those three at all (`observation_availability`/`externally_verified`, kept distinct from
`passed` - a prediction of "we could not verify" is a real prediction a trial can match, not the
same claim as verified success).

## Experiment

**Admission.** A fresh `uuid4()` action ID is minted before any dispatch and committed, with the
canonical payload, its digest, action type, receiver reference, and an ISO-8601 UTC timestamp, to
a caller-owned SQLite table (WAL journaling, `synchronous=FULL` - read back and recorded per
trial, not assumed). The commit is confirmed durable through a *second*, independent connection
that re-reads the row before Worker A is ever spawned - a real dispatch gate via process-spawn
ordering, not a timestamp comparison. Worker A itself, on start, re-reads its own action record
through its own fresh connection rather than accepting a payload from argv.

**Effect.** Reuses crashpoint's existing out-of-process ledger (`crashpoint.ledger.daemon`/`core`)
unmodified, in its deliberately unkeyed mode so no call is silently deduplicated. The ledger does
not retain raw payload bytes or a wall-clock timestamp - only `payload_digest` and `attempt_id`
per record - so every comparison below is built from exactly what `LedgerState.dump()`/the raw
JSONL actually store, not an assumed richer schema. A real, on-disk, zero-byte baseline is
established and independently verified before every trial (reset, then a strict re-check that the
ledger reports count=0 AND that the store file is genuinely absent-then-recreated at 0 bytes).

**Observation.** A separate, freshly spawned `observe` subprocess - never the harness's own
in-process read, never a live daemon dump, never a worker's exit code - opens the raw ledger file
with its own handle after the trial's terminal boundary (`seal()`, then a post-seal `dump()` as a
race-free anchor), archives the exact bytes, hashes them, and independently re-parses the
hash-chain and per-intent attempt order itself. Every expected failure (missing file, unreadable
file, a broken chain) is caught and reported as a structured, honest event with the observer
process still exiting 0; only a genuinely unexpected internal bug is allowed to raise, and even
then the harness (after a self-review fix - see below) degrades to `NEVER_READ` rather than
losing the rest of the trial's evidence.

## Six cases, 3 trials each (18 total)

| Case | Worker A | Effect | Predicted `external_outcome` |
|---|---|---|---|
| `clean` | dispatches, completes normally | 1, matching admitted digest | `ONE_EFFECT_MATCHING` |
| `effect_before_lost_receipt` | effect confirmed via the harness's own independent ledger check, then SIGKILLed before it can write its local completion receipt | 1, matching | `ONE_EFFECT_MATCHING` (client_claim=LOST) |
| `stopped_before_effect` | SIGKILLed at a deterministic pre-dispatch barrier, before ever calling the receiver | 0, real established-empty store | `NO_EFFECT` |
| `naive_retry` | same as `effect_before_lost_receipt`, then a fresh Worker B retries with no receiver-side deduplication | 2, both matching, in real recorded order (worker-a before worker-b-retry) | `MULTIPLE_EFFECTS_MATCHING` |
| `payload_mismatch` | dispatches normally but under `--fault payload-mismatch`: reads the correct admitted payload, deliberately sends a different one | 1, digest does NOT match the admitted digest | `ONE_EFFECT_MISMATCHED` |
| `unavailable_readback` | dispatches and completes exactly like `clean`; the harness then deliberately deletes the raw ledger file before the observer runs | none observable | `INDETERMINATE` (`observation_availability=UNAVAILABLE`, `externally_verified=False`) |

`payload_mismatch` and `unavailable_readback` are explicitly labeled negative controls in their
own receipts (`injected_fault`), distinguishing a deliberate test of the schema's handling from an
accidental harness malfunction - `validate_receipt` structurally requires this field to be set for
exactly these two cases and no others.

Frozen prediction: [`13-action-readback-prediction.json`](./13-action-readback-prediction.json)
(sha256 `1881868b0cb9b5bcfdd4b034c8bff9c0d5e50c46e6f8cce77f43e9f5aa85496d`), written before the
confirmatory batch ran and embedded, byte-identical, in the evidence bundle.

Command:

```bash
./.venv/bin/python -m crashpoint.harness.action_readback --name action_readback_self_reviewed_v2
```

## Result: 18/18 matching the frozen prediction

| Case | `passing` | Observed `external_outcome` |
|---|---|---|
| `clean` | 3/3 | `ONE_EFFECT_MATCHING` |
| `effect_before_lost_receipt` | 3/3 | `ONE_EFFECT_MATCHING` |
| `stopped_before_effect` | 3/3 | `NO_EFFECT` |
| `naive_retry` | 3/3 | `MULTIPLE_EFFECTS_MATCHING` |
| `payload_mismatch` | 3/3 | `ONE_EFFECT_MISMATCHED` |
| `unavailable_readback` | 3/3 | `INDETERMINATE` |

Every field above is independently re-derivable from retained bytes: the offline verifier
(`crashpoint.harness.action_readback_verify.verify_bundle`) re-reads the admission SQLite
snapshot, re-parses the raw ledger JSONL with its own fresh implementation (not shared with the
producer), recomputes payload digests and attempt order from scratch, and cross-checks all of it
against the manifest, the per-trial `receipt.json`, the retained worker/observer stdout, and a
separate append-only `journal.jsonl` written before final aggregation - including the observer's
own raw process output, not only the harness's own derived summary of it.

That verifier reached this state only after six review passes, not one: the first was this
session's own adversarial self-review; the second was an independent review that found 12 further
real gaps the first had missed; the third and fourth were self-audits, applying that same reviewer's
method (enumerate what the code actually produces, check what is actually read back, by name) to
find 13 more; a fifth found 5 more, including one (the observer's raw stdout never being retained
at all, so `observer_pid` could only ever be checked against a summary the harness itself derived
from the same data) that changed what the harness writes, not only what the verifier checks; a
sixth found 2 more, one level more fundamental still - the schema's own pure `derive_external_outcome`/
`derive_observation_availability`/`derive_client_claim` functions were never called again to
independently re-derive and check the fields they produce, which let a fabricated matching effect
be accepted on `unavailable_readback` specifically, the one case with no ledger file retained to
cross-check against at all. Every "complete" claim before the sixth pass was, in hindsight, wrong.
The full, unminimized finding-by-finding account of all six passes - including a near-miss where a
reviewer's own test harness nearly masked a real gap, and a second near-miss in Round 6's own first
attempt at testing the recomputation gap - is in `handoff/action-readback/SELF-REVIEW.md`; exact
commands and outputs in `handoff/action-readback/TEST-RESULTS.md`.

## Limitations

Also stated in every receipt. Same-host, same-user process separation (SIGKILL across local
subprocesses on one macOS/POSIX host) - not a sandbox, a container, or an independent organization
boundary. The receiver ledger is authoritative only for this local fixture's harmless effect; it
is not a payment provider or proof about an unobserved system. A hash proves consistency with
retained bytes, not independent authorship, wall-clock truth, or tamper-proof publication. No
global exactly-once claim, no distributed fencing, no host/power-loss durability test - the
admission store's durability configuration (WAL, synchronous=FULL) is recorded per trial and the
claim is bounded to a killed *worker* process, never a killed host. A zero-effect readback at the
terminal fixture boundary is not general permission to replay an ambiguous real-world operation. A
small, deterministic, predeclared trial count (3 per cell, 18 total): no statistical
reliability-rate claim. No CrewAI, LangGraph, SafeAgent, payment provider, or SABLE integration -
this is a bounded reference fixture for a future native-vs-SafeAgent-Control field comparison
(`handoff/action-readback/FIELD-MAPPING.md`), not that comparison itself, and no such comparison
has been run.
