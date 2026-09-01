# 09 - Vercel Workflow, and the Temporal/DBOS hidden barriers

Working record for the 2026-09-01 follow-up that closed the four items entry 08 left open. The rule
is unchanged: a runtime enters the model only after a predicted row family and faithful
crash/recovery evidence exist, and a hidden barrier is measured only after its own rule is written
down first.

## What entry 08 left open, and what happened to each

| Open item | Outcome |
|---|---|
| Vercel Workflow blocked by `Invalid version string: "bundled"` | Root-caused and measured; four rows added |
| Temporal hidden barriers blocked on a dev server | Two barriers modeled and measured at k=30 |
| DBOS hidden barriers blocked on Postgres | Four barriers modeled and measured at k=30 |
| Native macOS UID isolation not claimed | Implemented; needs root, so still unreceipted |

## Vercel Workflow: the Local World blocker

The failure is upstream, not in the fixture. `@workflow/world-local/dist/init.js` resolves its own
`package.json` relative to `import.meta.url`. Nitro v3 inlines the package into
`.output/server/_libs/@workflow/core+[...].mjs`, so that lookup misses, `getPackageInfo()` falls
back to the literal version string `bundled`, and `initDataDir()` hands it to `parseVersion()`,
which accepts semver only. The throw happens before any run can start. The inlined chunk carries
the bug verbatim: the `"../package.json"` read, the `version: "bundled"` fallback, and the throw are
all present in the built server.

Nitro v3 has no per-package `external` escape hatch (only `noExternals` and `traceDeps`), so the fix
is at the runtime's own world-selection layer:

```
WORKFLOW_TARGET_WORLD=@workflow/world-local
```

That is a documented knob. It sends the runtime down its custom-world path,
`createRequire(process.cwd() + '/package.json')(targetWorld)`, which loads the unbundled package
from `node_modules`, whose real `package.json` is readable. The server must therefore run with
`runtime/vercel-workflow` as its working directory. Two copies of the world package then coexist,
which is safe by design: world packages keep state on `globalThis` through `globalSingleton`, and
`@workflow/core` identifies world and error classes by name-based `.is()` checks rather than
`instanceof`. Verified: the data directory's `version.txt` reads
`@workflow/world-local@5.0.0-beta.41`.

The second knob is `WORKFLOW_INLINE_OWNERSHIP_LEASE_SECONDS=1`. Steps run inline and stamp the queue
message that owns them; after a crash the fresh server's re-enqueued run sees a dead owner and
defers re-execution until the ownership lease expires. The default is 860 s and the documented
minimum is 1 s. Only the wait changes, not the recovery path.

Nothing in the runtime calls `world.start()`, so the harness hits `/api/health` after every start;
that is what re-enqueues the crashed run.

### Rows and evidence

`r_vwf_naive`, `r_vwf_idem`, `r_vwf_nondet`, `r_vwf_twophase` were added to `runtimes.py` with the
predicted b1 column DUP / ONCE / DIVERGE / ONCE before any Vercel process was crashed. The matrix is
now 26 rows.

```
uv run python -m crashpoint.harness.vercel_matrix --k 30 --name vercel --timeout 60
```

```
runtime                      before_effect           between     after_persist
------------------------------------------------------------------------------
vercel_workflow_naive            ONCE/ONCE           DUP/DUP         ONCE/ONCE
vercel_workflow_idem             ONCE/ONCE         ONCE/ONCE         ONCE/ONCE
vercel_workflow_nondet           ONCE/ONCE   DIVERGE/DIVERGE         ONCE/ONCE
vercel_workflow_twophase         ONCE/ONCE  ONCE/ONCE@0.9333         ONCE/ONCE

disagreements (model wrong): []
```

360 trials, zero disagreements, receipt
`cp1_afb5fba49175b98c6f2ac47181bca58674d8a84a64e85635c36bbf2630257139`.

### The two VOID trials, named rather than hidden

