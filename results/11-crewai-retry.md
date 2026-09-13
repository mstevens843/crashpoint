# 11 - CrewAI tool-retry duplication

Working record for 2026-09-13. This experiment follows
[crewAIInc/crewAI#5802](https://github.com/crewAIInc/crewAI/issues/5802) and its comment thread, and
the open pull request [crewAIInc/crewAI#5822](https://github.com/crewAIInc/crewAI/pull/5822). It
asks a narrower question than the crash-barrier matrix in entries 00-10: not "does a runtime survive
a SIGKILL," but "can a framework's own, uncrashed retry path execute a logical tool action twice."

## Question

CrewAI's default `AgentExecutor` retries a failed tool call internally, inside `ToolUsage._use`
(crewai==1.15.21), up to `_max_parsing_attempts` (default 3) times, entirely within one
`crew.kickoff()` call. If a tool's external effect commits on its first invocation and the tool then
raises before returning success to CrewAI, does the retry perform that effect a second time?

The issue body itself names three candidate mechanisms without pinning down which one is
responsible: "via max_retry_limit, exception handling, or external re-trigger." The prediction,
registered before any treatment execution (`results/11-crewai-retry-prediction.json`), commits this
experiment to exactly one of those three - a same-process, synchronous ReAct **tool** retry, not
task-level `max_retry_limit`, not an external re-trigger, not process-crash recovery, and not
durable/fresh-worker recovery.

## Experiment

A deterministic, credential-free CrewAI worker (`crashpoint.harness.crewai_retry_runtime`) runs one
agent with one scripted `BaseLLM` (no network, no API key) and one tool, `local_action`, that appends
one harmless record through crashpoint's out-of-process ledger. The harness
(`crashpoint.harness.crewai_retry`) assigns a `logical_action_id` before the worker, LLM, agent, or
task is created, launches the worker in a fresh subprocess with only the ledger's execute socket -
never the control socket - and queries and seals the ledger only after the worker exits. The ledger
applies no deduplication here (`key=null` throughout, matching the naive rows elsewhere in this
repo), so two crossings of one logical action are admissible rather than silently absorbed.

| Case | Injection | Expected effect count |
|---|---|---|
| `clean` | none | 1 |
| `pre_effect` | `RuntimeError` before the ledger call | 1 (attempt 1 commits nothing; CrewAI's retry performs the first valid effect) |
| `post_effect` | `RuntimeError` after the ledger acknowledges, before the tool returns | 2 if the reported bug reproduces |

Command:

```bash
uv run --extra crewai python -m crashpoint.harness.crewai_retry --k 30 --name crewai_retry
```

### The actual retry path, read from installed crewai==1.15.21 and confirmed empirically

`Agent`'s default `executor_class` in this version is
`crewai.experimental.agent_executor.AgentExecutor` (the older `CrewAgentExecutor` is deprecated).
Its `execute_tool_action` Flow step (`crewai/experimental/agent_executor.py:1661`) calls the shared
`crewai.utilities.tool_utils.execute_tool_and_check_finality`, which constructs exactly one
`ToolUsage` per parsed ReAct action and calls `tool_usage.use(...)` once
(`crewai/utilities/tool_utils.py:200-360`). Inside `ToolUsage._use`
(`crewai/tools/tool_usage.py:503-758`), `tool.invoke(...)` calls the tool function with no
surrounding `try`/`except` (`crewai/tools/structured_tool.py:424-448`), so an injected `RuntimeError`
reaches `_use`'s own `except` block unmodified. That block increments `self._run_attempts` and,
while still within `self._max_parsing_attempts` (default 3), sets `should_retry = True`; the
trailing `if should_retry: return self.use(...)` is the retry itself - CrewAI re-entering
`_select_tool` then `_use` on the *same* `ToolUsage` instance, in the same process, invisibly to the
outer ReAct loop (the executor calls `execute_tool_and_check_finality` exactly once per parsed
action either way, so no extra LLM turn is spent on the retry).

This is a **tool retry**, not a task retry, an agent retry, or an external re-trigger: `Agent`'s
separate `_times_executed`/`max_retry_limit` path (`crewai/agent/core.py:785-787`) is a different
mechanism, and the harness sets `max_retry_limit=0` specifically to rule it out. Every collected
trial's `runtime_reported_result.agent_retries` reads 0. The harness confirms the retry is CrewAI's,
not the harness's own loop, by reading (never mutating) the live call stack for a frame whose code
object is `crewai.tools.tool_usage`'s `_use` at every tool entry
(`crashpoint.harness.crewai_retry_runtime.retry_frame`); `validate_injection_order` then checks that
the second attempt's `entry_point` is `"crewai.tools.tool_usage.ToolUsage._use"` and its
`run_attempt` - read live off CrewAI's own counter - is `2`.

## Result

90/90 trials (k=30 per case) matched the pre-registered prediction exactly, and every trial's
receipt independently passes the strict schema and cross-field checks in
`crashpoint.harness.crewai_retry_receipt.validate_receipt`.

| Observation | `clean` | `pre_effect` | `post_effect` |
|---|---:|---:|---:|
| tool attempts | 1 | 2 | 2 |
| LLM calls | 2 | 2 | 2 |
| ledger crossings recovered (by attempt) | `attempt-1` | `attempt-2` only | `attempt-1`, `attempt-2` |
| effect count | 1 | 1 | 2 |
| oracle classification | EXACTLY_ONCE | EXACTLY_ONCE | DUPLICATED |
| agent-level retries (`agent_retries`) | 0 | 0 | 0 |
| trials matching prediction | 30/30 | 30/30 | 30/30 |

The `effect_ids` recovered directly from the raw ledger record stream (read by the harness after the
subject exits, never reported by the subject itself) confirm the mechanism, not just its count:
every `pre_effect` trial's single crossing carries attempt-2's `attempt_id` - attempt 1 committed
nothing before it raised - and every `post_effect` trial's two crossings carry attempt-1 then
attempt-2 in that order, meaning attempt 1's effect committed *before* the injected failure, exactly
as the case requires. `validate_injection_order` checks this event order in every individual trial,
not only in the aggregate count.

Toolchain: crewai 1.15.21 (commit `a8d330de00812e52356f32d32c715b86392bfd41`, the commit the
1.15.21 tag points to), Python 3.12.13, crashpoint commit
`a08ef36f435b68343befa1c39681c1b4691302af`.
Receipt: `cp1_a7376a114c8eeb06eb7042039a8557b00630fde0cf946788d8be9a2cdd22541d`.

## Claim boundary

This reproduces crewAIInc/crewAI#5802 narrowly: a same-process, synchronous `ToolUsage` retry can
execute a tool's external effect twice when that effect is not idempotent and the tool fails between
committing it and returning success. It is not process-crash recovery, not fresh-worker recovery,
and not durable recovery - CrewAI never crashes in this experiment, and its own Flow-based
checkpoint/resume (`checkpoint=True`) is explicitly disabled and unexercised here. It does not
measure a real LLM or provider, any idempotency guard, or host power-loss durability of the ledger
itself. `crashpoint.harness.crewai_retry_receipt.LIMITATIONS` states these boundaries in every
receipt, not only here.

As of this writing, crewAIInc/crewAI#5822 is an **open, unmerged** pull request proposing an opt-in
`idempotent=True` pre-claim guard on `BaseTool` (the cache entry is written before execution instead
of after, so a crash mid-effect still leaves a claim behind) plus an unrelated fix for an
`_times_executed` counter leaking across tasks. crewai 1.15.21, measured here, contains neither
change. This entry does not evaluate that PR; it measures only the retry behavior of the release in
`pyproject.toml`.

## Independent context

The issue thread itself converged on close to this experiment's own shape before this repository
ran it. Commenters
[socksninja](https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5651114747) and
[impartshadow](https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5650981230) proposed
narrowing the report to one `UNGUARDED_RETRY` case with a fixed `logical_action_id`, an
independently-read effect store, and a receipt naming the runtime version/commit, the actual retry
entry point, attempt IDs, the injection point, worker exit status, observation completeness, and
`effect_count: null` on a failed read rather than an inferred zero or success - the same fail-closed
discipline this repository's ledger oracle already applies
([third comment](https://github.com/crewAIInc/crewAI/issues/5802#issuecomment-5656131833) repeats
the request for an auditable receipt). This entry's harness independently arrived at the same
design - out-of-process ledger, pre-assigned logical action ID, distinct attempt IDs, a receipt with
a nullable effect count - built from crashpoint's own conventions in entries 00-10, not from these
comments.
