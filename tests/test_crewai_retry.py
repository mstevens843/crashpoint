from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from crashpoint.canonical import receipt
from crashpoint.harness import crewai_retry
from crashpoint.harness.crewai_retry_receipt import (
    CrewAIRetryTrial,
    build_receipt,
    validate_receipt,
)
from crashpoint.harness.crewai_retry_runtime import PREFIX
from crashpoint.harness.ledger_process import LedgerDaemon

_ROOT = Path(__file__).resolve().parents[1]
_PREDICTION = json.loads((_ROOT / "results" / "11-crewai-retry-prediction.json").read_text())
_EVIDENCE_PATH = _ROOT / "evidence" / "crewai_retry.json"


def _event(event: str, **fields: Any) -> dict[str, Any]:
    return {"event": event, **fields}


def _clean_events(logical_action_id: str) -> list[dict[str, Any]]:
    return [
        _event("worker_started", case="clean", logical_action_id=logical_action_id),
        _event("llm_call", logical_action_id=logical_action_id, ordinal=1),
        _event(
            "tool_enter",
            logical_action_id=logical_action_id,
            attempt_id=f"{logical_action_id}:attempt-1",
            entry_point="crewai.tools.tool_usage.ToolUsage._use",
            run_attempt=1,
        ),
        _event(
            "effect_ack",
            logical_action_id=logical_action_id,
            attempt_id=f"{logical_action_id}:attempt-1",
        ),
        _event(
            "tool_return",
            logical_action_id=logical_action_id,
            attempt_id=f"{logical_action_id}:attempt-1",
        ),
        _event("llm_call", logical_action_id=logical_action_id, ordinal=2),
        _event("runtime_result", status="completed", tool_attempts=1, llm_calls=2),
    ]


def _pre_effect_events(logical_action_id: str) -> list[dict[str, Any]]:
    a1 = f"{logical_action_id}:attempt-1"
    a2 = f"{logical_action_id}:attempt-2"
    return [
        _event("worker_started", case="pre_effect", logical_action_id=logical_action_id),
        _event(
            "tool_enter",
            logical_action_id=logical_action_id,
            attempt_id=a1,
            entry_point="crewai.tools.tool_usage.ToolUsage._use",
            run_attempt=1,
        ),
        _event(
            "injected_failure",
            logical_action_id=logical_action_id,
            attempt_id=a1,
            point="before_effect",
        ),
        _event(
            "tool_enter",
            logical_action_id=logical_action_id,
            attempt_id=a2,
            entry_point="crewai.tools.tool_usage.ToolUsage._use",
            run_attempt=2,
        ),
        _event("effect_ack", logical_action_id=logical_action_id, attempt_id=a2),
        _event("tool_return", logical_action_id=logical_action_id, attempt_id=a2),
        _event("runtime_result", status="completed", tool_attempts=2, llm_calls=2),
    ]


def _post_effect_events(logical_action_id: str) -> list[dict[str, Any]]:
    a1 = f"{logical_action_id}:attempt-1"
    a2 = f"{logical_action_id}:attempt-2"
    return [
        _event("worker_started", case="post_effect", logical_action_id=logical_action_id),
        _event(
            "tool_enter",
            logical_action_id=logical_action_id,
            attempt_id=a1,
            entry_point="crewai.tools.tool_usage.ToolUsage._use",
            run_attempt=1,
        ),
        _event("effect_ack", logical_action_id=logical_action_id, attempt_id=a1),
        _event(
            "injected_failure",
            logical_action_id=logical_action_id,
            attempt_id=a1,
            point="after_effect_before_tool_return",
        ),
        _event(
            "tool_enter",
            logical_action_id=logical_action_id,
            attempt_id=a2,
            entry_point="crewai.tools.tool_usage.ToolUsage._use",
            run_attempt=2,
        ),
        _event("effect_ack", logical_action_id=logical_action_id, attempt_id=a2),
        _event("tool_return", logical_action_id=logical_action_id, attempt_id=a2),
        _event("runtime_result", status="completed", tool_attempts=2, llm_calls=2),
    ]


