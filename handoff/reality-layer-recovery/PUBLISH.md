# User-only publication handoff

The work is local, unstaged, uncommitted and unpushed. No comment has been posted. The
[`publication-files.txt`](publication-files.txt) list names every intended file individually,
including the frozen plan, final evidence, retained verification code, report, tests and handoff.
It excludes all development/QA captures in ignored `work/`, sibling experiments and their
uncommitted shortened comments. Review the [report](../../results/15-reality-layer-recovery-followup.md)
and [gate results](GATES.md) before publication.

Run this staging block yourself. It checks the absolute working directory, branch, both origin
URLs, published foundation and empty index before staging the explicit list. A failed check
stops the `&&` chain without changing interactive-shell options.

```bash
cd /Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-reality-layer-recovery-2026-09-24 &&
test "$(git branch --show-current)" = experiment/reality-layer-recovery-1822 &&
test "$(git remote get-url origin)" = https://github.com/mstevens843/crashpoint.git &&
test "$(git remote get-url --push origin)" = https://github.com/mstevens843/crashpoint.git &&
test "$(git rev-parse HEAD)" = 4c51e30972afb3ec272cfde0278c6ec56acf86ee &&
test -z "$(git diff --cached --name-only)" &&
git --literal-pathspecs add --pathspec-from-file=handoff/reality-layer-recovery/publication-files.txt &&
git --no-pager diff --cached --stat &&
git --no-pager diff --cached --check
```

After reviewing the staged files, run the normal commit and push yourself:

```bash
cd /Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-reality-layer-recovery-2026-09-24 &&
test "$(git branch --show-current)" = experiment/reality-layer-recovery-1822 &&
test "$(git remote get-url --push origin)" = https://github.com/mstevens843/crashpoint.git &&
git --no-pager diff --cached --check &&
git commit -m "Verify Reality Layer recovery fix against independent effect readback" &&
git push origin HEAD:refs/heads/experiment/reality-layer-recovery-1822 &&
python3 handoff/reality-layer-recovery/format-comment.py
```

The formatter is read-only. It checks the actual remote branch SHA, verifies that every listed
file's working bytes equal that pushed commit, reads the committed draft, checks linked artifact
objects and prints commit-pinned report/evidence URLs. It refuses to assert publication links
before the push, on a branch/remote mismatch, or when local publication files differ. It does
not invent a future SHA, assume remote equals local HEAD, or post anything.

Reply destination:
https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5807272468

Paste the formatter's output as the results reply. The [draft](github-comment-draft.md) currently
marks its links **UNPUBLISHED**. It is a results comment, not another acknowledgement. This
handoff neither starts an adoption pilot nor authorizes automatic publication.
