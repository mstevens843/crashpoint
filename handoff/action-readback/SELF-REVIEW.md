# Self-review: pre-dispatch action identity / external readback

Five rounds. Round 1 (below, unchanged from the original pass) was my own adversarial review,
performed before any evidence was first reported final. It was not sufficient: an independent
review (Codex) ran its own mutation script against the same bundle afterward and found 12 real
gaps - 9 silently accepted, 3 crashed instead of a structured diagnostic - none of which Round 1
had caught. **Round 2** covers what Round 1 missed, why, and the fixes. Asked afterward to review
that completion myself, the same way Codex had, **Round 3** found 11 further real gaps of the same
general kind by systematically enumerating every event the runtime emits and every schema
field/database column, then checking which were actually cross-checked - a method Round 1 should
have used from the start instead of generalizing from a prior, different experiment's findings.
Round 3 also documents a mistake in its own first pass: three of the eleven were nearly
misreported as "already caught" because the test harness itself had not kept `journal.jsonl`
synchronized, so an incidental journal-mismatch was masking the absence of a real, field-specific
check. Asked for one further sweep, **Round 4** moved up a level - from per-trial receipt fields to
manifest-level summary fields (`status`, `cases`) - and found 2 more real gaps by the same
enumerate-then-grep method. Asked again, **Round 5** found 5 more: 4 by auditing `validate_receipt`
itself (not just the offline verifier) for internal-consistency rules the same exhaustive way, and
one deeper structural finding - the observer's raw stdout was never retained at all, so
`observer_pid` could only ever be checked against a summary the harness itself derived from the
same in-memory data, not genuinely independent evidence. Fixing that changed what the harness
writes, not only what the verifier checks, so the evidence bundle was recaptured
(`action_readback_self_reviewed_v2`; the prior `action_readback_self_reviewed` is preserved,
superseded, unreferenced). The bundle and test counts referenced throughout the rest of this
handoff (`TEST-RESULTS.md`, `SUMMARY.md`, `CLAIM-MATRIX.md`) are the POST-Round-5 state.

## Round 1 (original pass)

Mandatory adversarial review performed in the same session as the build, before any evidence was
reported as final, per the assignment's explicit requirement that this work is not optional
follow-up. Three parts, in order: (A) live fault injection into the real production entry points
(`run_trial()`/`run()`), (B) one-mutation-at-a-time offline-verifier tests against the real
recorded bundle, (C) proof that each high-risk check is actually what makes its test pass, by
disabling it in a disposable copy and re-running the same mutation.

## Part A: protocol and cleanup faults (live, real entry points)

All of the following are permanent tests in `tests/test_action_readback.py`, each patching one
real call and asserting on the actual `run_trial()`/`run()` outcome - not a helper-only
approximation.

| Injection point | Test | Assertion |
|---|---|---|
| Admission readback confirmation fails (the dispatch gate itself) | `test_injected_admission_readback_confirmation_failure_is_a_clean_gate_rejection` | Worker A is never spawned (`worker_a_pid is None`); honest failure receipt retained |
| Receiver reset/baseline establishment leaves a stale file | `test_injected_receiver_baseline_establishment_failure_still_retains_a_receipt` | `RuntimeError`, `receiver_baseline_established=False` in the retained receipt |
| Worker A launch fails (`Popen` raises) | `test_injected_worker_launch_failure_leaks_no_process_and_retains_a_receipt` | No leaked process; honest failure receipt; `worker_a_pid is None` |
| Worker B (retry) launch fails - sibling case, not named in the prompt | `test_injected_worker_b_launch_failure_in_naive_retry_leaks_no_process` | Worker A's own already-completed lifecycle is retained correctly; no leak |
| Observer subprocess itself cannot be launched (`subprocess.run` raises) | `test_injected_observer_launch_failure_is_never_read_not_a_crash` | Trial completes (not aborted); `NEVER_READ`/`UNAVAILABLE`/`externally_verified=False`; `passed=False` since this diverges from the `clean` prediction |
| Journal append fails for one trial | `test_injected_journal_append_failure_records_execution_failure_not_a_crash` | Batch continues; `status=INCOMPLETE`; the trial's own (successful) result is still in `manifest.trials`; failure recorded in `execution_failures`, not silently dropped |
| Manifest finalization (`manifest.json` write) fails | `test_injected_manifest_write_failure_is_fatal_not_silently_continued` | Raises `RuntimeError` - a distinct, intentional choice: unlike a journal failure, primary-storage failure is treated as "retention itself is impossible," not degraded gracefully (see rationale below) |
| A trial fails mid-batch | `test_run_level_aggregates_a_trial_failure_without_crashing_the_batch` | Genuine `run()`-level test (patches `run_trial`, calls real `run()`): `status=INCOMPLETE`, `all_agree=False`, correct `trial_count`/`expected_trial_count`, one `execution_failures` entry |

