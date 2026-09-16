# Closeout response to CODEX-PUBLICATION-PROBE-RESULTS.json

Every finding below was reproduced against the corrected code before being marked fixed. Source of
the findings: `handoff/langgraph-noncrash-control/CODEX-PUBLICATION-PROBE-RESULTS.json`. Schema
bumped `crashpoint.langgraph_control.receipt.v1` -> `v2`; the v1 bundle
(`evidence/langgraph_noncrash_control/`) is retained byte-for-byte, not deleted or reinterpreted. The
corrected bundle is `evidence/langgraph_noncrash_control_v2/` (receipt
`cp1_a437fe27bcb38c062370cce6bd23af94a259e43571f6998f65521aa12a839dad`).

## 1. Six false accepts

| # | Finding | Root cause | Fix | Permanent test | Observed output after fix |
|---|---|---|---|---|---|
| 1 | `admission.accepted_before_invoke=false` verified clean | Nothing checked this field against `passed`/`agreement` | `compute_agreement()` (shared by harness and verifier, `langgraph_control_receipt.py`) requires `admission_accepted_before_invoke is True`; `validate_receipt` recomputes and cross-checks `agreement` | `tests/test_langgraph_control_receipt.py::test_not_accepted_before_invoke_cannot_pass`, `tests/test_langgraph_control_verify.py::test_false_accept_not_accepted_before_invoke` | `result.agreement True does not match recomputed False` |
| 2 | `worker_exit_status=77` with `worker_ok=true` verified clean | No coherence check between the two fields | Unconditional check in `validate_receipt`: `worker_ok is True` requires `worker_exit_status == 0`, flagged regardless of `passed` | `tests/test_langgraph_control_receipt.py::test_worker_exit_77_with_worker_ok_true_is_unconditionally_rejected`, `tests/test_langgraph_control_verify.py::test_false_accept_worker_exit_77_with_worker_ok_true` | `runtime.worker_ok is True but runtime.worker_exit_status is 77, not 0: a worker that exited nonzero cannot coherently have self-reported ok` |
| 3 | `invoke_count=2` verified clean | Nothing compared `runtime.invoke_count` to `result.expected.invoke_count` | Unconditional check in `validate_receipt` | `tests/test_langgraph_control_receipt.py::test_invoke_count_mismatch_is_unconditionally_rejected`, `tests/test_langgraph_control_verify.py::test_false_accept_invoke_count_2` | `runtime.invoke_count 2 does not match result.expected.invoke_count 1` |
| 4 | Retained `contract.json` mutated, only its hash re-signed, `result.expected`/`manifest.json` left stale, verified clean | Verifier only hash-checked `contract.json`/`manifest.json`, never parsed and compared their content | `verify_bundle` now reads and deep-compares `contract.json`'s parsed content against `result.expected`, and `manifest.json`'s content against `source.*`/`contract.sha256` | `tests/test_langgraph_control_verify.py::test_false_accept_contract_disagrees_with_retained_file` | `result.expected does not match the retained artifact's actual content`; `manifest.json's contract_sha256 does not match receipt.contract.sha256` |
| 5 | Deleted `observer-report.json`, receipt still declared it, verified clean | Verifier never required or read the observer report at all | `verify_bundle` requires the file when `observer.report_path` is non-null, hash-checks it, and cross-checks every field (status, pid, chain_valid, count, digests, attempt_ids, hashes, intent_id) against `receipt.observer`/`.effect`/`.identity` | `tests/test_langgraph_control_verify.py::test_false_accept_missing_observer_report` | `missing required artifact: observer report not found at .../observer-report.json` |
| 6 | Deleted `source/langgraph_control.py`, verified clean | Verifier `continue`d past a missing retained source file instead of failing | `verify_bundle` now requires every basename in `REQUIRED_EXECUTING_SOURCE_BASENAMES` (defined once in `langgraph_control_receipt.py`) to be present, hashed, and matching | `tests/test_langgraph_control_verify.py::test_false_accept_missing_source_module` | `missing required artifact: retained source copy 'langgraph_control.py' not in bundle`; `bundle source/ is missing required module(s): ['langgraph_control.py']` |

