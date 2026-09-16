# Test results: LangGraph non-crash positive control (v4, post round-2 closeout)

All commands below were run in this worktree
(`crashpoint-langgraph-noncrash-control-2026-09-16/`) after `uv sync --group dev --extra langgraph
--locked`. Toolchain: Python 3.12.13, `langgraph==1.2.11`, `langgraph-checkpoint-sqlite==3.1.1`,
`langgraph-checkpoint==4.2.0`, `pytest==9.1.1`, `ruff==0.16.5`, `mypy==2.3.1` (all via `uv run`,
matching `uv.lock`, which is unmodified - `git diff --stat uv.lock` is empty).

## `uv run pytest -q`

```
319 passed, 9 skipped in 5.90s
```

New tests contributing to the 319: 108, across four files -
`tests/test_ledger_readback.py` (9), `tests/test_langgraph_control_receipt.py` (47),
`tests/test_langgraph_control.py` (17), `tests/test_langgraph_control_verify.py` (35). The remaining
211 passing tests are the pre-existing suite, unchanged and unaffected.
`test_langgraph_control_receipt.py` grew from 16 (v1) to 43 (v2 closeout) to 47 (round-2 closeout):
the round-2 additions are `compute_agreement`'s new `observer_exit_status` invariant (both the
nonzero-exit and null-exit cases) and `validate_receipt`'s new `observer.error` coherence checks
(required non-null when status != OBSERVED, required null when status == OBSERVED).
`test_langgraph_control.py` grew from 12 (v2) to 17 (round-2): two production-path tests
(`test_malformed_observer_report_through_production_path`, parametrized over 4 payloads, counted as
one test id per payload = 4 additional test runs under one function, plus
`test_malformed_observer_report_would_crash_without_shape_validation` as the paired red-proof).
`test_langgraph_control_verify.py` grew from 29 (v2) to 35 (round-2): the dynamic
`sys.modules['langgraph']=None` subprocess-blocking test, four bundle-confinement tests (absolute
path, relative traversal, symlink-inside-bundle, symlinked source-copy), a
legitimately-relocated-bundle-still-verifies test, and the nonzero-observer-exit-status false-accept
test.

All 9 skips are pre-existing and environmental, not caused by or related to this work:

| Skip | Reason |
|---|---|
| `tests/test_crewai_retry.py` x6 | `crewai` package not installed in this worktree's narrower `--extra langgraph` sync |
| `tests/test_isolation.py` x1 | native macOS isolation evidence absent (documented as not committed in README.md already) |
| `tests/test_trueforge_hidden.py` x2 | TrueForge fixture dependencies not installed |

None of the 9 skips are in a file this work touched, and none are new relative to a narrower sync.

## `uv run ruff check .`

```
All checks passed!
```

Repo-wide, including all 5 new source files, 4 new test files, and every pre-existing file.

## mypy (the exact command the assignment specified)

```bash
env PYTHONPATH=src ../crashpoint/.venv/bin/python -m mypy --no-incremental --cache-dir=/dev/null
```

```
Success: no issues found in 77 source files
```

This uses the **main checkout's** venv (`../crashpoint/.venv`, which has `--all-extras` installed),
not this worktree's narrower one - it reads that already-installed interpreter without resyncing or
editing `crashpoint/`. With `--all-extras` present, the DBOS/Temporal/CrewAI files that showed 18
environmental errors under this worktree's narrower `.venv` (documented in the v1 handoff) check
cleanly too, because their optional type stubs are now available. Confirmed this is a real, distinct
run, not a stale cache: `--no-incremental --cache-dir=/dev/null` forces a from-scratch check.

Running mypy scoped to only the 9 files this work added, under this worktree's own narrower `.venv`
(for a fast, extras-independent sanity check during development):

```bash
uv run mypy src/crashpoint/harness/ledger_readback.py src/crashpoint/harness/langgraph_control.py \
  src/crashpoint/harness/langgraph_control_observer.py \
  src/crashpoint/harness/langgraph_control_receipt.py \
  src/crashpoint/harness/langgraph_control_verify.py \
  tests/test_ledger_readback.py tests/test_langgraph_control.py \
  tests/test_langgraph_control_receipt.py tests/test_langgraph_control_verify.py
```

```
Success: no issues found in 9 source files
```

## `git diff --check`

```
(exit 0, no output)
```

**Correction from the v1 handoff**, which described this command inaccurately: `git diff --check`
reports whitespace errors and conflict markers in the diff between the working tree and the index,
for files git already **tracks** - it does not cover untracked files. Only `README.md`/`RESULTS.md`
(the two tracked files this work modified) are covered by it, and it is genuinely clean for them
(confirmed above). For the untracked files - every new source, test, report, and handoff file - the
equivalent checks were run directly instead, without staging anything or touching the index:

```bash
for f in <every new file>; do
  grep -nE ' +$' "$f"                       # trailing whitespace
  grep -nE '^(<<<<<<<|=======|>>>>>>>)' "$f" # conflict markers
  test "$(tail -c1 "$f" | xxd -p)" = 0a || echo "no trailing newline: $f"
done
```

