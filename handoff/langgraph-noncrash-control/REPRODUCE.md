# Reproduce: LangGraph non-crash positive control

Run everything from this worktree's root:
`crashpoint-langgraph-noncrash-control-2026-09-16/`. All commands below were actually run to produce
the recorded evidence and the numbers in `SUMMARY.md`/`TEST-RESULTS.md`. v2 update: the mypy
acceptance command now uses the *main checkout's* venv (`../crashpoint/.venv`), which has
`--all-extras` installed, per the assignment's final-acceptance instructions - see section 2. v4
update (round-2 closeout): the authoritative bundle is now `evidence/langgraph_noncrash_control_v4/`
(schema `crashpoint.langgraph_control.receipt.v3`); see section 1.

## 0. Environment

```bash
uv sync --group dev --extra langgraph --locked
```

This worktree is synced with `--extra langgraph` only, not `--all-extras` (the host was briefly under
1 GiB of free disk during the first session; `uv cache clean` reclaimed 2.3 GiB and disk has since
recovered to double digits of GiB free). This installs the exact locked versions the assignment named
- `langgraph==1.2.11`, `langgraph-checkpoint-sqlite==3.1.1`, `langgraph-checkpoint==4.2.0`,
Python 3.12.13 - and does not touch `uv.lock` (`git diff --stat uv.lock` is empty throughout).

## 1. Run the primary control and verify it offline

This is what actually produced `evidence/langgraph_noncrash_control_v4/` (the authoritative bundle;
`evidence/langgraph_noncrash_control/` (v1), `evidence/langgraph_noncrash_control_v2/`, and
`evidence/langgraph_noncrash_control_v3/` are earlier, superseded recordings, kept byte-for-byte -
see `CODEX-CLOSEOUT-RESPONSE.md`). Running it again will refuse (the harness rejects an existing
output directory before touching anything) - point `--output` elsewhere to try it yourself:

```bash
uv run --extra langgraph python -m crashpoint.harness.langgraph_control \
  --output /tmp/my-langgraph-control-run --name my_run

uv run python -m crashpoint.harness.langgraph_control_verify /tmp/my-langgraph-control-run
```

Expected: `oracle_classification: EXACTLY_ONCE`, `passed: True`, and
`PASS: ... is internally consistent with its retained evidence` from the verifier (exit 0). The
second command does not need LangGraph installed or imported - only `sqlite3`, `json`, and `hashlib`
from the standard library plus this repo's own `canonical`/`ledger_readback`/
`langgraph_control_receipt` modules (none of which import LangGraph; checked both statically -
`tests/test_langgraph_control_verify.py::test_verifier_closure_never_imports_langgraph` - and
dynamically, by running the verifier in a subprocess with `sys.modules['langgraph'] = None` set
before it even starts - `::test_verifier_subprocess_cannot_import_langgraph_even_if_it_tried`).

Malformed-observer-report production-path check (not a parser unit test: the real worker and real
observer both run; the observer's real output file is overwritten afterward), against
`tests/test_langgraph_control.py::test_malformed_observer_report_through_production_path`:

```bash
uv run pytest tests/test_langgraph_control.py -k malformed_observer_report -v
```

Expected: no uncaught exception, `receipt.json` is written, `observer.status == "OBSERVER_ERROR"` with
a non-null `observer.error`, `effect.observed_count is None`, `result.oracle_classification == "VOID"`,
`result.passed is False`, the available ledger bytes are still preserved (archived or parent-fallback),
and the offline verifier still returns zero problems against that honestly-failed bundle.

Relocation check (the verifier resolves every path as bundle-relative, so this must still pass):

```bash
cp -r /tmp/my-langgraph-control-run /tmp/relocated-somewhere-else
uv run python -m crashpoint.harness.langgraph_control_verify /tmp/relocated-somewhere-else
```

## 2. Required checks (repo-wide gate)

```bash
uv run pytest -q
uv run ruff check .
env PYTHONPATH=src ../crashpoint/.venv/bin/python -m mypy --no-incremental --cache-dir=/dev/null
git diff --check
```

The mypy command uses the *main checkout's* venv (`../crashpoint/.venv`, which has `--all-extras`
installed) rather than this worktree's narrower one, so DBOS/Temporal/CrewAI files that only fail
type-checking when those optional packages are absent check cleanly too - it does not resync or edit
`crashpoint/`, only reads its already-installed interpreter.