**Why manifest-write failure is fatal but journal-write failure is not:** the assignment asks to
"distinguish simulated global storage failure, where retention itself is impossible, from ordinary
observation failure." `journal.jsonl` is a secondary artifact; if it alone fails to write while
`manifest.json` (the primary, most-relied-upon record) keeps writing successfully, storage is
evidently still partially usable, so the batch continues and the gap is recorded honestly. A
`manifest.json` write failure is the strongest available signal that storage itself may no longer
be usable at all - and there is no other reliable place to durably record even that failure - so
it is surfaced as a hard error rather than a silently-degraded run.

Also exercised directly (unit-level, no live subprocess needed):
`test_kill_and_reap_process_already_exited_before_kill`,
`test_reap_owned_is_idempotent_and_reports_no_problems_for_dead_processes`,
`test_wait_for_barrier_bounded_timeout_on_a_process_that_never_emits_it`.

## Part B: one-mutation-at-a-time offline verifier tests

Each test in `tests/test_action_readback_verify.py` starts from the real recorded bundle, mutates
exactly one thing, re-syncs the redundant `receipt.json` copy and recomputes the outer manifest
hash (so an unrelated checksum failure cannot hide a missing semantic check), and asserts the
*specific* rejection reason - not merely a nonzero problem count. 30 tests total; see
`TEST-RESULTS.md` for the full list and exact command/output. Categories covered, matching the
assignment's checklist: omitted/duplicated trials, wrong declared counts, mixed run IDs; false
admission/protocol booleans, changed payload/action ID, effect-identity substituted for
pre-dispatch identity; missing/unrelated effects, reversed raw attempt order, changed effect
digest, fabricated zero counts; missing/truncated/unreadable ledger, malformed admission SQLite,
missing required worker events, a nonzero observer exit trusted as a full observation; false
`externally_verified`, malformed numeric types; omitted required source/lock bindings, altered
prediction hash/content, absolute-path and symlink escapes; journal content/coverage mismatches.
Also: two distinct admitted actions with identical payloads are confirmed NOT merged
(`test_mutation_two_distinct_admitted_actions_with_identical_payload_are_not_merged`), both as a
verifier-level mutation test and as a property of the real recorded batch
(`test_checked_in_evidence_two_admitted_clean_trials_are_distinct_actions_same_payload` in
`test_action_readback.py`).

## Part C: proving the checks are load-bearing

For six of the highest-risk checks, the exact validation logic was removed in a disposable copy of
the module (never the real source file - see `verify_ProdName_disposable_copy.py`-style files kept
only in the session scratchpad, never committed), loaded under `crashpoint.harness._disposable_*`
so its own relative imports resolve against the real, unmodified `crashpoint` package, and the
same mutation used by the real permanent test was re-run against it.

| Check disabled | Same mutation re-applied | Result with the check removed |
|---|---|---|
| Raw ledger attempt-order/lineage check | Reversed `naive_retry` ledger records | Reversal no longer detected |
| Cross-trial admission-contamination check | A foreign `completions` row inserted into another trial's own admission.sqlite | Contamination no longer detected |
| Nonzero-observer-exit-degrades-availability check | `observer_exit_status=1` on an otherwise-FULL trial | Nonzero exit no longer flagged |
| `digests_match_admission` recompute check | Admission payload digest changed to `f`*64 | Digest mismatch no longer flagged |
| Receipt-level killed/exit-status-vs-case structural check | `worker_a_killed=False` on a `stopped_before_effect` trial | Contradiction no longer flagged |
| Harness real-empty-baseline verification | A mock `reset()` that leaves a stale file behind, exactly as the live test injects | No `RuntimeError` - the stale file goes undetected |

All six: with the check present (the real, undisabled code), every permanent test passes; with it
removed, the exact same input is silently accepted. The real source files were never edited during
this process (only disposable copies were), and the full real test suite (75 tests across the
three `test_action_readback*.py` files) was re-run afterward and still passes, confirming the real
tree was never touched.

## Production bugs found and fixed during this process

None of these were present in the assignment; each was discovered by actually running the checks
above against the real code, not assumed.

