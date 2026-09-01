# 08 - Vercel Workflow probe and LangGraph pending-write edge

Working record for the 2026-09-01 follow-up. The rule is unchanged: a runtime enters the model only
after a predicted row family and faithful crash/recovery evidence exist.

## Baseline

The repo started from the release-ready state and passed:

```
uv sync --group dev --all-extras --locked
uv run pytest
uv run ruff check .
uv run mypy
```

The starting `uv run pytest` result was 140 passed.

## Vercel Workflow probe

Current Vercel Workflow docs describe JS/TS Workflow DevKit support with `workflow`/Nitro and beta
Python support. The attempted local fixture chose the JS/TS Express/Nitro path because it is the
documented local server path and can expose the crashpoint ledger boundary from a step.

Added source fixture:

```
runtime/vercel-workflow/
```

It pins `workflow@5.0.0-beta.47`, defines a workflow with naive/idempotent/nondeterministic/two-phase
shape, and exposes `/api/start`, `/api/output/:runId`, and `/api/health` from an Express handler.

Commands run:

```
cd runtime/vercel-workflow
npm install --package-lock-only --cache /tmp/crashpoint-npm-cache
npm ci --cache /tmp/crashpoint-npm-cache
npm run build
NITRO_HOST=127.0.0.1 NITRO_PORT=4097 \
  WORKFLOW_LOCAL_BASE_URL=http://127.0.0.1:4097 \
  WORKFLOW_LOCAL_DATA_DIR=/tmp/crashpoint-vwf-data-smoke \
  npm run start
curl http://127.0.0.1:4097/api/health
NITRO_HOST=127.0.0.1 NITRO_PORT=4098 \
  WORKFLOW_LOCAL_BASE_URL=http://127.0.0.1:4098 \
  WORKFLOW_LOCAL_DATA_DIR=/tmp/crashpoint-vwf-dev-data \
  npm run dev
curl http://127.0.0.1:3000/api/health
```

`npm run build` compiled the workflow. Both the built server and `nitro dev` failed before any run
could start:

```
Invalid version string: "bundled"
```

The stack is inside `@workflow/world-local` data-directory initialization after Nitro bundles the
Local World without a semver package version. Because no durable local run can be started, this phase
does not add Vercel Workflow model rows, evidence, or runtime claims.

## LangGraph hidden barrier

Added `lg_pending_writes_after_persist` as a LangGraph-only hidden barrier. It is disjoint from the
shared b0/b1/b2 barriers: b1 kills before `put_writes` persists; this barrier kills after
`put_writes` returns but before the superseding checkpoint path completes.

Predicted rule: death after pending writes are durable but before the superseding checkpoint returns
preserves the node result, so recovery consumes those writes instead of re-running the external
effect. Predicted outcome: EXACTLY_ONCE.

Smoke:

```
uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 1 \
  --name langgraph_hidden_pending_smoke --barrier lg_pending_writes_after_persist
```

Deliberate evidence:

```
uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 \
  --name langgraph_hidden_pending --barrier lg_pending_writes_after_persist
```

Result: k=50, EXACTLY_ONCE at rate 1.0, zero disagreements, receipt
`cp1_d6c738d55b00a2cf4510bfb12e1b7497e7555de567e7b56e0406d366747d2553`.

The previous `lg_pre_first_checkpoint` evidence remains unchanged.

## Temporal and DBOS hidden barriers

No fresh Temporal or DBOS hidden-barrier measurements were run in this pass. Service checks showed no
available live substrate:

```
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'
temporal operator cluster health --address 127.0.0.1:7233
psql postgresql://cpuser:dbos@localhost:5433/cpdbos -c 'select 1'
```

Temporal returned connection refused on `127.0.0.1:7233`. DBOS Postgres on `5433` also returned
connection refused, and no Docker containers were running. Existing Temporal and DBOS evidence was
left untouched.

