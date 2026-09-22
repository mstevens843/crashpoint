# Adversarial self-review

One engineer performed implementation and review; no subagents or external reviewers were used.
This document records corrections, not independent endorsement. See [claim matrix](CLAIMS.md),
[final checks](checks.json), [run catalog](run-catalog.json), and [final bundle](../../evidence/reality_layer/reality-layer-final-20260922).

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