Additionally fixed as part of the same pass, found while reproducing #6: the old `new_source_files`
list was derived from `git status --porcelain`, so a fully committed, clean checkout would silently
record an EMPTY list (the "dirty/untracked-only inventory" gap named in the assignment). Replaced with
`_hash_executing_source_files()`, which unconditionally hashes the fixed
`REQUIRED_EXECUTING_SOURCE_MODULES` list regardless of git status; `dirty`/`base_commit`/
`worktree_branch` remain separate, git-derived, informational provenance fields. Verified with a clean
git status simulated via monkeypatch (no real commit made):
`tests/test_langgraph_control.py::test_executing_source_files_recorded_regardless_of_git_status`.

## 2. Malformed evidence

| Finding | Root cause | Fix | Permanent tests | Observed output after fix |
|---|---|---|---|---|
| `result.expected=[]` (refreshed receipt) raised `AttributeError: 'list' object has no attribute 'get'` | No type check on `result.expected`/`result.measured` before `.get()` calls | `validate_receipt` now type-checks both as objects before any further use; the whole structural-check block runs before deeper checks that assume dict shape | `tests/test_langgraph_control_receipt.py::test_result_expected_must_be_an_object`, `::test_result_measured_must_be_an_object`, `tests/test_langgraph_control_verify.py::test_result_expected_wrong_type_returns_a_diagnostic_not_a_traceback_api` (+ `_cli`) | API: `["result.expected must be an object"]`, no exception. CLI: exit 1, `result.expected must be an object` on stdout, no `Traceback` in stdout/stderr |
| `receipt.json` containing invalid UTF-8 raised `UnicodeDecodeError` | `.read_text()` decodes eagerly with no error handling | New `_read_json_file()` helper reads bytes first, decodes explicitly, catches `UnicodeDecodeError`/`json.JSONDecodeError`/`OSError` at every step | `tests/test_langgraph_control_verify.py::test_invalid_utf8_receipt_returns_a_diagnostic_not_a_traceback_api` (+ `_cli`) | API: `["receipt.json is not valid UTF-8: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"]`. CLI: exit 1, message on stdout, no traceback |

A last-resort safety net was also added to `main()` (`try/except Exception` around `verify_bundle`,
printing `FAIL: verifier encountered an unexpected <Type>: <msg>` and exiting 2) for anything still
unanticipated - defense in depth, not a substitute for the targeted fixes above, which handle every
case reproduced by the probe without reaching that fallback.

## 3. Preserve failure receipts

