# Handoff: Pre-dispatch action identity / external readback

Single authoritative summary for this task. The detailed narrative and claim boundary live in
[`results/13-action-readback.md`](../../results/13-action-readback.md); the mandatory self-review
findings live in [`SELF-REVIEW.md`](./SELF-REVIEW.md); exact commands/outputs in
[`TEST-RESULTS.md`](./TEST-RESULTS.md) and [`REPRODUCE.md`](./REPRODUCE.md); the per-claim
evidence table in [`CLAIM-MATRIX.md`](./CLAIM-MATRIX.md); the future-comparison scaffolding in
[`FIELD-MAPPING.md`](./FIELD-MAPPING.md).

## Status: complete after six review rounds - each of the first five was not sufficient alone

Built end to end: schema, runtime (worker/observer subprocesses), harness, offline verifier,
permanent tests, an 18-trial confirmatory batch, then switched from author to adversarial reviewer
before first reporting anything final (**Round 1**). That review found and fixed three real
production bugs (a missed `receipt.json` write, an observer-launch failure that crashed the whole
trial instead of degrading gracefully, and a verifier check whose own SQL WHERE clause made it
unable to ever detect the thing it claimed to check), added two more resilience fixes directly
from the assignment's own injection-point checklist, and proved six high-risk checks load-bearing
by disabling each in a disposable copy.

