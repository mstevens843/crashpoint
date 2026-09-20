# Exact test results

All commands run from the worktree root:
`/Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-action-readback-2026-09-20`

## Confirmatory batch

```bash
./.venv/bin/python -m crashpoint.harness.action_readback --name action_readback_self_reviewed_v2
```

```
Action readback evidence - 18/18 trials (3 per cell x 6 cases), status=COMPLETE
all trials agree with the pre-registered prediction: True
clean: 3/3 pass; outcomes={'ONE_EFFECT_MATCHING': 3}
effect_before_lost_receipt: 3/3 pass; outcomes={'ONE_EFFECT_MATCHING': 3}
stopped_before_effect: 3/3 pass; outcomes={'NO_EFFECT': 3}
naive_retry: 3/3 pass; outcomes={'MULTIPLE_EFFECTS_MATCHING': 3}
payload_mismatch: 3/3 pass; outcomes={'ONE_EFFECT_MISMATCHED': 3}
unavailable_readback: 3/3 pass; outcomes={'INDETERMINATE': 3}

receipt: cp1_7b5610ba2a59e074d440b4b08975c2c0731ea0031a87da88aeb61140a751a90f
wrote .../evidence/action_readback/action_readback_self_reviewed_v2/manifest.json
```

Wall clock: ~1.1s for all 18 trials (real subprocesses, real SIGKILLs, real fresh retries). This is
the Round-5-recaptured bundle (adds `observer.stdout`/`observer.stderr` per trial); see
`SELF-REVIEW.md` for why the harness itself changed, not only the verifier.

## Offline verification, in place and after relocation

```bash
./.venv/bin/python -c "
from pathlib import Path
from crashpoint.harness.action_readback_verify import verify_bundle
report = verify_bundle(Path('evidence/action_readback/action_readback_self_reviewed_v2'))
print(report['trial_count'], report['manifest_receipt_valid'], report['problems'])
"
# 18 True []
```

```bash
cp -R evidence/action_readback/action_readback_self_reviewed_v2 /some/unrelated/path/relocated
./.venv/bin/python -c "
from pathlib import Path
from crashpoint.harness.action_readback_verify import verify_bundle
print(verify_bundle(Path('/some/unrelated/path/relocated'))['problems'])
"
# []
```

No SafeAgent-style third-party dependency exists in this experiment at all (no per-release venv
to worry about); the verifier imports only `crashpoint.*` and the standard library.

## Permanent test suite (this experiment)

```bash
./.venv/bin/python -m pytest tests/test_action_readback.py tests/test_action_readback_receipt.py tests/test_action_readback_verify.py -q
```

```
.................................................................................................................... [100%]
114 passed in ~4s
```

Breakdown (114, up from 75 originally - see `SELF-REVIEW.md`'s Round 2-6 sections for what each
addition covers and why it wasn't there before): `test_action_readback.py` (15 - live fault
injection into `run_trial()`/`run()`, the genuine `run()`-level batch-aggregation test, a
sibling-case Worker B launch-failure test, unit tests for
`_kill_and_reap`/`_reap_owned`/`wait_for_barrier`, and two properties of the real recorded batch),
`test_action_readback_receipt.py` (40, up from 30 - pure `derive_*` function tests,
`validate_receipt` structural-contract tests including the two invariants corrected during Round
1's self-review, 4 Round-5 tests for the `worker_b_used=False` internal-consistency rule, and 6
Round-6 tests for the raw-field derive-function recompute and top-level-duplicate-field checks),
`test_action_readback_verify.py` (59, up from 30 - the real bundle verifies clean, survives
relocation, 27 Round-1 one-mutation-at-a-time tests, 12 Round-2 tests ported directly from Codex's
independent review, 11 Round-3 tests from a self-audit of per-trial fields/events/columns, 3
Round-4 tests from a self-audit of manifest-level summary fields, and 3 Round-5 tests for the
observer's raw stdout requirement/content).

## Full repository suite

```bash
./.venv/bin/python -m pytest -q
```

```
406 passed, 31 skipped in ~9s
```

Skips, all pre-existing and unrelated to this work: `crewai` (6, module not installed),
`langgraph` (3, module not installed), native macOS isolation evidence (1, absent in this
worktree), TrueForge fixtures/evidence (2, not installed/absent), and the safeagent-ttl
experiment's own isolated per-release venvs (19 - `.local/safeagent-envs/` is gitignored and this
fresh worktree never installed them; unrelated to this assignment's own scope, which uses no
third-party package).

## Lint and typecheck

