# 14 — Reality Layer: real process death and independent effect readback

**Completed bounded experiment, 2026-09-22.** At Reality Layer v1.8.1
[`c9d1ca86969f5567cf771ab8a0f3247770a1dfb7`](https://github.com/shimjaemandu/reality-layer/tree/c9d1ca86969f5567cf771ab8a0f3247770a1dfb7),
a real post-effect process kill left the original action queryable as `STARTED`, with
`reconciliation.required=false` / `NOT_REQUIRED` in all 3 final trials. The public
reconciliation call left those records unchanged. Retrying the original plan was rejected and
produced no additional receiver effect. This reproduces a recovery/status boundary, not a
public duplicate-execution bypass.

[Final evidence](../evidence/reality_layer/reality-layer-final-20260922) · [Frozen plan](14-reality-layer-plan.json) ·
[Reproduction](../handoff/reality-layer/REPRODUCE.md) ·
[Self-review](../handoff/reality-layer/SELF-REVIEW.md) ·
[Claim matrix](../handoff/reality-layer/CLAIMS.md) ·
[Handoff](../handoff/reality-layer/SUMMARY.md)

## Pins and invitation

Crashpoint base: `bb9cd47c4b0b02527aab7b369d17b32829cc4e20`, verified as the published
`experiment/action-readback` branch. All work is on the separate
`experiment/reality-layer-crash-readback` worktree. Node v22.22.1, Python 3.12.13; existing
`uv.lock` unchanged. The subject has no npm dependencies.

The [invitation](https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5777277483) already had
[a source-review reply](https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5781297242)
identifying the `STARTED` question. The initial and closeout remote checks found the subject's
main still at the baseline, with no newer patch to pair. Exact check metadata is retained in
[`upstream-check.json`](../handoff/reality-layer/upstream-check.json). This report is the measured
follow-up, not a claim that source inspection alone established crash behavior.

## Method and instrumentation seam

The unmodified upstream LangGraph-style client calls the real `/mcp` routes for planning,
execution, status, retry and reconciliation. The server constructs the Typed Action IR and
performs its normal policy, plan-consumption and executor/ledger logic. Before dispatch, the
controller fsyncs and rereads the caller record containing original plan/action IDs, full IR and
legacy operation/payload binding. Recovery reuses those IDs; it never creates a replacement plan.

The startup shim replaces only the exported **virtual Beta light adapter** before the normal
adapter registry loads it. That legacy seam receives device/capability/args, not an IR. The shim
checks the exact intended device/operation/args and attaches the action ID from the pre-dispatch
caller record. Production plan IR, raw ledger and public status independently bind that ID.
This is disclosed test instrumentation, not a claim that the native adapter passes an action ID.
The shim does not change ledger writes, status mappings, pending plans or recovery responses.

The harmless effect is an append to a separate local receiver. It reuses Crashpoint's
`LedgerState` chain/payload hashing under a serialized receiver lock, with an additional fsync
before acknowledgment. Each adapter invocation gets a distinct UUID attempt ID. No receiver
deduplication masks retries. A separate observer process opens the actual file and archives its
bytes at initial, before-recovery, after-retry and final sealed boundaries. The offline verifier
reparses those bytes and the raw order independently of stored counts. Empty readable evidence
is distinct from missing/unreadable evidence.

For post-effect crashes the adapter blocks after the receiver acknowledgment, a fresh observer
confirms one effect, and the controller snapshots `STARTED` before SIGKILL. For pre-effect crashes
the adapter blocks before any receiver request, a fresh observer confirms a readable empty file,
and `STARTED` is retained before SIGKILL. Recovery starts a new server against the same per-trial
subject directory. Polling waits for explicit events with monotonic deadlines; elapsed sleep is
not the crash trigger. Ordinary/unknown completion failures are deliberately injected exceptions,
not process-death trials. Corruption/deletion occur only after a known successful action; original
bytes and the mutation event are retained before any restart/status read.

## Final observations

Counts below come from the frozen inventory and verified observations: **9 valid final
trials, zero invalid final trials**, in seven cases. Each effect count is per trial.

| Case | Trials | Effects | Status before reconciliation | Reconciliation required | Reconciliation result |
|---|---:|---:|---|---|---|
| Clean control | 1 | 1 | SUCCEEDED | false | not requested |
| Truncated subject ledger | 1 | 1 | action not found | not returned | not requested |
| Deleted subject ledger | 1 | 1 | action not found | not returned | not requested |
| Ordinary post-effect exception | 1 | 1 | FAILED | false | not requested |
| Post-effect SIGKILL | 3 | 1 | STARTED | false | STARTED, unchanged |
| Pre-effect SIGKILL | 1 | 0 | STARTED | false | STARTED, unchanged |
| Explicit unknown-outcome exception | 1 | 1 | UNKNOWN | true | SUCCEEDED |

All 9 original-plan retry attempts were rejected; independent readback found
0 additional effects. The final receiver total is 8 effects across 9 trials,
with the pre-effect case contributing zero. All 9 source-informed predictions matched;
that agreement is separate from evidence validity and satisfaction of the intended contract.

- **Recovered unresolved actions:** post- and pre-effect kills produced the same retained
  `STARTED`/not-required status despite opposite external observations. The supplied reconciliation
  attempts returned `changed=false`. The proposed regression asks for recovered unresolved actions
  to require reconciliation and is intentionally red on this baseline (four failing assertions).
  Whether to promote recovered `STARTED` to `UNKNOWN` or use another recovery policy is an author
  decision; no runtime redesign or speculative patch is included.
- **Adapter completion:** an ordinary exception after the receiver commit produced `FAILED`;
  setting the existing `executionOutcomeUnknown=true` signal produced `UNKNOWN`. This is an
  adapter integration boundary: a reported ordinary error does not prove external non-execution.
  The genuinely generated `UNKNOWN` reconciled to `SUCCEEDED` using a caller-supplied note
  containing the retained observer digest/count, with no added effect. The
  [contract](https://github.com/shimjaemandu/reality-layer/blob/c9d1ca86969f5567cf771ab8a0f3247770a1dfb7/docs/EXTERNAL-RUNTIME.md)
  already discloses this note-based test primitive; the runtime does not independently verify it.
- **Subject storage loss:** both truncated and deleted ledgers returned an ordinary missing-action
  response while receiver evidence remained one effect. Corrupt bytes remained malformed after
  query; the missing file was recreated as an empty array. These cases are not evidence of zero
  effects. Accidentally missing archived evidence instead invalidates the offline verification.

## Evidence and review

Run ID: `f5c50fd0-f821-45ed-9c57-8920d673d4e8`. Manifest SHA-256:
`48d8033761025e29c6da02ddbe12ca1f59de0cbf487c0138b45a0bf7f6a71b86`.
All trial receipts equal their incremental journal records and manifest records in full.
[`results-derived.json`](../handoff/reality-layer/results-derived.json) retains derived counts.

The final repository gate passed **474 tests**, with **22 explicitly reported fixture-related
skips**. All **59** new tests passed, including 35 semantic evidence mutations, 14 individual
verifier-guard removal/restoration controls, and nine real harness failure-path injections.
Ruff, mypy (88 files), scoped formatting, both Node syntax checks, source audit and upstream
`npm test` passed. The adopted-contract assertion separately exits 1 for the baseline's four
unresolved crash outcomes. See exact commands/statuses in the handoff.

The bundle verifies after local relocation using its retained verifier with site packages
disabled, no Node in PATH, and audit-hook denials for network/process operations and original
checkout reads. Upstream source is omitted because redistribution permission was not established;
a separate retrieval audit matched all 77 tracked files to the baseline pin and declared hashes.
Offline evidence verification does **not** verify an omitted runtime source copy.

Development captures remain separate: 10 exploratory trials, 36 superseded confirmatory trials,
and one initial Node-version preflight rejection with no trial. Across review and final gates,
25 deliberate harness-failure runs produced 23 trial records (two source-read failures launched
none); these are QA negative controls, not additional subject findings. The final QA rerun contains
nine rejected runs. Earlier captures are preserved locally and excluded from the publication
file list. The [run catalog](../handoff/reality-layer/run-catalog.json) enumerates them without
merging their counts into the final nine.

## Limits

Same-host, same-user local process separation; no hostile-process isolation, independent
organization, real provider/device, concurrent dispatch, multi-framework integration, distributed
fencing, or host/power-loss test. fsync acknowledgment is recorded but is not a tested power-loss
guarantee. Hashes establish consistency, not authorship, trustworthy wall-clock time or immunity
to coordinated rewriting. No reliability-rate or general exactly-once claim. A readable empty
fixture store is not permission to replay an ambiguous real-world action. A lost/consumed plan
handle is not a proof about external effects. Direct executor replay and other source concerns
are untested here. No CrewAI, LangGraph or SafeAgent code was changed; no comment, commit or push
was performed by this task.