The `vercel_workflow_twophase` b1 cell reads 28 EXACTLY_ONCE and 2 VOID, which is why its rate is
0.933. VOID is the fail-closed outcome: the recovered run never reached `completed` inside the
timeout, so the ledger count is not the count of a finished recovery and exactly-once cannot be
certified. Scoring those trials EXACTLY_ONCE because the ledger happened to hold one effect would
have been the dishonest reading.

The mechanism is visible in the kept trial directory. The crash lands after world-local has linked
the lazy step-create claim (`.locks/steps/<run>-<step>.created`) but before the step entity and its
`step_created` event are written. On recovery the re-enqueued run's lazy step start hits the stale
claim, world-local raises `EntityConflictError`, the runtime maps that to `skipped`, and the flow
delivery hangs until the local queue's header timeout and again on redelivery. The durable log for
such a run holds exactly two events, `run_created` and `run_started`, and no step.

That is a real crash-consistency gap in world-local's claim/entity ordering, but it is a different
barrier from b1, so it is not claimed here. It is inventoried as
`vwf_step_create_claim_before_event` and stays `blocked` until it has a deterministic injection
point and its own rule.

## Temporal hidden barriers

`src/crashpoint/harness/temporal_hidden.py`, same subject/recovery subprocess shape as the LangGraph
hidden harness. Both use a naive effect: the question is whether the framework re-runs the unit at
that edge, not whether an idempotent boundary would hide it. Each trial also records the recovered
event history, so the mechanism is evidence and not narration.

- `tmp_activity_scheduled_before_worker_poll`. The subject runs a workflows-only worker (no activity
  poller), starts the workflow, polls the history until `ActivityTaskScheduled` exists, then
  SIGKILLs itself. Predicted rule, written first: the schedule is durable but no attempt has
  started, so the task waits in matching and the recovery worker's first attempt runs the body once.
  Predicted EXACTLY_ONCE.
- `tmp_workflow_task_replay`. The subject runs a full worker; the workflow code SIGKILLs the process
  immediately after the activity result comes back on a live, non-replay workflow task. Predicted
  rule: `ActivityTaskCompleted` is durable and only the consuming workflow task died, so the service
  times it out, reschedules it, and replay reads the result from history without re-running the
  activity. Predicted EXACTLY_ONCE.

```
temporal server start-dev --headless --ip 127.0.0.1 --port 7233 --db-filename /tmp/crashpoint-temporal.db --log-level error
uv run --extra temporal python -m crashpoint.harness.temporal_hidden --k 30 --barrier tmp_activity_scheduled_before_worker_poll --name temporal_hidden_scheduled
uv run --extra temporal python -m crashpoint.harness.temporal_hidden --k 30 --barrier tmp_workflow_task_replay --name temporal_hidden_replay
```

Both read EXACTLY_ONCE at rate 1.0, k=30, with the history agreeing in 30/30 trials: exactly one
`ActivityTaskScheduled`, the started attempt equal to 1, and `WorkflowTaskTimedOut` absent for the
scheduled edge and present for the replay edge. Receipts
`cp1_474a7d54882e905f53c8a40c2534e26b48442a02c58742a257a2dc22c4c36628` and
`cp1_857a9ab3979a63291d6bac7edc0c92ff8cd4e0b61ab7a9550898ff8882aadc26`.

## DBOS hidden barriers

`src/crashpoint/harness/dbos_hidden.py`. Hooks are installed in the crash process only, after
`DBOS.launch()`, and each names the exact persistence boundary it crashes on. Every trial records
the `workflow_status` row and the `operation_outputs` function names before and after recovery, read
by the harness over its own SQLAlchemy connection.

| Barrier | Where the crash lands | Predicted | Observed k=30 |
|---|---|---|---|
| `dbos_step_output_uncommitted` | the step-output INSERT has run on an open transaction | DUPLICATED | DUPLICATED 1.0 |
| `dbos_step_output_committed_before_resume` | the step output committed, the workflow has not resumed | EXACTLY_ONCE | EXACTLY_ONCE 1.0 |
| `dbos_workflow_outcome_uncommitted` | the SUCCESS status UPDATE has run, the commit has not | EXACTLY_ONCE | EXACTLY_ONCE 1.0 |
| `dbos_duplicate_workflow_name_recovery` | the b1 point, with two modules registering the workflow name `process` | DIVERGED | DIVERGED 1.0 |

