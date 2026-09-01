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
```

Temporal used:

```
temporal server start-dev --headless --ip 127.0.0.1 --port 7233 --db-filename /tmp/crashpoint-temporal.db --log-level error
```

DBOS used the existing local `cp-postgres` container:

```
docker start cp-postgres
```

The resulting evidence is 3,120 crash+recover trials, zero disagreements:

| evidence | k | trials | receipt |
|---|---|---|---|
| controls | 100 | 1,800 | `cp1_667ba86d7791b962cbf291518d38c0339307e4ca6b0c81a50332f077fbd20edc` |
| langgraph | 50 | 600 | `cp1_bffad8a0407e66ad6f2dc5ad4a3f39a96d7528ce4e4388ec6be1f9059be13073` |
| temporal | 30 | 360 | `cp1_6a057f0779fb2b98c60fe71c4b8e8111328a5385ff8069b1598f4d7fa50728a5` |
| dbos | 30 | 360 | `cp1_0793cb0b8ad9925a9ae547057b707355dc420ac763aec94ce1f7dd0f2334c220` |

## What was implemented but not measured as evidence

### Linux UID isolation

`src/crashpoint/adversaries/isolation.py` implements a stronger Linux proof. When run as root on
Linux with `setpriv` and a `nobody` user, it starts the ledger with the invoke socket in a public
directory, the control socket and store in a private `0700` directory, and the subject process
dropped to `nobody`. The subject is intentionally given the forbidden paths and must prove it can
execute exactly one effect but cannot dump, reset, seal, connect to the control socket, or read/write
the store.

On this macOS host:

```
uv run python -m crashpoint.adversaries.isolation
ISOLATION BLOCKED: requires Linux UID isolation; this host is not Linux
```

So the current checked-in evidence does not claim Linux isolation passed. It claims only that the
proof exists and is gated correctly on this host.

### Real model sampler

The adapter base now supports:

```
export CRASHPOINT_NONDET_SOURCE=model
export CRASHPOINT_MODEL_SAMPLER_CMD='your-sampler-command'
```

The command receives the prompt on stdin and must print the sampled memo to stdout. This keeps
secrets and provider choice outside the repo. No `ollama`, `llm`, or configured sampler command was
available in this workspace, so no model-backed evidence was generated. The current nondeterministic
evidence remains UUID/draw-backed and should be cited only as irreproducibility evidence.

## What remains explicitly unmodeled

`src/crashpoint/harness/barrier_inventory.py` lists hidden crash-point candidates and prevents them
from being silently treated as b0/b1/b2:

```
uv run python -m crashpoint.harness.barrier_inventory
```

Current blockers:

- LangGraph pending-write and pre-first-checkpoint edges need distinct model rules before being added
  to the matrix.
- Temporal activity scheduling, workflow-task replay, and post-activity sentinels need event-history
  instrumentation against a live dev server before they are evidence.
- DBOS step output commit, workflow status commit, and duplicate workflow-name recovery need
  internal schema/transaction instrumentation before they are modeled external-effect barriers.

## Deferred runtimes

`src/crashpoint/harness/deferred_runtimes.py` records runtime adapters that are still not present:

```
uv run python -m crashpoint.harness.deferred_runtimes
```

- Restate is deferred because no local Restate server/CLI is installed here and no faithful
  crash/recovery adapter has been validated against Restate's service journal.
- Vercel Workflow DevKit is deferred because it is a TypeScript/JavaScript runtime with a local
  Workflow Development Server; this Python harness needs a minimal Node worker crash/recovery harness
  before any rows can be modeled or measured.

Neither runtime is a skipped failing row. They are not in `runtimes.py` until there is a predicted row
family and receipted crash evidence.
