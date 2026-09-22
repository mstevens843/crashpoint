# Reality Layer crash/readback handoff

The bounded experiment is complete. Nothing is staged, committed, pushed or posted.
Publication links remain **unpublished** until the user commits and pushes this branch.

- Branch: `experiment/reality-layer-crash-readback`.
- Worktree: sibling `crashpoint-reality-layer-2026-09-22`.
- Published Crashpoint base: `bb9cd47c4b0b02527aab7b369d17b32829cc4e20`.
- Reality Layer v1.8.1 pin: `c9d1ca86969f5567cf771ab8a0f3247770a1dfb7`; no newer remote patch found.
- Verified remote: `origin` → `https://github.com/mstevens843/crashpoint.git` (fetch and push).
- Node v22.22.1; Python 3.12.13; inherited lockfile/dependency declarations unchanged.
- Final run: `f5c50fd0-f821-45ed-9c57-8920d673d4e8`.
- Manifest SHA-256: `48d8033761025e29c6da02ddbe12ca1f59de0cbf487c0138b45a0bf7f6a71b86`.

## Outcome

Nine final valid trials, zero invalid final trials. Post-effect SIGKILL (3 trials) and pre-effect
SIGKILL (1) retained queryable `STARTED` with reconciliation not required; the tested public
reconciliation calls did not change those records. All nine original-plan retry attempts were
rejected, with no additional effect. Ordinary post-effect errors yielded `FAILED`; the explicit
unknown signal yielded `UNKNOWN` and reconciled through the disclosed note primitive. Truncated
and missing subject ledgers both yielded missing-action responses while the independent receiver
still retained one effect. The missing file became `[]`; malformed bytes remained malformed.

Read the [report](../../results/14-reality-layer-crash-readback.md),
[claim matrix](CLAIMS.md), [self-review](SELF-REVIEW.md), and [reproduction instructions](REPRODUCE.md).
The [derived counts](results-derived.json), [run catalog](run-catalog.json), and
[final bundle](../../evidence/reality_layer/reality-layer-final-20260922) distinguish all categories:
10 exploratory trials, 36 superseded confirmatory trials, 9 final confirmatory trials, one initial
preflight rejection without a trial, and 25 deliberately rejected QA runs across review passes.
The QA runs contain 23 trial records; two source-read failpoints launched no trial. Only the final
nine-trial bundle and final nine QA failure runs are selected for publication; other captures are
preserved locally, unchanged.

## Changes and commands actually checked

All changes are additions scoped to this experiment. No inherited tracked file was edited.
The exact publication file list is [publication-files.txt](publication-files.txt), one literal
file path per line. It includes the five `reality_layer*.py` harness modules, one test module,
four files under `runtime/reality-layer/`, the report/frozen plan, the final evidence/QA files,
and the explicitly listed handoff files. Earlier local captures and development logs are excluded.
Do not stage the containing directories wholesale.

| Command / check | Actual exit | Result |
|---|---:|---|
| `uv sync --locked --group dev --all-extras` | 0 | Pinned inherited dependencies installed |
| Final harness capture, frozen `results/14-reality-layer-plan.json` | 0 | 9 valid final observations |
| `uv run --no-sync python -m crashpoint.harness.reality_layer_verify evidence/reality_layer/reality-layer-final-20260922` | 0 | Offline evidence valid |
| `uv run --no-sync pytest` (real failure paths enabled) | 0 | 474 passed, 22 skipped; all 59 new tests passed |
| `uv run --no-sync ruff check .` | 0 | Repository lint passed |
| `uv run --no-sync mypy` | 0 | 88 files passed |
| `uv run --no-sync ruff format --check` on the new harness/tests/regression | 0 | Formatting passed |
| `node --check` on shim and client | 0 each | Syntax passed |
| `--audit-source` at the retained pin | 0 | All 77 tracked upstream source hashes matched |
| `npm test` in audited isolated upstream checkout | 0 | Upstream suites passed |
| `uv run --no-sync python handoff/reality-layer/verify-relocated.py` | 0 | Relocated source loaded; original paths/network/processes denied |
| Proposed `contract_regression.py` against final bundle | **1, expected** | Four recovered `STARTED` records did not require reconciliation |
| Nine final CLI failure injections | **1 each, expected** | Partial retention checked; all owned children reaped |

Exact command arrays, durations and log filenames are in [checks.json](checks.json). The pytest
log replaces only this worktree's absolute path with `<experiment-worktree>`; no exception content
was removed. Raw originals remain under ignored `work/reality-layer-final-gates/`. The 22 skips
are explicitly unavailable inherited fixtures (19 SafeAgent, 1 isolation, 2 TrueForge), not failed
new tests. Earlier red/green-test mistakes and the initial relocation assertion failure are
recorded in self-review; they were fixed, not counted as successful negative controls.

## Limits and readiness

Same-host, same-user fixture; no real providers, live devices, model calls, concurrent dispatch,
distributed/power-loss claims or proof that an unresolved action is impossible to recover by all
other means. No public duplicate bypass was reproduced. The generic reconciliation note is an
explicit upstream test primitive. Runtime source is not redistributed because licensing was not
established; source retrieval/provenance audit is separate from offline evidence validity.
The final [closeout checks](closeout.json) cover retained historical hashes, main checkout status,
process cleanup, publication scanning and the final file inventory. The work is ready for user
review and publication using the explicit list below.

## User-only publication commands

From this experiment worktree (not the main checkout):

```bash
set -euo pipefail
test "$(git branch --show-current)" = experiment/reality-layer-crash-readback
test "$(git remote get-url --push origin)" = https://github.com/mstevens843/crashpoint.git
git --literal-pathspecs add --pathspec-from-file=handoff/reality-layer/publication-files.txt
git --no-pager diff --cached --stat
git --no-pager diff --cached --check
git commit -m "Add Reality Layer crash/readback experiment and strict evidence checks"
git push --set-upstream origin experiment/reality-layer-crash-readback
python3 handoff/reality-layer/format-comment.py
```

The formatter derives URLs from the actual new `HEAD`, confirms that the remote branch points to
that SHA and that the artifacts exist in that commit, then prints the complete GitHub-formatted
[draft reply](github-comment-draft.md). It does not post anything. Destination:
<https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5777277483>.
