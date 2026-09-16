from __future__ import annotations

import copy
from typing import Any

import pytest

from crashpoint.harness.langgraph_control_receipt import (
    REQUIRED_EXECUTING_SOURCE_MODULES,
    SCHEMA,
    classify_control,
    compute_agreement,
    sable_status,
    validate_receipt,
)


def _classify(worker_ok: bool, status: str, count: int | None, digests: list[str]) -> str:
    return classify_control(
        worker_ok=worker_ok, observer_status=status, effect_count=count, effect_digests=digests
    )


def test_classify_runtime_failure_is_unverified_regardless_of_observer() -> None:
    assert _classify(False, "OBSERVED", 1, ["d"]) == "UNVERIFIED"
    assert _classify(False, "STORE_MISSING", None, []) == "UNVERIFIED"


def test_classify_store_missing_is_void_not_zero() -> None:
    assert _classify(True, "STORE_MISSING", None, []) == "VOID"


def test_classify_corrupt_is_void() -> None:
    assert _classify(True, "CORRUPT", None, []) == "VOID"


def test_classify_observer_error_is_void_not_an_exception() -> None:
    """The publication review found OBSERVER_ERROR raised ValueError instead of classifying. It
    must be treated the same as STORE_MISSING/CORRUPT: an honest VOID, never raised."""
    assert _classify(True, "OBSERVER_ERROR", None, []) == "VOID"


def test_classify_null_count_with_observed_status_is_void() -> None:
    assert _classify(True, "OBSERVED", None, []) == "VOID"


def test_classify_zero_observed_effects_is_lost() -> None:
    assert _classify(True, "OBSERVED", 0, []) == "LOST"


def test_classify_one_observed_effect_is_exactly_once() -> None:
    assert _classify(True, "OBSERVED", 1, ["d"]) == "EXACTLY_ONCE"


def test_classify_two_identical_payloads_is_duplicated() -> None:
    assert _classify(True, "OBSERVED", 2, ["d", "d"]) == "DUPLICATED"


def test_classify_two_different_payloads_is_diverged() -> None:
    assert _classify(True, "OBSERVED", 2, ["d1", "d2"]) == "DIVERGED"


def test_classify_rejects_unknown_observer_status() -> None:
    with pytest.raises(ValueError, match="unknown observer_status"):
        _classify(True, "WAT", 1, [])


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        ("EXACTLY_ONCE", "PASS"),
        ("LOST", "FAIL"),
        ("DUPLICATED", "FAIL"),
        ("DIVERGED", "FAIL"),
        ("VOID", "UNKNOWN"),
        ("UNVERIFIED", "UNKNOWN"),
    ],
)
def test_sable_status_mapping(classification: str, expected: str) -> None:
    assert sable_status(classification) == expected


def test_sable_status_rejects_unknown_classification() -> None:
    with pytest.raises(ValueError, match="unknown oracle_classification"):
        sable_status("NOT_A_REAL_CLASSIFICATION")


def _agree(**overrides: object) -> bool:
    base: dict[str, object] = {
        "classification": "EXACTLY_ONCE",
        "admission_status_after": "completed",
        "admission_accepted_before_invoke": True,
        "worker_exit_status": 0,
        "observer_exit_status": 0,
        "terminal_state": {"done": True},
        "expected_terminal_state": {"done": True},
        "invoke_count": 1,
        "expected_invoke_count": 1,
        "checkpoint_count": 3,
    }
    base.update(overrides)
    return compute_agreement(**base)  # type: ignore[arg-type]


def test_compute_agreement_true_when_every_invariant_holds() -> None:
    assert _agree() is True


def test_compute_agreement_false_on_wrong_classification() -> None:
    assert _agree(classification="DUPLICATED") is False


def test_compute_agreement_false_when_admission_never_completed() -> None:
    assert _agree(admission_status_after="accepted") is False


def test_compute_agreement_false_when_admission_not_accepted_before_invoke() -> None:
    """The publication review's "not-accepted" false accept: accepted_before_invoke=False must
    never coexist with agreement=True."""
    assert _agree(admission_accepted_before_invoke=False) is False


def test_compute_agreement_false_on_nonzero_worker_exit_status() -> None:
    assert _agree(worker_exit_status=77) is False


def test_compute_agreement_false_on_nonzero_observer_exit_status() -> None:
    """A well-formed, byte-verifiable OBSERVED classification is not enough for agreement if the
    observer process itself did not exit cleanly - reproduced by Codex: exit_status=2 with an
    otherwise valid OBSERVED report previously still yielded passed=true."""
    assert _agree(observer_exit_status=2) is False


