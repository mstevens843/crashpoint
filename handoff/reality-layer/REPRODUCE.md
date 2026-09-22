# Reproduce the bounded Reality Layer experiment

## Read-only, fully offline evidence verification

From this Crashpoint worktree, with Python 3.12 (capture used 3.12.13):

```bash
set -euo pipefail
bundle="$PWD/evidence/reality_layer/reality-layer-final-20260922"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$bundle/sources/src" \
  .venv/bin/python -S -m crashpoint.harness.reality_layer_verify "$bundle"
```

`-S` deliberately disables site packages, including an editable Crashpoint installation. The
retained source directories work as namespace packages. No subject runtime, SDK, model key,
network, live receiver or original checkout is required. Exit 0 means consistent, complete
retained evidence, including the measured runtime deficiencies; it does not mean the runtime
satisfied the desired reconciliation property. Exit 1 reports malformed/incomplete evidence.
Hashes establish internal consistency, not authorship or protection against coordinated rewriting.

The relocation control additionally copies the bundle to a new temporary directory, checks that
the verifier actually loads there, supplies an empty executable PATH, and installs a Python audit
hook denying network/process operations and reads from the original experiment/runtime checkouts:

```bash
uv run --no-sync python handoff/reality-layer/verify-relocated.py
```

This is local relocation on the same host, not validation by a second organization or machine.
The relocation copy is retained locally; its path is recorded only under ignored `work/`.

## Development checks

```bash
uv sync --locked --group dev --all-extras --python 3.12.13
uv run --no-sync pytest tests/test_reality_layer.py
uv run --no-sync ruff check .
uv run --no-sync mypy
```

The nine real-process failure-path tests skip unless `CRASHPOINT_REALITY_SUBJECT` names the
isolated baseline checkout. The remaining 50 tests are offline, including 35 evidence mutations,
14 single-guard removal/restoration tests, and the recorded public-API findings.

To run all relevant tests, including real failure paths, select Node **v22.22.1** in PATH first:

```bash
set -euo pipefail
test "$(node --version)" = v22.22.1
export CRASHPOINT_REALITY_SUBJECT="$PWD/../reality-layer-c9d1ca8-2026-09-22"
export CRASHPOINT_REALITY_QA="$PWD/work/reality-layer-qa-$(date -u +%Y%m%dT%H%M%SZ)"
uv run --no-sync pytest
```

No dependency upgrades are needed. `uv.lock` is inherited and unchanged. Captures refuse a
different Python patch version or Node version. On the measurement host `/usr/local/bin/node`
was v22.22.1; `/opt/homebrew/bin/node` was v22.21.0. Select the intended executable explicitly via
PATH; do not assume the first `node` in another shell is the same binary.

## Separate source-provenance retrieval

The baseline contains no redistribution license we could establish. Its source is **not** in the
public bundle. The bundle retains a 77-file pinned hash inventory; offline evidence verification
does not authenticate omitted runtime source. Retrieve it separately to audit source provenance:

```bash
set -euo pipefail
subject="$PWD/work/reality-layer-source-$(date -u +%Y%m%dT%H%M%SZ)"
git clone --no-checkout https://github.com/shimjaemandu/reality-layer.git "$subject"
git -C "$subject" checkout --detach c9d1ca86969f5567cf771ab8a0f3247770a1dfb7
uv run --no-sync python -m crashpoint.harness.reality_layer \
  --subject "$subject" --plan results/14-reality-layer-plan.json --audit-source
```

The audit compares local bytes with Git's pinned blobs and the retained inventory. It does not
claim signed third-party authorship. Do not point the harness at a configured live installation.
The checked-in package has no npm dependencies or install hooks. `npm test` was inspected and run
in the isolated checkout: its own runner copies source into fresh temporary directories.

## Genuinely fresh process-running experiment

After source retrieval/audit and runtime selection:

```bash
set -euo pipefail
test "$(node --version)" = v22.22.1
run="$PWD/work/reality-layer-fresh-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir "$run"
uv run --no-sync python -m crashpoint.harness.reality_layer \
  --subject "$subject" --plan "$run/plan.json" --freeze
uv run --no-sync python -m crashpoint.harness.reality_layer \
  --subject "$subject" --plan "$run/plan.json" --output "$run/bundle"
uv run --no-sync python -m crashpoint.harness.reality_layer_verify "$run/bundle"
```

This starts a fresh receiver/server/client/observer set for each trial. It performs real SIGKILL
at explicit pre/post effect barriers; other completion errors are deliberate adapter exceptions.
The source hash freeze is checked before capture. Existing output directories are rejected.
The harness creates fresh per-trial data directories, allowlists child environment variables,
sets `REALITY_LIVE=0` / `PC_ADAPTER_DRY_RUN=1`, and uses random test credentials only in child
memory. Request transcripts omit the Authorization header explicitly. No browser is launched.

The nine final cases come from the frozen inventory, not a caller-supplied repetition override.
The fixture's only effect is a receiver append. Source copies/data in ignored `work/` remain
local, including failed attempts. The main and sibling historical experiments are untouched.

## Adoptable regression

```bash
uv run --no-sync python runtime/reality-layer/contract_regression.py "$run/bundle"
```

Expected **exit 1** on this baseline: the four killed executions are queryable as `STARTED` with
reconciliation not required. This is a proposed assertion for a recovered unresolved action,
after the evidence verifier succeeds. It is not part of a falsely green runtime suite. A
maintainer can reuse the harmless adapter/barrier and assertion after choosing recovery policy;
the harness's source pin/hash inventory must be deliberately updated for a different revision.
No production patch or reconciliation/storage redesign is included.