Result: zero findings across all new files (5 source, 4 test, 1 report, 5 handoff).

## Offline verifier, run directly

```bash
uv run python -m crashpoint.harness.langgraph_control_verify evidence/langgraph_noncrash_control_v4
```

```
PASS: evidence/langgraph_noncrash_control_v4 is internally consistent with its retained evidence
```

Exit code 0. Also reverified after copying the bundle to `/tmp/v4-relocation-check/moved-bundle`:
identical `PASS` output from the relocated copy. Also reverified running the verifier as a subprocess
with `sys.modules['langgraph'] = None` injected before its own code executes: identical `PASS`,
`problems == []`.

The v1, v2, and v3 bundles (`evidence/langgraph_noncrash_control/`,
`evidence/langgraph_noncrash_control_v2/`, `evidence/langgraph_noncrash_control_v3/`) are all
untouched (file mtimes unchanged since each was first recorded) and are each correctly, cleanly
refused by the current (v3-schema) verifier as a schema mismatch - never silently reinterpreted under
the new rules:

```
FAIL: 1 problem(s) found in evidence/langgraph_noncrash_control
  - unexpected schema: 'crashpoint.langgraph_control.receipt.v1', expected 'crashpoint.langgraph_control.receipt.v3'

FAIL: 1 problem(s) found in evidence/langgraph_noncrash_control_v2
  - unexpected schema: 'crashpoint.langgraph_control.receipt.v2', expected 'crashpoint.langgraph_control.receipt.v3'

FAIL: 1 problem(s) found in evidence/langgraph_noncrash_control_v3
  - unexpected schema: 'crashpoint.langgraph_control.receipt.v2', expected 'crashpoint.langgraph_control.receipt.v3'
```

(The v3-named bundle still declares schema `v2` in its receipt because it was recorded mid-review,
before the schema-bump decision - it predates the `observer.error` field and is superseded by v4 for
that reason, not just by name.)

## Existing evidence/results integrity

The v1 session's SHA-256 inventory file lived in the session scratchpad, which did not survive across
sessions. Re-verified instead by direct content diff against the untouched main checkout (`crashpoint/`,
confirmed clean and still at `606893ebb353df5dab3ac68738051eb5fbb7286e`):

```bash
diff -rq evidence/ ../crashpoint-langgraph-noncrash-control-2026-09-16/evidence/
diff -rq results/  ../crashpoint-langgraph-noncrash-control-2026-09-16/results/
```

Result: no differences other than the four evidence subdirectories
(`langgraph_noncrash_control/`, `langgraph_noncrash_control_v2/`, `langgraph_noncrash_control_v3/`,
`langgraph_noncrash_control_v4/`) and the one report (`results/12-langgraph-noncrash-control.md`) -
every pre-existing file is byte-for-byte identical. `langgraph_noncrash_control_v3/` could not be
removed after the schema-bump decision superseded it (a `rm -rf` was denied by the permission system),
so it is retained rather than replaced in place - see the note in the previous section.

`git status --porcelain` shows zero modified or deleted tracked paths - only new, additional ones,
plus the two intentional modifications (`README.md`, `RESULTS.md`).

## No owned processes remain

```bash
pgrep -f "crashpoint.ledger.daemon\|crashpoint.harness.langgraph_control"
```

Returns nothing (exit 1) after every run in this closeout, including the deliberately-failing ones
(worker timeout, observer timeout, observer internal exception, malformed observer report).

## What running the tests actually does (not read-only)

Across `tests/test_langgraph_control.py` and `tests/test_langgraph_control_verify.py`: 12 full
`run_primary_control()` pipeline executions (each spawning a ledger daemon, a worker subprocess, and
an observer subprocess) - a `primary_bundle`/`valid_bundle` module-scoped fixture reused read-only
across most tests in each file (2 total), plus distinct-identity (2 runs), no-leftover-process
(1 run), worker-timeout (1 run whose worker never completes by design), observer-timeout (1 run
whose observer never completes by design), and, new in round 2, the production-path malformed-observer
-report tests: `test_malformed_observer_report_through_production_path` (4 runs, one per parametrized
payload - `[]`, `null`, `{}`, invalid JSON - each a real worker + real observer execution with the
observer's real output file overwritten afterward, not a mock) and
`test_malformed_observer_report_would_crash_without_shape_validation` (1 run, the paired red-proof,
with `_validate_observer_report_shape` monkeypatched to a no-op so it reproduces the original
`AttributeError`). Confinement and observer-exit-status tests reuse the `valid_bundle` fixture's
already-recorded output via mutated copies - they do not spawn new subprocess pipelines. Two
additional standalone subprocess invocations: the missing-admission-row worker-only test, and the
dynamic `sys.modules['langgraph']=None` verifier-blocking test (a check of the verifier, not a control
run). The observer-internal-exception and checkpoint-table-missing tests call
`langgraph_control_observer.main()` / `langgraph_control._safe_thread_checkpoint_count()` directly, no
subprocess. None inject a crash; none approach benchmark scale.
