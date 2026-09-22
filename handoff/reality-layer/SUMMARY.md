# Reality Layer retention follow-up

The reproduced retention defect is corrected and verified. The prior experiment commit
`be91c322165f7bb472201e9ab99fed97f171a6d0` is already published on
`experiment/reality-layer-crash-readback`. This follow-up is **unstaged, uncommitted and unpushed**.
The GitHub results comment remains on hold; nothing was posted.

- Worktree: sibling `crashpoint-reality-layer-2026-09-22`.
- Verified origin: `https://github.com/mstevens843/crashpoint.git`.
- Original experiment foundation: `bb9cd47c4b0b02527aab7b369d17b32829cc4e20` (the plan's `base`).
- Follow-up Git parent: `be91c322165f7bb472201e9ab99fed97f171a6d0`; use a normal new commit.
- Unchanged Reality Layer v1.8.1 pin: `c9d1ca86969f5567cf771ab8a0f3247770a1dfb7`.
- Python 3.12.13, Node v22.22.1; inherited lockfile/declarations unchanged.
- New final run: `91f1fc2d-5efd-43a4-a6dd-84f5f2f8d222`.
- Manifest SHA-256: `403921101a3aca6b64f3fe4142689609fba666e78a880f07e161a6430a7cb910`.

## Correction and evidence

An EIO reading `runtime-first.stdout` during final artifact hashing, after real cleanup, could
drop the completed trial from the manifest and skip both receipts. The published nine-trial
measurements remained valid; the failure-retention promise was incomplete.

The fix preserves readable hashes, identified trial/cleanup records and structured artifact
errors, writes an invalid receipt when possible, and reports no successful finding from incomplete
evidence. The batch owns the record before dispatch, including when a finalizer unexpectedly
escapes. If both receipt paths fail, their errors survive in the writable manifest. A cleanup
log-close error also preserves actual process results. The verifier and upstream runtime are unchanged.

The [red/green proof](retention-fix/red-green.json) fails specifically on the lost manifest trial
with the exact published harness in a disposable copy, then passes with the fix. See the
[before pair](retention-fix/paired-before.json), [after cases](retention-fix/paired-after.json),
[self-review](SELF-REVIEW.md), [claim matrix](CLAIMS.md), and [reproduction](REPRODUCE.md).

After code/tests were final, one new plan and nine-trial confirmatory bundle were captured:
[plan](../../results/14-reality-layer-retention-plan.json),
[authoritative bundle](../../evidence/reality_layer/reality-layer-retention-fixed-20260922),
[report](../../results/14-reality-layer-crash-readback.md), [derived counts](results-derived.json).
This is a source-informed repetition after a harness correction, not a newly blinded discovery.
Historical bundles are preserved; the old `run-catalog.json`, `checks.json`, `closeout.json` and
`capture-final.json` describe the previous publication, not this follow-up.

## Results and gates

The new bundle has nine valid trials and zero invalid trials. Post-effect SIGKILL (3/3) and
pre-effect SIGKILL (1/1) retain `STARTED` with reconciliation not required; tested public
reconciliation leaves those records unchanged. All nine original-plan retries are rejected with
zero added effects. Ordinary post-effect errors yield `FAILED`; explicit unknown errors yield
`UNKNOWN` and reconcile via the disclosed note primitive. Corrupt/missing subject ledgers return
missing-action responses while the receiver retains one effect. Total receiver effects: eight.

| Follow-up gate | Actual result |
|---|---|
| New frozen confirmatory capture | Exit 0; 9 valid trials |
| Offline verification in place | Exit 0 |
| Relocated retained verifier, site packages disabled | Exit 0; original paths/network/process access denied, no Node in PATH |
| Focused suite, all real cases enabled | Exit 0; **68 passed** |
| Full repository suite, all real cases enabled | Exit 0; **483 passed, 22 skipped** |
| Ruff | Exit 0 |
| mypy | Exit 0; 88 source files |
| Scoped formatting | Exit 0 |

[Exact commands/logs](retention-fix/checks.json) retain the final gate outcomes. The 22 skips are
unavailable inherited fixture environments: 19 SafeAgent, 1 macOS isolation, 2 TrueForge. All
Reality Layer tests ran. Nine added cases cover real control/read-error/both-receipt/finalizer/
cleanup paths and four deterministic inventory failures. Existing primary-write fallback and
verifier mutations also passed. The [QA catalog](retention-fix/qa-catalog.json) retains 14 final
focused captures (one control, 13 expected failures), separate from the confirmatory matrix.
Public logs replace local path prefixes and trim line-end whitespace; raw originals remain under ignored
`work/reality-layer-retention-fix/`. No new upstream test campaign was performed.

## Scope and closeout

Same-host, same-user fixture; no providers/model APIs/devices, distributed or power-loss claims,
concurrent dispatch, duplicate bypass or generic replay-safety guarantee. The reconciliation note
is the upstream test primitive. Runtime source is omitted for licensing; the unchanged pin and
77-file inventory support a separate provenance audit. Offline hashes establish consistency,
not authorship. Receipt retention requires a writable destination; disk-wide write failure cannot
be promised persistence. This correction is in Crashpoint's harness, not a Reality Layer patch.

[Follow-up closeout](retention-fix/closeout.json) records historical byte preservation, frozen
source checks, main/subject checkout state, owned process cleanup and publication scanning.
Only the explicit [publication file list](publication-files.txt) is intended for the follow-up;
it excludes older untracked exploratory bundles and development captures. Do not stage entire
directories indiscriminately.

## User-only follow-up publication commands

From the main or experiment checkout, these commands have **not** been executed. Review the
staged diff before committing. Do not amend
the published parent, create another branch, or force-push.

```bash
set -euo pipefail
cd ../crashpoint-reality-layer-2026-09-22
test "$(git branch --show-current)" = experiment/reality-layer-crash-readback
test "$(git remote get-url --push origin)" = https://github.com/mstevens843/crashpoint.git
git --literal-pathspecs add --pathspec-from-file=handoff/reality-layer/publication-files.txt
git --no-pager diff --cached --stat
git --no-pager diff --cached --check
git --no-pager diff --cached
git commit -m "Preserve Reality Layer trial receipts on artifact read failures"
git push origin experiment/reality-layer-crash-readback
python3 handoff/reality-layer/format-comment.py
```

The formatter uses the actual new HEAD, verifies that the remote branch points to it and that
linked artifacts exist in that commit, then prints the complete
[GitHub reply](github-comment-draft.md) with commit-pinned URLs. It does not post. No future commit
URL is invented. Intended destination:
<https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5777277483>.
