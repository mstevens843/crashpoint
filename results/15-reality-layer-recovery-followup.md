# 15 - Reality Layer: pinned recovery-fix verification

**The narrow startup recovery fix was verified in this same-host fixture on 2026-09-24.**
Eight fresh primary trials used the same version-aware harness and independent effect receiver.
After three real post-effect SIGKILLs per version, v1.8.1 returned the original action as
`STARTED / NOT_REQUIRED`, while v1.8.2.2 returned `UNKNOWN / REQUIRED`. Each trial retained
exactly one matching receiver effect. Every original-plan retry was rejected without adding an
effect. Both clean controls completed as `SUCCEEDED` with one effect.

[Final evidence](../evidence/reality_layer/recovery-1822-20260924) |
[Frozen plan](15-reality-layer-recovery-plan.json) |
[Derived results](../handoff/reality-layer-recovery/results-derived.json) |
[Reproduction](../handoff/reality-layer-recovery/REPRODUCE.md) |
[Self-review](../handoff/reality-layer-recovery/SELF-REVIEW.md) |
[Gate logs](../handoff/reality-layer-recovery/GATES.md) |
[Publication handoff](../handoff/reality-layer-recovery/PUBLISH.md)

This report, bundle and results comment are **UNPUBLISHED**. No commit, push, comment or adoption
pilot was performed. The earlier published experiment remains intact and is additional history,
not extra fresh repetitions. Its v1.8.1 observations are valid evidence.

## Exact pins and scope