| Finding | Root cause | Fix | Permanent tests | Observed output after fix |
|---|---|---|---|---|
| Observer emits `OBSERVER_ERROR`; `build_receipt`/`classify_control` raised `ValueError` | `_VALID_OBSERVER_STATUS` did not include `OBSERVER_ERROR` | Added `OBSERVER_ERROR` to `_VALID_OBSERVER_STATUS`; `classify_control` treats it like `STORE_MISSING`/`CORRUPT` -> `VOID` | `tests/test_langgraph_control_receipt.py::test_classify_observer_error_is_void_not_an_exception` | `classify_control(..., "OBSERVER_ERROR", ...) == "VOID"`, no exception; a full `build_receipt()` call with an `OBSERVER_ERROR` observation returns `passed: False`, no exception |
| Observer timeout (harness-level, real subprocess) | Untested end to end | `run_primary_control` already had a synthetic `OBSERVER_ERROR` fallback for "no report file"; now additionally nulls `report_path`/`report_sha256` (previously pointed at a nonexistent file) and takes a **parent-process fallback archive** of the ledger bytes when the observer never archived its own, clearly labeled and never claimed as proof the observer itself succeeded | `tests/test_langgraph_control.py::test_observer_timeout_yields_honest_void_receipt_with_parent_fallback_archive` | `observer.status == "OBSERVER_ERROR"`, `report_path is None`, `parent_fallback_archive_path` set and hash-verified, `oracle_classification == "VOID"`, `passed is False`, `verify_bundle(...) == []` (internally consistent) |
| Observer nonzero exit (internal exception, deterministic) | Untested; relied on timing before | Reproduced without any timing dependency: `--archive-to`'s parent directory is blocked by a plain file, so `observe()` raises `NotADirectoryError` partway through and `main()`'s existing exception handler converts it | `tests/test_langgraph_control.py::test_observer_internal_exception_produces_observer_error_report` | exit code 2, report `{"status": "OBSERVER_ERROR", "error": "NotADirectoryError: ...", ...}`, no traceback |
| Missing/malformed observer report (bundle-level) | Covered by false-accept #5 above (missing) | Same fix as #5; a truncated/corrupt-but-present report file is handled in the harness by `run_primary_control`'s `except (json.JSONDecodeError, UnicodeDecodeError, OSError)` around the report parse, producing a distinct `OBSERVER_ERROR` message ("could not be parsed") while still recording the malformed file's real path+hash for independent inspection | `tests/test_langgraph_control_verify.py::test_false_accept_missing_observer_report` (missing case); the malformed-but-present path is exercised by construction in `langgraph_control.py`'s `run_primary_control` and covered indirectly by the CLI/API malformed-JSON tests using the same `_read_json_file` machinery | see false-accept #5 |
| Missing checkpoint table (real disk-pressure reproduction: `no such table: checkpoints`) | `_thread_checkpoint_count` had no exception handling around `conn.execute(...)`; an `sqlite3.OperationalError` crashed `run_primary_control` entirely, losing the chance to record ANY receipt | Renamed to `_safe_thread_checkpoint_count`, returns `(count, error)`; `checkpoint.thread_scoped_checkpoint_count` is now nullable with a required, non-null `checkpoint.checkpoint_error` explaining why whenever it is null (mirrors the ledger's missing-vs-empty discipline) | `tests/test_langgraph_control.py::test_checkpoint_table_missing_is_reported_not_raised`, `tests/test_langgraph_control_receipt.py::test_null_checkpoint_count_requires_a_checkpoint_error`, `::test_checkpoint_count_and_error_cannot_both_be_set` | `count is None`, `error == "OperationalError: no such table: checkpoints"`, no exception |

**On the original timeout probe's disk-pressure run specifically**: that run's "no checkpoint table,
empty worker stdout, no receipt" outcome was a worker **startup** failure (LangGraph could not even be
imported under critical disk pressure), not "successful execution followed by observer timeout" - this
response does not claim otherwise. The checkpoint-table failure is now reproduced and tested
deterministically and separately from the observer-timeout scenario (two different tests, above), each
targeting its own specific failure mode rather than relying on that original ambiguous, disk-pressure-
dependent run.

**Partial evidence preservation**: `contract.json`, `manifest.json`, `admission.sqlite`,
`worker-stdout.log`/`worker-stderr.log`, and (new) the parent-fallback ledger archive are all written
directly to the retained `output_dir`, never the ephemeral temp directory, so they survive regardless
of what fails afterward - unchanged design, reconfirmed by the timeout test above finding all of them
present.

**Owned processes**: unchanged design (`subprocess.run(..., timeout=...)` and `LedgerDaemon`'s context
manager both bound and reap their own children); reconfirmed with `pgrep -f
"crashpoint.ledger.daemon\|crashpoint.harness.langgraph_control"` returning nothing after every run in
this closeout, including the deliberately-failing ones.

## 4. Final acceptance

Run from `crashpoint-langgraph-noncrash-control-2026-09-16/` (the exact commands from the assignment):

```
$ uv run pytest -q
304 passed, 9 skipped in 5.06s

$ uv run ruff check .
All checks passed!

$ env PYTHONPATH=src ../crashpoint/.venv/bin/python -m mypy --no-incremental --cache-dir=/dev/null
Success: no issues found in 77 source files

$ git diff --check
(exit 0, no output - covers tracked/unstaged changes only; see the correction below)
```

All 9 skips are the same pre-existing, environmental skips as before this closeout (crewai/TrueForge
extras not installed in this worktree's narrower `--extra langgraph` sync; native macOS isolation
evidence absent) - none are new, none are in a file this work touched.

