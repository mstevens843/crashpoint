"""Strict machine-readable receipt schema for the CrewAI tool-retry experiment.

WHAT THIS IS. One receipt per trial, naming its own retry mechanism, source references, and oracle
classification, so a reader can check the claim without re-running the experiment. ``build_receipt``
assembles a receipt from one completed trial; ``validate_receipt`` fails closed, the same discipline
as ``ledger.oracle``: a receipt missing a required field, or claiming a pass with no certifiable
effect count, is invalid, never silently accepted.

WHERE THE ENTRY-POINT CHAIN CAME FROM. Read from installed crewai==1.15.21
(``crewai/experimental/agent_executor.py``, ``crewai/utilities/tool_utils.py``,
``crewai/tools/tool_usage.py``, ``crewai/tools/structured_tool.py``, ``crewai/agents/
tools_handler.py``, ``crewai/agent/core.py``), byte-identical to the vendored copy in
``.local/crewai-source/wheel/crewai`` (``diff -q`` empty), and confirmed empirically: one diagnostic
trial per case reproduced the pre-registered prediction exactly, including ``agent_retries: 0`` in
every case, which is what rules out ``Agent._times_executed``/``max_retry_limit`` as the mechanism.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any, Final

SCHEMA: Final = "crashpoint.crewai_retry.receipt.v1"

CREWAI_VERSION: Final = "1.15.21"
# The commit the 1.15.21 tag points to (.local/crewai-source/tag-target.json: object.sha).
CREWAI_SOURCE_COMMIT: Final = "a8d330de00812e52356f32d32c715b86392bfd41"

RETRY_CATEGORY: Final = "tool_retry"  # not task retry, not agent retry, not external re-trigger

ENTRY_POINT: Final = (
    "crewai.experimental.agent_executor.AgentExecutor.execute_tool_action "
    "(crewai/experimental/agent_executor.py:1661, the default executor_class in 1.15.21) -> "
    "crewai.utilities.tool_utils.execute_tool_and_check_finality "
    "(crewai/utilities/tool_utils.py:200-360, constructs one ToolUsage and calls .use() once) -> "
    "crewai.tools.tool_usage.ToolUsage.use (tool_usage.py:148) -> "
    "ToolUsage._use (tool_usage.py:503) -> on the tool's exception, ToolUsage._use recurses "
    "through self.use(...) (tool_usage.py:755-756), same process, same ToolUsage instance"
)

SOURCE_REFERENCES: Final[tuple[str, ...]] = (
    "crewai/experimental/agent_executor.py:1660-1699 AgentExecutor.execute_tool_action - the "
    "actual default executor (CrewAgentExecutor is deprecated as of 1.15.21) calls "
    "execute_tool_and_check_finality once per parsed ReAct action",
    "crewai/utilities/tool_utils.py:200-360 execute_tool_and_check_finality constructs exactly "
    "one ToolUsage per action and calls tool_usage.use(...) once",
    "crewai/tools/tool_usage.py:97-135 ToolUsage.__init__ - _run_attempts starts at 1, "
    "_max_parsing_attempts defaults to 3 (2 for OPENAI_BIGGER_MODELS)",
    "crewai/tools/tool_usage.py:503-758 ToolUsage._use - the except block (line ~708) catches "
    "the tool's exception, increments _run_attempts, and while _run_attempts <= "
    "_max_parsing_attempts sets should_retry=True; the trailing `if should_retry: return "
    "self.use(...)` (line 755-756) is the retry itself",
    "crewai/tools/structured_tool.py:424-448 CrewStructuredTool.invoke calls self.func(...) "
    "with no surrounding try/except, so an injected RuntimeError reaches ToolUsage._use "
    "unmodified",
    "crewai/agents/tools_handler.py:26-39 ToolsHandler.on_tool_use sets last_used_tool only "
    "after a successful call, so the repeated-tool-usage cache guard "
    "(ToolUsage._check_tool_repeated_usage) cannot suppress the retry",
    "crewai/agent/core.py:785-787 Agent._check_execution_error - the separate agent-level "
    "_times_executed/max_retry_limit retry path; empirically unused here (agent_retries stayed "
    "0 in every diagnostic and every collected trial)",
)

LIMITATIONS: Final = (
    "No real LLM or provider: the ReAct transcript is produced by a scripted, deterministic "
    "local BaseLLM. No idempotency guard at the ledger boundary (key=null throughout), so this "
    "measures raw retry duplication, not a dedup boundary recovering it. No crash or fresh-process "
    "recovery scope: CrewAI never crashes here; the retry is entirely a same-process, synchronous "
    "ToolUsage recursion inside one crew.kickoff() call. CrewAI's optional Flow-based "
    "checkpoint/resume is explicitly disabled (checkpoint=False) and not exercised. The local "
    "ledger survives a worker exception, but host power-loss durability of the ledger itself is "
    "not tested."
)

REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "schema",
    "crewai_version",
    "crewai_source_commit",
    "python_version",
    "crashpoint_version",
    "crashpoint_commit",
    "case",
    "retry_category",
    "entry_point",
    "built_in_retry",
    "external_retrigger",
    "same_process",
    "fresh_process",
    "logical_action_id",
    "attempt_ids",
    "injection_point",
    "worker_exit_status",
    "runtime_reported_result",
    "observation_complete",
    "effect_ids",
    "effect_count",
    "expected_result",
    "observed_result",
    "passed",
    "oracle_classification",
    "source_references",
    "limitations",
)

_VALID_CLASSIFICATIONS = frozenset(
    {"EXACTLY_ONCE", "DUPLICATED", "DIVERGED", "LOST", "VOID", "UNVERIFIED"}
)


@dataclass(frozen=True)
class CrewAIRetryTrial:
    """One completed trial's observations, before it is stamped into a receipt."""

    case: str
    logical_action_id: str
    attempt_ids: tuple[str, ...]
    injection_point: str
    worker_exit_status: int | None
    runtime_reported_result: dict[str, object]
    observation_complete: bool
    effect_ids: tuple[str | None, ...]
    effect_count: int | None
    expected_result: dict[str, object]
    observed_result: dict[str, object]
    oracle_classification: str
    injection_problems: tuple[str, ...]


