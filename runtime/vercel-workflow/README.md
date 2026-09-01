# Vercel Workflow Local World fixture

This is the JS/TS Workflow DevKit fixture behind the `r_vwf_*` rows: an Express app served by
Nitro that exposes the crashpoint ledger boundary from a step. It is measured by
`crashpoint.harness.vercel_matrix`, which starts and restarts the built server itself, one fresh
Local World data directory per trial.

## Build

```bash
cd runtime/vercel-workflow
npm ci
npm run build          # writes .output/server/index.mjs
```

Pinned: `workflow@5.0.0-beta.47` (which resolves `@workflow/world-local@5.0.0-beta.41`),
`nitro@3.0.260610-beta`, Node 22.

## Why the harness sets two environment variables

Both are documented knobs of the runtime under test; no runtime code is patched.

- `WORKFLOW_TARGET_WORLD=@workflow/world-local`. Nitro inlines `@workflow/world-local` into
  `.output/server/_libs/@workflow/core+[...].mjs`. The inlined copy locates its `package.json`
  relative to `import.meta.url`, misses, falls back to the version string `"bundled"`, and its
  data-directory initializer rejects that with `Invalid version string: "bundled"` before any run
  can start (the 2026-09-01 blocker recorded in `results/08-*.md`). Naming the package explicitly
  makes the runtime load it through its documented custom-world path
  (`createRequire(process.cwd() + '/package.json')`), i.e. the unbundled copy under
  `node_modules/`, whose real `package.json` is readable. The server must therefore run with
  this directory as its working directory.
- `WORKFLOW_INLINE_OWNERSHIP_LEASE_SECONDS=1`. Steps run inline and stamp the queue message that
  owns them. After a crash, the fresh server's re-enqueued run sees a dead owner and defers
  re-execution until the ownership lease expires. The default lease is 860 s; the documented
  minimum is 1 s. The recovery path is unchanged, only the wait is shorter.

## Run one server by hand

```bash
cd runtime/vercel-workflow
NITRO_HOST=127.0.0.1 NITRO_PORT=4097 \
WORKFLOW_LOCAL_BASE_URL=http://127.0.0.1:4097 \
WORKFLOW_LOCAL_DATA_DIR=/tmp/crashpoint-vwf-data \
WORKFLOW_TARGET_WORLD=@workflow/world-local \
WORKFLOW_INLINE_OWNERSHIP_LEASE_SECONDS=1 \
node .output/server/index.mjs
curl http://127.0.0.1:4097/api/health      # {"ok":true}; also calls world.start()
```

`/api/health` is what starts the Local World, and `world.start()` is what re-enqueues every
pending or running run found in the data directory. Nothing in the runtime calls it for you, so a
recovery server must be hit on `/api/health` before it recovers anything.

## What the workflow does

`src/workflows/crashpoint.ts` runs `charge` (the external effect through the ledger's invoke
socket) and then `sentinel`; `twophase` mode adds a `prepareIdentity` step first. The barrier is
carried in the run's input and crashes the process once, guarded by a marker file, so the recovered
run does not crash again:

- `b0` kills inside `charge` before the effect;
- `b1` kills inside `charge` after the effect, before `step_completed` is durable;
- `b2` kills inside `sentinel`, after `charge` completed.

The DevKit runs the first invocation eagerly inside `start()`, so a crash trial usually kills the
server before `/api/start` can answer; the harness reads the run id from the trial's own
`runs/<runId>.json` instead.

## Measure

```bash
uv run python -m crashpoint.harness.vercel_matrix --k 30 --name vercel --timeout 60
```