**`git diff --check` correction** (the assignment flagged the earlier handoff's documentation of this
command as wrong, and it was): `git diff --check` reports whitespace errors and conflict markers in
the diff between the working tree and the index for files git already tracks - it does **not** cover
untracked files, and every new file in this deliverable is untracked (nothing is staged or committed).
`README.md`/`RESULTS.md` are the only tracked files this work modified, and `git diff --check` against
them is genuinely clean. For the untracked files, a direct scan was run instead (trailing whitespace,
`<<<<<<<`/`=======`/`>>>>>>>` conflict markers, missing trailing newline) over every new source, test,
report, and handoff file - zero findings, without staging anything or touching the user's index.

**Fresh corrected bundle**: `evidence/langgraph_noncrash_control_v2/`, recorded via
`uv run --extra langgraph python -m crashpoint.harness.langgraph_control --output
evidence/langgraph_noncrash_control_v2 --name langgraph_noncrash_control_v2`. Verified in place and
after relocation to `/tmp/final-relocation-check/moved-bundle`: both report
`PASS: ... is internally consistent with its retained evidence`.

**LangGraph-import-blocking test**: `tests/test_langgraph_control_verify.py::test_verifier_subprocess_cannot_import_langgraph_even_if_it_tried`
runs the verifier in a **fresh subprocess** with `sys.modules['langgraph'] = None` set before
`verify_bundle` is even imported - assigning `None` to a `sys.modules` entry makes Python raise
`ImportError` immediately for that name or anything under it, for any import anywhere in the call
graph, present or added later. This is a stronger, dynamic guarantee than the pre-existing static AST
scan (`test_verifier_closure_never_imports_langgraph`), which only proves the current fixed list of
files never imports LangGraph and would miss a new import added to a file the list does not yet name.
Both tests are kept: the AST test gives a fast, precise "which file did it" diagnostic; the subprocess
test is the actual proof the assignment asked for.

**Original evidence hashes rechecked**: this closeout's scratchpad hash inventory from the earlier
session was cleared between sessions, so re-verification was done directly against the untouched main
checkout instead (`git status`/`git rev-parse HEAD` in `crashpoint/` confirm it is still clean at
`606893ebb353df5dab3ac68738051eb5fbb7286e`): `diff -rq` between main's `evidence/`/`results/` and this
worktree's shows **no differences** other than the two new evidence subdirectories
(`langgraph_noncrash_control/`, `langgraph_noncrash_control_v2/`) and the one new report
(`results/12-langgraph-noncrash-control.md`) - every pre-existing file is byte-for-byte identical. The
v1 bundle (`evidence/langgraph_noncrash_control/`) itself is also untouched (file mtimes all
`Sep 16 11:12`, unchanged since it was originally recorded); the v2 verifier correctly refuses to
verify it (`unexpected schema: 'crashpoint.langgraph_control.receipt.v1', expected
'crashpoint.langgraph_control.receipt.v2'`) rather than silently reinterpreting it under the new rules.

**Documentation updated to the final bundle**: `results/12-langgraph-noncrash-control.md`,
`README.md`, and `RESULTS.md` now reference `evidence/langgraph_noncrash_control_v2/` and its receipt
hash as authoritative, with the v1 bundle explicitly named as a retained, superseded record - see
those files' diffs for the exact wording.

---

# Round 2 addendum: independent reproduction after the v2 closeout

An independent reproduction of the v2 work (full suite reran successfully) found four further gaps.
Every finding below was reproduced against the corrected code before being marked fixed. The
authoritative bundle is now `evidence/langgraph_noncrash_control_v4/` (receipt
`cp1_f644c93d28cebc7ab00eae2b8f24cb9df6de009f5004687d3eebbaa2773248a9`). Schema bumped
`crashpoint.langgraph_control.receipt.v2` -> `v3`, because `observer.error` is a new required field
(a shape change, not just a new derived rule on existing fields) - `evidence/langgraph_noncrash_control/`
(v1), `evidence/langgraph_noncrash_control_v2/`, and `evidence/langgraph_noncrash_control_v3/` (see
the note on that directory below) are all retained byte-for-byte, not deleted or reinterpreted.

