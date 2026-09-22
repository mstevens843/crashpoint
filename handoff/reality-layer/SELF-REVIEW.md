# Adversarial self-review

The retention defect below was independently reproduced by the reviewer and supplied to this
follow-up. Implementation and the review of the narrow correction used one agent, with no
subagents. This is correction evidence, not independent endorsement of the fix. See the
[current report](../../results/14-reality-layer-crash-readback.md),
[follow-up checks](retention-fix/checks.json), and
[new bundle](../../evidence/reality_layer/reality-layer-retention-fixed-20260922).

## Retention follow-up at published parent be91c322

The original review missed an unguarded inventory comprehension in `trial()`'s `finally` block.
An artifact read error after completed observations and process cleanup escaped before either
receipt write. Because the batch appended only a returned trial, it also lost the trial record.
The published measurements remained valid; the failure-retention promise was incomplete.

The unchanged reviewer probe was run before editing. Its exit 0 asserts the **broken** behavior,
not successful retention: control complete/one trial/receipt; EIO invalid/zero trials/no receipt;
no process leaks in either. See [paired before](retention-fix/paired-before.json). Raw local
reproducer output remains in ignored `work/`; public logs replace private path prefixes and trim line-end whitespace.

The fix guards inventory iteration, stat/classification and byte reads individually, preserving
successful hashes and specific error context. Invalid records have no successful findings.
Capture allocates and retains the attempted identity before dispatch and keeps the shared record
if a finalizer escapes. Both failed receipt destinations are reported in the writable manifest.
A narrow adjacent review found that log-close exceptions could discard otherwise complete process
results; all handles are now attempted, and actual process records survive cleanup errors.
No upstream runtime, frozen historical receipt, schema redesign or verifier guard was changed.

New permanent coverage:

- `test_capture_retains_trial_after_finalization_fault`: five real capture cases — valid control,
  post-`Owned.close()` EIO on `runtime-first.stdout`, both receipt writes failing, unexpected
  finalizer exception, and cleanup-close error after real reaping. Observations must complete;
  each case checks every spawned PID in `finally` before receipt assertions. Failed cases must
  retain one identified invalid manifest/journal record and specific error stages, exit 1 and
  report no finding. A receipt is required whenever a destination remains writable.
- `test_final_inventory_retains_readable_artifacts`: four deterministic cases — a file disappears
  between stat and read, enumeration fails after an available entry, stat fails, and a nonregular
  entry fails classification. Available hashes and a specific invalid receipt survive.
- The existing `test_real_harness_failure_cleanup[retention]` still covers primary failure with a
  writable partial receipt. It ran in the final focused and full gates.
- The new read-failure case removes required stdout from a disposable copy and requires the
  offline per-trial predicate to reject specifically with `artifact_unavailable`. The valid
  control uses that same predicate successfully. This avoids an unrelated exploratory-inventory
  rejection masking a missing-artifact check. Existing semantic mutation tests remain unchanged.

[Red/green evidence](retention-fix/red-green.json) uses the exact published harness in a
disposable source copy with the new test. Red is a missing completed manifest-trial assertion,
after completed-observation and cleanup checks; it is not a syntax/import/setup error. Restored
corrected source passes.

After freezing the final code/tests, one new nine-trial capture completed and verified in place
and after relocation. **68 focused passed; 483 full-suite passed, 22 skipped; Ruff, mypy (88
files), formatting passed.** The 22 skips are inherited absent optional fixture/environment
requirements (19 SafeAgent, 1 isolation, 2 TrueForge); all Reality Layer tests ran. No repeated
baseline campaign or upstream suite was needed. The [QA catalog](retention-fix/qa-catalog.json)
contains 14 final focused captures, separate from empirical confirmation. The
[closeout](retention-fix/closeout.json) checks source freezes, historical bytes and checkout/process
state. Retention still depends on at least one writable destination; an unwritable disk cannot
be promised a receipt. Same-host fixture limits remain unchanged.

## Original review at be91c322 (historical)

The sections below describe the original published experiment and its 59-test/474-test gates;
they are preserved as review history, not the current gate totals.

## Findings in the harness and tests

1. **Runtime selection was not stable across shells.** The first preflight selected Node
   v22.21.0 and exited 1 before any trial. Version enforcement prevented a mislabeled run.
   Measurement commands explicitly selected v22.22.1. The initial plan remains local.
2. **A helper argument collided with an event field.** Type checking found `event(name=...)`
   also receiving its event name positionally. Renamed the positional parameter before the first
   process trial. No failed trial was reinterpreted as a valid experiment.
3. **Copying directories could include untracked configuration.** Replaced directory copies
   with a pinned tracked-file allowlist, verified each copied file, and retained
   `execution-source-hashes.json` before dispatch. The upstream source is kept locally because no
   redistribution license was established. This is explicitly separate from offline evidence QA.
4. **Test-only credential handling.** Early exploratory source used a fixed disposable token.
   Replaced it with a fresh random in-memory token before confirmatory capture. Wire transcripts
   omit authentication; earlier exploratory sources remain local and are excluded from publication.