def test_compute_agreement_false_on_null_observer_exit_status() -> None:
    assert _agree(observer_exit_status=None) is False


def test_compute_agreement_false_on_wrong_invoke_count() -> None:
    """The publication review's "invoke-count-2" false accept."""
    assert _agree(invoke_count=2) is False


def test_compute_agreement_false_on_terminal_state_mismatch() -> None:
    assert _agree(terminal_state={"done": False}) is False


def test_compute_agreement_false_on_null_checkpoint_count() -> None:
    assert _agree(checkpoint_count=None) is False


def test_compute_agreement_false_on_zero_checkpoint_count() -> None:
    assert _agree(checkpoint_count=0) is False


def test_compute_agreement_false_on_bool_checkpoint_count() -> None:
    # bool is an int subclass; must not be accepted as a positive checkpoint count.
    assert _agree(checkpoint_count=True) is False


def _valid_receipt_body() -> dict[str, Any]:
    """A complete, self-consistent, PASSING v2 receipt body - shaped exactly like what
    ``build_receipt`` produces for a real successful run. Tests mutate a deep copy of this and
    assert the specific ``validate_receipt`` rejection reason, without needing a real LangGraph
    execution (this module has no LangGraph dependency and neither do these tests)."""
    exec_files = [
        {"path": p, "sha256": "0" * 64} for p in REQUIRED_EXECUTING_SOURCE_MODULES
    ]
    return {
        "schema": SCHEMA,
        "case": "unit_test",
        "scope": "scope",
        "experiment_family": "application_noncrash_effect_binding",
        "framework_fix": False,
        "identity": {
            "admission_id": "lgnc-aaaa", "thread_id": "lgnc-aaaa", "intent_id": "lgnc-aaaa",
            "identity_binding_note": "note",
        },
        "admission": {
            "admission_db_path": "admission.sqlite", "admission_db_sha256": "a" * 64,
            "accepted_before_invoke": True, "accepted_at_utc": "2026-01-01T00:00:00+00:00",
            "original_input": {"done": False}, "original_input_source": "note",
            "events_after_run": ["accepted", "completed"], "status_after_run": "completed",
        },
        "runtime": {
            "langgraph_version": "1.2.11", "langgraph_checkpoint_version": "4.2.0",
            "langgraph_checkpoint_sqlite_version": "3.1.1", "python_version": "3.12.13",
            "platform": "darwin", "durability_setting": "sync", "crash_injected": False,
            "invoke_count": 1, "worker_exit_status": 0, "worker_ok": True,
            "worker_error_type": None, "worker_error_message": None,
            "worker_returned_state": {"done": True},
        },
        "checkpoint": {
            "checkpoint_db_path": "checkpoint.sqlite", "checkpoint_db_sha256": "b" * 64,
            "thread_scoped_checkpoint_count": 3,
            "checkpoint_query": "select count(*) from checkpoints where thread_id = ?",
            "checkpoint_error": None,
        },
        "observer": {
            "status": "OBSERVED", "pid": 123, "pid_note": "note", "exit_status": 0,
            "stdout_path": "observer-stdout.log", "stderr_path": "observer-stderr.log",
            "report_path": "observer-report.json", "report_sha256": "c" * 64,
            "archived_ledger_readback_path": "ledger-readback.jsonl",
            "archived_ledger_readback_sha256": "d" * 64,
            "original_readback_sha256": "d" * 64, "archive_matches_original": True,
            "chain_valid": True, "first_broken_index": -1, "record_count": 1,
            "representation_note": "note", "parent_fallback_archive_path": None,
            "parent_fallback_archive_sha256": None, "error": None,
        },
        "effect": {
            "observed_count": 1, "effect_digests": ["e" * 64],
            "effect_attempt_ids": ["lgnc-aaaa:attempt-1"],
        },
        "result": {
            "expected": {"invoke_count": 1, "expected_terminal_state": {"done": True}},
            "measured": {
                "runtime_completion": "success", "observation_status": "OBSERVED",
                "effect_count": 1, "admission_status_after": "completed",
                "terminal_state": {"done": True},
            },
            "oracle_classification": "EXACTLY_ONCE", "agreement": True, "passed": True,
        },
        "contract": {"path": "contract.json", "sha256": "f" * 64},
        "manifest": {"path": "manifest.json", "sha256": "g" * 64},
        "source": {
            "base_commit": "0" * 40, "worktree_branch": "main", "dirty": True,
            "executing_source_files": exec_files, "uv_lock_sha256": "h" * 64,
        },
        "sable_facing_mapping": {"note": "n"},
        "limitations": "limits",
    }