## 1. Malformed observer-report handling through `run_primary_control()`

| Root cause | Fix | Permanent tests (production path, not a parser unit test) | Observed output after fix |
|---|---|---|---|
| `cast(dict[str, Any], json.loads(...))` performed no runtime validation. A report containing `[]` (or `null`, `{}`, invalid JSON) crashed `run_primary_control()` with `AttributeError: 'list' object has no attribute 'get'` on the first `.get()` call, **after** the real worker had already completed successfully - losing the chance to write any receipt at all | New `_validate_observer_report_shape()` checks type/status/every field's type before the parsed report is ever trusted; on any problem, a synthetic `OBSERVER_ERROR` report is built instead (never a raw `cast`) | `tests/test_langgraph_control.py::test_malformed_observer_report_through_production_path` (parametrized over `[]`, `null`, `{}`, invalid JSON - the **real** worker and **real** observer subprocess both run; `subprocess.run` is monkeypatched to call through to the real implementation first, then overwrite the observer's real output file with the corrupt payload afterward) and the paired red-proof `::test_malformed_observer_report_would_crash_without_shape_validation` (monkeypatches `_validate_observer_report_shape` back to a no-op and asserts the original `AttributeError` reproduces) | For all four payloads: no uncaught exception; `receipt.json` is written; `observer.status == "OBSERVER_ERROR"`; `observer.error` is a non-empty diagnostic string; `effect.observed_count is None`; `result.oracle_classification == "VOID"`; `result.passed is False`; the available ledger bytes are still preserved (archived readback or parent-fallback archive, whichever exists); `verify_bundle(...) == []` (the verifier agrees this is an internally-consistent, honestly-recorded failure, not a masqueraded success). Red-proof: with validation disabled, `run_primary_control()` raises `AttributeError` as originally reported. |

A related gap found while implementing this fix: the harness built the synthetic `OBSERVER_ERROR`
diagnostic string in memory but never copied it into the receipt, so `receipt.json` had no way to
explain *why* `observer.status != "OBSERVED"`. Fixed by adding `observer.error` (nullable string) to
the receipt schema, wiring `"error": observer_report.get("error")` into `ControlObservation`, and
adding `validate_receipt` coherence checks: `observer.error` must be non-null whenever
`observer.status != "OBSERVED"`, and must be null when `observer.status == "OBSERVED"` (tests:
`tests/test_langgraph_control_receipt.py::test_observed_status_requires_null_error`,
`::test_non_observed_status_requires_a_non_null_error`). A second gap found while testing the
verifier side: it originally flagged a genuinely unparseable, honestly-retained `OBSERVER_ERROR`
report file as an *inconsistency* (content-parse failure), wrongly penalizing an honest failure
record. Fixed by always hash-checking the report file but only treating content-parse failure as a
problem when `receipt.observer.status != "OBSERVER_ERROR"`.

## 2. Bundle confinement

