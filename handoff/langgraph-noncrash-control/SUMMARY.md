# Handoff: LangGraph non-crash positive control

**v2 update:** a publication review (`CODEX-PUBLICATION-PROBE-RESULTS.json`) found six internally
contradictory bundles the v1 verifier accepted, two malformed-evidence inputs that raised uncaught
exceptions, and an `OBSERVER_ERROR` status the v1 receipt builder rejected instead of recording. All
fixed; every finding is mapped to its fix, test, and observed output in
`CODEX-CLOSEOUT-RESPONSE.md`.

**v3/v4 update (round 2 closeout):** an independent reproduction found four further gaps in the v2
work: an unvalidated `cast()` that let a malformed observer report (`[]`, `null`, `{}`, invalid JSON)
crash `run_primary_control()` with an uncaught `AttributeError` *after* the real worker had already
succeeded, losing the receipt entirely; a bundle-confinement bypass (`Path.__truediv__` silently
discards the bundle root when joined with an absolute path, so a mutated `contract.path` pointing
outside the bundle - with its hash recomputed to match - was wrongly accepted); an observer that
exits nonzero (`exit_status=2`) while still emitting a valid, hash-verified `OBSERVED` report was
wrongly treated as a passing control; and two documentation overclaims (implying the admission SQLite
row itself stores a durable timestamp, and "tested on another machine" for what was actually a local
relocation check). All four are fixed, each with a production-path test (the real worker and observer
subprocess run; the observer's real output is intercepted and corrupted afterward - not a parser unit
test) and a red/green proof that the regression fails without the fix and passes with it. See the
round-2 addendum in `CODEX-CLOSEOUT-RESPONSE.md`.

Because `observer.error` is a new required receipt field (a shape change, not just a new derived
rule), the schema was bumped to `crashpoint.langgraph_control.receipt.v3`. The authoritative bundle is
now `evidence/langgraph_noncrash_control_v4/`. `evidence/langgraph_noncrash_control/` (v1),
`evidence/langgraph_noncrash_control_v2/`, and `evidence/langgraph_noncrash_control_v3/` (recorded
mid-review, before the schema-bump decision, and itself superseded) are all retained byte-for-byte as
superseded records; the current verifier cleanly refuses all three as a schema mismatch rather than
silently reinterpreting them. This file and `REPRODUCE.md`/`TEST-RESULTS.md` below are updated to the
v4 state.

Isolated worktree: `crashpoint-langgraph-noncrash-control-2026-09-16` (sibling of the main
`crashpoint` checkout), branch `experiment/langgraph-noncrash-control`, branched from main at
`606893ebb353df5dab3ac68738051eb5fbb7286e` (verified clean and up to date with `origin/main` before
branching). **Nothing in this worktree has been committed, pushed, or posted anywhere.** All new
files below are uncommitted working-tree additions only.

## What this is