def test_valid_receipt_body_has_no_problems() -> None:
    assert validate_receipt(_valid_receipt_body()) == []


def test_wrong_schema_is_rejected_with_one_clean_reason() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["schema"] = "crashpoint.langgraph_control.receipt.v1"
    problems = validate_receipt(body)
    assert len(problems) == 1
    assert "unexpected schema" in problems[0]


def test_not_accepted_before_invoke_cannot_pass() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["admission"]["accepted_before_invoke"] = False
    problems = validate_receipt(body)
    assert any("agreement" in p for p in problems)


def test_worker_exit_77_with_worker_ok_true_is_unconditionally_rejected() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["runtime"]["worker_exit_status"] = 77
    problems = validate_receipt(body)
    assert any("cannot coherently have self-reported ok" in p for p in problems)


def test_invoke_count_mismatch_is_unconditionally_rejected() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["runtime"]["invoke_count"] = 2
    problems = validate_receipt(body)
    assert any("invoke_count" in p and "does not match" in p for p in problems)


def test_result_expected_must_be_an_object() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["result"]["expected"] = []
    problems = validate_receipt(body)
    assert any("result.expected must be an object" in p for p in problems)


def test_result_measured_must_be_an_object() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["result"]["measured"] = "not an object"
    problems = validate_receipt(body)
    assert any("result.measured must be an object" in p for p in problems)


def test_null_checkpoint_count_requires_a_checkpoint_error() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["checkpoint"]["thread_scoped_checkpoint_count"] = None
    problems = validate_receipt(body)
    assert any("checkpoint_error does not explain why" in p for p in problems)


def test_checkpoint_count_and_error_cannot_both_be_set() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["checkpoint"]["checkpoint_error"] = "no such table: checkpoints"
    problems = validate_receipt(body)
    assert any("checkpoint_error is also" in p for p in problems)


def test_missing_required_executing_source_module_is_rejected() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["source"]["executing_source_files"] = [
        f for f in body["source"]["executing_source_files"]
        if not f["path"].endswith("langgraph_control.py")
    ]
    problems = validate_receipt(body)
    assert any("missing required module" in p for p in problems)


def test_unexpected_executing_source_module_is_rejected() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["source"]["executing_source_files"].append(
        {"path": "tests/test_something.py", "sha256": "i" * 64}
    )
    problems = validate_receipt(body)
    assert any("unexpected module" in p for p in problems)


def test_report_path_null_requires_observer_error_status() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["observer"]["report_path"] = None
    body["observer"]["report_sha256"] = None
    problems = validate_receipt(body)
    assert any("report_path is null" in p for p in problems)


def test_report_path_and_sha_must_be_both_null_or_both_set() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["observer"]["report_sha256"] = None
    problems = validate_receipt(body)
    assert any("report_path and report_sha256" in p for p in problems)


def test_parent_fallback_path_and_sha_must_be_both_null_or_both_set() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["observer"]["parent_fallback_archive_path"] = "parent-fallback-ledger-readback.jsonl"
    problems = validate_receipt(body)
    assert any("parent_fallback_archive_path and _sha256" in p for p in problems)


def test_passed_true_requires_archive_matches_original() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["observer"]["archive_matches_original"] = False
    problems = validate_receipt(body)
    assert any("archive_matches_original is not True" in p for p in problems)


def test_observed_status_requires_null_error() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["observer"]["error"] = "should not be set alongside OBSERVED"
    problems = validate_receipt(body)
    assert any("observer.status is OBSERVED but observer.error is also set" in p for p in problems)


def test_non_observed_status_requires_a_non_null_error() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    body["observer"]["status"] = "OBSERVER_ERROR"
    body["observer"]["archived_ledger_readback_path"] = None
    body["observer"]["archived_ledger_readback_sha256"] = None
    body["observer"]["original_readback_sha256"] = None
    body["observer"]["archive_matches_original"] = False
    body["observer"]["chain_valid"] = False
    body["effect"]["observed_count"] = None
    body["effect"]["effect_digests"] = []
    body["effect"]["effect_attempt_ids"] = []
    body["result"]["oracle_classification"] = "VOID"
    body["result"]["agreement"] = False
    body["result"]["passed"] = False
    problems = validate_receipt(body)
    assert any("does not explain why" in p for p in problems)


def test_missing_top_level_field_is_reported() -> None:
    body = copy.deepcopy(_valid_receipt_body())
    del body["checkpoint"]
    problems = validate_receipt(body)
    assert problems == ["missing required field: checkpoint"]
