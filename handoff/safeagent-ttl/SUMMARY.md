# Handoff: SafeAgent TTL / sweep / fresh-client experiment

Single authoritative summary for this task, current as of the 2026-09-19 **second** same-day
correction pass; no other status document should be treated as current. The detailed narrative
and claim boundary live in
[`results/12-safeagent-ttl.md`](../../results/12-safeagent-ttl.md), and the finding-to-fix-to-test
mapping for **both** correction passes lives in [`CORRECTIONS.md`](./CORRECTIONS.md).

## Status: complete, after two same-day correction passes

The original harness measured the correct central result but understated its own evidentiary
rigor. A first independent review found the offline verifier would accept a bundle missing 20 of
its 30 trials, one with fabricated protocol/summary/provenance fields, one with most of its
retained evidence deleted, and one with a ledger file symlinked outside the bundle - and that a
snapshot failure in the runner could leak a live worker process forever while losing the trial's
evidence (10 findings, all fixed). A **second** independent review then ran the corrected code
(not the original bundle) against 12 further targeted mutations, tested individually per its own
instruction that a combined failing mutation does not prove every field is checked: 6 receipt
fields that were structurally required by the schema's docstrings but never actually checked by
`validate_receipt` (`reset_confirmed`, `ttl_boundary_confirmed`, `measured_age_at_decision`,
`pending_ttl_seconds`, `worker_a_exit_status`, `sweep_invoked`), plus a manifest-level
`trial_count` self-consistency gap, an embedded-source-inventory gap (the verifier only checked
whatever the manifest's own dict happened to list, never an independent required set), a missing
`retry_claim` event that went unrequired, a raw-ledger attempt-order/lineage gap (counts and
digests were checked, order never was), and two findings that turned out to be the same root
cause as first-pass finding 3 (`missing_raw_ledger`), whose original fix was itself still
incomplete. All 12 are now fixed, each independently re-verified via the second review's own
mutation harness (kept only in a local scratchpad, never committed) and each with its own
permanent regression test - one mutation, one test, not a shared combined test. See
`CORRECTIONS.md` for the full table of both passes. The measured central result was never in
dispute in either pass and is unchanged.

## Where this lives

- Worktree: `/Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-safeagent-ttl-2026-09-19`
- Branch: `experiment/safeagent-ttl`
- Base SHA: `606893ebb353df5dab3ac68738051eb5fbb7286e` (`main`/`origin/main` at worktree creation)
- The main checkout (`.../third-party-systems/crashpoint`) was never switched or modified; `git
  status` there remains clean at `606893e`. Sibling worktrees
  (`crashpoint-crewai-retry-repro-2026-09-13`, `crashpoint-langgraph-noncrash-control-2026-09-16`)
  were not touched. Confirmed again at the end of this second pass via `git worktree list`.

## Evidence bundles present (four; only the first and last are meaningful going forward)

- `evidence/safeagent_ttl/safeagent_ttl/` (421 files) - the **original** 30-trial bundle, produced
  before either correction pass. Preserved byte-for-byte (its outer receipt hash is re-verified
  against its own current bytes by
  `tests/test_safeagent_ttl.py::test_historical_pre_hardening_bundle_preserved_and_still_self_consistent`).
  It predates the schema hardening below and is **not** re-verified against the current, far
  stricter `verify_bundle` - that would fail on missing fields/files/embedded sources it was never
  built to have, which is not evidence of tampering. Retained as historical record, per "never
  rewrite frozen evidence to erase earlier claims."
- `evidence/safeagent_ttl/safeagent_ttl_corrected/` (611 files) - the first pass's bundle.
  Superseded as the authoritative capture, but **not** demoted to "predates the schema" status
  the way the original bundle is: its capture was already correct, only the checks were missing
  at the time, so it still passes today's far stricter `verify_bundle` completely unchanged.
  Preserved byte-for-byte (outer receipt hash re-verified against current bytes) **and**
  independently re-validated in place, by
  `tests/test_safeagent_ttl_verify.py::test_safeagent_ttl_corrected_round2_bundle_preserved_and_still_validates`.
- `evidence/safeagent_ttl/safeagent_ttl_v2/` - an intermediate regeneration from partway through
  the first pass. Superseded, not otherwise referenced, left in place rather than deleted without
  being asked to. Unchanged this pass.
- `evidence/safeagent_ttl/safeagent_ttl_corrected_review2/` (611 files) - the **current,
  authoritative** bundle: a fresh 30-trial confirmatory batch produced with the second pass's
  corrected harness/verifier, the same frozen prediction (byte-identical, sha256
  `28a8d9ae822e927dca8e7ad21b281096da030870171737c5a2f88fc328212b73`), the same embedded
  provenance (harness/ledger source, the actual measured `sqlite_store.py` per release, a copy of
  the prediction - now checked against an independent required-file list, not just whatever the
  manifest's own dict claims), and verified clean (0 problems) by the corrected offline verifier,
  in place and after relocation, with no SafeAgent installation available to the verifying
  interpreter.

## Exact commands run, and their outcomes (this second correction pass)

Reproduction, before any of this pass's fixes (run against the first pass's corrected code, not
the original bundle - each of the 12 mutations tested individually):

```bash
./.venv/bin/python /private/tmp/safeagent-ttl-review-2-2026-09-19/check_remaining.py
```

The script's own final section is a *third*, live re-run of `run_trial` with `reset`/`seal`
mocked to simulate a deleted-baseline/reset-failure scenario; run again after this pass's fixes,
that section now raises `FileExistsError`, because its mocks simulate exactly what the new
`_reset_and_establish_baseline` does natively (it now creates and independently verifies the
baseline file itself, so the script's mock and the harness's own new code collide trying to
create the same file). This is expected, not a regression - see CORRECTIONS.md finding 11 - and
is superseded by two dedicated permanent tests
(`test_injected_deletion_of_a_real_known_empty_baseline_is_missing_evidence`,
`test_injected_reset_failure_still_retains_a_receipt`) that exercise the real, now-native code
path without the mock collision. All 12 mutations (findings 12-21 below, plus a re-run of the
first pass's `malformed_sqlite` as a non-regression check) were confirmed rejected, both against
the first pass's bundle and again against the freshly captured bundle below, via the script's own
mutation-harness logic extracted into a local scratchpad runner (not committed - a review
artifact, like the review's own script).

After fixes, regenerated confirmatory batch, under a new bundle name (the runner refuses to
overwrite an existing one):

```bash
./.venv/bin/python -m crashpoint.harness.safeagent_ttl --name safeagent_ttl_corrected_review2
# SafeAgent TTL evidence - 30/30 trials (3 per cell x 5 cases x 2 releases), status=COMPLETE
# all trials agree with the pre-registered prediction: True
# receipt: cp1_e71b4a336a818c34b9080bdcdb7dd6f5e5527037ff28a3c357ba6133191cb1fb
# wrote .../evidence/safeagent_ttl/safeagent_ttl_corrected_review2/manifest.json
# wall clock: 58.6s
```

Offline verification, in place and after relocation, confirmed with an interpreter that has no
SafeAgent installation (`.venv`, which never had `safeagent_exec_guard` installed at any point in
this task):

```bash
./.venv/bin/python -m crashpoint.harness.safeagent_ttl_verify verify-bundle \
    --dir evidence/safeagent_ttl/safeagent_ttl_corrected_review2
# exit 0; manifest_found=true, manifest_receipt_valid=true, trial_count=30, problems=[]

cp -R evidence/safeagent_ttl/safeagent_ttl_corrected_review2 /some/unrelated/path/relocated
./.venv/bin/python -m crashpoint.harness.safeagent_ttl_verify verify-bundle --dir /some/unrelated/path/relocated
# exit 0; identical result after relocation
```

Full gates:

```bash
./.venv/bin/python -m pytest -q
# 311 passed, 12 skipped in ~35s
# skips: crewai (6), langgraph (3), native macOS isolation (1), TrueForge (2) - all pre-existing,
# unrelated to this task, caused only by --group dev without --all-extras

./.venv/bin/python -m ruff check .
# All checks passed!

./.venv/bin/python -m mypy --no-incremental --cache-dir=/dev/null
# 22 errors in 9 files, ALL pre-existing and unrelated (sqlalchemy/pydantic missing, untyped
# decorators in langgraph/temporal/dbos adapters/runtimes) - the same 9 files and error count as
# the first pass; none in any safeagent_ttl* file (src or tests), checked with a fresh
# (non-incremental, /dev/null-cached) run using the project's own configured file set
# (files = ["src", "tests"] in pyproject.toml) specifically to rule out a stale-cache false
# negative or a path-scoping false positive from hand-picking files.
```

Test breakdown (103 tests across 3 files, up from 89 after the first pass):
`test_safeagent_ttl.py` (34: +3 from the first pass's 31 - a genuine `run()`-level test that
patches `run_trial` to fail on one of two trials and asserts the real `run()`'s batch-level
accounting, `INCOMPLETE`/`all_agree=False`/`execution_failures`, not just `run_trial`'s own return
value; a real-venv version-mismatch test for `_release_info`'s own guard, which nothing had
exercised before; and the rewritten baseline-deletion test described in CORRECTIONS.md finding
11), `test_safeagent_ttl_receipt.py` (34: +9 new structural checks, one per previously-unchecked
field, each its own independent test rather than one combined mutation), and
`test_safeagent_ttl_verify.py` (35: +5 - one per remaining verifier gap: declared trial-count
self-consistency, the required embedded-source inventory, the required `retry_claim` event, raw
ledger attempt-order/lineage, and the new preservation+re-validation test for the first pass's
bundle).

## Observed matrix (30/30 matching prediction; unchanged from both earlier runs)

| Case | `0.1.23` | `0.1.24` |
|---|---|---|
| `settled_control` | 1 effect, COMMITTED, retry denied | same |
| `pending_before_ttl` | 1 effect, PENDING, retry denied | same |
| `pending_expired_no_sweep` | 1 effect, PENDING, retry denied | same |
| `pending_expired_swept` | **2 effects (DUPLICATED)**, COMMITTED, retry **admitted** | 1 effect, PENDING, retry denied |
| `pre_effect_expired_swept` | 1 effect, COMMITTED, retry admitted | **0 effects**, PENDING, retry denied |

Full per-trial detail: `evidence/safeagent_ttl/safeagent_ttl_corrected_review2/manifest.json` and
`evidence/safeagent_ttl/safeagent_ttl_corrected_review2/trials/<trial_id>/`.

## Limitations

Also stated in every receipt; see `results/12-safeagent-ttl.md`'s "bounded pre-effect finding"
section for the full, precise wording.

`SQLiteExecutionStore` only - no `/sweep` HTTP route, no `PostgresExecutionStore`, no MCP/HTTP
server, no payment/x402/`agent_id` gating, no distributed fencing, no stale-owner overlap, no
PostgreSQL, no production deployment or credentials, no CrewAI/n8n/SABLE integration. Same-host,
same-user process separation, not a sandbox or an independent organization boundary. Thirty
predeclared trials: no statistical reliability-rate claim. The `0.1.24` pre-effect finding shows
that the tested claim/sweep path does not restore liveness for that claim within this experiment's
scope; it does not show that every possible application-level recovery is impossible, and
`settle()` remains available for a truthful, caller-reconciled result at any time.

## Fresh re-run (new output directory required - the runner refuses to overwrite)

```bash
cd /Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-safeagent-ttl-2026-09-19
./.venv/bin/python -m crashpoint.harness.safeagent_ttl --name safeagent_ttl_rerun_$(date +%Y%m%d%H%M%S)
```

## Cleanup performed (owned resources only)

- All worker/sweep/retry/observer subprocesses spawned during this pass (including the smoke
  test, the full confirmatory batch, and the mutation-reproduction runs) exit on their own or were
  reaped via `Popen.wait()`/`communicate()` after SIGKILL; `ps aux | grep -i "safeagent\|ledger"`
  returns nothing after the full test suite and batch regeneration.
- No cron jobs, background agents, or long-lived processes were created.
- The isolated venvs and all four evidence bundles remain on disk (reproducibility artifacts;
  venvs are gitignored via the existing `.local/` rule). Nothing outside this worktree was
  modified. The original bundle and the first pass's `safeagent_ttl_corrected` bundle are both
  re-verified byte-for-byte unchanged by permanent tests (see "Evidence bundles present" above),
  not just a one-time manual hash diff.

## Staging, commit message, and push - left to Mathew, not executed

No new files beyond the second pass's evidence bundle; the file set to stage is unchanged from the
first pass (the new bundle lives under the already-included `evidence/safeagent_ttl/`):

```bash
cd /Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-safeagent-ttl-2026-09-19
git add pyproject.toml results/12-safeagent-ttl-prediction.json results/12-safeagent-ttl.md \
        src/crashpoint/harness/safeagent_ttl.py src/crashpoint/harness/safeagent_ttl_receipt.py \
        src/crashpoint/harness/safeagent_ttl_runtime.py src/crashpoint/harness/safeagent_ttl_verify.py \
        tests/test_safeagent_ttl.py tests/test_safeagent_ttl_receipt.py tests/test_safeagent_ttl_verify.py \
        evidence/safeagent_ttl/ handoff/safeagent-ttl/

git commit -m "$(cat <<'EOF'
Measure SafeAgent's SQLite claim/sweep TTL boundary across 0.1.23/0.1.24

sweep_stale_pending() deleted stale PENDING claims in 0.1.23, letting a
fresh retry duplicate an already-recorded effect (3/3 trials in the
central cell). 0.1.24 made it an unconditional no-op, which prevents
that duplication but leaves a claim whose worker died before any effect
ran PENDING with zero effects and no automatic release through the
tested API (also 3/3 trials, 0.1.24 only - 0.1.23 recovers this case by
retrying to exactly one effect). Full 5x2x3 matrix in
results/12-safeagent-ttl.md.

Two same-day independent reviews found the harness/verifier understated
their own evidentiary rigor (22 findings total across both passes, none
touching the central result); both are fixed, each with its own
permanent regression test, documented end to end in
handoff/safeagent-ttl/CORRECTIONS.md. The authoritative evidence bundle
is evidence/safeagent_ttl/safeagent_ttl_corrected_review2/; two earlier
bundles are preserved byte-for-byte as historical record.
EOF
)"

git push -u origin experiment/safeagent-ttl
```

After a successful push, derive the pinned commit/file links from the real HEAD rather than
guessing them ahead of time:

```bash
SHA=$(git rev-parse HEAD)
echo "https://github.com/mstevens843/crashpoint/blob/${SHA}/results/12-safeagent-ttl.md"
echo "https://github.com/mstevens843/crashpoint/blob/${SHA}/handoff/safeagent-ttl/CORRECTIONS.md"
echo "https://github.com/mstevens843/crashpoint/tree/${SHA}/evidence/safeagent_ttl/safeagent_ttl_corrected_review2"
```

Nothing above has been run. Review the diff and evidence bundle first.
