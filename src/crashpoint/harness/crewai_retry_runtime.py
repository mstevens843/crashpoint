"""Credential-free subject for the specialized CrewAI tool-retry experiment.

Only the execute socket is supplied. No retry loop, executor replacement, or monkeypatch:
CrewAI catches the injected RuntimeError and decides whether to invoke the tool again.
Read-only stack inspection exposes its actual retry counter at each tool invocation.
"""

from __future__ import annotations

import argparse
import json
import sys
from types import FrameType
from typing import Any

from ..ledger.daemon import execute

PAYLOAD: dict[str, object] = {"operation": "append_local_marker", "marker": "harmless"}
TOOL_RESULT = "local-effect-acknowledged"
FINAL_RESULT = "local action complete"
PREFIX = "CRASHPOINT_CREWAI "


def emit(event: str, **fields: Any) -> None:
    print(PREFIX + json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def retry_frame() -> dict[str, Any]:
    """Inspect, never change, the innermost CrewAI ToolUsage._use frame."""
    frame: FrameType | None = sys._getframe(1)
    try:
        while frame is not None:
            if (
                frame.f_globals.get("__name__") == "crewai.tools.tool_usage"
                and frame.f_code.co_name == "_use"
            ):
                usage = frame.f_locals["self"]
                return {
                    "entry_point": "crewai.tools.tool_usage.ToolUsage._use",
                    "run_attempt": usage._run_attempts,
                    "max_attempts": usage._max_parsing_attempts,
                    "arguments": dict(frame.f_locals["calling"].arguments or {}),
                }
            frame = frame.f_back
    finally:
        del frame
    raise RuntimeError("The tool did not enter through the inspected CrewAI retry path")


def subject(invoke: str, logical_action_id: str, case: str) -> int:
    # Optional runtime imports stay out of the shared model and oracle.
    from crewai import Agent, Crew, Task
    from crewai.crews.crew_output import CrewOutput
    from crewai.llms.base_llm import BaseLLM
    from crewai.tools import tool
    from pydantic import PrivateAttr

    class ScriptedLLM(BaseLLM):
        _calls: int = PrivateAttr(default=0)

        def supports_function_calling(self) -> bool:
            return False

        def call(
            self, messages: Any, tools: Any = None, callbacks: Any = None,
            available_functions: Any = None, from_task: Any = None,
            from_agent: Any = None, response_model: Any = None,
        ) -> str:
            self._calls += 1
            emit("llm_call", ordinal=self._calls, logical_action_id=logical_action_id)
            if self._calls == 1:
                return 'Thought: Perform the local action.\nAction: local_action\nAction Input: {}'
            if self._calls == 2 and TOOL_RESULT in str(messages):
                return f"Thought: The tool returned.\nFinal Answer: {FINAL_RESULT}"
            raise RuntimeError("Scripted LLM expected a successful tool observation")

    attempts = 0
    llm = ScriptedLLM(model="crashpoint-scripted", temperature=0)
    agent: Any = None

    def runtime_state() -> dict[str, Any]:
        handler = agent.tools_handler
        return {
            "tool_result_records": len(agent.tools_results),
            "last_used_tool_present": handler.last_used_tool is not None,
            "cache_present": handler.cache is not None,
            "cached_result": handler.cache.read(tool="local_action", input="")
            if handler.cache is not None else None,
        }

    @tool("local_action")
    def local_action() -> str:
        """Append one harmless local marker for the already assigned logical action."""
        nonlocal attempts
        attempts += 1
        attempt_id = f"{logical_action_id}:attempt-{attempts}"
        fields = {"logical_action_id": logical_action_id, "attempt_id": attempt_id}
        emit("tool_enter", **fields, **retry_frame(), runtime_state=runtime_state())
        if case == "pre_effect" and attempts == 1:
            emit("injected_failure", **fields, point="before_effect", exception="RuntimeError",
                 runtime_state=runtime_state())
            raise RuntimeError("crashpoint injected pre-effect failure")
        response = execute(invoke, logical_action_id, None, PAYLOAD, attempt_id=attempt_id)
        if response != {"ok": True, "receipt": "receipt-ok", "outcome": "OK"}:
            raise RuntimeError("Execute-only ledger did not acknowledge the append")
        emit("effect_ack", **fields, runtime_state=runtime_state())
        if case == "post_effect" and attempts == 1:
            emit("injected_failure", **fields, point="after_effect_before_tool_return",
                 exception="RuntimeError", runtime_state=runtime_state())
            raise RuntimeError("crashpoint injected post-effect pre-confirmation failure")
        emit("tool_return", **fields, result=TOOL_RESULT)
        return TOOL_RESULT

    agent = Agent(
        role="Local marker operator", goal="Perform the one assigned local action",
        backstory="A deterministic local reliability fixture.", llm=llm,
        tools=[local_action], max_iter=3, max_retry_limit=0, cache=True,
        allow_delegation=False, verbose=False, checkpoint=False,
    )
    task = Task(description="Perform the assigned local action once.",
                expected_output=FINAL_RESULT, agent=agent)
    crew = Crew(agents=[agent], tasks=[task], cache=True, memory=False,
                verbose=False, tracing=False, checkpoint=False)
    emit("worker_started", case=case, logical_action_id=logical_action_id,
         executor=f"{agent.executor_class.__module__}.{agent.executor_class.__name__}")
    try:
        output = crew.kickoff()
    except Exception as exc:
        emit("runtime_result", status="error", result=None,
             error_type=type(exc).__name__, llm_calls=llm._calls, tool_attempts=attempts,
             agent_retries=agent._times_executed, runtime_state=runtime_state())
        return 1
    # This fixture never enables streaming (no stream=True anywhere above), so kickoff() always
    # returns the non-streaming CrewOutput; asserted rather than cast, since a real divergence
    # here would mean the fixture stopped measuring what it claims to measure.
    assert isinstance(output, CrewOutput), f"expected CrewOutput, got {type(output).__name__}"
    emit("runtime_result", status="completed", result=output.raw, error_type=None,
         llm_calls=llm._calls, tool_attempts=attempts, agent_retries=agent._times_executed,
         runtime_state=runtime_state())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--invoke", required=True)
    ap.add_argument("--logical-action-id", required=True)
    ap.add_argument("--case", choices=("clean", "pre_effect", "post_effect"), required=True)
    args = ap.parse_args(argv)
    return subject(args.invoke, args.logical_action_id, args.case)


if __name__ == "__main__":
    raise SystemExit(main())