**Correction from the v1 handoff:** `git diff --check` reports whitespace errors/conflict markers in
the diff between the working tree and the index, for files git already tracks. It does **not** cover
untracked files - and every new file in this deliverable is untracked, since nothing is staged or
committed. Only `README.md`/`RESULTS.md` (the two tracked files this work modified) are covered by
that command; it is genuinely clean for them. For the untracked files, run the equivalent checks
directly instead (see TEST-RESULTS.md for the exact loop used - trailing whitespace, conflict
markers, missing trailing newline - over every new source/test/report/handoff file, without staging
anything or touching the index).

## 3. Disposable regression demonstrations (NOT read-only - each creates real, disposable output)

These are the same small real non-crash executions the automated test suite runs, shown standalone
for a reviewer who wants to watch one happen without running pytest. Each writes to a throwaway
directory; delete it afterward if you like.

```bash
# One real non-crash run into a scratch directory
uv run --extra langgraph python -m crashpoint.harness.langgraph_control \
  --output /tmp/scratch-control-a --name scratch_a

# Confirm a second real run gets a DIFFERENT identity (never reused across runs)
uv run --extra langgraph python -m crashpoint.harness.langgraph_control \
  --output /tmp/scratch-control-b --name scratch_b
python3 -c "
import json
a = json.load(open('/tmp/scratch-control-a/receipt.json'))
b = json.load(open('/tmp/scratch-control-b/receipt.json'))
assert a['identity']['admission_id'] != b['identity']['admission_id']
print('distinct identities confirmed')
"

# Overwrite refusal: this must fail with FileExistsError before touching anything
uv run --extra langgraph python -m crashpoint.harness.langgraph_control \
  --output /tmp/scratch-control-a --name scratch_a_again  # expect: FileExistsError, exit 1
```

The full negative-control matrix - missing readback, corrupt bytes, broken chain, wrong identity,
altered count/reference, missing required-nullable field, malformed JSON/types, invalid UTF-8, a
valid-zero and a valid-duplicate observation, runtime-success-with-unavailable-observer, the six
round-1 publication-review false accepts (`accepted_before_invoke=False`, `worker_exit_status`
disagreeing with `worker_ok`, `invoke_count` disagreeing with the contract, a retained `contract.json`
disagreeing with the receipt, a missing observer report, a missing required source module), and the
round-2 findings (an absolute `contract.path` escaping the bundle, a relative `..` traversal escape, a
symlink inside the bundle resolving outside it, a symlinked source-copy escape, and a nonzero
`observer.exit_status` alongside an otherwise-valid `OBSERVED` report - each with the outer hash
recomputed to match so hash-checking alone cannot catch it) - is exercised by
`tests/test_langgraph_control_verify.py`, each against a mutated COPY of one real recorded bundle
(built once per test session, not per test), with the outer receipt hash recomputed after every
mutation - run it directly to watch just that file:

```bash
uv run pytest tests/test_langgraph_control_verify.py -v
```

## 4. Publication status (nothing here was executed)

**This is locally executed recorded evidence, not a CI execution.** No GitHub Actions workflow was
changed to build, upload, or publish this record - `.github/workflows/ci.yml` is untouched by this
work. If this is later wired into CI the way the CrewAI evidence artifact is
(`crashpoint.harness.crewai_retry_artifact`, published by the existing `ci.yml` job), that would be a
separate, deliberate follow-up, not something this handoff does or assumes. No commit SHA, CI run/job
ID, or artifact digest is referenced anywhere in this work because none exists yet - see
`github-reply-draft.md` for the one explicitly marked placeholder (an unpublished URL for a reviewer
to fill in once this branch actually has a home).

Nothing in this worktree has been committed, pushed, or posted. `git status --porcelain` in
`crashpoint-langgraph-noncrash-control-2026-09-16/` shows only new, uncommitted paths.
