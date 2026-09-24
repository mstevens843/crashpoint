# Recovery-fix verification handoff

The narrow fix is verified: three fresh v1.8.1 post-effect crash trials recover as
`STARTED / NOT_REQUIRED`; three equivalent v1.8.2.2 trials recover as `UNKNOWN / REQUIRED`.
Each retains exactly one receiver effect. Both clean controls succeed, and all eight tested
original-plan retries add zero effects. The two candidate trust probes leave the action unresolved
and add no effect, with different MCP and REST responses.

- [Report and measured old/new table](../../results/15-reality-layer-recovery-followup.md)
- [Frozen source-informed plan](../../results/15-reality-layer-recovery-plan.json)
- [Final eight-trial evidence](../../evidence/reality_layer/recovery-1822-20260924)
- [Raw-derived results](results-derived.json), [self-review](SELF-REVIEW.md), [exact gates](GATES.md)
- [Reproduce or independently verify](REPRODUCE.md)
- [User-only staging, commit, push and comment commands](PUBLISH.md)
- [Explicit publication file list](publication-files.txt)

Gates: **88 focused passed; 503 full-suite passed, 22 optional-fixture skips**. Lint, fresh types
(90 files), Node syntax, formatting and whitespace pass. Offline verification and a second raw
implementation pass in place and after relocation with tested original-read/network/process denials.
Old-version property RED follows successful evidence validation and fails only the intended status
and reconciliation requirements. The published historical bundle still verifies unchanged.

Worktree: `/Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-reality-layer-recovery-2026-09-24`.
Branch: `experiment/reality-layer-recovery-1822`.
Foundation HEAD: `4c51e30972afb3ec272cfde0278c6ec56acf86ee`.
Origin: `https://github.com/mstevens843/crashpoint.git`.

All work remains **unstaged, uncommitted, unpushed and unposted**. Sibling checkouts and the original
experiment's deliberate comment edits/exploratory files are preserved; no owned runtime remains.
The draft's links are explicitly UNPUBLISHED. After the user's push, the formatter checks the real
remote commit and prints real commit-pinned URLs. Reply at
https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5807272468.

This is a same-host bounded recovery experiment, not an adoption pilot or universal exactly-once
claim. The consumed/lost-plan retry does not establish direct executor or concurrent-dispatch
behavior. No upstream production code was modified or redistributed.