```bash
./.venv/bin/python -m ruff check .
# All checks passed!

./.venv/bin/python -m mypy --no-incremental --cache-dir=/dev/null
# 22 errors in 9 files, ALL pre-existing and unrelated (sqlalchemy/pydantic missing, untyped
# decorators in langgraph/temporal/dbos adapters/runtimes) - none in any action_readback* file
# (src or tests), checked with a fresh (non-incremental, /dev/null-cached) run using the
# project's own configured file set (files = ["src", "tests"] in pyproject.toml).
```

## Self-review

```bash
./.venv/bin/python -m pytest tests/test_action_readback.py -k "injected or run_level" -q
```

```
........                                                                  [100%]
8 passed in 1.16s
```

(Part-C proof script - disposable-copy bug-detection proof, kept only in the session scratchpad,
never committed - output reproduced in full in `SELF-REVIEW.md`; all 6 checks confirmed load-bearing,
and the real test suite re-run afterward, still 75 passed, confirming the real source tree was
never modified.)

## Round 2: Codex's independent review, reproduced and re-run after fixes

The exact script Codex ran (`check_bundle.py`) reproduced against the real, unmodified verifier
before any fix - one function needed a small adjustment for this codebase's actual worker-stdout
event-line prefix, the mutation's intent was otherwise unchanged (see `SELF-REVIEW.md`):

```bash
./.venv/bin/python <adapted check_bundle.py>
```

Before fixes: `AssertionError: Verifier must reject each mutation with structured problems, not
accept or raise.` - 9 of 12 mutations accepted, 3 raised an unhandled exception
(`AttributeError`/`AttributeError`/`UnicodeDecodeError`).

After fixes, same script, same bundle:

```
{
  "original": {"problems": [], "trials": 18},
  "deleted_completion": {"accepted": false, ...},
  "wrong_admission_read_identity": {"accepted": false, ...},
  "false_effect_payload_digests": {"accepted": false, ...},
  "duplicate_journal_id": {"accepted": false, ...},
  "extra_journal_id": {"accepted": false, ...},
  "prediction_symlink_escape": {"accepted": false, ...},
  "journal_symlink_escape": {"accepted": false, ...},
  "observer_report_array": {"accepted": false, ...},
  "cell_summary_array": {"accepted": false, ...},
  "ledger_invalid_utf8": {"accepted": false, ...},
  "false_terminal_anchor": {"accepted": false, ...},
  "false_local_receipt": {"accepted": false, ...}
}
```

