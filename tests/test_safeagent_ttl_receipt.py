from __future__ import annotations

from typing import Any

from crashpoint.harness.safeagent_ttl_receipt import (
    SafeAgentTTLTrial,
    build_receipt,
    derive_label,
    validate_receipt,
)


def _trial(**overrides: Any) -> SafeAgentTTLTrial:
    defaults: dict[str, Any] = dict(
        case="settled_control",
        release="0.1.23",
        trial_id="settled_control-0.1.23-0",
        action_id="safeagent-ttl-settled_control-0.1.23-0",
        pending_ttl_seconds=1.5,
        safeagent_version="0.1.23",
        safeagent_dist_sha256="a" * 64,
        safeagent_sqlite_store_sha256="b" * 64,
        reset_confirmed=True,
        worker_a_pid=1234,
        worker_a_killed=False,
        worker_a_exit_status=0,
        barrier_observed=True,
        barrier_point="settled",
        sweep_invoked=True,
        sweep_return_value=0,
        has_count_stale_pending=False,
        stale_count_before_sweep=None,
        claimed_at=1000.0,
        measured_age_at_decision=2.0,
        ttl_boundary_confirmed=True,
        retry_claim_admitted=False,
        retry_performed_effect=False,
        effect_count=1,
        effect_ledger_classification="EXACTLY_ONCE",
        ledger_dump_count_before_seal=1,
        final_claim_row={"status": "COMMITTED", "result": {"outcome": "worker-a-settled"}},
        observation_complete=True,
        invalid_reason=None,
        expected_result={
            "retry_claim_admitted": False,
            "effect_count": 1,
            "effect_ledger_classification": "EXACTLY_ONCE",
            "final_claim_status": "COMMITTED",
            "task_completion_label": "settled_once_retry_blocked",
        },
        observed_result={
            "retry_claim_admitted": False,
            "effect_count": 1,
            "effect_ledger_classification": "EXACTLY_ONCE",
            "final_claim_status": "COMMITTED",
            "task_completion_label": "settled_once_retry_blocked",
        },
    )
    defaults.update(overrides)
    return SafeAgentTTLTrial(**defaults)


# --------------------------------------------------------------------------------------------
# derive_label: a pure function of the three separated observations.
# --------------------------------------------------------------------------------------------


def test_derive_label_settled_once_retry_blocked() -> None:
    assert (
        derive_label(
            observation_complete=True, effect_count=1, retry_claim_admitted=False,
            final_status="COMMITTED",
        )
        == "settled_once_retry_blocked"
    )


def test_derive_label_blocked_pending_effect_already_recorded() -> None:
    assert (
        derive_label(
            observation_complete=True, effect_count=1, retry_claim_admitted=False,
            final_status="PENDING",
        )
        == "blocked_pending_effect_already_recorded"
    )


def test_derive_label_duplicated_effect_after_reclaim() -> None:
    assert (
        derive_label(
            observation_complete=True, effect_count=2, retry_claim_admitted=True,
            final_status="COMMITTED",
        )
        == "duplicated_effect_after_reclaim"
    )


def test_derive_label_reclaimed_and_settled_once() -> None:
    assert (
        derive_label(
            observation_complete=True, effect_count=1, retry_claim_admitted=True,
            final_status="COMMITTED",
        )
        == "reclaimed_and_settled_once"
    )


def test_derive_label_blocked_pending_task_unperformed() -> None:
    """The pre-effect, fixed-arm shape: zero effects must never read as success."""
    assert (
        derive_label(
            observation_complete=True, effect_count=0, retry_claim_admitted=False,
            final_status="PENDING",
        )
        == "blocked_pending_task_unperformed"
    )


def test_derive_label_unverified_when_observation_incomplete() -> None:
    assert (
        derive_label(
            observation_complete=False, effect_count=1, retry_claim_admitted=False,
            final_status="COMMITTED",
        )
        == "UNVERIFIED"
    )


def test_derive_label_unverified_when_effect_count_missing() -> None:
    assert (
        derive_label(
            observation_complete=True, effect_count=None, retry_claim_admitted=False,
            final_status="COMMITTED",
        )
        == "UNVERIFIED"
    )


def test_derive_label_unexpected_shape_does_not_guess() -> None:
    """A combination the label vocabulary doesn't recognize (2 effects but retry denied, an
    internally contradictory shape) must fall through to UNEXPECTED_SHAPE, never silently to
    the label of the nearest-looking real case."""
    assert (
        derive_label(
            observation_complete=True, effect_count=2, retry_claim_admitted=False,
            final_status="COMMITTED",
        )
        == "UNEXPECTED_SHAPE"
    )


# --------------------------------------------------------------------------------------------
# build_receipt / validate_receipt: fail-closed schema checks.
# --------------------------------------------------------------------------------------------


