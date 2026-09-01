# 07 - two-phase recovery, optional model sampling, and open boundaries

Working record for the 2026-09-01 next phase. The rule is unchanged: only claim what was actually
measured. This entry separates implemented-and-crashed work from implemented-but-blocked work.

## What was implemented and measured

The model now has an explicit `EffectMode.TWO_PHASE`: the action identity is derived from durable
pre-call inputs, recorded or passed before the nondeterministic draw, and carried through the external
effect. The b1 rule is different from content-derived idempotency: replay may redraw the payload, but
it presents the same key at the ledger boundary, so the second attempt dedups.

Rows added:

- `r_twophase` - control/reference two-phase row.
- `r_lg_twophase` - LangGraph predecessor-node prepared identity.
- `r_tmp_twophase` - Temporal workflow argument prepared before activity retry.
- `r_dbos_twophase` - DBOS committed prepared-identity step before the effect step.
- `r_restate_naive`, `r_restate_idem`, `r_restate_nondet`, `r_restate_twophase` - Restate durable
  operation rows measured through a Python ASGI worker and local Docker dev server.

Recomputability:

```
uv run python -m crashpoint.harness.recomputability
```

The probe reports RECOMPUTABLE for deterministic/content-derived identity, NOT_RECOMPUTABLE for
nondeterministic/content-derived identity, and RECOMPUTABLE for the two-phase prepared identity.

Fresh evidence commands run in this phase:

```
uv run python -m crashpoint.harness.matrix --k 100 \
  --runtimes r_null,r_dup,r_lost,r_idem,r_diverge,r_twophase --name controls
uv run --extra langgraph python -m crashpoint.harness.matrix --k 50 \
  --runtimes r_lg_naive,r_lg_idem,r_lg_nondet,r_lg_twophase --name langgraph
uv run --extra temporal python -m crashpoint.harness.matrix --k 30 \
  --runtimes r_tmp_naive,r_tmp_idem,r_tmp_nondet,r_tmp_twophase --name temporal
uv run --extra dbos python -m crashpoint.harness.matrix --k 30 \
  --runtimes r_dbos_naive,r_dbos_idem,r_dbos_nondet,r_dbos_twophase --name dbos
uv run --extra restate python -m crashpoint.harness.restate_matrix --k 10 \
  --name restate --timeout 45
ANTHROPIC_MODEL=claude-haiku-4-5-20251001 \
  CRASHPOINT_NONDET_SOURCE=model \
  CRASHPOINT_MODEL_SAMPLER_CMD='python scripts/anthropic_sampler.py' \
  CRASHPOINT_MODEL_SAMPLER_TIMEOUT=90 \
  CRASHPOINT_MODEL_PROMPT='Return only a fresh random-looking 12-character lowercase hexadecimal payment memo. Choose a different value each time.' \
  uv run --extra langgraph python -m crashpoint.harness.matrix --k 5 \
  --runtimes r_lg_nondet,r_lg_twophase --name langgraph_model
uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 \
  --name langgraph_hidden_pending --barrier lg_pending_writes_after_persist
```

Temporal used:

```
temporal server start-dev --headless --ip 127.0.0.1 --port 7233 --db-filename /tmp/crashpoint-temporal.db --log-level error
```

DBOS used the existing local `cp-postgres` container:

```
docker start cp-postgres
```

The resulting default shared b0/b1/b2 evidence is 3,240 crash+recover trials, zero disagreements:

| evidence | k | trials | receipt |
|---|---|---|---|
| controls | 100 | 1,800 | `cp1_667ba86d7791b962cbf291518d38c0339307e4ca6b0c81a50332f077fbd20edc` |
| langgraph | 50 | 600 | `cp1_bffad8a0407e66ad6f2dc5ad4a3f39a96d7528ce4e4388ec6be1f9059be13073` |
| temporal | 30 | 360 | `cp1_6a057f0779fb2b98c60fe71c4b8e8111328a5385ff8069b1598f4d7fa50728a5` |
| dbos | 30 | 360 | `cp1_0793cb0b8ad9925a9ae547057b707355dc420ac763aec94ce1f7dd0f2334c220` |
| restate | 10 | 120 | `cp1_69b85c39d1257ac1307088ef5718b854ef5cfae490ffea9e4f9493870e85bfb7` |
| langgraph_model | 5 | 30 | `cp1_9c99ccf1c57336a7c1ca84bc4b08dad1a7c3519b8e17976b5bf806ebda47cb9d` |
| langgraph_hidden_pending | 50 | 50 | `cp1_d6c738d55b00a2cf4510bfb12e1b7497e7555de567e7b56e0406d366747d2553` |

Restate used:

```
docker run -d --name crashpoint-restate -p 8080:8080 -p 9070:9070 -p 9071:9071 \
  --add-host=host.docker.internal:host-gateway docker.restate.dev/restatedev/restate:latest
```

Restate's server and CLI images reported version 1.7.8; the Python SDK version was 1.0.4.

### Linux UID isolation