# --------------------------------------------------------------------------------------------
# parse_events: the prefix filter must recover only the structured lines, ignoring CrewAI's own
# banners, tracing-preference panel, and blank lines.
# --------------------------------------------------------------------------------------------


def test_parse_events_filters_non_prefixed_noise() -> None:
    stdout = "\n".join(
        [
            "",
            "╭──── Tracing Preference Saved ────╮",
            PREFIX + json.dumps({"event": "worker_started", "case": "clean"}),
            "some unrelated banner line",
            PREFIX + json.dumps({"event": "llm_call", "ordinal": 1}),
            "",
        ]
    )
    events = crewai_retry.parse_events(stdout)
    assert events == [
        {"event": "worker_started", "case": "clean"},
        {"event": "llm_call", "ordinal": 1},
    ]


# --------------------------------------------------------------------------------------------
# validate_injection_order: synthetic event streams, both well-formed and deliberately broken.
# --------------------------------------------------------------------------------------------


def test_injection_order_accepts_well_formed_clean_case() -> None:
    assert crewai_retry.validate_injection_order(_clean_events("clean-x"), "clean", "clean-x") == []


def test_injection_order_accepts_well_formed_pre_effect_case() -> None:
    events = _pre_effect_events("pre-x")
    assert crewai_retry.validate_injection_order(events, "pre_effect", "pre-x") == []


def test_injection_order_accepts_well_formed_post_effect_case() -> None:
    events = _post_effect_events("post-x")
    assert crewai_retry.validate_injection_order(events, "post_effect", "post-x") == []


def test_injection_order_rejects_effect_before_injected_failure_in_pre_effect() -> None:
    """A corrupted pre_effect trace where attempt-1 commits an effect before failing must be
    flagged: that would mean the harness's injection point moved, not that CrewAI behaved
    differently."""
    logical_action_id = "pre-bad"
    a1 = f"{logical_action_id}:attempt-1"
    events = _pre_effect_events(logical_action_id)
    # Rewrite the effect_ack to (wrongly) belong to attempt-1 instead of attempt-2.
    for e in events:
        if e["event"] == "effect_ack":
            e["attempt_id"] = a1
    problems = crewai_retry.validate_injection_order(events, "pre_effect", logical_action_id)
    assert any("attempt-2" in p for p in problems)


def test_injection_order_rejects_late_commit_in_post_effect() -> None:
    """post_effect must commit its first effect BEFORE the injected failure; if the event order
    is reversed, the trial is not measuring what the case claims to measure."""
    logical_action_id = "post-bad"
    events = _post_effect_events(logical_action_id)
    ack_event = next(e for e in events if e["event"] == "effect_ack")
    injected_event = next(e for e in events if e["event"] == "injected_failure")
    events.remove(ack_event)
    injected_index = events.index(injected_event)
    events.insert(injected_index + 1, ack_event)  # now injected_failure comes first
    problems = crewai_retry.validate_injection_order(events, "post_effect", logical_action_id)
    assert any("effect_ack(1) before injected_failure(1)" in p for p in problems)


def test_injection_order_rejects_harness_driven_retry() -> None:
    """If the retried tool_enter did not originate inside ToolUsage._use, or CrewAI's own
    _run_attempts counter did not advance to 2, the retry was not actually CrewAI's."""
    logical_action_id = "pre-notcrewai"
    events = _pre_effect_events(logical_action_id)
    second_enter = [e for e in events if e["event"] == "tool_enter"][1]
    second_enter["entry_point"] = "harness.fake_retry_loop"
    second_enter["run_attempt"] = 99
    problems = crewai_retry.validate_injection_order(events, "pre_effect", logical_action_id)
    assert any("ToolUsage._use" in p for p in problems)
    assert any("run_attempt == 2" in p for p in problems)


def test_injection_order_rejects_wrong_logical_action_id() -> None:
    events = _clean_events("clean-y")
    events[0]["logical_action_id"] = "someone-elses-id"
    problems = crewai_retry.validate_injection_order(events, "clean", "clean-y")
    assert any("someone-elses-id" in p for p in problems)