| Root cause | Fix | Permanent tests (correct hashes, so rejection proves confinement, not hash-checking) | Observed output after fix |
|---|---|---|---|
| `bundle_root / rel_path` silently discards `bundle_root` when `rel_path` is absolute (`Path.__truediv__`'s documented behavior) - so a mutated `receipt.contract.path` pointing at an absolute path outside the bundle, with its hash **recomputed to match that outside file**, was read and accepted | New `_resolve_in_bundle(bundle_root, rel_path, label)` is the single checked path-resolution boundary every artifact read goes through: rejects a non-relative-string path, rejects `.is_absolute()`, and rejects anywhere `(bundle_root / candidate).resolve()` (which follows symlinks) is not `.is_relative_to(bundle_root.resolve())`. Wired through every artifact read: contract, manifest, observer report, archived readback, parent-fallback archive, retained source copies, checkpoint DB, admission DB | `tests/test_langgraph_control_verify.py::test_absolute_contract_path_escapes_bundle_confinement`, `::test_relative_traversal_path_escapes_bundle_confinement`, `::test_symlink_inside_bundle_escaping_it_is_rejected`, `::test_symlinked_source_copy_escaping_the_bundle_is_rejected`, plus `::test_legitimately_relocated_self_contained_bundle_still_verifies` (a real, unmutated copy of the bundle moved to a new directory must still pass - confinement must not break the case the whole verifier exists to support) | Each escape attempt: `problems` non-empty, containing a message naming the specific confinement violation (e.g. `"...: declared path must be relative to the bundle, got absolute '...'"`, `"...resolves outside the bundle"`). The relocated-bundle test: `problems == []`. Red/green proof: temporarily reverted `_resolve_in_bundle` to the old unconfined `bundle_root / rel_path` join (backed up first); all four confinement tests failed with `assert []` (reproducing the exact reported bypass); restored from the backup (`diff` confirmed byte-identical); reran to confirm all four pass again. |

## 3. Observer exit handling

| Root cause | Fix | Permanent tests | Observed output after fix |
|---|---|---|---|
| `compute_agreement()` never looked at `observer.exit_status`, so a process that exited nonzero (e.g. `exit_status=2`) while still emitting a valid, hash-verified `OBSERVED` report was treated as a passing control | `compute_agreement()` now takes `observer_exit_status` and additionally requires `observer_exit_status == 0` for `PASS`, alongside every existing check. Any genuine readback evidence the observer produced before exiting nonzero is still retained in the bundle (a process failure does not erase or reinterpret evidence that was already written to disk) - only the pass/fail verdict changes | `tests/test_langgraph_control_receipt.py::test_compute_agreement_false_on_nonzero_observer_exit_status`, `::test_compute_agreement_false_on_null_observer_exit_status`, `tests/test_langgraph_control_verify.py::test_false_accept_observer_nonzero_exit_with_valid_observed_report` (bundle-level: a real recorded bundle's `observer.exit_status` is mutated to `2`, the outer hash recomputed to match, and the verifier must still reject it) | `compute_agreement(...) is False` in both unit cases; the bundle-level test's `problems` is non-empty and names the exit-status disagreement. Red/green proof: commented out the `and observer_exit_status == 0` line (backed up first); both the pure unit tests and the bundle-level test failed (the mutated bundle was wrongly accepted, exactly reproducing the finding); restored from the backup (`diff` confirmed byte-identical); reran to confirm all three pass again. |

## 4. Documentation corrections

- The admission SQLite database stores identity, original input, and the accepted/completed event
  sequence - **not** a durable timestamp. `accepted_at_utc` is generated by the harness process right
  before that row is committed and recorded only in the receipt; it is not itself re-derived from a
  durable admission-table column. Corrected in `results/12-langgraph-noncrash-control.md` and
  `github-reply-draft.md` (both previously implied the database itself stored the timestamp).
- "Verified after relocation to another local directory," not "tested on another machine" - the
  relocation check copies the bundle to a different path on the same host; it was never run on a
  separate machine. Corrected in the same two files.
- Added a "claim boundary" paragraph distinguishing facts the offline verifier genuinely **re-derives**
  from raw bytes (ledger chain/effect count, checkpoint count, admission status/event sequence - each
  independently requeried from the retained SQLite/JSONL artifacts, not read from the receipt) from
  facts it only checks for **checksum consistency** (recorded tool/library versions, timestamps, PIDs,
  the outer receipt hash itself) - the latter are self-reported by the process that ran and only
  proved *internally consistent*, not independently re-authenticated. Not every runtime claim in the
  receipt carries the same evidentiary weight, and the report/draft no longer imply otherwise.

## 5. Final acceptance (round 2)

Run from `crashpoint-langgraph-noncrash-control-2026-09-16/`:

```
$ uv run pytest -q
319 passed, 9 skipped in 10.19s

$ uv run ruff check .
All checks passed!

$ env PYTHONPATH=src ../crashpoint/.venv/bin/python -m mypy --no-incremental --cache-dir=/dev/null
Success: no issues found in 77 source files

$ git diff --check
(exit 0, no output - tracked/unstaged changes only; untracked files checked separately, see TEST-RESULTS.md)
```

All 9 skips are the same pre-existing, environmental skips as before (crewai/TrueForge extras not
installed in this worktree's narrower `--extra langgraph` sync; native macOS isolation evidence
absent) - none are new, none are in a file this round touched. 108 of the 319 passing tests are the
langgraph-control-specific suite (9 + 47 + 17 + 35 across the four dedicated test files, up from 93 in
the v2 closeout); the remaining 211 are the pre-existing repo suite, unaffected.

**Fresh corrected bundle**: `evidence/langgraph_noncrash_control_v4/`, recorded via
`uv run --extra langgraph python -m crashpoint.harness.langgraph_control --output
evidence/langgraph_noncrash_control_v4 --name langgraph_noncrash_control_v4`. Verified three ways,
all rerun fresh immediately before writing this addendum:

```
$ uv run python -m crashpoint.harness.langgraph_control_verify evidence/langgraph_noncrash_control_v4
PASS: evidence/langgraph_noncrash_control_v4 is internally consistent with its retained evidence

# after copying the bundle to a new local directory outside the worktree:
PASS: <relocated path>/moved-bundle is internally consistent with its retained evidence

# run in a subprocess with sys.modules["langgraph"] = None injected before verify_bundle is even imported:
problems: []
```

**Superseded bundles re-verified against the new (v3) schema, all cleanly refused, none silently
reinterpreted**:

```
$ uv run python -m crashpoint.harness.langgraph_control_verify evidence/langgraph_noncrash_control
FAIL: 1 problem(s) found in evidence/langgraph_noncrash_control
  - unexpected schema: 'crashpoint.langgraph_control.receipt.v1', expected 'crashpoint.langgraph_control.receipt.v3'

$ uv run python -m crashpoint.harness.langgraph_control_verify evidence/langgraph_noncrash_control_v2
FAIL: 1 problem(s) found in evidence/langgraph_noncrash_control_v2
  - unexpected schema: 'crashpoint.langgraph_control.receipt.v2', expected 'crashpoint.langgraph_control.receipt.v3'

$ uv run python -m crashpoint.harness.langgraph_control_verify evidence/langgraph_noncrash_control_v3
FAIL: 1 problem(s) found in evidence/langgraph_noncrash_control_v3
  - unexpected schema: 'crashpoint.langgraph_control.receipt.v2', expected 'crashpoint.langgraph_control.receipt.v3'
```

**Note on `evidence/langgraph_noncrash_control_v3/`**: this bundle was recorded mid-review, before the
decision to bump the schema to v3 (its receipt still declares schema `v2`). After that decision, it
needed to be re-recorded; an `rm -rf` on it was denied by the tool permission system, so rather than
work around that denial it was left in place and a correctly-schema-bumped bundle was recorded under a
new name (`_v4`) instead. It is documented here as a third superseded bundle, same as v1 and v2 - not
deleted, not silently accepted by the current verifier.

**No owned processes remain**: `pgrep -f "crashpoint.ledger.daemon\|crashpoint.harness.langgraph_control"`
returns nothing (exit 1) after every run in this round, including the deliberately-failing ones.

**Existing evidence/results integrity reconfirmed**: `diff -rq` between the untouched main checkout's
`evidence/`/`results/` and this worktree's shows no differences other than the four evidence
subdirectories (`langgraph_noncrash_control/`, `_v2/`, `_v3/`, `_v4/`) and the one report
(`results/12-langgraph-noncrash-control.md`) - every pre-existing file is byte-for-byte identical. The
main checkout (`../crashpoint/`) is confirmed still clean (`git status --porcelain` empty) and at the
same commit as before this work began (`606893ebb353df5dab3ac68738051eb5fbb7286e`).

**Documentation updated to the v4 bundle**: `results/12-langgraph-noncrash-control.md`, `README.md`,
`RESULTS.md`, `handoff/langgraph-noncrash-control/SUMMARY.md`, `REPRODUCE.md`, and `TEST-RESULTS.md`
all now reference `evidence/langgraph_noncrash_control_v4/`, its receipt hash, schema v3, and the
108-test count, with v1/v2/v3 all explicitly named as retained, superseded records.