| # | Bug | Root cause | Fix |
|---|---|---|---|
| 1 | The happy-path trial receipt was never written to `receipt.json` on disk - only embedded in the manifest and (for failures) the separate failure-record path | `run_trial`'s success-path `return _finalize_trial(...)` never called `.write_text()`; only `_write_failure_record` did | The success path now writes `receipt.json` before returning, matching the failure path |
| 2 | `unavailable_readback` (Worker A completes normally, exit 0, never killed) was rejected by `validate_receipt`'s protocol contract, which grouped every case except `clean`/`payload_mismatch` into "must be killed" | The case grouping did not account for a fault injected by the *harness* after a normal worker lifecycle, rather than by killing the worker | `unavailable_readback` added to the not-killed case group |
| 3 | `validate_receipt` required `passed=False` whenever `effect_count` is null - which is exactly `unavailable_readback`'s *designed* state, and it is designed to be able to pass (match its own prediction of "unverified") | The rule conflated "achieved a successful effect" with "agreed with the prediction" | Replaced with the correct invariant: `effect_count` null forbids `externally_verified=True`, not `passed=True` |
| 4 | The cross-trial-contamination check queried `completions WHERE action_id = ?` (this trial's own ID) and then asked whether any returned row belonged to a *different* action_id - which can never be true, since the WHERE clause already excludes them | Reused `read_completion_rows` (correctly scoped for its own, different purpose: reading a trial's own completions) for a check that needed the opposite query | Added `read_foreign_completion_rows` (`WHERE action_id != ?`) and switched the check to use it |
| 5 | `_run_observer` did not catch `subprocess.run` itself raising (only "ran but produced no event") - an observer launch failure crashed the whole trial via the outer exception handler, discarding evidence the trial had already genuinely captured | Missing try/except around the `subprocess.run` call | Wrapped in `try/except (OSError, SubprocessError)`, degrading to the same `NEVER_READ` fallback shape used for "ran but no event" |
| 6 (proactive, not test-discovered) | A journal-append failure for one trial would have propagated uncaught and crashed the whole batch, per the assignment's explicit list of required injection points | Not yet implemented when the assignment's checklist was reviewed | Wrapped in `try/except OSError`, recorded as an `execution_failures` entry; batch continues (see Part A rationale above for why manifest-write failure is instead treated as fatal) |

All six are exercised by the tests listed above. The evidence bundle referenced everywhere in this
handoff (`action_readback_self_reviewed`) was captured *after* every fix in this table, so its
embedded provenance sources reflect the code that actually produced it - not a stale snapshot from
before the review. The earlier `action_readback_corrected` bundle (captured after bugs 1-3 but
before 4-6, none of which affect nominal-case receipt content) is preserved, unreferenced, rather
than deleted.

## Round 2: an independent review (Codex) found what Round 1 missed

Round 1 declared the verifier complete. It was not. Codex ran its own mutation script
(`check_bundle.py`, kept only in a local review directory, not committed) against the exact same
`action_readback_self_reviewed` bundle and found 12 real gaps: 9 mutations the verifier silently
accepted, and 3 that crashed the verifier with an unhandled Python exception instead of a
structured diagnostic - which the assignment explicitly required ("Reject... malformed JSON/SQLite
... with structured diagnostics rather than tracebacks"). Every one of the 12 was independently
reproduced against the real, unmodified verifier before any fix was written (confirmed via a
locally-adapted copy of Codex's own script - one function needed adjusting because it assumed raw,
unprefixed JSON lines in `worker_a.stdout`, when this codebase's actual convention, inherited from
the TTL experiment, prefixes every event line with `CRASHPOINT_ACTION_READBACK `; the mutation's
*intent* was unaffected by that fix).

### Why Round 1 missed these

Looking at the pattern honestly, rather than case by case: Round 1's Part B mutation coverage was
built by generalizing from the TTL experiment's own prior review findings (attempt order, embedded
source inventory, declared counts, missing worker events) - a reasonable starting point, but it
meant Round 1 tested "the kinds of things a bundle-tampering reviewer found in a DIFFERENT
experiment last time," not "every field this experiment's OWN schema actually declares." Four
concrete fields the schema defines - `effect_payload_digests`, `worker_a_local_receipt`/
`worker_b_local_receipt`, `receiver_seal_dump_count`/`receiver_seal_dump_head` - were written by
the harness and read into `validate_receipt`'s structural checks, but never cross-checked by the
offline verifier against anything independently re-derivable at all. Two bundle-root files
(`prediction.json`, `journal.jsonl`) were confined and symlink-checked for every per-trial file
during Round 1, but the two files living directly at the bundle root were never put through the
same treatment - an asymmetry Round 1's own build notes do not explain, and was simply an
oversight. And three separate "trust the shape of untrusted JSON without checking it" bugs
(`observer_report.json`, `cells_summary[case]`, raw ledger bytes assumed to be valid UTF-8) slipped
through despite this exact class of bug (crash instead of a structured diagnostic) being a named,
explicit assignment requirement Round 1 believed it had satisfied.

### The 12 findings, fixes, and permanent tests