`src/crashpoint/adversaries/isolation.py` implements a stronger Linux proof. When run as root on
Linux with `setpriv` and a `nobody` user, it starts the ledger with the invoke socket in a public
directory, the control socket and store in a private `0700` directory, and the subject process
dropped to `nobody`. The subject is intentionally given the forbidden paths and must prove it can
execute exactly one effect but cannot dump, reset, seal, connect to the control socket, or read/write
the store.

The direct macOS command still reports BLOCKED:

```
uv run python -m crashpoint.adversaries.isolation
ISOLATION BLOCKED: requires Linux UID isolation; this host is not Linux
```

A Dockerized Linux proof was run from this macOS host:

```
docker run --rm -v "$PWD:/work:ro" -v "$PWD/evidence:/evidence" -w /work \
  -e PYTHONPATH=src python:3.12-slim \
  python -m crashpoint.adversaries.isolation --require \
  --evidence-path /evidence/isolation_linux.json
```

It passed with receipt
`cp1_9d16e122023c860eafdf320ed69d8241a57f74c790975d883ffd7d77a6bd496d`. The proof is narrow:
execute-only access for the UID-dropped subject in that Linux container, not a full container escape
audit and not native macOS UID isolation.

### Real model sampler

The adapter base now supports:

```
export CRASHPOINT_NONDET_SOURCE=model
export CRASHPOINT_MODEL_SAMPLER_CMD='your-sampler-command'
```

The command receives the prompt on stdin and must print the sampled memo to stdout. This keeps
secrets and provider choice outside the repo. `scripts/anthropic_sampler.py` is available for an
operator who sets `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` in the shell or in a local ignored
`.env`.

Measured run:

```
ANTHROPIC_MODEL=claude-haiku-4-5-20251001 \
  CRASHPOINT_NONDET_SOURCE=model \
  CRASHPOINT_MODEL_SAMPLER_CMD='python scripts/anthropic_sampler.py' \
  CRASHPOINT_MODEL_SAMPLER_TIMEOUT=90 \
  CRASHPOINT_MODEL_PROMPT='Return only a fresh random-looking 12-character lowercase hexadecimal payment memo. Choose a different value each time.' \
  uv run --extra langgraph python -m crashpoint.harness.matrix --k 5 \
  --runtimes r_lg_nondet,r_lg_twophase --name langgraph_model
```

Result: k=5, `r_lg_nondet` b1 DIVERGED at rate 1.0, `r_lg_twophase` b1 EXACTLY_ONCE at rate 1.0,
zero disagreements, receipt `cp1_9c99ccf1c57336a7c1ca84bc4b08dad1a7c3519b8e17976b5bf806ebda47cb9d`.
This is narrow real-model evidence, not a broad claim about all samplers/providers.

### LangGraph hidden barriers

`src/crashpoint/harness/langgraph_hidden.py` measures LangGraph edges that are intentionally outside
the shared b0/b1/b2 matrix. The pre-first-checkpoint rule is LOST because recovery has no resumable
state and no external effect has crossed.

Command:

```
uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 --name langgraph_hidden
```

Result: k=50, LOST at rate 1.0, zero disagreements, receipt
`cp1_993c57f79dae43a92e42013a8403adf93c0f38088ad6b7864816e7885cc76ff8`.

This phase also measured `lg_pending_writes_after_persist`: death after pending writes are durable but
before the superseding checkpoint path returns. The predicted rule is EXACTLY_ONCE because recovery
can consume the durable pending writes instead of re-running the external-effect node.

Command:

```
uv run --extra langgraph python -m crashpoint.harness.langgraph_hidden --k 50 \
  --name langgraph_hidden_pending --barrier lg_pending_writes_after_persist
```

Result: k=50, EXACTLY_ONCE at rate 1.0, zero disagreements, receipt
`cp1_d6c738d55b00a2cf4510bfb12e1b7497e7555de567e7b56e0406d366747d2553`.

## What remains explicitly unmodeled

`src/crashpoint/harness/barrier_inventory.py` lists hidden crash-point candidates and prevents them
from being silently treated as b0/b1/b2:

```
uv run python -m crashpoint.harness.barrier_inventory
```

Current status:

- LangGraph `lg_pre_first_checkpoint` and `lg_pending_writes_after_persist` are measured separately
  and remain disjoint from b0/b1/b2.
- Temporal activity scheduling, workflow-task replay, and post-activity sentinels need event-history
  instrumentation against a live dev server before they are evidence.
- DBOS step output commit, workflow status commit, and duplicate workflow-name recovery need
  internal schema/transaction instrumentation before they are modeled external-effect barriers.

## Deferred runtimes

`src/crashpoint/harness/deferred_runtimes.py` records runtime adapters that are still not measured:

```
uv run python -m crashpoint.harness.deferred_runtimes
```

- Vercel Workflow is deferred. An optional JS/TS Nitro fixture in `runtime/vercel-workflow/` follows
  the documented Express/Nitro shape and exposes the ledger boundary, but workflow@5.0.0-beta.47
  fails before any local run on this host because the bundled Local World raises
  `Invalid version string: "bundled"` during data-dir initialization. It needs a faithful local or
  managed backend recovery substrate before rows can enter the model.

The deferred runtime is not a skipped failing row. It is not in `runtimes.py` until there is a
predicted row family and receipted crash evidence.