5. **Two initial guard-removal tests did not isolate their premise.** Disabling findings
   recomputation still hit aggregate recomputation; disabling symlink rejection still hit path
   confinement. Changed the mutations so the aggregate stayed truthful and the symlink target
   stayed within the trial directory. A later tightening to trial-relative confinement required
   moving that disposable symlink target into the trial as well. These failed tests are not
   counted as successful guard-removal evidence.
6. **Incomplete semantic checks.** Added exact IR operation/input binding, checks for the
   actual pre-dispatch request and transport failure on killed requests, preservation/readback
   classification of missing versus malformed subject bytes, and validation of invocation IDs
   even when the pre-effect crash produced no receiver attempt. Final summary fields are derived
   from those observations. `FAILED` is never treated as proof of zero effects.
7. **Empty runtime log diagnostic.** An empty log could reach indexing instead of a focused
   evidence error. Added `runtime_events_empty` / `recovery_events_empty` guards and a real bundle
   mutation. It now exits 1 with a diagnostic, without a traceback.
8. **Preflight/retention paths.** Source checks originally preceded initial manifest retention.
   Moved them inside the guarded run after the initial manifest. Added source-read and final
   manifest write injections. Final manifest failure retains a named partial manifest; a receipt
   write failure retains a named partial receipt. Output-medium failure is surfaced, not claimed
   as successful retention. Every owned child is closed/reaped in `finally`.
9. **Frozen-plan shape validation.** Added validation of prediction inventory and actual runtime
   types, including rejecting a Boolean effect count after superficial hashes are recomputed.
10. **Relocation control false negative.** The first local relocation assertion compared a
    resolved module path against the temporary directory alias used by macOS.
    It exited 1. Normalized both paths; the rerun confirms the verifier is loaded from the moved
    bundle, with site packages disabled and original-checkout/network/process access denied.

Capture-affecting corrections were frozen before fresh runs. Exploratory and four superseded
confirmatory bundles remain untouched locally. The final capture uses the final frozen source;
its results were not rewritten to agree with predictions. The separate relocation helper's path
normalization does not alter captured evidence or runtime instrumentation.

## Tests that actually ran

Final repository gate: **474 passed, 22 skipped**, exit 0. All **59** Reality Layer tests passed
inside that gate. The skips identify unavailable inherited SafeAgent isolated venvs (19), native
macOS isolation evidence (1), and TrueForge fixture/evidence (2). No failing check is dismissed as
pre-existing. No request was made to rerun historical empirical experiments.

The new tests include **35** semantic mutations of disposable copies of fresh retained evidence.
They recompute superficial artifact/receipt checksums so the intended guard is reached. Each
asserts its particular diagnostic and checks that the CLI did not print a traceback. Coverage:
false status/reconciliation/effect counts; changed raw payload/action binding; raw order and
attempt sequence; a deliberately synthetic two-attempt reordering; cross-trial requests;
missing/duplicate/reordered trials; wrong repetition counts; changed incremental contents;
missing raw evidence and source inventory; source-byte changes; Boolean/integer/null/JSON/version
errors; empty logs/journal; and escaping paths/symlinks, including fixed filenames. The synthetic
ordering mutation is test data, not an observed duplicate effect.

**14 single-guard red/green controls** execute a disposable verifier with exactly the named
`need` guard disabled. Bad evidence is then accepted (the negative regression turns red); restoring
that guard rejects it for the intended reason (green). Covered guards: findings recomputation,
status-to-ledger equality, effect payload binding, attempt order, incremental equality, aggregate
recomputation, source hashes, exact source inventory, symlink rejection, receiver trial binding,
case counts, trial order, runtime integer types, and manifest version. The real verifier and
historical bundles are never weakened.

**Nine actual harness failure injections** run through the CLI: source read, startup, receiver
reset, post-dispatch status transport, malformed observer output, observer nonzero exit,
incremental write, receipt retention, and final manifest retention. Every injection exits 1.
Tests inspect every PID from the retained spawn log before any assertion can exit, and verify
that no child remains. Partial raw effects stay explicitly labeled controller fallback, never
masquerading as the independent observer. The source-read failure starts no children. The
incremental/final-manifest failures retain otherwise valid trial observations but invalidate the
run's retention. Final raw QA bundles are under `evidence/reality_layer/qa-final/` and are
intentionally rejected by the confirmatory verifier.

The offline verifier succeeds both in place and after local relocation. The source-provenance
audit separately matches 77 upstream files at the pin. Upstream `npm test` exits 0. The proposed
recovered-action contract regression exits 1 with four explicit `STARTED`/false findings, as
expected for the measured baseline. That intentional red regression is kept distinct from the
successful harness/evidence tests.

## Scope review

The report/reply do not claim duplicate execution through the consumed-plan public route,
permanent inability to recover, provider readback, host/power-loss durability, or a general replay
safety guarantee. Reconciliation notes are the author's disclosed test primitive. Same-user local
process separation is not a hostile-process security boundary. Hashes check internal consistency;
a coordinated rewrite of code, observations and hashes is outside that claim.