| # | Finding | Fix (in `action_readback_verify.py` unless noted) | Permanent test |
|---|---|---|---|
| 1 | `worker_a_local_receipt` claims a completion, but `admission.sqlite`'s `completions` table has none for that `attempt_id` (deleted) | New cross-check: for both `worker_a`/`worker_b`, the claimed local receipt (or its deliberate absence) is checked against `read_completion_rows` | `test_mutation_deleted_admission_completion_row` |
| 2 | The inverse: `worker_a_local_receipt=None`, but a real completion row exists | Same cross-check, other direction | `test_mutation_false_local_receipt_omits_a_real_completion` |
| 3 | `worker_a.stdout`'s own `admission_read` event is falsified to a different `action_id`/digest - only the event's *existence* was checked, never its content | Added content comparison (`action_id`, `admission_payload_digest`) against the trial's own fields | `test_mutation_wrong_admission_read_event_identity` |
| 4 | The receipt's own `effect_payload_digests` list is falsified directly | Added a direct list-equality check against the raw ledger's own `effect_digests` (previously only the derived `digests_match_admission` boolean was recomputed, not the raw list itself) | `test_mutation_false_effect_payload_digests` |
| 5 | `receiver_seal_dump_count`/`receiver_seal_dump_head` (the post-seal terminal anchor) are falsified | Added `final_head`/`total_records` fields to the independent `LedgerObservation` re-parse and cross-checked both against the trial's claims | `test_mutation_false_terminal_anchor` |
| 6 | `journal.jsonl` has two entries for the same `trial_id` (a dict-keyed parse silently let the second overwrite the first) | Replaced the dict-keyed parse with an explicit `Counter` over every trial_id seen; a count > 1 is now its own problem | `test_mutation_duplicate_journal_trial_id` |
| 7 | `journal.jsonl` has an entry for a `trial_id` that was never run at all | Added the reverse direction: every journal entry's `trial_id` must appear in `manifest.trials`, not only the direction already checked (every manifest trial has a journal entry) | `test_mutation_extra_journal_trial_id_not_in_manifest` |
| 8 | `prediction.json` (bundle root) replaced with a symlink to a file outside the bundle | Bundle-root fixed files were never path-confined at all (only per-trial files were); now routed through the same `_confined_file` used for trial files | `test_mutation_prediction_json_symlink_escape` |
| 9 | `journal.jsonl` (bundle root), same class of gap | Same fix | `test_mutation_journal_jsonl_symlink_escape` |
| 10 | `observer_report.json` is a JSON array, not an object - crashed with `AttributeError: 'list' object has no attribute 'get'` | Added an `isinstance(obs_report, dict)` guard before any `.get()` call | `test_malformed_observer_report_array_is_not_a_crash` |
| 11 | `manifest.cells_summary[case]` is a JSON array - same crash class | Added an `isinstance(declared_cell, dict)` guard | `test_malformed_cells_summary_entry_array_is_not_a_crash` |
| 12 | Raw ledger bytes are not valid UTF-8 (`b"\xff"`) - crashed with `UnicodeDecodeError` | Wrapped the decode in `try/except UnicodeDecodeError`, returning a structured `TAMPERED` status instead of raising; the same fix was also applied to the in-trial observer's own parser (`action_readback_runtime.py`) for symmetry, even though the harness's existing observer-launch-failure fallback (Round 1, finding 5) already caught a crashed observer subprocess gracefully - an honest, specific reason is better than an accidental safety net | `test_malformed_ledger_invalid_utf8_is_not_a_crash` |

Reproduced against the real (fixed) verifier via the same adapted Codex script: all 12 mutations
now rejected with specific reasons, zero crashes, script exit code 0 (its own
`assert all(...accepted is False...)` passes). The real bundle
(`action_readback_self_reviewed`) was re-verified against every new check and still returns zero
problems - these are new checks catching genuinely absent bugs, not false positives against valid
data, so no re-capture of the evidence bundle was needed this round (unlike Round 1, none of these
12 fixes changed what the harness *writes*, only what the verifier *checks*).

### Part C, round 2: proving the three highest-risk new checks are load-bearing

Same discipline as Round 1 - disable the exact check in a disposable copy, never the real source,
re-run the same mutation:

| Check disabled | Mutation re-applied | Result with the check removed |
|---|---|---|
| Local-receipt-vs-completions-table cross-check | `worker_a_local_receipt=None` while a real completion row exists | No longer flagged |
| Journal duplicate-trial-id detection | A byte-identical duplicate journal line appended | No longer flagged |
| Bundle-root file confinement (`prediction.json`/`journal.jsonl`) | Symlink to a file outside the bundle | No longer flagged |

All three: check present -> mutation caught; check removed -> mutation silently accepted. The real
source files were never edited (only disposable copies); the real test suite (87 tests across the
three files, up from 75) was re-run afterward and still passes, confirming the real tree was never
touched by this proof process either.

### What Round 2 alone still meant for "publication readiness"

Round 1's own claim of a complete, load-bearing verifier was wrong, and this document said so
plainly rather than reframing it. What changed process-wise for Round 2: the same disable-and-prove
discipline was applied, this time to checks found by someone else. That was still not the end of
it - see Round 3 below, performed on request, without a further external mutation script to work
from.

## Round 3: a self-audit, using Codex's own method rather than waiting to be shown more gaps

Asked to review this work the same adversarial way Codex had, the honest starting position was
that Round 2's fixes might themselves be incomplete in the same way Round 1's were - so this round
did not re-read the code looking for reasons the checks were fine. It enumerated every event
`action_readback_runtime.py` actually emits (`grep`, not memory) and every column the
`admissions`/`completions` SQLite tables actually have, then grepped the verifier for each one by
name to see which were genuinely referenced versus assumed. That method - "what does the producer
actually write, and is each piece of it independently read back" - is exactly what should have
built Round 1's coverage in the first place, instead of generalizing from what a reviewer happened
to find in the unrelated TTL experiment.