# --------------------------------------------------------------------------------------------
# validate_receipt: fail-closed schema checks that do not require CrewAI at all.
# --------------------------------------------------------------------------------------------


def _sample_trial(**overrides: Any) -> CrewAIRetryTrial:
    defaults: dict[str, Any] = dict(
        case="clean",
        logical_action_id="clean-0",
        attempt_ids=("clean-0:attempt-1",),
        injection_point="none",
        worker_exit_status=0,
        runtime_reported_result={"status": "completed"},
        observation_complete=True,
        effect_ids=("clean-0:attempt-1",),
        effect_count=1,
        expected_result={"effect_count": 1},
        observed_result={"effect_count": 1},
        oracle_classification="EXACTLY_ONCE",
        injection_problems=(),
    )
    defaults.update(overrides)
    return CrewAIRetryTrial(**defaults)


def test_build_receipt_of_a_clean_trial_validates() -> None:
    rec = build_receipt(_sample_trial(), crashpoint_commit="deadbeef")
    assert validate_receipt(rec) == []
    assert rec["passed"] is True


def test_validate_receipt_flags_missing_fields() -> None:
    rec = build_receipt(_sample_trial(), crashpoint_commit="deadbeef")
    del rec["effect_count"]
    problems = validate_receipt(rec)
    assert any("effect_count" in p for p in problems)


