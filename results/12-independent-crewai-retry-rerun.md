# 12 - Independent CrewAI tool-retry rerun

An evidence-only, independent rerun of the experiment recorded in
[`results/11-crewai-retry.md`](./11-crewai-retry.md). It was executed by `@impartshadow` from a
clean clone of `mstevens843/crashpoint` and adds one retained result file. It changes no runtime
code, harness code, model rule, test, or project policy.

## Exact revisions

- crashpoint source revision: `fb92665d7955880f225d4269664661afc9aa6bac` (the `main` head at the
  time of the run; recorded as `crashpoint_commit` in every trial)
- CrewAI: `crewai==1.15.21`, resolved source revision `a8d330de00812e52356f32d32c715b86392bfd41`
  (recorded as `crewai_source_commit` in every trial)
- Interpreter: Python 3.12.3, Linux
- Dependencies: the tracked `uv.lock`, installed with `--locked`

## Reproduction

From a clean checkout of the revision above, with the working tree unmodified:

```bash
uv sync --group dev --extra crewai --locked
uv run --locked --extra crewai python -m crashpoint.harness.crewai_retry \
  --k 30 --name crewai_retry_impartshadow_20260925
```

The harness assigns each `logical_action_id` before the worker starts, runs the CrewAI worker in a
fresh subprocess with only the ledger's execute socket, and reads effect counts from the
out-of-process ledger after the worker exits. The command wrote the retained machine-readable result
to [`evidence/crewai_retry_impartshadow_20260925.json`](../evidence/crewai_retry_impartshadow_20260925.json).

## Retained result

All 90 trials matched the pre-registered prediction (`all_agree: true`).

| Case | Trials | Observed classification | Effect count per trial | Tool attempts per trial | Wilson 95% |
|---|---:|---|---:|---:|---|
| `clean` | 30/30 | `EXACTLY_ONCE` | 1 | 1 | [0.8865, 1.0] |
| `pre_effect` | 30/30 | `EXACTLY_ONCE` | 1 | 2 | [0.8865, 1.0] |
| `post_effect` | 30/30 | `DUPLICATED` | 2 | 2 | [0.8865, 1.0] |

Ledger receipt: `cp1_414e9ee57f3b156cdfd6af219efa59caba1c47f7fd409727a10916d3df9b5c20`.

SHA-256 of the retained file as committed:

```text
5d2a886612834836b8be5393f7a27b9e85b51c37ea98656a7210476902ff99da  evidence/crewai_retry_impartshadow_20260925.json
```

## Claim boundaries

- This rerun corroborates only the repository's existing narrow result: CrewAI 1.15.21's built-in,
  same-process `ToolUsage._use` retry crosses an unguarded external-effect boundary a second time
  when the first crossing has already committed and the tool raises before returning success.
- It is an independent execution on a different machine by a different operator, against the same
  harness, the same scripted deterministic local LLM, and the same pinned CrewAI revision. It is not
  an independent reimplementation of the harness or the ledger.
- It does not test a real model provider, an idempotency guard at the ledger boundary, task-level
  `max_retry_limit`, an external re-trigger, process-crash or fresh-worker recovery, CrewAI
  checkpoint/resume, or host power-loss durability.
- 30/30 in each case is evidence that the recorded result reproduces. It is not a new frequency
  estimate beyond the Wilson interval shown, and it makes no claim about newer CrewAI releases.
- The original entry 11 remains the reference record. This entry adds a second, separately produced
  observation of the same cells and nothing else.