A single, narrowly scoped research control, separate from the crash/recovery experiment in
`results/10-langgraph-admission-ledger.md`. It follows
[`langchain-ai/langgraph#8764`](https://github.com/langchain-ai/langgraph/issues/8764#issuecomment-5700047295),
where socksninja requested one control with the evidence chain `admission_id -> thread_id ->
execution result -> external effect reference -> independently rereadable state hash -> receipt`.
Full narrative and the recorded result table: `results/12-langgraph-noncrash-control.md`.

**Existing evidence is untouched.** Every file under `evidence/` and `results/` that existed before
this work was inventoried by SHA-256 before any change; re-verified in the v2 closeout by direct
content diff against the untouched main checkout (`diff -rq`), since the v1 session's scratchpad hash
file did not survive across sessions - zero differences other than the new evidence subdirectories and
report. `git status --porcelain` shows zero modified or deleted tracked files - only new, additional
paths (`README.md`/`RESULTS.md` are the two intentionally modified tracked files, both minimal
additions).

## What was built

New files, all uncommitted:

- `src/crashpoint/harness/ledger_readback.py` - independent, from-bytes parsing/chain-verification
  of the out-of-process ledger's raw JSONL store. No LangGraph import. Shared by the observer and the
  offline verifier so both recompute the same thing from the same bytes.
- `src/crashpoint/harness/langgraph_control.py` - the harness (admission setup, contract/manifest,
  spawns the worker and observer subprocesses, assembles the native receipt) and the worker process
  entry point (`--worker`). Imports LangGraph locally inside `_build_app` only, matching
  `langgraph_admission.py`'s existing pattern.
- `src/crashpoint/harness/langgraph_control_observer.py` - the fresh observer subprocess entry
  point. No LangGraph import.
- `src/crashpoint/harness/langgraph_control_receipt.py` - the native receipt schema, structural
  validator, and `EXACTLY_ONCE/DUPLICATED/DIVERGED/LOST/VOID/UNVERIFIED` classifier. No LangGraph
  import.
- `src/crashpoint/harness/langgraph_control_verify.py` - the offline verifier CLI. No LangGraph
  import anywhere in its local-import closure (statically checked by
  `tests/test_langgraph_control_verify.py::test_verifier_closure_never_imports_langgraph`).
- `tests/test_ledger_readback.py`, `tests/test_langgraph_control_receipt.py`,
  `tests/test_langgraph_control.py`, `tests/test_langgraph_control_verify.py` - 108 focused tests
  (see TEST-RESULTS.md).
- `results/12-langgraph-noncrash-control.md` - the report.
- `README.md`, `RESULTS.md` - minimal additions (one new bullet/row each in the relevant sections
  plus a "what this does not prove" entry each); no existing claim was reworded.
- `evidence/langgraph_noncrash_control_v4/` - the authoritative, retained primary-control bundle:
  `receipt.json`, `contract.json`, `manifest.json`, `admission.sqlite`, `checkpoint.sqlite`,
  `ledger-readback.jsonl` (the observer's archived exact-byte copy), worker/observer stdout/stderr
  logs, `observer-report.json`, and `source/` (retained copies of the five executing harness
  modules, for portable hash verification after relocation).
- `evidence/langgraph_noncrash_control/` (v1), `evidence/langgraph_noncrash_control_v2/`, and
  `evidence/langgraph_noncrash_control_v3/` - retained byte-for-byte as superseded records (the
  current verifier correctly refuses all three as a schema mismatch, rather than silently
  reinterpreting them).
- `handoff/langgraph-noncrash-control/CODEX-CLOSEOUT-RESPONSE.md` - every publication-review
  finding from both review rounds mapped to its fix, permanent test, and observed output.

## The recorded result

One real, non-crashed LangGraph execution (v4, round-2-corrected code, schema v3). Native receipt
hash: `cp1_f644c93d28cebc7ab00eae2b8f24cb9df6de009f5004687d3eebbaa2773248a9`.

| Field | Value |
|---|---|
| `runtime.worker_ok` / `worker_exit_status` | `true` / `0` |
| `runtime.worker_returned_state` | `{"done": true}` |
| `admission.status_after_run` | `"completed"` (`accepted -> completed`) |
| `checkpoint.thread_scoped_checkpoint_count` | `3` |
| `observer.status` / `chain_valid` | `"OBSERVED"` / `true` |
| `observer.exit_status` | `0` |
| `observer.archive_matches_original` | `true` |
| `observer.error` | `null` |
| `effect.observed_count` | `1` |
| `result.oracle_classification` | `"EXACTLY_ONCE"` |
| `result.passed` | `true` |

Offline verification: `PASS: evidence/langgraph_noncrash_control_v4 is internally consistent with its
retained evidence` (exit 0, zero problems) in place, after copying the bundle to a different local
directory (not a separate machine - see the "Scope and boundary" note below), and run in a subprocess
with `sys.modules['langgraph'] = None` set before the verifier's own code runs, which would raise
`ImportError` immediately if any import in its closure ever touched LangGraph (the verifier's own
import closure never references it, checked both statically and via that dynamic block - see
TEST-RESULTS.md).

## Scope and boundary (do not over-read this)

This is a positive control and an evidence-portability test. It does **not** demonstrate a LangGraph
fix, crash recovery, a global exactly-once guarantee, provider idempotency, admission/dispatch
fencing, SABLE certification, or an independent third party rerunning the experiment. n=1 by design:
one recorded run, no statistical rate, no Wilson interval. The external effect is a harmless local
ledger fixture, not a real provider. A process ID is retained as diagnostic metadata only, never as
proof of isolation - the worker and observer run as the same OS user with no additional sandboxing.
The admission SQLite row itself stores identity, input, and the accepted/completed event sequence -
not a durable timestamp; the `accepted_at_utc` value in the receipt is generated by the harness
process right before that row is committed, not re-derived from the database. "Relocation" was
verified by copying the bundle to another local directory on the same machine, not by testing on a
separate machine. The offline verifier re-derives ledger, checkpoint, and admission facts from raw
retained bytes, but other runtime metadata (recorded versions, timestamps, PIDs, the outer receipt
hash) is checked for checksum consistency only, not independently re-authenticated.

## SABLE / publication status

Mathew already asked socksninja for a pinned submission schema and verifier command in the `#8764`
thread; socksninja offered to handle the field mapping. **No pinned schema or mapping was available**
at the time of this work, so none is integrated - this is stated as pending, not treated as a blocker
on the native control (per the assignment). The receipt's `sable_facing_mapping` field is a
descriptive-only note (native `EXACTLY_ONCE -> PASS`, `LOST/DUPLICATED/DIVERGED -> FAIL`,
`VOID/UNVERIFIED -> UNKNOWN`, admission status reported independently of effect count) that does not
alter or gate on native evidence. No GitHub Actions changes were made, no CrewAI artifact was
touched/republished, and no links/commit pins/CI run IDs are included anywhere (none exist yet - see
`github-reply-draft.md` for the explicitly marked placeholder).

## Confirmed NOT done (by design, per the assignment)

- No commits, no pushes, no GitHub comments/reactions/issue creation, no submissions of any kind.
- No edits to the main checkout or to the sibling TrueFoundry/Roboflow worktrees.
- No background/fork agents were used; all work in this session was direct.
- No benchmark-sized rerun: one primary control, plus the small real non-crash executions the
  assignment permits for focused regression tests (a handful, itemized in TEST-RESULTS.md).
- `langgraph_admission.py`'s existing measurements, its fixed thread ID, and its `recovery()` path
  are all unmodified and uncalled by this work.

## Next steps for a reviewer

1. Read `CODEX-CLOSEOUT-RESPONSE.md` for the finding-by-finding fix mapping.
2. Read `REPRODUCE.md` and run the listed commands yourself.
3. Read `TEST-RESULTS.md` for exact pass/skip/fail counts and what the skips are (all pre-existing
   and environmental, not regressions - reasoning included).
4. Inspect `evidence/langgraph_noncrash_control_v4/receipt.json` and the raw artifacts alongside it.
5. If posting a reply to `#8764` is wanted, review `github-reply-draft.md` first - it is a draft only
   and has not been posted.