### The 11 findings, fixes, and permanent tests

| # | Finding | Fix (`action_readback_verify.py`) | Permanent test |
|---|---|---|---|
| 1 | `worker_b.stdout`'s own `worker_started.pid` was never cross-checked against `worker_b_pid` - only `worker_a`'s was | Added the symmetric check for `worker_b` | `test_mutation_worker_b_started_pid_falsified` |
| 2 | `worker_b.stdout`'s `admission_read` event (existence or content) was never checked at all - only `worker_a`'s was | Added the same existence-and-content check used for `worker_a`, applied to `worker_b` | `test_mutation_worker_b_admission_read_dropped` |
| 3 | `worker_a.stdout`'s own `effect_ack.dispatch_payload_digest` (the worker's OWN claim of what it sent) was never cross-checked against `worker_a_dispatch_payload_digest` - only the RECEIVER-side ledger digest was independently verified, never the CLIENT-side self-report layer the schema's own docstring says is kept separate on purpose | Added the cross-check for `worker_a`, and (using `trial.admission_payload_digest`, since worker_b never has an injected fault) for `worker_b`'s own `effect_ack` too | `test_mutation_effect_ack_dispatch_digest_falsified` |
| 4 | `observer_report.json`'s own retained `_observer_pid` was never cross-checked against the receipt's `observer_pid` field | Added the cross-check | `test_mutation_observer_pid_falsified` |
| 5 | A trial's own `crashpoint_commit` was never cross-checked against `manifest.crashpoint_commit` - a mixed-provenance run (some trials from a different commit than the manifest declares) would go unnoticed, unlike the equivalent `run_id` check that already existed | Added the check | `test_mutation_trial_crashpoint_commit_disagrees_with_manifest` |
| 6 | `admission.sqlite`'s `receiver_ref` column was never cross-checked against the receipt's `receiver_ref` | Added the check, alongside the existing `run_id`/`action_type` admission-row checks | `test_mutation_admission_receiver_ref_falsified` |
| 7 | `admission.sqlite`'s `admitted_at_utc` column was never cross-checked against the receipt's `admitted_at_utc` | Added the check | `test_mutation_admission_timestamp_falsified` |
| 8 | `worker_a_barrier_observed` (the boolean) was never checked against whether a barrier event actually exists - only "if killed, some barrier event must exist" was checked, never the field's own truthfulness in either direction | Added a direct boolean-vs-event-presence check | `test_mutation_barrier_observed_flag_disagrees_with_retained_event` |
| 9 | `admission.sqlite`'s `trial_id` column was never cross-checked | Added the check | `test_mutation_admission_trial_id_column_falsified` |
| 10 | `admission.sqlite`'s `case_name` column was never cross-checked against the receipt's `case` | Added the check | `test_mutation_admission_case_name_column_falsified` |
| 11 | The completions-table cross-check added in Round 2 only compared the `outcome` column, not the full row - a falsified `completed_at_utc` with a genuine `outcome` would have passed | Broadened to a full dict comparison (`claimed != row`) | `test_mutation_completion_row_full_content_falsified` |

All 11 were confirmed genuinely accepted against the real, unmodified (post-Round-2) verifier
before any fix was written, using a scratch mutation script (kept only in the session scratchpad,
never committed) - not assumed from the static-analysis reasoning above. After fixing all 11, the
real bundle (`action_readback_self_reviewed`) was re-verified and still returns zero problems - no
false positives - and the real test suite, still passing throughout.

### The near-miss in this round's own first pass

Before fixing anything, the first run of the scratch mutation script reported findings 4, 5, and 8
as already caught. They were not: the script's own `synchronize()` helper updated each mutated
trial's `receipt.json` but never `journal.jsonl`, so every mutation incidentally triggered the
*existing* "journal.jsonl's record differs from manifest.trials" problem regardless of whether
there was any check specific to the mutated field. Re-running with `journal.jsonl` also kept in
sync (matching how Codex's own Round 2 script did it) showed all three were, in fact, accepted.
This is recorded here because it is exactly the kind of near-self-deception a reviewer has to
watch for: an incidental problem masking the absence of the one actually being tested for.

### Part C, round 3: proving three representative new checks are load-bearing

| Check disabled | Mutation re-applied | Result with the check removed |
|---|---|---|
| `worker_b.stdout` `admission_read` check | The event dropped from `worker_b.stdout` | No longer flagged |
| `worker_a.stdout` `effect_ack` digest check | `dispatch_payload_digest` falsified in the retained event | No longer flagged |
| Full-row completions comparison (narrowed back to `outcome`-only) | `completed_at_utc` falsified while `outcome` stays genuine | No longer flagged |

Real source untouched (disposable copies only); real test suite (98 tests across the three files,
up from 87) re-run afterward and still passes.

### What Round 3 alone still meant for "publication readiness"

Two external-or-self-initiated review passes past Round 1 both found real, structurally similar
gaps: fields and events the schema/runtime actually define that the verifier had simply never
been pointed at. Round 3's method (enumerate what is actually produced; check what is actually
read back, by name, not by assumption) is the one that should be applied to any FUTURE change to
this schema, not treated as a one-time cleanup - which is exactly what Round 4 then did, one level
up the document.