- Crashpoint foundation: `4c51e30972afb3ec272cfde0278c6ec56acf86ee`.
- Baseline v1.8.1: [`c9d1ca86969f5567cf771ab8a0f3247770a1dfb7`](https://github.com/shimjaemandu/reality-layer/tree/c9d1ca86969f5567cf771ab8a0f3247770a1dfb7).
- Candidate v1.8.2.2: [`4213c479bd9333558522db1ca820d657b99effbf`](https://github.com/shimjaemandu/reality-layer/tree/4213c479bd9333558522db1ca820d657b99effbf).
- [Upstream patch](https://github.com/shimjaemandu/reality-layer/pull/1) and
  [maintainer request/reply destination](https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5807272468).

Work is isolated on `experiment/reality-layer-recovery-1822`, based on the published foundation.
The candidate is a separate detached checkout. The original subject and experiment were read-only.
All 77 baseline and 80 candidate tracked files were matched byte-for-byte to their pinned Git
blobs and retained inventories. Neither subject has an established redistribution license, so
upstream source is omitted. Offline evidence verification does not authenticate omitted source;
the separate [source audit](../handoff/reality-layer-recovery/source-audit.log) does that comparison.
Node v22.22.1 was selected at `/usr/local/bin/node`; Python was 3.12.13 from the inherited environment.
Dependency declarations and `uv.lock` did not change.

## Measured results

The table is regenerated from retained raw artifacts by the offline verifier, including all
receipt/journal/manifest consistency checks. Effect counts are per trial. The same recovery
predicate is applied to both versions only after evidence verification succeeds.

| Version | Case | Trials | Public status | Reconciliation | Effects | Added on retry | Property satisfied |
|---|---|---:|---|---|---:|---:|---:|
| v1.8.1 | Post-effect SIGKILL | 3 | `STARTED` | `NOT_REQUIRED` | 1 | 0 | 0/3 |
| v1.8.1 | Clean completion | 1 | `SUCCEEDED` | `NOT_REQUIRED` | 1 | 0 | 1/1 |
| v1.8.2.2 | Post-effect SIGKILL | 3 | `UNKNOWN` | `REQUIRED` | 1 | 0 | 3/3 |
| v1.8.2.2 | Clean completion | 1 | `SUCCEEDED` | `NOT_REQUIRED` | 1 | 0 | 1/1 |

All **8 primary trials are valid**, with **0 invalid**. All 8 source-informed predictions agree
with the observations. The desired crash-recovery property fails in all three old trials because
status is `STARTED`, `required=false` and state is `NOT_REQUIRED`; it succeeds in all three
candidate trials. These are separate statements: prediction agreement does not make the old
recovery behavior satisfy the property, and property failure does not invalidate its evidence.
Three repetitions establish no reliability rate.

Candidate post-crash ledger bytes at server startup contain the same action ID, `UNKNOWN`, and
`runtime-recovery` evidence with reason `runtime_interrupted_before_terminal_outcome`. Public API
queries equal the retained ledger actions. Across restart, original-plan retry and reconciliation,
receiver readback remains one matching effect per trial. Total effects across the eight trials: 8.

Old reconciliation submits `action_id`, `outcome` and an observer-digest `evidence_note`; the old
`STARTED` record remains unchanged. Candidate reconciliation submits only `action_id`. With the
virtual adapter, it correctly returns `changed=false` and `UNKNOWN / REQUIRED`, citing
`trusted_reconciliation_not_available_for_adapter`. No trusted provider adapter was invented.

Two separately labelled adjunct probes ran on candidate `post_crash-0`:

| Route | Actual response | Subsequent status/readback |
|---|---|---|
| MCP with forged `outcome` and `evidence_note` | HTTP 200 tool error, `ok=false`, `arguments.evidence_note is not allowed` | `UNKNOWN / REQUIRED`; 1 effect |
| Legacy REST with forged `outcome`, `evidence_note` and `evidence` | HTTP 200, `ok=true`, `changed=false`; caller assertions ignored | `UNKNOWN / REQUIRED`; 1 effect |

The MCP error names `evidence_note` because the retained request serializes keys in sorted order.
The two routes do not share an HTTP rejection contract. Each probe is bound to its exact request,
real response, subsequent public action, ledger snapshot and fresh receiver observer. The REST
probe obtains the actual loopback session token; the random credential stays out of transcripts.
Neither probe terminally resolves the action or adds an effect. These are not extra primary trials.

## Boundary and instrumentation

A real upstream MCP client calls production planning, admission and execution. Before dispatch,
the controller fsyncs and rereads the planned action ID, plan ID, exact admitted IR and payload.
Only the established virtual Beta adapter's exported `execute` boundary is replaced with a
harmless, non-deduplicating loopback receiver append. The shim attaches the retained caller ID
because this legacy adapter signature carries device/capability/args, not an action IR; exact
payload checks and raw plan/ledger bindings constrain that disclosed seam.

After the receiver's durable append acknowledgment, the adapter holds at an explicit barrier.
A fresh observer opens and parses receiver bytes; the controller binds exactly one effect to
this action/payload/attempt before killing. The actual `STARTED` record, barrier, observation,
SIGKILL request, PID and `-9` exit are retained. A fresh runtime loads the same per-trial persisted
state, and the candidate's production `server.js` performs recovery during startup. The harness
never seeds `STARTED` or calls `recoverInterrupted()` itself. It then queries the original action,
archives the ledger, and independently observes the receiver before and after retry and probes.
Clean controls perform real completion without a restart; shared snapshot phase names do not
claim otherwise. All 112 primary child processes were reaped.

Child environments are allowlisted, with `REALITY_LIVE=0` and `PC_ADAPTER_DRY_RUN=1`; all endpoints
are loopback. No models, provider credentials, discovery plugins, live devices, Home Assistant
connections or paid services were used. Execution sources are clean verified copies. Every owned
child has bounded waits and finally-based termination/reaping. Artifact read/enumeration/stat
failures preserve identity, cleanup results and structured errors; unavailable readback is never
zero effects. Both failed receipt writes still retain an identified attempt when the manifest
is writable. The inherited retention and confinement regressions pass.

## Independent challenge and final gates

The retained offline verifier re-derives observations from receiver chains, payload/action/attempt
bindings, raw MCP/REST transcripts, snapshots and process chronology. It checks exact Boolean and
integer types, inventories, source hashes, path confinement, receipt/journal equality and summary
consistency. A second stdlib-only [raw auditor](../handoff/reality-layer-recovery/independent-audit.py)
imports no Crashpoint capture or verifier implementation and independently derives the same eight
status/requirement/effect rows. This is process separation and independent derivation by the same
author, not endorsement by another organization or a hostile-process isolation guarantee.

Final focused gate: **88 passed**. Full inherited suite: **503 passed, 22 skipped**, no failures.
The skips are 19 absent optional SafeAgent environments, 1 absent macOS isolation fixture and
2 absent TrueForge fixtures. All Reality Layer real-process tests were enabled and ran. Ruff,
fresh mypy (**90 source files**), scoped formatting, Node syntax and whitespace checks passed.
The old property CLI deliberately exits 1 only after successful evidence verification; the new
property CLI exits 0. This expected RED is separate from harness-test outcomes.

Thirteen new re-signed evidence mutations reject swapped/mislabelled pins, false UNKNOWN claims,
dropped trials, action/payload mismatches, missing receiver/probe bytes, contradictory raw status,
false startup snapshots, Boolean-as-integer flags, chronology changes and forged probe summaries.
Two disposable guard-removal controls demonstrate that omitting startup-snapshot equality permits
the targeted false evidence and omitting the property predicate falsely turns old RED into GREEN;
the permanent regressions catch both for those reasons. Installed subjects and retained evidence
were not edited. Four new real-process protocol tests cover both profiles' crash and clean paths.

The final bundle verifies in place and after relocation under Python `-I -S`, with the retained
verifier's loaded path asserted and original-checkout reads, network and external subprocesses
denied by tested audit hooks. The second auditor also passes inside that restriction. See
[exact commands/exits](../handoff/reality-layer-recovery/checks.json) and
[relocation proof](../handoff/reality-layer-recovery/relocated-offline.log).

## Preservation and limits

[Closeout](../handoff/reality-layer-recovery/closeout.json) confirms all 2,272 historical evidence
files in this worktree are unchanged; all 9,111 tracked/unignored files in the original experiment,
including deliberate shortened-comment edits and exploratory evidence, retain their bytes and
Git status. Both subject checkouts and the original main checkout are unchanged. The final frozen
capture sources match their plan. All 800 observed child PIDs across development, primary and QA
captures had exited at closeout. Nothing is staged.

The [run catalog](../handoff/reality-layer-recovery/run-catalog.json) separates 8 final primary trials
from 26 development/superseded trials (one invalid due to the corrected MCP error-text expectation)
and 34 final QA trials in 36 capture runs (two intentional source-read failures launched no trial).
Development and QA bundles remain locally under ignored `work/` and are excluded from publication.
Their outcomes are not folded into the primary comparison.

This verifies the requested post-effect startup/status boundary and two caller-trust probes only.
Consumed/lost original-plan rejection does not independently prove direct executor same-action-ID
or concurrent-dispatch guarantees. No general concurrent-writer, history-eviction, distributed
fencing, power-loss durability, provider-attribution or universal exactly-once result is claimed.
Other changes in the upstream patch remain untested here. Digests prove consistency, not independent
authorship, trusted timestamps or protection against coordinated rewriting. There is no adoption
or production-readiness claim. See the explicit [user-only publication commands](../handoff/reality-layer-recovery/PUBLISH.md).

## Bundle identities

- `v181` run: `2231ff50-1004-4707-89c9-cfe1d0888bcb`; manifest SHA-256: `ea31e6f8654923105990ff2816c78029e4591687dbec1433ac3155083d7f990b`.
- `v1822` run: `9f1bf3c3-e0da-494e-8d46-cf0a0e9f8daf`; manifest SHA-256: `07657bce304242f10eb01190c32870fb0e1d6297a7c99c448f0299d6fdee04b7`.