The database agreed with the rule in 30/30 trials for all four. Receipts:
`cp1_be064c2d616ea1e3dfc8eda0ef4969f535900f528bbc4e4ddc4e9dc0faa5bfb1`,
`cp1_9558f926b0c7009241be375bf78243794e6cfb500ab12067d898b56cf6b60258`,
`cp1_d578aefe28182e80c58ed3ce62728abcdf0b820afc5391116a6c55c2bc9c240a`,
`cp1_0c9bcb9bde825f1edaac9c20ec113cab15745938c02090b2600ece6095d07b18`.

The duplicate-name row is the one worth reading twice. `workflow_status` stores only the function
name, so when two registered workflows share it, recovery dispatches the pending workflow to
whichever registration won in the recovery process. The crashed billing step is re-run by the
shipping function body, the two ledger crossings carry different payloads, and the oracle reads
DIVERGED. The probe committed in `dbos_duplicate_workflow_recovery_probe.py` showed the dispatch;
this turns it into a modeled external-effect barrier with a predicted rule and receipted evidence.

### A measurement bug found and fixed mid-run

The first `dbos_step_output_uncommitted` run read 27 DUPLICATED and 3 EXACTLY_ONCE, and the first
`dbos_workflow_outcome_uncommitted` run agreed with its rule in only 3/30 trials. Both were the
harness's fault, not the runtime's. `os.kill(os.getpid(), SIGKILL)` was being issued from a worker
thread and returning to that thread; process teardown is not instantaneous for the caller, so the
thread sometimes ran far enough to send the COMMIT the barrier was defined to precede. The crash
helpers now block after the kill. Re-running gave 30/30 for both.

A standalone probe of the underlying behavior (200 children, each SIGKILLing itself from a worker
thread) recorded `returncodes={-9: 200}` and zero threads continuing past the kill, so the surviving
window is narrow rather than routine. That is exactly why it showed up as three trials in thirty and
not as a clean failure.

## Native macOS UID isolation

`adversaries/isolation.py` now covers both platforms. Linux keeps the util-linux
`setpriv --reuid/--regid --clear-groups` path unchanged, so the existing Docker receipt still
re-derives. macOS has no setpriv, so Python drops the child itself through
`subprocess.run(user=..., group=..., extra_groups=[])`; the socket permissions and the 0700 control
directory are then enforced by the macOS kernel. The proof directory moved to `/tmp`, because
macOS's per-user `$TMPDIR` is 0700 and would block the dropped subject before it ever reached the
public socket. The evidence record is named `isolation_macos_uid` on Darwin and reports both
`platform` and `drop_method`.

It needs root:

```
sudo -E .venv/bin/python -m crashpoint.adversaries.isolation --require --evidence-path evidence/isolation_macos.json
```

Without root the command reports `BLOCKED: requires root so the subject process can drop to nobody`.
No native macOS receipt is committed, so the claim stays exactly where entry 08 left it: Linux UID
isolation is proven in a container, macOS is not.

## Inventory changes

- `barrier_inventory.py`: the four Temporal and DBOS candidates flip from `blocked` to `measured`
  with evidence paths, the DBOS entry splits into the three real persistence boundaries plus the
  duplicate-name row, and `vwf_step_create_claim_before_event` is added as `blocked`.
- `deferred_runtimes.py`: the Vercel Local World entry is replaced by the managed Vercel World
  (`r_vwf_managed_`), which cannot be crashed at a named barrier from this sandbox. The deferred
  entry is still not a skipped failing row; it is a runtime with no faithful substrate.

## Substrate

Node 22.22.1 with `workflow@5.0.0-beta.47`, `@workflow/world-local@5.0.0-beta.41`, and
`nitro@3.0.260610-beta`. Temporal CLI 1.8.2 / server 1.31.2 with `temporalio` 1.32.0. DBOS 2.31.0
against the existing `cp-postgres` container (PostgreSQL 16.15 on 5433).
