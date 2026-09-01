# Vercel Workflow Probe

This is an optional, unmeasured Vercel Workflow fixture. It follows the current JS/TS
Workflow DevKit Express/Nitro setup shape and exposes the crashpoint ledger boundary, but it
does not produce model rows or evidence yet.

The attempted local substrate on 2026-09-01 used:

```bash
cd runtime/vercel-workflow
npm ci
npm run build
WORKFLOW_LOCAL_BASE_URL=http://127.0.0.1:3000 \
WORKFLOW_LOCAL_DATA_DIR=/tmp/crashpoint-vwf-dev-data \
npm run dev
curl http://127.0.0.1:3000/api/health
```

Both the built server and `nitro dev` compiled the workflow, but the Local World failed before
any workflow run could start:

```text
Invalid version string: "bundled"
```

That error comes from `@workflow/world-local` data-directory initialization after Nitro bundles
the Local World package without a semver package version. Because no durable local run can be
started, crashpoint does not claim Vercel Workflow behavior and does not add Vercel model rows or
receipted evidence from this fixture.

