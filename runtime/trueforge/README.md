# TrueForge hard-kill fixture

This fixture runs the published `@truefoundry/trueforge` server without patching it. A local,
deterministic OpenAI-compatible endpoint emits one MCP tool call, and a local MCP sidecar invokes
crashpoint's out-of-process effect ledger.

The measured boundary is after the MCP receiver commits the effect but before its response reaches
TrueForge. At that point the sidecar sends `SIGKILL` to the TrueForge process. The sidecar and ledger
survive, so fresh-process inspection cannot be influenced by runtime memory.

## Install

```bash
cd runtime/trueforge
npm install
```

The package is pinned to `@truefoundry/trueforge@0.2.0-rc.5` and requires Node 22.14 or newer.

## Measure

```bash
uv run python -m crashpoint.harness.trueforge_hidden --k 10 --name trueforge_hidden
```

The harness uses standalone mode and one fresh SQLite database per trial. It configures the model
and MCP server through TrueForge's public HTTP API, creates an inline agent and session, starts a
background turn, injects the crash, restarts TrueForge against the same database, and records only
the state visible through the API and the independent effect ledger.