def test_validate_receipt_forbids_pass_with_null_effect_count() -> None:
    """The oracle-unreadable rule: null effect_count must never carry classification PASS-able
    values, and passed must be False."""
    trial = _sample_trial(
        observation_complete=False,
        effect_count=None,
        oracle_classification="EXACTLY_ONCE",  # wrong: must be VOID or UNVERIFIED
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef")
    # passed is derived correctly by build_receipt (False, since observation_complete is False)...
    assert rec["passed"] is False
    # ...but the classification is still wrong, and validate_receipt must catch it.
    problems = validate_receipt(rec)
    assert any("VOID or UNVERIFIED" in p for p in problems)


def test_validate_receipt_accepts_unverified_on_incomplete_observation() -> None:
    trial = _sample_trial(
        observation_complete=False,
        effect_count=None,
        oracle_classification="UNVERIFIED",
        injection_problems=("subprocess timed out before completion",),
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef")
    assert rec["passed"] is False
    assert validate_receipt(rec) == []


def test_validate_receipt_requires_mutually_exclusive_retry_flags() -> None:
    rec = build_receipt(_sample_trial(), crashpoint_commit="deadbeef")
    rec["external_retrigger"] = True  # now both built_in_retry and external_retrigger are True
    problems = validate_receipt(rec)
    assert any("built_in_retry and external_retrigger" in p for p in problems)


def test_validate_receipt_requires_distinct_attempt_ids() -> None:
    trial = _sample_trial(attempt_ids=("clean-0:attempt-1", "clean-0:attempt-1"))
    rec = build_receipt(trial, crashpoint_commit="deadbeef")
    problems = validate_receipt(rec)
    assert any("distinct" in p for p in problems)


# --------------------------------------------------------------------------------------------
# Real CrewAI integration: one trial per case, shared across the tests below to avoid paying
# CrewAI's import/startup cost three times per test function.
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def crewai_receipts() -> dict[str, dict[str, Any]]:
    pytest.importorskip("crewai")
    with tempfile.TemporaryDirectory() as tmp, LedgerDaemon(Path(tmp) / "ledger") as ledger:
        return {
            case: crewai_retry.run_trial(
                ledger, case, 0, _PREDICTION, "test-commit", timeout=60.0
            )
            for case in crewai_retry.CASES
        }


def test_clean_case_records_exactly_one_effect(
    crewai_receipts: dict[str, dict[str, Any]],
) -> None:
    rec = crewai_receipts["clean"]
    assert validate_receipt(rec) == []
    assert rec["effect_count"] == 1
    assert rec["oracle_classification"] == "EXACTLY_ONCE"
    assert rec["attempt_ids"] == ["clean-0:attempt-1"]
    assert rec["passed"] is True


def test_pre_effect_does_not_commit_before_the_injected_failure(
    crewai_receipts: dict[str, dict[str, Any]],
) -> None:
    rec = crewai_receipts["pre_effect"]
    assert validate_receipt(rec) == []
    assert rec["injection_problems"] == []
    # The only ledger crossing came from attempt-2: attempt-1 raised before ever calling execute().
    assert rec["effect_ids"] == ["pre_effect-0:attempt-2"]
    assert rec["effect_count"] == 1
    assert rec["oracle_classification"] == "EXACTLY_ONCE"
    assert rec["passed"] is True


def test_post_effect_commits_before_the_injected_failure(
    crewai_receipts: dict[str, dict[str, Any]],
) -> None:
    rec = crewai_receipts["post_effect"]
    assert validate_receipt(rec) == []
    assert rec["injection_problems"] == []
    # Both attempts crossed the ledger: attempt-1's effect committed, THEN the failure was
    # injected, THEN CrewAI retried and attempt-2 committed a second, distinct crossing.
    assert rec["effect_ids"] == ["post_effect-0:attempt-1", "post_effect-0:attempt-2"]
    assert rec["effect_count"] == 2
    assert rec["oracle_classification"] == "DUPLICATED"
    assert rec["passed"] is True


@pytest.mark.parametrize("case", ["pre_effect", "post_effect"])
def test_second_invocation_is_initiated_by_crewai(
    crewai_receipts: dict[str, dict[str, Any]], case: str
) -> None:
    rec = crewai_receipts[case]
    tool_enters = [e for e in rec["events"] if e["event"] == "tool_enter"]
    assert len(tool_enters) == 2
    second = tool_enters[1]
    # Read live off CrewAI's own ToolUsage._run_attempts counter and stack frame, not asserted
    # by the harness: the retry was CrewAI re-entering ToolUsage._use, not a harness-side loop.
    assert second["entry_point"] == "crewai.tools.tool_usage.ToolUsage._use"
    assert second["run_attempt"] == 2
    # And the separate agent-level retry path (Agent._times_executed/max_retry_limit) never
    # engaged: this is a tool retry, not an agent retry.
    assert rec["runtime_reported_result"]["agent_retries"] == 0
    assert rec["runtime_reported_result"]["llm_calls"] == 2


def test_full_run_three_cases_agree_with_prediction() -> None:
    pytest.importorskip("crewai")
    record = crewai_retry.run(1, "test_crewai_retry")
    assert record["all_agree"] is True
    assert record["framework_fix"] is False
    assert record["experiment_family"] == "tool_retry_duplication"
    cases = record["cases"]
    assert isinstance(cases, dict)
    assert cases["clean"]["expected_classification"] == "EXACTLY_ONCE"
    assert cases["pre_effect"]["expected_classification"] == "EXACTLY_ONCE"
    assert cases["post_effect"]["expected_classification"] == "DUPLICATED"
    trials = record["trials"]
    assert isinstance(trials, list)
    for rec in trials:
        assert validate_receipt(rec) == []


# --------------------------------------------------------------------------------------------
# Checked-in evidence: the receipted k=30-per-case record must still validate and still match
# the pre-registered prediction, without re-running anything.
# --------------------------------------------------------------------------------------------


def test_checked_in_crewai_retry_evidence_receipt() -> None:
    record = json.loads(_EVIDENCE_PATH.read_text())
    body = dict(record)
    body.pop("receipt")
    assert record["receipt"] == receipt(body)
    assert record["experiment_family"] == "tool_retry_duplication"
    assert record["framework_fix"] is False
    assert record["all_agree"] is True
    assert record["cases"]["clean"]["classifications"] == {"EXACTLY_ONCE": record["k_per_case"]}
    assert record["cases"]["pre_effect"]["classifications"] == {
        "EXACTLY_ONCE": record["k_per_case"]
    }
    assert record["cases"]["post_effect"]["classifications"] == {
        "DUPLICATED": record["k_per_case"]
    }
    for rec in record["trials"]:
        assert validate_receipt(rec) == []