Exit code 0 (the script's own `assert all(...accepted is False...)` now passes). Full per-mutation
reasons in `SELF-REVIEW.md`'s Round 2 finding table. The real evidence bundle
(`action_readback_self_reviewed`) was re-verified against every new check afterward and still
returns zero problems - these are new checks catching genuinely absent bugs against a deliberately
mutated copy, not false positives against the real, valid bundle.

Part-C round 2 (proving the three highest-risk new checks - the local-receipt/completions
cross-check, journal duplicate-detection, and bundle-root symlink confinement - are load-bearing,
same disable-in-a-disposable-copy discipline as round 1):

```
{
  "completions_check_disabled": {"false_local_receipt_flagged": false, "expect": false},
  "journal_dup_check_disabled": {"duplicate_flagged": false, "expect": false},
  "root_confinement_disabled": {"symlink_escape_flagged": false, "expect": false}
}

ALL THREE NEW CHECKS PROVEN NECESSARY: True
```

Real test suite re-run after this second proof process: still 87 passed, confirming the real
source tree was not touched by it either.

## Round 3: self-audit, performed on request using Codex's own method

No external script this time - every event `action_readback_runtime.py` emits and every column
the `admissions`/`completions` tables have was enumerated directly, then grepped for in the
verifier to see what was actually cross-checked versus assumed. 8 gaps confirmed on the first
mutation run; 3 more (`observer_pid`, `crashpoint_commit`, `worker_a_barrier_observed`) were
nearly misreported as already-caught until noticing the script's own `synchronize()` helper never
kept `journal.jsonl` in sync, so an incidental journal-mismatch was masking the absence of a real
check - fixed in the script itself before trusting its results (see `SELF-REVIEW.md`). A further
3 gaps (`admissions.trial_id`/`case_name` columns, and the completions comparison checking only
`outcome` rather than the full row) were found by a second pass down the column list:

```
STILL ACCEPTED (real gaps): NONE
```

(after fixes; before fixes, all 11 were accepted). Full per-mutation output in `SELF-REVIEW.md`'s
Round 3 finding table. Part-C proof (three representative checks disabled in disposable copies,
same discipline as rounds 1 and 2):

```
ALL THREE PROVEN NECESSARY: True
```

Real bundle re-verified clean after all 11 fixes (`18 trials, [] problems`, in place and after
relocation). Real test suite: 98 passed (up from 87), full repository suite 390 passed/31 skipped,
ruff clean, mypy clean in every `action_readback*` file, no owned processes remaining.

## Round 4: one more sweep, at the manifest level

`manifest.get("status")` and `manifest.get("cases")` both had zero hits anywhere in the verifier
(grep-confirmed before testing). Empirically:

```
"status_incomplete_when_actually_complete": {"accepted": true, ...}
"cases_list_omits_a_real_case": {"accepted": true, ...}
"cases_list_adds_a_fake_case": {"accepted": true, ...}
```

(A fourth and fifth candidate - a `bool` disguised as `worker_a_pid`, and a negative
`receiver_seal_dump_count` - were both already correctly rejected; every existing cross-check uses
plain equality against an independently recomputed value, which is type-safe regardless of the
left-hand side's type. Documented as a real but non-exploitable gap in `validate_receipt`'s
explicit type-guard coverage, not forced into an unnecessary fix.)

After fixing both real gaps and re-running:

```
STILL ACCEPTED (real gaps): NONE
```

Part-C proof (the `status` check specifically, disabled in a disposable copy): mutation no longer
flagged with the check removed. Real bundle re-verified clean (`18 trials, [] problems`). Real
test suite: 101 passed (up from 98). Full repository suite: 393 passed, 31 skipped (unchanged
skip set). Ruff clean. Mypy clean in every `action_readback*` file (22 pre-existing, unrelated
errors elsewhere, unchanged from every prior round). No owned processes remaining.

## Round 5: one more sweep, at internal-consistency and evidence-completeness

Two angles - `validate_receipt` itself (not just the offline verifier), and whether every retained
file is genuinely independent evidence:

```
"worker_b_pid_fabricated_on_clean": {"validate_receipt_problems": [], "verify_bundle_accepted": true, ...}
"worker_b_attempt_id_fabricated_on_clean": {"validate_receipt_problems": [], "verify_bundle_accepted": true, ...}
"worker_b_local_receipt_fabricated_on_clean": {"validate_receipt_problems": [], "verify_bundle_accepted": true, ...}
```

(checked at BOTH layers explicitly, not assuming one implies the other) - all 3 accepted at both
before the fix. A 4th (`worker_b_exit_status`) found the same way while implementing the fix.
Separately, `observer_pid` was found to be cross-checked only against `observer_report.json` - a
file the harness itself derived from the same data as the receipt field, not independent evidence,
unlike the workers' own retained raw stdout. Fixing that required retaining the observer's raw
stdout/stderr for the first time (`action_readback.py`), which meant recapturing the evidence
bundle (`action_readback_self_reviewed_v2`) rather than patching the existing one in place.

After all 5 fixes:

```
STILL ACCEPTED (real gaps): NONE
```

Part-C proof (the new `observer.stdout`-based `observer_pid` check, disabled in a disposable
copy): the exact mutation the Round-3 check used to catch is, with this check removed, accepted
with **zero** problems reported - not degraded to a different message, completely silent. The
prior bundle (`action_readback_self_reviewed`) is preserved as a superseded historical artifact,
not deleted or edited in place.

New bundle verified clean in place and after relocation (`18 trials, [] problems` both times).
Real test suite: 108 passed (up from 101). Full repository suite: 400 passed, 31 skipped
(unchanged skip set). Ruff clean. Mypy clean in every `action_readback*` file. No owned processes
remaining.

## Round 6: one more sweep, at the derive-function-recomputation layer

Grep-confirmed before testing anything: `derive_external_outcome`/`derive_observation_availability`/
`derive_client_claim` are called only from `build_receipt`, never from `validate_receipt` or
`verify_bundle`.

```bash
./.venv/bin/python /path/to/scratchpad/self_check_round7b.py
```

Before the fix - fabricating `effect_count=1`/`effect_attempt_ids`/`effect_payload_digests`/
`effect_ledger_classification="EXACTLY_ONCE"`/`digests_match_admission=True` on an
`unavailable_readback` trial, leaving `observed_result`/`expected_result` untouched (both
correctly `INDETERMINATE`):

```json
{
  "validate_receipt_problems": [],
  "verify_bundle_accepted": true,
  "verify_bundle_problems": []
}

REAL GAP: True
```

After the fix (direct recompute of the three fields via their own `derive_*` functions, added to
`validate_receipt`):

```json
{
  "validate_receipt_problems": [
    "observed_result['external_outcome']='INDETERMINATE' is not what the raw evidence fields imply ('ONE_EFFECT_MATCHING')"
  ],
  "verify_bundle_accepted": false,
  "verify_bundle_problems": [
    {
      "trial_id": "unavailable_readback-0",
      "reason": "receipt schema: observed_result['external_outcome']='INDETERMINATE' is not what the raw evidence fields imply ('ONE_EFFECT_MATCHING')"
    }
  ]
}

REAL GAP: False
```

Second gap, found while implementing the fix above: `external_outcome`/`observation_availability`/
`client_claim`/`externally_verified` are each written to the receipt dict twice (top level and
nested in `observed_result`); grep confirmed the verifier only ever reads the nested copy. Before
the fix, diverging the top-level copy of a `clean` trial's `external_outcome` while leaving the
nested copy correct:

```
validate_receipt problems: []
verify_bundle accepted: True
verify_bundle problems: []
```

After the fix (direct top-level-vs-nested equality check, added for all five structurally
identical duplicated fields including `protocol_valid`, which was separately confirmed - by
testing both divergence directions - to already be fully constrained by the pre-existing
`passed=True` contract check and the `expected_result`/`externally_verified` backstops, and is
included for structural symmetry rather than because it was independently exploitable):

```
validate_receipt problems: ["top-level rec['external_outcome']='NO_EFFECT' disagrees with observed_result['external_outcome']='ONE_EFFECT_MATCHING'"]
verify_bundle accepted: False
verify_bundle problems: [{'trial_id': 'clean-0', 'reason': "receipt schema: top-level rec['external_outcome']='NO_EFFECT' disagrees with observed_result['external_outcome']='ONE_EFFECT_MATCHING'"}]
```

Part-C proof (both new checks, each disabled independently in its own disposable copy loaded under
`crashpoint.harness._disposable_*` so its own relative imports resolve against the real installed
`crashpoint` package):

```
Proof 1 (recompute check disabled) - fabricated effect_count on unavailable_readback:
  problems: []
  UNDETECTED (proves check load-bearing): True

Proof 2 (top-level-duplicate check disabled) - top-level external_outcome diverged:
  problems: []
  UNDETECTED (proves check load-bearing): True

BOTH PART-C PROOFS PASSED: both new checks are load-bearing, not decorative.
```

Both fixes are entirely within `validate_receipt` (a pure function of an already-produced receipt
dict), not the harness's own emission logic - no evidence recapture was needed. Real bundle
(`action_readback_self_reviewed_v2`) re-verified clean, 18/18, in place and after relocation, after
each fix. Real test suite: 114 passed (up from 108) - 6 new tests, all in
`test_action_readback_receipt.py` (34 → 40): 3 for the raw-field recompute check
(`external_outcome`/`observation_availability`/`client_claim`), and 3 for the top-level-vs-
`observed_result` duplicate-field check (`external_outcome`, `externally_verified`, and
`protocol_valid` - the last documenting the confirmed non-finding, since that field's divergence
was already caught by pre-existing checks in both directions, rather than a newly-exploitable
gap). Full repository suite: 406 passed, 31 skipped (unchanged skip set). Ruff clean. Mypy clean in
`action_readback_receipt.py`/`action_readback_verify.py`. No owned processes remaining;
`git status` unchanged (still only new, untracked files in this worktree).

## Process/evidence hygiene

```bash
ps aux | grep -iE "action_readback|ledger.daemon" | grep -v grep
# (no output - no owned processes remain)
```

- Original bundle `action_readback_corrected` preserved, unmodified, unreferenced elsewhere.
- Historical `safeagent_ttl*` and `crewai_retry*` evidence bundles (inherited from the base commit)
  untouched - no test or script in this session wrote to `evidence/safeagent_ttl/` or
  `evidence/crewai_retry/`.
- Main checkout and sibling worktrees (`crashpoint-crewai-retry-repro-2026-09-13`,
  `crashpoint-langgraph-noncrash-control-2026-09-16`, `crashpoint-safeagent-ttl-2026-09-19`) were
  never entered or modified; this worktree's own git status shows only new, untracked files (no
  modifications to any inherited file):

```
?? evidence/action_readback/
?? results/13-action-readback-prediction.json
?? src/crashpoint/harness/action_readback.py
?? src/crashpoint/harness/action_readback_receipt.py
?? src/crashpoint/harness/action_readback_runtime.py
?? src/crashpoint/harness/action_readback_verify.py
?? tests/test_action_readback.py
?? tests/test_action_readback_receipt.py
?? tests/test_action_readback_verify.py
```

(`results/13-action-readback.md` and `handoff/action-readback/` are also new/untracked, added
after this listing was taken.)