def build_receipt(trial: CrewAIRetryTrial, *, crashpoint_commit: str) -> dict[str, object]:
    """Stamp one completed trial into a self-describing receipt. Pure given its input."""
    passed = (
        trial.observation_complete
        and trial.effect_count is not None
        and not trial.injection_problems
        and trial.observed_result == trial.expected_result
    )
    return {
        "schema": SCHEMA,
        "crewai_version": CREWAI_VERSION,
        "crewai_source_commit": CREWAI_SOURCE_COMMIT,
        "python_version": platform.python_version(),
        "crashpoint_version": "0.0.0",
        "crashpoint_commit": crashpoint_commit,
        "case": trial.case,
        "retry_category": RETRY_CATEGORY,
        "entry_point": ENTRY_POINT,
        "built_in_retry": True,
        "external_retrigger": False,
        "same_process": True,
        "fresh_process": False,
        "logical_action_id": trial.logical_action_id,
        "attempt_ids": list(trial.attempt_ids),
        "injection_point": trial.injection_point,
        "worker_exit_status": trial.worker_exit_status,
        "runtime_reported_result": trial.runtime_reported_result,
        "observation_complete": trial.observation_complete,
        "effect_ids": list(trial.effect_ids),
        "effect_count": trial.effect_count,
        "expected_result": trial.expected_result,
        "observed_result": trial.observed_result,
        "passed": passed,
        "oracle_classification": trial.oracle_classification,
        "source_references": list(SOURCE_REFERENCES),
        "limitations": LIMITATIONS,
        "injection_problems": list(trial.injection_problems),
    }


def validate_receipt(rec: dict[str, Any]) -> list[str]:
    """Return violations; empty means valid. Fail-closed: an ill-shaped receipt cannot PASS."""
    problems: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in rec:
            problems.append(f"missing required field: {field}")
    if problems:
        return problems  # cross-field checks below assume the shape is at least present

    if rec["schema"] != SCHEMA:
        problems.append(f"unexpected schema: {rec['schema']!r}")

    attempt_ids = rec["attempt_ids"]
    if not isinstance(attempt_ids, list) or not attempt_ids:
        problems.append("attempt_ids must be a non-empty list")
    elif len(set(attempt_ids)) != len(attempt_ids):
        problems.append("attempt_ids must be distinct per actual tool entry")

    classification = rec["oracle_classification"]
    if classification not in _VALID_CLASSIFICATIONS:
        problems.append(f"unknown oracle_classification: {classification!r}")

    effect_count = rec["effect_count"]
    if effect_count is None:
        if classification not in {"VOID", "UNVERIFIED"}:
            problems.append(
                "effect_count is null: classification must be VOID or UNVERIFIED, got "
                f"{classification!r}"
            )
        if rec["passed"] is not False:
            problems.append("effect_count is null: passed must be False, the case must not PASS")
    else:
        invalid_count = (
            not isinstance(effect_count, int)
            or isinstance(effect_count, bool)
            or effect_count < 0
        )
        if invalid_count:
            problems.append(
                f"effect_count must be a non-negative int or null, got {effect_count!r}"
            )
        if not rec["observation_complete"]:
            problems.append("effect_count is non-null but observation_complete is False")

    if rec["built_in_retry"] == rec["external_retrigger"]:
        problems.append("built_in_retry and external_retrigger must disagree")
    if rec["same_process"] == rec["fresh_process"]:
        problems.append("same_process and fresh_process must disagree")

    refs = rec["source_references"]
    if not isinstance(refs, list) or not refs:
        problems.append("source_references must be a non-empty list")

    if not isinstance(rec["logical_action_id"], str) or not rec["logical_action_id"]:
        problems.append("logical_action_id must be a non-empty string")

    return problems