## Round 4: one more sweep, moved to the manifest level

Round 3 audited per-trial receipt fields and per-action database columns exhaustively. Asked for
one more sweep, this round applied the same method one layer up: which fields does
`_build_manifest` (`action_readback.py`) actually write into the run-level manifest, and which of
those does the verifier actually check by name, independent of the per-trial checks already
covering `trials`/`cells_summary`/`trial_count`/etc.

Two real gaps, both grep-confirmed absent (`manifest.get("status")`/`manifest.get("cases")` had
zero hits in the verifier) before being tested, then empirically confirmed accepted:

| # | Finding | Fix | Permanent test |
|---|---|---|---|
| 1 | `manifest.status="INCOMPLETE"` on a genuinely complete, all-passing run was never checked - only the fabricated-COMPLETE-despite-failures direction was caught, and only indirectly, via the pre-existing `all_agree` recompute (a coincidence of that check's own logic, not a dedicated `status` check) | Added a direct recompute: `status` must equal `"COMPLETE" if not execution_failures else "INCOMPLETE"`, matching the harness's own literal formula in `action_readback.py`'s `run()` | `test_mutation_status_incomplete_when_actually_complete` |
| 2 | `manifest.cases` (omitting a real case, or listing a fake one) was never cross-checked against anything - the per-cell accounting used the canonical `CASES` constant directly, but never compared it to what the manifest itself declares | Added `set(manifest.cases) == set(CASES)` | `test_mutation_cases_list_omits_a_real_case`, `test_mutation_cases_list_adds_a_fake_case` |

Also tested in this round and confirmed to be genuine non-findings, not gaps: a `bool` disguised
as `worker_a_pid` (`isinstance(True, int)` is `True` in Python - a classic type-confusion vector)
and a negative `receiver_seal_dump_count` were both already caught, because every cross-check
added in Rounds 2-3 uses plain equality (`!=`) against an independently recomputed value, which
Python evaluates safely regardless of the left-hand type - there is no arithmetic operation
anywhere in this codebase's cross-checks that a wrong-typed value could crash. `validate_receipt`
still has no NAMED type guard for these specific numeric fields (only `effect_count` gets that
explicit treatment), which is a real gap in *documented, explicit* rigor relative to this
codebase's own established convention - but not a case of anything being silently *accepted* that
should be rejected, so it was not forced into a fix for its own sake.

Part-C proof (the `status` check, disabled in a disposable copy, same mutation re-applied):
mutation no longer flagged with the check removed, confirming it is load-bearing and not
redundant with the `all_agree` check that happens to also catch the OTHER direction.

Real bundle re-verified clean after both fixes; real test suite 393 passed (up from 390), full
repository suite unaffected in count of unrelated tests, ruff and mypy clean, no owned processes.

### What this means for "publication readiness"

Four rounds in, the pattern was consistent: every time this document's own "complete" claim was
tested against a fresh, systematic enumeration, it found more of the same class of gap. Round 5
(below) both confirmed that pattern once more and, for the first time, found a gap one level
deeper than "the verifier never checks field X" - the evidence chain itself was incomplete for one
field, not just the checking of it.

## Round 5: one more sweep, at the internal-consistency and evidence-completeness layers

Two different angles, since Round 3/4's "enumerate fields/events/columns, grep the verifier"
method had already been applied to per-trial fields, DB columns, and manifest summary fields.

**Angle 1: `validate_receipt` itself, not just the offline verifier's file-based cross-checks.**
Does the schema enforce its own internal coherence regardless of which file evidence exists?
`worker_b_used` was checked against the case (must be True only for `naive_retry`), but nothing
checked the converse: that `worker_b_used=False` implies `worker_b_pid`/`worker_b_attempt_id`/
`worker_b_exit_status`/`worker_b_local_receipt` are all null. A `clean` trial (no `worker_b.stdout`
file exists for that case at all, so the file-based checks never even run) could fabricate any of
these four fields with nothing catching it, at either layer - `validate_receipt` structurally, or
`verify_bundle` end to end.

| # | Finding | Fix (`action_readback_receipt.py`) | Permanent test |
|---|---|---|---|
| 1-4 | `worker_b_used=False` did not imply `worker_b_pid`/`worker_b_attempt_id`/`worker_b_exit_status`/`worker_b_local_receipt` are null - checked at neither the schema layer nor the file-evidence layer | Added an unconditional (not gated on case or pass/fail) internal-consistency rule in `validate_receipt` | `test_validate_receipt_rejects_fabricated_worker_b_pid_when_unused` and three siblings for the other three fields |

All 4 confirmed accepted at BOTH `validate_receipt` and `verify_bundle` before the fix, using a
script that checked both layers explicitly rather than assuming a `verify_bundle` pass meant
`validate_receipt` also passed (or vice versa).

**Angle 2: is every retained evidence file itself as independently re-derivable as it should be?**
`worker_a.stdout`/`worker_b.stdout` are the workers' own raw process output, genuinely independent
of what the harness computes from them. `observer_report.json` is different in kind: it is the
harness's OWN dict, built from data the SAME harness process already parsed - checking a receipt
field against `observer_report.json` is checking one artifact the harness wrote against another
artifact the same harness wrote from the same underlying facts, not independent confirmation. This
was true of the `observer_pid` cross-check added in Round 3.

| # | Finding | Fix | Permanent test |
|---|---|---|---|
| 5 | `observer_pid` was only ever cross-checked against `observer_report.json`, which is derived, not raw, evidence - `_run_observer` (`action_readback.py`) never retained the observer subprocess's own raw stdout/stderr at all, unlike both workers | The observer's raw stdout/stderr are now retained per trial (`observer.stdout`/`observer.stderr`, added to the always-required file set); the verifier now independently re-parses `observer.stdout` for its own `observer_started`/`readback_result` events and cross-checks `observer_pid` and `observer_raw_ledger_state` against THAT, replacing the circular `observer_report.json`-based check entirely | `test_verify_bundle_requires_observer_stdout_file`, `test_mutation_observer_stdout_missing_readback_result_event`, `test_mutation_observer_stdout_raw_state_disagrees_with_receipt`, and the updated `test_mutation_observer_pid_falsified` |

This is the first fix across all five rounds that changed what the **harness** writes, not only
what the verifier checks - so the evidence bundle was recaptured end to end
(`action_readback_self_reviewed_v2`, 18/18 matching the frozen prediction, verified clean in place
and after relocation) rather than patched in place. The prior bundle
(`action_readback_self_reviewed`) is preserved, byte-for-byte, as a superseded historical artifact
- it predates this fix and would correctly fail the current, stricter required-file check (it has
no `observer.stdout`), the same "preserve old evidence, don't retroactively edit it, version
forward" discipline used throughout this repo's other experiments.

Part-C proof (the new `observer.stdout`-based `observer_pid` check, disabled in a disposable
copy): the same falsified-`observer_pid` mutation that Round 3's check caught is, with this check
removed, accepted with **zero** problems reported - not degraded, not caught by some other check,
completely silent. That is the strongest single confirmation in this document that a check was
truly load-bearing rather than redundant with something else.

Real test suite after all 5 fixes: 108 passed (up from 101). Full repository suite: 400 passed, 31
skipped (unchanged skip set). Ruff clean. Mypy clean in every `action_readback*` file. Real bundle
(`action_readback_self_reviewed_v2`) verified clean in place and after relocation. No owned
processes remaining.

### What this means for "publication readiness"

Five rounds in, the pattern remains consistent, and Round 5 sharpened it: it is not enough to ask
"does the verifier check field X" - the prior question is "does independent, raw evidence for X
even exist to check against." The practice this document recommends going forward, updated:
for any future change to this schema, enumerate what the new code actually writes (fields, events,
columns, AND which of those are backed by genuinely independent raw evidence versus a
harness-derived summary), and grep the verifier for each one by name before trusting that "the
verifier already checks that."

## Round 6: `validate_receipt` recomputed `externally_verified` from three fields it never itself recomputed

Asked for one more sweep, this round started from a more fundamental question than Rounds 1-5 had
asked: `derive_external_outcome`/`derive_observation_availability`/`derive_client_claim` are pure
functions that `build_receipt` calls to PRODUCE `observed_result`, but grep confirmed they are
never called anywhere else - `validate_receipt` only checks that `observed_result`'s three
underlying fields are *members of a valid enum*, and recomputes `externally_verified` from THOSE
three fields, but never recomputes the three fields themselves from the raw evidence
(`effect_count`, `digests_match_admission`, `observer_raw_ledger_state`, `observer_exit_status`,
`worker_a_local_receipt`/`worker_b_local_receipt`, `worker_a_killed`) that the pure functions
actually take as input. This is the same class of gap as every prior round (a thing the schema
defines is not itself checked), but one level further upstream than Round 5's angle.

The obvious first attempt to test this - mutate `observed_result` fields directly - was itself a
methodological trap this round caught in its own first pass, not from outside feedback: mutating
`observed_result.external_outcome` alone gets caught by the `expected_result`-vs-frozen-prediction
backstop (since the frozen prediction and `observed_result` must match for `passed=True`), which
tests that backstop, not whether the field is independently re-derived. The design only has a
single frozen prediction per case, so an attacker who changes `observed_result` must also change
`expected_result` to stay internally consistent - but `expected_result` is separately hash-checked
against the embedded, frozen `prediction.json`, so that combined attack is already caught for
different reasons. The real question was narrower: is there ANY case where the raw evidence fields
(`effect_count` etc.) can be fabricated while leaving `observed_result`/`expected_result`
completely untouched? `unavailable_readback` was the answer - it deliberately has no
`ledger.jsonl` retained (the absence IS the evidence for that case), so the verifier's
ledger-based `effect_count` cross-check (added Rounds 1-2) never runs for that case AT ALL.

| # | Finding | Fix (`action_readback_receipt.py`) | Permanent test |
|---|---|---|---|
| 1 | `effect_count`/`effect_attempt_ids`/`effect_payload_digests`/`effect_ledger_classification`/`digests_match_admission` could be fabricated into a fully self-consistent fake "one matching effect" on an `unavailable_readback` trial (whose real, correct values are `null`/`[]`/`[]`/`UNVERIFIED`/`null`), with `observed_result` left as the correctly-INDETERMINATE frozen prediction - accepted with **zero** problems by both `validate_receipt` and `verify_bundle` before the fix, confirmed empirically before assuming | Added a direct recompute in `validate_receipt`: `external_outcome`/`observation_availability`/`client_claim` are each recomputed via their own `derive_*` function from the raw fields and compared against `observed_result`'s copy, exactly mirroring the pre-existing `externally_verified` recompute pattern | `test_validate_receipt_rejects_fabricated_effect_count_on_unavailable_readback`, `test_validate_receipt_recomputes_observation_availability_from_raw_ledger_state`, `test_validate_receipt_recomputes_client_claim_from_worker_local_receipt` |

A second, related gap surfaced while implementing the fix above: `external_outcome`,
`observation_availability`, `client_claim`, and `externally_verified` are each written to the
receipt dict TWICE by `build_receipt` - once flattened at the top level (`rec["external_outcome"]`
etc.) and once nested inside `observed_result`. Grepping the verifier confirmed it only ever reads
the nested copy (`trial.get("observed_result", {}).get(...)`), so the top-level copy was a
completely unconstrained duplicate. `protocol_valid` turned out to be written the same way, though
empirically it was already fully constrained in both directions by the pre-existing `passed=True`
contract check (top-level) and the `expected_result`/`externally_verified` backstops (nested) - a
genuine non-finding, not just an assumption, confirmed by testing both directions before
concluding so. It is included in the fix anyway for structural symmetry with the other four
identically-shaped duplicated fields, not because it was independently exploitable.

| # | Finding | Fix | Permanent test |
|---|---|---|---|
| 2 | `external_outcome`/`observation_availability`/`client_claim`/`externally_verified` are each stored twice (top level + nested in `observed_result`); nothing checked the two copies agreed, so the top-level copy was an unconstrained duplicate a mutation could set to anything, including a value contradicting the one actually used by every other check | Added a direct equality check between the top-level and nested copies of all five duplicated fields (including `protocol_valid`, confirmed a non-exploitable but structurally identical case) | `test_validate_receipt_rejects_top_level_external_outcome_disagreeing_with_observed`, `test_validate_receipt_rejects_top_level_externally_verified_disagreeing_with_observed`, `test_validate_receipt_rejects_top_level_protocol_valid_disagreeing_with_observed` |

Part-C proof (both new checks, each disabled independently in its own disposable copy): the
`unavailable_readback` fabricated-`effect_count` mutation and the top-level-`external_outcome`
divergence mutation are each accepted with **zero** problems once their respective check is
removed - both confirmed load-bearing, not redundant with anything else.

Also confirmed by exhaustive census (grepping every `REQUIRED_FIELDS` name against
`action_readback_verify.py`'s reference count) that no other schema field is written to the receipt
dict under two different keys - `external_outcome`/`observation_availability`/`client_claim`/
`externally_verified`/`protocol_valid` are the only five, and all five are now covered.

Real bundle (`action_readback_self_reviewed_v2`) re-verified clean, 18/18, in place and after
relocation - no false positives from either new check. Real test suite: 114 passed (up from 108).
Full repository suite: 406 passed, 31 skipped (unchanged skip set). Ruff clean, mypy clean. No
owned processes remaining; `git status` in this worktree unchanged (still only new, untracked
files).

### What this means for "publication readiness"

Six rounds in: every one of the first five "complete" claims was wrong, and this sixth found two
more real gaps by asking a more fundamental question than any prior round had - not "is field X
checked against retained evidence" (Rounds 1-5's question) but "are the pure functions this schema
itself defines to PRODUCE a field ever used to independently RE-DERIVE and check it, or only to
produce it once." The `unavailable_readback` case remains the sharpest instance of the underlying
risk across all six rounds: a case designed around the deliberate absence of one kind of evidence
(the ledger) is exactly where a check gated on that evidence's presence goes silently missing, and
that blind spot generalizes to any future case or field where a raw-evidence cross-check is
conditional on a file that a given case may not retain.