**That was reported as complete, and it was not.** An independent review (Codex) then ran its own
mutation script against the same bundle and found 12 further real gaps Round 1 had missed - 9
silently accepted by the verifier, 3 that crashed it outright instead of producing a structured
diagnostic. **Round 2** fixed every one of those 12, added a permanent test for each (ported
directly from Codex's own script), and re-applied the same disable-and-prove discipline to the
three highest-risk new checks.

**Asked to then review that completion the same adversarial way, rather than declare done again**,
**Round 3** found 11 further real gaps of the same general kind (fields and events the schema/
runtime actually define that were simply never cross-checked), by enumerating what the code
actually produces instead of trusting what had already been reviewed - the method Round 1 should
have used from the start. It also caught and corrected a near-miss in its own first pass: 3 of the
11 were almost misreported as already-caught because of an unsynchronized test fixture, not a real
check.

**Asked for one further sweep**, **Round 4** moved the same method up to the manifest level
(`status`, `cases`) and found 2 more real gaps - one of them a direction (a genuinely complete run
mislabeled INCOMPLETE) that the OTHER direction's check had been coincidentally, not deliberately,
silent about.

**Asked again**, **Round 5** found 5 more: 4 by auditing the schema's own internal-consistency
rules (`validate_receipt`), not just the offline verifier's file-based cross-checks - a `clean`
trial could fabricate `worker_b_pid`/`worker_b_attempt_id`/`worker_b_exit_status`/
`worker_b_local_receipt` with nothing catching it at either layer. The fifth was structurally
deeper than anything found before: the observer subprocess's raw stdout was never retained at all,
so `observer_pid` could only ever be cross-checked against `observer_report.json` - a file this
same harness process derived from the same data, not genuinely independent evidence, unlike the
workers' own retained raw stdout. That is the first fix across all five rounds that changed what
the **harness** writes, not only what the verifier checks, so the evidence bundle was recaptured
end to end.

**Asked again for one more sweep, Round 6** went one level more fundamental than any prior round:
the schema defines pure `derive_external_outcome`/`derive_observation_availability`/
`derive_client_claim` functions to PRODUCE `observed_result`, but nothing ever called them again to
independently RE-DERIVE and check it - `validate_receipt` only checked the three fields were valid
enum members, then recomputed `externally_verified` from them, never recomputing the three
themselves from the raw evidence they actually derive from. This let a fabricated matching effect
be accepted, with zero problems reported, on an `unavailable_readback` trial specifically - the one
case with no `ledger.jsonl` retained at all, so the ledger-based cross-check simply never runs.
While implementing the fix, a second gap surfaced: the same four fields (plus `protocol_valid`)
are each stored twice in the receipt - flattened at the top level and nested in `observed_result` -
and the verifier only ever reads the nested copy, so the top-level copy was a completely
unconstrained duplicate. Both fixed; both proven load-bearing via disable-and-prove. Full honest
account of all six rounds, including the Round 3 near-miss and Round 6's own methodological
near-miss (recognized and worked through in-pass, not from outside feedback), in
[`SELF-REVIEW.md`](./SELF-REVIEW.md) - this summary does not minimize that the first five
"complete" claims were each wrong.

## Where this lives

- Worktree: `/Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-action-readback-2026-09-20`
- Branch: `experiment/action-readback`, based on the published TTL commit
  `b66e205e69927626072cb4a130351258e9ac282d`
- The main checkout (`.../third-party-systems/crashpoint`) and every sibling worktree
  (`crashpoint-crewai-retry-repro-2026-09-13`, `crashpoint-langgraph-noncrash-control-2026-09-16`,
  `crashpoint-safeagent-ttl-2026-09-19`) were never entered or modified.
- This worktree's own `git status` shows only new, untracked files - no modification to any file
  inherited from the base commit (see `TEST-RESULTS.md` for the exact listing).

## Evidence bundles present (three; the third is authoritative)

- `evidence/action_readback/action_readback_corrected/` (159 files) - captured after Round 1's
  first three fixes but before its last three. Measured results identical to the authoritative
  bundle. Preserved, not referenced by any test or doc, rather than deleted.
- `evidence/action_readback/action_readback_self_reviewed/` (159 files) - captured after every
  Round 1 fix; was the authoritative bundle through Rounds 1-4. Superseded by Round 5's harness
  change (it has no `observer.stdout`/`observer.stderr` and correctly fails the current, stricter
  required-file check). Preserved, byte-for-byte, as historical record rather than edited in
  place - the same "version forward, never rewrite frozen evidence" discipline used elsewhere in
  this repo.
- `evidence/action_readback/action_readback_self_reviewed_v2/` (still 18 trials; file count per
  trial grew by 2 for the new `observer.stdout`/`observer.stderr`) - the **current, authoritative**
  bundle: captured after every fix through Round 5, so its embedded provenance
  (`sources/harness/*.py`) reflects the code that actually produced it. Round 6's fixes were both
  entirely within `validate_receipt` (a pure function of an already-produced receipt), not the
  harness's own emission logic, so no recapture was needed - re-verified clean (0 problems) against
  the fixed verifier, in place and after relocation, after each Round 6 fix. Receipt:
  `cp1_7b5610ba2a59e074d440b4b08975c2c0731ea0031a87da88aeb61140a751a90f` (unchanged from Round 5).

## Exact commands and results

See `TEST-RESULTS.md` for every command with its literal output: the confirmatory batch, offline
verification in place and after relocation, the 114-test permanent suite for this experiment, the
406-test full repository suite (31 skips, all pre-existing/unrelated), ruff, mypy, and the process/
evidence-hygiene checks.

## Observed matrix (18/18 matching the frozen prediction)

| Case | Observed `external_outcome` |
|---|---|
| `clean` | `ONE_EFFECT_MATCHING` (3/3) |
| `effect_before_lost_receipt` | `ONE_EFFECT_MATCHING` (3/3) |
| `stopped_before_effect` | `NO_EFFECT` (3/3) |
| `naive_retry` | `MULTIPLE_EFFECTS_MATCHING` (3/3) |
| `payload_mismatch` | `ONE_EFFECT_MISMATCHED` (3/3) |
| `unavailable_readback` | `INDETERMINATE` (3/3), `externally_verified=False` |

Full per-trial detail: `evidence/action_readback/action_readback_self_reviewed_v2/manifest.json`
and `.../trials/<trial_id>/`.

## Limitations

Also stated in every receipt and in `results/13-action-readback.md`. Same-host process separation,
not a sandbox or organization boundary. The receiver ledger is authoritative only for this local
fixture's harmless effect - not a payment provider. No distributed fencing, no host/power-loss
durability claim (bounded to a killed *worker*, with the admission store's WAL/`synchronous=FULL`
configuration recorded per trial, not assumed). 18 predeclared trials: no statistical
reliability-rate claim. No CrewAI/LangGraph/SafeAgent/payment-provider/SABLE integration, and no
SafeAgent Control comparison has been run (`FIELD-MAPPING.md`'s adapter status is explicitly NOT
RUN).

## Fresh re-run (new output directory required - the runner refuses to overwrite)

```bash
cd /Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-action-readback-2026-09-20
./.venv/bin/python -m crashpoint.harness.action_readback --name action_readback_rerun_$(date +%Y%m%d%H%M%S)
```

## Cleanup performed (owned resources only)

- All worker/observer/ledger-daemon subprocesses spawned during this session (the confirmatory
  batch, every smoke test, every live self-review test, the Part-C proof script) exit on their own
  or were reaped; `ps aux | grep -iE "action_readback|ledger.daemon"` returns nothing after the
  full test suite and batch regeneration.
- No cron jobs, background agents, or long-lived processes were created.
- Disposable module copies used for the Part-C proof were written only to the session's local
  scratchpad directory, never inside this worktree, and never committed.
- The evidence bundles and `.venv` remain on disk (reproducibility artifacts). Nothing outside
  this worktree was modified.

## Staging, commit, and push - left to Mathew, not executed

```bash
cd /Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-action-readback-2026-09-20

git add results/13-action-readback.md results/13-action-readback-prediction.json \
        src/crashpoint/harness/action_readback.py \
        src/crashpoint/harness/action_readback_receipt.py \
        src/crashpoint/harness/action_readback_runtime.py \
        src/crashpoint/harness/action_readback_verify.py \
        tests/test_action_readback.py tests/test_action_readback_receipt.py \
        tests/test_action_readback_verify.py \
        evidence/action_readback/ handoff/action-readback/

git commit -m "$(cat <<'EOF'
Measure pre-dispatch action identity linked to external readback

A fresh, caller-minted action ID is committed to a caller-owned SQLite
store and independently confirmed durable before dispatch; a separate
observer process, never a worker's exit code or a live daemon dump,
independently reads back what an out-of-process receiver ledger
actually retains. Six cases x 3 trials (18 total): clean completion,
effect-recorded-then-lost-receipt, killed-before-dispatch, a naive
unkeyed retry producing two real recorded effects in the correct raw
order, a deliberately mismatched payload exposed rather than folded
into success, and evidence deliberately denied after the fact, which
stays classified as unverified/indeterminate, never as zero effects or
confirmed success. 18/18 matched the frozen prediction.

Built with its own mandatory adversarial self-review, then reviewed
five more times after each "complete" claim turned out to be wrong -
once by an independent review (Codex, 12 findings) and four more
self-audits applying that same method (20 more findings: per-trial
fields/events/DB columns, manifest-level summary fields, schema
internal-consistency rules, the observer's raw stdout never being
retained at all (the one fix that changed what the harness itself
writes), and finally the schema's own derive_* functions never being
called again to independently re-derive and check the fields they
produce - caught via a fabricated effect accepted on the one case
with no ledger file retained to cross-check against). 32 real findings
total across six rounds, each with its own fix, permanent test, and
(for the highest-risk ones) a disable-in-a-disposable-copy proof that
the check is actually load-bearing. Full, unminimized findings in
handoff/action-readback/SELF-REVIEW.md.

This is a bounded reference fixture for a future native-vs-SafeAgent-
Control comparison (handoff/action-readback/FIELD-MAPPING.md), not
that comparison itself - no such comparison has been run, and no
CrewAI/LangGraph/SafeAgent/payment-provider code was touched.
EOF
)"

git push -u origin experiment/action-readback
```

After a successful push, derive pinned links from the real HEAD rather than guessing them ahead of
time:

```bash
SHA=$(git rev-parse HEAD)
echo "https://github.com/mstevens843/crashpoint/blob/${SHA}/results/13-action-readback.md"
echo "https://github.com/mstevens843/crashpoint/blob/${SHA}/handoff/action-readback/SELF-REVIEW.md"
echo "https://github.com/mstevens843/crashpoint/tree/${SHA}/evidence/action_readback/action_readback_self_reviewed_v2"
```

Nothing above has been run. Review the diff and evidence bundle first.
