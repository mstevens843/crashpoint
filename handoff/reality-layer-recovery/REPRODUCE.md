# Reproduce the pinned recovery comparison

This is a source-informed post-fix verification. The final bundle is
[`recovery-1822-20260924`](../../evidence/reality_layer/recovery-1822-20260924), with exactly
four primary trials per version: one clean completion and three post-effect SIGKILL trials.
The two trust probes are adjuncts on candidate `post_crash-0`. Development and QA runs are
separate and are not additional primary repetitions.

## Offline verification

From a checkout containing this handoff, no subject checkout, Node, dependency installation,
network or provider access is needed to verify the retained evidence:

```bash
cd /Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems/crashpoint-reality-layer-recovery-2026-09-24
PYTHONPATH=src ../crashpoint/.venv/bin/python -m crashpoint.harness.reality_layer_recovery \
  --verify evidence/reality_layer/recovery-1822-20260924
PYTHONPATH=src ../crashpoint/.venv/bin/python -m crashpoint.harness.reality_layer_recovery \
  --verify evidence/reality_layer/recovery-1822-20260924 --property v181
PYTHONPATH=src ../crashpoint/.venv/bin/python -m crashpoint.harness.reality_layer_recovery \
  --verify evidence/reality_layer/recovery-1822-20260924 --property v1822
../crashpoint/.venv/bin/python handoff/reality-layer-recovery/independent-audit.py \
  evidence/reality_layer/recovery-1822-20260924
```

Expected exits: **0, 1, 0, 0**. The old property command first verifies all raw evidence,
then fails on the three old crash observations' status/requirement/state only. It does not fail
on imports, pins, schema or unavailable evidence. The default historical verifier still accepts
the unchanged published nine-trial bundle:

```bash
PYTHONPATH=src ../crashpoint/.venv/bin/python -m crashpoint.harness.reality_layer_verify \
  evidence/reality_layer/reality-layer-retention-fixed-20260922
```

For relocated verification, first copy the new bundle outside the original checkout parent.
Use a system Python 3.12 or the inherited interpreter to launch the retained script:

```bash
recovery_relocated="$(mktemp -d /private/tmp/reality-recovery.XXXXXX)/bundle"
cp -R evidence/reality_layer/recovery-1822-20260924 "$recovery_relocated"
PATH=/nonexistent ../crashpoint/.venv/bin/python -I -S \
  "$recovery_relocated/v1822/sources/handoff/reality-layer-recovery/offline-check.py" \
  "$recovery_relocated" \
  /Users/devlegacy/Desktop/projects/ai-gap-coverage-projects/third-party-systems
```

That script proves the retained verifier module was loaded from the relocated bundle, runs a
second stdlib-only raw-byte derivation, and tests audit-hook denial of original-checkout reads,
socket creation and external subprocesses. Python runs with isolated mode and site packages
disabled. Hashes establish consistency, not independent authorship or coordinated-rewrite resistance.

## Retrieve and audit the subjects separately

Neither pin has an established source redistribution license. Subject source is omitted from
the evidence. Offline verification validates the inventories' internal consistency; separately
retrieved Git objects are required to authenticate working bytes against those inventories.
Use new destinations, never overwrite configured or historical installations:

```bash
recovery_sources="$(mktemp -d /private/tmp/reality-sources.XXXXXX)"
git clone --no-checkout https://github.com/shimjaemandu/reality-layer.git "$recovery_sources/old" &&
git -C "$recovery_sources/old" checkout --detach c9d1ca86969f5567cf771ab8a0f3247770a1dfb7 &&
git clone --no-checkout https://github.com/shimjaemandu/reality-layer.git "$recovery_sources/new" &&
git -C "$recovery_sources/new" checkout --detach 4213c479bd9333558522db1ca820d657b99effbf
PYTHONPATH=src ../crashpoint/.venv/bin/python -m crashpoint.harness.reality_layer_recovery \
  --baseline "$recovery_sources/old" --candidate "$recovery_sources/new" \
  --plan results/15-reality-layer-recovery-plan.json --audit-source
```

The final audit compared all **77 old and 80 candidate tracked files** with their exact pinned
Git blobs. The harness copies only verified `server.js`, `package.json`, `src/` and `integrations/`
files into isolated per-trial scratch state. It does not run install hooks or upstream launch
scripts, copy user configuration, or change either original subject checkout.

## Fresh real-process capture

The measurement environment used Python **3.12.13** from the inherited locked environment and
Node **v22.22.1** at `/usr/local/bin/node`. The Homebrew Node binary differs on this host.
No dependency or lockfile upgrade is needed. On another machine, prepare these versions before
capture. Verification of existing evidence does not require exactly these interpreter versions.

```bash
export PATH=/usr/local/bin:/usr/bin:/bin
/usr/local/bin/node --version
../crashpoint/.venv/bin/python --version
recovery_run="$(mktemp -d /private/tmp/reality-rerun.XXXXXX)"
PYTHONPATH=src ../crashpoint/.venv/bin/python -m crashpoint.harness.reality_layer_recovery \
  --baseline "$recovery_sources/old" --candidate "$recovery_sources/new" \
  --plan "$recovery_run/plan.json" --freeze
PYTHONPATH=src ../crashpoint/.venv/bin/python -m crashpoint.harness.reality_layer_recovery \
  --baseline "$recovery_sources/old" --candidate "$recovery_sources/new" \
  --plan "$recovery_run/plan.json" --output "$recovery_run/bundle"
PYTHONPATH=src ../crashpoint/.venv/bin/python -m crashpoint.harness.reality_layer_recovery \
  --verify "$recovery_run/bundle"
```

The source freeze includes capture, client/shim, observer/receiver, verifier, permanent tests,
second auditor, dependency declarations and lockfile. Output and plan collisions are refused.
The controller has bounded waits and finally-based termination/reaping. The runtime receives an
allowlisted environment with `REALITY_LIVE=0` and `PC_ADAPTER_DRY_RUN=1`. All endpoints bind
loopback; the only effect is a harmless receiver append. MCP/REST session credentials are random,
kept in memory and omitted from wire transcripts. There are no model/provider/device calls.

## Permanent gates

Use the same PATH and inherited interpreter. The full command/environment/exit/timing inventory
is in [checks.json](checks.json); [gate results](GATES.md) explains expected RED and skipped tests.
Set both subject variables to clean pinned source retrievals to enable the real protocol tests:

```bash
export CRASHPOINT_REALITY_SUBJECT="$recovery_sources/old"
export CRASHPOINT_REALITY_CANDIDATE="$recovery_sources/new"
PYTHONPATH=src ../crashpoint/.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_reality_layer.py tests/test_reality_layer_recovery.py
PYTHONPATH=src ../crashpoint/.venv/bin/python -m pytest -q -p no:cacheprovider
../crashpoint/.venv/bin/python -m ruff check src tests handoff/reality-layer-recovery/*.py
../crashpoint/.venv/bin/python -m mypy --no-incremental src tests
```

The verifier treats missing/unreadable effects as invalid evidence, never zero effects. The
retention tests deliberately inject failures after real cleanup; both receipt-write failures
must still leave the identified attempted trial in a writable manifest. These are harness QA
negative controls, not subject-runtime findings.