def test_build_receipt_of_a_clean_trial_validates() -> None:
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    assert validate_receipt(rec) == []
    assert rec["passed"] is True
    assert rec["task_completion_label"] == "settled_once_retry_blocked"


def test_validate_receipt_flags_missing_field() -> None:
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    del rec["effect_count"]
    problems = validate_receipt(rec)
    assert any("effect_count" in p for p in problems)


def test_validate_receipt_forbids_pass_with_null_effect_count() -> None:
    trial = _trial(
        observation_complete=False,
        invalid_reason="barrier not observed: timed out",
        effect_count=None,
        effect_ledger_classification="EXACTLY_ONCE",  # wrong: must be VOID or UNVERIFIED
        final_claim_row=None,
        observed_result={
            "retry_claim_admitted": None, "effect_count": None,
            "effect_ledger_classification": "EXACTLY_ONCE", "final_claim_status": None,
            "task_completion_label": "UNVERIFIED",
        },
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef")
    assert rec["passed"] is False
    problems = validate_receipt(rec)
    assert any("VOID or UNVERIFIED" in p for p in problems)


def test_validate_receipt_rejects_zero_effects_labelled_exactly_once() -> None:
    """The specific rule the pre_effect_expired_swept/0.1.24 finding depends on: zero effects
    can never be reported as EXACTLY_ONCE, even if every other field is internally consistent."""
    rec = build_receipt(
        _trial(
            effect_count=0,
            effect_ledger_classification="EXACTLY_ONCE",
            retry_claim_admitted=False,
            final_claim_row={"status": "PENDING", "result": None},
        ),
        crashpoint_commit="deadbeef",
    )
    problems = validate_receipt(rec)
    assert any("zero effects" in p for p in problems)


def test_validate_receipt_rejects_nonzero_effects_labelled_zero() -> None:
    rec = build_receipt(
        _trial(effect_count=1, effect_ledger_classification="ZERO"),
        crashpoint_commit="deadbeef",
    )
    problems = validate_receipt(rec)
    assert any("effect_count is >=1 but" in p for p in problems)


def test_validate_receipt_forbids_pass_when_invalid_reason_set() -> None:
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    rec["invalid_reason"] = "pre-kill effect boundary mismatch"
    rec["passed"] = True  # tampered: build_receipt would never itself produce this combination
    problems = validate_receipt(rec)
    assert any("invalid_reason is set" in p for p in problems)


# --------------------------------------------------------------------------------------------
# Regression tests for review findings: check_review.py's contradict_summary / contradict_protocol
# / malformed_count mutations, ported against build_receipt/validate_receipt directly (the
# production functions), independent of any bundle or verify_bundle machinery.
# --------------------------------------------------------------------------------------------


def test_malformed_effect_count_string_is_a_structured_rejection_not_a_crash() -> None:
    """check_review.py's malformed_count: effect_count='bad' must never raise TypeError."""
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    rec["effect_count"] = "bad"
    problems = validate_receipt(rec)  # must not raise
    assert any("effect_count must be a non-negative int" in p for p in problems)


def test_validate_receipt_rejects_passed_true_with_mismatched_observed_expected() -> None:
    """check_review.py's contradict_summary: observed_result/expected_result wildly disagree
    but passed=True. This must be caught by validate_receipt alone, with no file I/O."""
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    rec["observed_result"] = {"effect_count": 999}
    rec["expected_result"] = {"effect_count": -999}
    rec["passed"] = True
    problems = validate_receipt(rec)
    assert any("is not what observation_complete" in p for p in problems)


def test_validate_receipt_rejects_impossible_sweep_return_value() -> None:
    """check_review.py's contradict_protocol: sweep_return_value=999 (only 0 or 1 possible)."""
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    rec["sweep_return_value"] = 999
    problems = validate_receipt(rec)
    assert any("impossible for this experiment" in p for p in problems)


def test_validate_receipt_rejects_negative_measured_age() -> None:
    """check_review.py's contradict_protocol: measured_age_at_decision=-100."""
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    rec["measured_age_at_decision"] = -100
    problems = validate_receipt(rec)
    assert any("negative" in p for p in problems)


def test_validate_receipt_rejects_unkilled_worker_a_on_a_passing_kill_based_case() -> None:
    """check_review.py's contradict_protocol: worker_a_killed=False injected into a case that
    structurally requires a kill for a passing trial."""
    trial = _trial(
        case="pending_expired_swept", release="0.1.23", worker_a_killed=True,
        worker_a_exit_status=-9,
        retry_claim_admitted=True, retry_performed_effect=True, effect_count=2,
        effect_ledger_classification="DUPLICATED",
        final_claim_row={"status": "COMMITTED", "result": {"outcome": "retry-settled"}},
        expected_result={
            "retry_claim_admitted": True, "effect_count": 2,
            "effect_ledger_classification": "DUPLICATED", "final_claim_status": "COMMITTED",
            "task_completion_label": "duplicated_effect_after_reclaim",
        },
        observed_result={
            "retry_claim_admitted": True, "effect_count": 2,
            "effect_ledger_classification": "DUPLICATED", "final_claim_status": "COMMITTED",
            "task_completion_label": "duplicated_effect_after_reclaim",
        },
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef")
    assert validate_receipt(rec) == []  # sanity: the honest version validates cleanly
    rec["worker_a_killed"] = False  # tamper: claim the kill never happened, still passed=True
    problems = validate_receipt(rec)
    assert any("requires Worker A to be killed" in p for p in problems)


def test_validate_receipt_rejects_settled_control_falsely_marked_killed() -> None:
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")  # settled_control, killed=False
    assert validate_receipt(rec) == []
    rec["worker_a_killed"] = True
    problems = validate_receipt(rec)
    assert any("never kills Worker A" in p for p in problems)


# --------------------------------------------------------------------------------------------
# Protocol-as-a-contract: each field checked individually against an otherwise-valid passing
# trial, per the 2026-09-19 second review (check_remaining.py) - a combined mutation of several
# fields does not prove each one is independently checked.
# --------------------------------------------------------------------------------------------


def _passing_kill_based_trial(**overrides: Any) -> dict[str, object]:
    defaults: dict[str, Any] = dict(
        case="pending_expired_swept", release="0.1.23", worker_a_killed=True,
        worker_a_exit_status=-9, retry_claim_admitted=True, retry_performed_effect=True,
        effect_count=2, effect_ledger_classification="DUPLICATED",
        final_claim_row={"status": "COMMITTED", "result": {"outcome": "retry-settled"}},
        expected_result={
            "retry_claim_admitted": True, "effect_count": 2,
            "effect_ledger_classification": "DUPLICATED", "final_claim_status": "COMMITTED",
            "task_completion_label": "duplicated_effect_after_reclaim",
        },
        observed_result={
            "retry_claim_admitted": True, "effect_count": 2,
            "effect_ledger_classification": "DUPLICATED", "final_claim_status": "COMMITTED",
            "task_completion_label": "duplicated_effect_after_reclaim",
        },
    )
    defaults.update(overrides)
    trial = _trial(**defaults)
    rec = build_receipt(trial, crashpoint_commit="deadbeef")
    assert validate_receipt(rec) == []  # sanity: the honest version must validate cleanly
    return rec


def test_validate_receipt_rejects_false_reset_confirmed() -> None:
    rec = _passing_kill_based_trial()
    rec["reset_confirmed"] = False
    problems = validate_receipt(rec)
    assert any("reset_confirmed=True" in p for p in problems)


def test_validate_receipt_rejects_false_ttl_boundary_confirmed() -> None:
    rec = _passing_kill_based_trial()
    rec["ttl_boundary_confirmed"] = False
    problems = validate_receipt(rec)
    assert any("ttl_boundary_confirmed=True" in p for p in problems)


def test_validate_receipt_rejects_null_measured_age_on_a_passing_trial() -> None:
    rec = _passing_kill_based_trial()
    rec["measured_age_at_decision"] = None
    problems = validate_receipt(rec)
    assert any("non-null measured_age_at_decision" in p for p in problems)


def test_validate_receipt_rejects_false_sweep_invoked_on_a_sweeping_case() -> None:
    rec = _passing_kill_based_trial()  # pending_expired_swept requires sweep_invoked=True
    rec["sweep_invoked"] = False
    problems = validate_receipt(rec)
    assert any("requires sweep_invoked=True" in p for p in problems)


def test_validate_receipt_rejects_wrong_pending_ttl_seconds() -> None:
    """check_remaining.py's false_pending_ttl_seconds: 1000 instead of the real 1.5s config."""
    rec = _passing_kill_based_trial()
    rec["pending_ttl_seconds"] = 1000
    problems = validate_receipt(rec)
    assert any("requires pending_ttl_seconds=1.5" in p for p in problems)


def test_validate_receipt_rejects_clean_exit_status_on_a_killed_case() -> None:
    """check_remaining.py's false_worker_a_exit_status: 0 (clean exit) in a case that requires
    the real recorded SIGKILL exit status, -9."""
    rec = _passing_kill_based_trial()
    rec["worker_a_exit_status"] = 0
    problems = validate_receipt(rec)
    assert any("-9 (SIGKILL)" in p for p in problems)


def test_validate_receipt_accepts_settled_control_clean_exit_status() -> None:
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    assert rec["worker_a_exit_status"] == 0
    assert validate_receipt(rec) == []


def test_validate_receipt_pending_before_ttl_requires_its_own_larger_ttl() -> None:
    rec = _passing_kill_based_trial(case="pending_before_ttl", pending_ttl_seconds=30.0)
    assert validate_receipt(rec) == []
    rec["pending_ttl_seconds"] = 1.5
    problems = validate_receipt(rec)
    assert any("requires pending_ttl_seconds=30.0" in p for p in problems)


def test_validate_receipt_pending_expired_no_sweep_requires_sweep_invoked_false() -> None:
    rec = _passing_kill_based_trial(
        case="pending_expired_no_sweep", sweep_invoked=False, sweep_return_value=None,
        effect_count=1, effect_ledger_classification="EXACTLY_ONCE", retry_claim_admitted=False,
        retry_performed_effect=False, final_claim_row={"status": "PENDING", "result": None},
        expected_result={
            "retry_claim_admitted": False, "effect_count": 1,
            "effect_ledger_classification": "EXACTLY_ONCE",
            "final_claim_status": "PENDING",
            "task_completion_label": "blocked_pending_effect_already_recorded",
        },
        observed_result={
            "retry_claim_admitted": False, "effect_count": 1,
            "effect_ledger_classification": "EXACTLY_ONCE",
            "final_claim_status": "PENDING",
            "task_completion_label": "blocked_pending_effect_already_recorded",
        },
    )
    assert validate_receipt(rec) == []
    rec["sweep_invoked"] = True
    problems = validate_receipt(rec)
    assert any("requires sweep_invoked=False" in p for p in problems)


def test_validate_receipt_requires_recognized_case_and_release() -> None:
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    rec["case"] = "not_a_real_case"
    rec["release"] = "9.9.9"
    problems = validate_receipt(rec)
    assert any("unknown case" in p for p in problems)
    assert any("unknown release" in p for p in problems)


def test_validate_receipt_requires_recognized_label() -> None:
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    rec["task_completion_label"] = "definitely_fine_trust_me"
    problems = validate_receipt(rec)
    assert any("unknown task_completion_label" in p for p in problems)


def test_validate_receipt_requires_unverified_label_when_observation_incomplete() -> None:
    rec = build_receipt(_trial(), crashpoint_commit="deadbeef")
    rec["observation_complete"] = False
    rec["invalid_reason"] = "something went wrong"
    rec["passed"] = False
    # task_completion_label is left as "settled_once_retry_blocked" - inconsistent with
    # observation_complete=False, and validate_receipt must catch that a summary label was not
    # honestly downgraded to UNVERIFIED.
    problems = validate_receipt(rec)
    assert any("must be UNVERIFIED" in p for p in problems)


def test_duplicated_effect_after_reclaim_trial_validates() -> None:
    """The central 0.1.23 finding's shape, end to end through the schema."""
    trial = _trial(
        case="pending_expired_swept",
        release="0.1.23",
        worker_a_killed=True, worker_a_exit_status=-9,
        retry_claim_admitted=True,
        retry_performed_effect=True,
        effect_count=2,
        effect_ledger_classification="DUPLICATED",
        final_claim_row={"status": "COMMITTED", "result": {"outcome": "retry-settled"}},
        expected_result={
            "retry_claim_admitted": True, "effect_count": 2,
            "effect_ledger_classification": "DUPLICATED", "final_claim_status": "COMMITTED",
            "task_completion_label": "duplicated_effect_after_reclaim",
        },
        observed_result={
            "retry_claim_admitted": True, "effect_count": 2,
            "effect_ledger_classification": "DUPLICATED", "final_claim_status": "COMMITTED",
            "task_completion_label": "duplicated_effect_after_reclaim",
        },
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef")
    assert validate_receipt(rec) == []
    assert rec["passed"] is True
    assert rec["task_completion_label"] == "duplicated_effect_after_reclaim"


def test_blocked_pending_task_unperformed_trial_validates() -> None:
    """The new 0.1.24 finding's shape: zero effects, permanently PENDING, not a success."""
    trial = _trial(
        case="pre_effect_expired_swept",
        release="0.1.24",
        worker_a_killed=True, worker_a_exit_status=-9,
        retry_claim_admitted=False,
        retry_performed_effect=False,
        effect_count=0,
        effect_ledger_classification="ZERO",
        final_claim_row={"status": "PENDING", "result": None},
        expected_result={
            "retry_claim_admitted": False, "effect_count": 0,
            "effect_ledger_classification": "ZERO", "final_claim_status": "PENDING",
            "task_completion_label": "blocked_pending_task_unperformed",
        },
        observed_result={
            "retry_claim_admitted": False, "effect_count": 0,
            "effect_ledger_classification": "ZERO", "final_claim_status": "PENDING",
            "task_completion_label": "blocked_pending_task_unperformed",
        },
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef")
    assert validate_receipt(rec) == []
    assert rec["passed"] is True
    assert rec["task_completion_label"] == "blocked_pending_task_unperformed"
