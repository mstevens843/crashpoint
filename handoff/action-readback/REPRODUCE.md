# Reproduce

All commands from the worktree root:
`/Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-action-readback-2026-09-20`
(a sibling worktree, branch `experiment/action-readback`, based on the published TTL commit
`b66e205e69927626072cb4a130351258e9ac282d`). No third-party package or isolated venv is needed -
this experiment uses only crashpoint's own out-of-process ledger and its own SQLite admission
fixture.

## 1. Environment

```bash
uv sync --group dev
```

## 2. Confirmatory batch (produces a NEW evidence bundle; the runner refuses to overwrite)

```bash
./.venv/bin/python -m crashpoint.harness.action_readback --name <new-bundle-name>
```

Expect: `18/18 trials`, `status=COMPLETE`, `all trials agree with the pre-registered prediction:
True`, matching the per-case outcomes table in `results/13-action-readback.md`. Wall clock ~1s.

## 3. Offline verification (in place and after relocation)

```bash
./.venv/bin/python -c "
from pathlib import Path
from crashpoint.harness.action_readback_verify import verify_bundle
print(verify_bundle(Path('evidence/action_readback/<new-bundle-name>'))['problems'])
"
```

Expect `[]`. Then copy the bundle directory anywhere else on disk and re-run the same call against
the new path - expect the identical `[]`.

## 4. Permanent test suite

```bash
./.venv/bin/python -m pytest tests/test_action_readback.py tests/test_action_readback_receipt.py tests/test_action_readback_verify.py -q
# 75 passed
```

## 5. Full gates

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check .
./.venv/bin/python -m mypy --no-incremental --cache-dir=/dev/null
```

Exact expected output for each is in `TEST-RESULTS.md`.

## 6. Self-review (optional; not required to trust the evidence, since it is already baked into
the permanent test suite above)

The Part-B mutation audit and Part-C disabled-check proofs described in `SELF-REVIEW.md` were run
from ad hoc scratch scripts kept only in the session's local scratchpad (never committed, since
they are review artifacts, not part of the experiment). Every one of their findings that survived
is now a permanent test in step 4 above; there is nothing further to run to reproduce the
self-review's conclusions beyond running the permanent suite.

## Notes for anyone copying these commands

- The `--name` bundle name must be new; `action_readback.run()` raises `FileExistsError` rather
  than overwrite an existing bundle, by design (so a fresh capture can never silently clobber
  retained evidence).
- No command above pushes, commits, purchases anything, or contacts an external service - the
  ledger daemon is a local subprocess reachable only over a Unix socket in a temp directory.
- If reproducing on a different machine, no isolated per-release venv is required (unlike the TTL
  experiment this worktree is based on) - `uv sync --group dev` is sufficient.
