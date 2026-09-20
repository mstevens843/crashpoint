from __future__ import annotations

from typing import Any, cast

from crashpoint.harness.action_readback_receipt import (
    ActionReadbackTrial,
    build_receipt,
    derive_client_claim,
    derive_external_outcome,
    derive_externally_verified,
    derive_observation_availability,
    validate_receipt,
)

# --------------------------------------------------------------------------------------------
# Pure derive_* functions.
# --------------------------------------------------------------------------------------------


def test_derive_external_outcome_zero_effects_is_no_effect_regardless_of_digest_field() -> None:
    assert derive_external_outcome(0, None) == "NO_EFFECT"
    assert derive_external_outcome(0, True) == "NO_EFFECT"


def test_derive_external_outcome_none_count_is_indeterminate() -> None:
    assert derive_external_outcome(None, None) == "INDETERMINATE"


def test_derive_external_outcome_one_effect_matching_vs_mismatched() -> None:
    assert derive_external_outcome(1, True) == "ONE_EFFECT_MATCHING"
    assert derive_external_outcome(1, False) == "ONE_EFFECT_MISMATCHED"


def test_derive_external_outcome_multiple_effects_matching_vs_diverged() -> None:
    assert derive_external_outcome(2, True) == "MULTIPLE_EFFECTS_MATCHING"
    assert derive_external_outcome(2, False) == "MULTIPLE_EFFECTS_DIVERGED"


def test_derive_external_outcome_positive_count_with_unknown_digest_agreement() -> None:
    # A positive effect count where the admitted-digest comparison itself could not be made
    # (digests_match_admission is None) must not silently become "matching".
    assert derive_external_outcome(1, None) == "INDETERMINATE"


def test_derive_observation_availability_never_read_and_read_failed_are_both_unavailable() -> None:
    assert derive_observation_availability("NEVER_READ", None) == "UNAVAILABLE"
    assert derive_observation_availability("READ_FAILED", None) == "UNAVAILABLE"


def test_derive_observation_availability_confirmed_empty_with_good_exit_is_full() -> None:
    assert derive_observation_availability("READ_EMPTY_CONFIRMED", True) == "FULL"


def test_derive_observation_availability_ok_read_with_bad_exit_is_partial_not_full() -> None:
    assert derive_observation_availability("READ_OK", False) == "PARTIAL"
    assert derive_observation_availability("READ_OK", None) == "PARTIAL"


def test_derive_client_claim_success_from_local_receipt() -> None:
    assert derive_client_claim(
        final_local_receipt={"outcome": "OK"}, worker_killed_before_receipt=False
    ) == "SUCCESS"


def test_derive_client_claim_lost_when_killed_before_any_receipt() -> None:
    assert derive_client_claim(
        final_local_receipt=None, worker_killed_before_receipt=True
    ) == "LOST"


def test_derive_client_claim_failure_when_not_killed_but_no_receipt() -> None:
    assert derive_client_claim(
        final_local_receipt=None, worker_killed_before_receipt=False
    ) == "FAILURE"


def test_derive_externally_verified_requires_full_availability_and_determinate_outcome() -> None:
    assert derive_externally_verified(
        observation_availability="FULL", external_outcome="ONE_EFFECT_MATCHING",
        protocol_valid=True,
    ) is True
    assert derive_externally_verified(
        observation_availability="UNAVAILABLE", external_outcome="INDETERMINATE",
        protocol_valid=True,
    ) is False
    assert derive_externally_verified(
        observation_availability="FULL", external_outcome="INDETERMINATE", protocol_valid=True,
    ) is False
    assert derive_externally_verified(
        observation_availability="FULL", external_outcome="ONE_EFFECT_MATCHING",
        protocol_valid=False,
    ) is False


# --------------------------------------------------------------------------------------------
# build_receipt / validate_receipt round trip.
# --------------------------------------------------------------------------------------------


def _observed(rec: dict[str, object]) -> dict[str, Any]:
    """build_receipt returns dict[str, object]; observed_result is itself a dict these tests
    need to mutate/read by key, so this narrows the type at the one place that matters."""
    return cast(dict[str, Any], rec["observed_result"])


def _clean_trial(**overrides: Any) -> ActionReadbackTrial:
    action_id = "11111111-1111-1111-1111-111111111111"
    digest = "a" * 64
    defaults: dict[str, Any] = dict(
        run_id="run-1", case="clean", trial_id="clean-0", action_id=action_id,
        receiver_ref="/tmp/inv.sock", admitted_at_utc="2026-09-20T00:00:00+00:00",
        admission_payload_digest=digest, admission_journal_mode="wal",
        admission_synchronous="2", admission_commit_confirmed=True,
        worker_a_pid=111, worker_a_attempt_id=f"{action_id}:worker-a",
        worker_a_read_admission_confirmed=True, worker_a_dispatch_payload_digest=digest,
        worker_a_killed=False, worker_a_exit_status=0, worker_a_barrier_observed=False,
        worker_a_barrier_point=None,
        worker_a_local_receipt={"outcome": "OK", "attempt_id": f"{action_id}:worker-a"},
        worker_b_used=False, worker_b_pid=None, worker_b_attempt_id=None,
        worker_b_exit_status=None, worker_b_local_receipt=None, injected_fault=None,
        receiver_baseline_established=True, receiver_seal_dump_count=1,
        receiver_seal_dump_head="deadbeef", effect_count=1,
        effect_attempt_ids=[f"{action_id}:worker-a"], effect_payload_digests=[digest],
        effect_ledger_classification="EXACTLY_ONCE", digests_match_admission=True,
        observer_pid=222, observer_exit_status=0, observer_raw_ledger_state="READ_OK",
        observer_raw_ledger_sha256="b" * 64, protocol_valid=True, observation_complete=True,
        invalid_reason=None,
        expected_result={
            "protocol_valid": True, "observation_availability": "FULL",
            "client_claim": "SUCCESS", "external_outcome": "ONE_EFFECT_MATCHING",
            "externally_verified": True,
        },
    )
    defaults.update(overrides)
    return ActionReadbackTrial(**defaults)


def test_build_receipt_of_a_clean_trial_passes_validation() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    assert rec["passed"] is True
    assert validate_receipt(rec) == []


def test_build_receipt_unavailable_readback_shape() -> None:
    trial = _clean_trial(
        case="unavailable_readback", worker_a_local_receipt={"outcome": "OK"},
        effect_count=None, effect_attempt_ids=[], effect_payload_digests=[],
        effect_ledger_classification="UNVERIFIED", digests_match_admission=None,
        observer_exit_status=0, observer_raw_ledger_state="READ_FAILED",
        observer_raw_ledger_sha256=None,
        injected_fault="observer_evidence_denied",
        expected_result={
            "protocol_valid": True, "observation_availability": "UNAVAILABLE",
            "client_claim": "SUCCESS", "external_outcome": "INDETERMINATE",
            "externally_verified": False,
        },
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef" * 5)
    assert rec["passed"] is True
    assert _observed(rec)["externally_verified"] is False
    assert validate_receipt(rec) == []


def test_validate_receipt_rejects_missing_fields() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    del rec["effect_count"]
    problems = validate_receipt(rec)
    assert any("missing required field: effect_count" in p for p in problems)


def test_validate_receipt_rejects_malformed_effect_count_without_crashing() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["effect_count"] = "bad"
    problems = validate_receipt(rec)  # must not raise
    assert any("effect_count must be a non-negative int or null" in p for p in problems)


def test_validate_receipt_rejects_bool_disguised_as_effect_count() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["effect_count"] = True  # isinstance(True, int) is True in Python - must be excluded
    problems = validate_receipt(rec)
    assert any("effect_count must be a non-negative int or null" in p for p in problems)


def test_validate_receipt_rejects_zero_effects_labeled_exactly_once() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["effect_count"] = 0
    problems = validate_receipt(rec)
    assert any("zero effects must never be reported as EXACTLY_ONCE" in p for p in problems)


def test_validate_receipt_null_effect_count_does_not_force_passed_false() -> None:
    """unavailable_readback is DESIGNED to pass with a null effect_count - this is the exact
    invariant the first draft of this schema got wrong (see CORRECTIONS/SELF-REVIEW)."""
    trial = _clean_trial(
        case="unavailable_readback", effect_count=None, effect_attempt_ids=[],
        effect_payload_digests=[], effect_ledger_classification="UNVERIFIED",
        digests_match_admission=None, observer_raw_ledger_state="READ_FAILED",
        observer_raw_ledger_sha256=None, injected_fault="observer_evidence_denied",
        expected_result={
            "protocol_valid": True, "observation_availability": "UNAVAILABLE",
            "client_claim": "SUCCESS", "external_outcome": "INDETERMINATE",
            "externally_verified": False,
        },
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef" * 5)
    assert rec["passed"] is True
    assert validate_receipt(rec) == []


def test_validate_receipt_null_effect_count_still_forbids_externally_verified_true() -> None:
    rec = build_receipt(
        _clean_trial(
            case="unavailable_readback", effect_count=None, effect_attempt_ids=[],
            effect_payload_digests=[], effect_ledger_classification="UNVERIFIED",
            digests_match_admission=None, observer_raw_ledger_state="READ_FAILED",
            observer_raw_ledger_sha256=None, injected_fault="observer_evidence_denied",
            expected_result={
                "protocol_valid": True, "observation_availability": "UNAVAILABLE",
                "client_claim": "SUCCESS", "external_outcome": "INDETERMINATE",
                "externally_verified": True,
            },
        ),
        crashpoint_commit="deadbeef" * 5,
    )
    _observed(rec)["externally_verified"] = True
    problems = validate_receipt(rec)
    assert any("externally_verified" in p and "must be False" in p for p in problems)


def test_validate_receipt_rejects_recomputed_externally_verified_mismatch() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    # Only the boolean itself is falsified - protocol_valid/observation_availability/
    # external_outcome (which it must be a pure function of) are left exactly as they were.
    _observed(rec)["externally_verified"] = False
    problems = validate_receipt(rec)
    assert any(
        "externally_verified" in p and "is not what" in p and "imply" in p for p in problems
    )


def test_validate_receipt_rejects_passed_true_with_mismatched_observed_expected() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["passed"] = True
    rec["observed_result"] = dict(_observed(rec), client_claim="FAILURE")
    problems = validate_receipt(rec)
    assert any("passed=True is not what" in p for p in problems)


def test_validate_receipt_rejects_unkilled_worker_a_on_a_case_that_requires_a_kill() -> None:
    trial = _clean_trial(
        case="stopped_before_effect", worker_a_killed=True, worker_a_exit_status=-9,
        worker_a_barrier_observed=True, worker_a_barrier_point="pre_dispatch",
        worker_a_local_receipt=None, effect_count=0, effect_attempt_ids=[],
        effect_payload_digests=[], effect_ledger_classification="ZERO",
        digests_match_admission=None,
        expected_result={
            "protocol_valid": True, "observation_availability": "FULL",
            "client_claim": "LOST", "external_outcome": "NO_EFFECT",
            "externally_verified": True,
        },
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef" * 5)
    assert rec["passed"] is True  # sanity: genuinely valid before the targeted mutation below
    rec["worker_a_killed"] = False  # now contradicts the case's own structural requirement
    problems = validate_receipt(rec)
    assert any("requires Worker A to be killed" in p for p in problems)


def test_validate_receipt_rejects_killed_worker_a_on_clean() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["worker_a_killed"] = True
    problems = validate_receipt(rec)
    assert any("never kills Worker A" in p for p in problems)


def test_validate_receipt_rejects_naive_retry_without_worker_b() -> None:
    action_id = "22222222-2222-2222-2222-222222222222"
    digest = "c" * 64
    rec = build_receipt(
        _clean_trial(
            case="naive_retry", action_id=action_id, admission_payload_digest=digest,
            worker_a_attempt_id=f"{action_id}:worker-a",
            worker_a_dispatch_payload_digest=digest, worker_a_killed=True,
            worker_a_exit_status=-9, worker_a_barrier_observed=True,
            worker_a_barrier_point="post_effect_pre_receipt", worker_a_local_receipt=None,
            worker_b_used=False, effect_count=1, effect_attempt_ids=[f"{action_id}:worker-a"],
            effect_payload_digests=[digest], effect_ledger_classification="EXACTLY_ONCE",
            digests_match_admission=True,
            expected_result={
                "protocol_valid": True, "observation_availability": "FULL",
                "client_claim": "LOST", "external_outcome": "ONE_EFFECT_MATCHING",
                "externally_verified": True,
            },
        ),
        crashpoint_commit="deadbeef" * 5,
    )
    problems = validate_receipt(rec)
    assert any("worker_b_used=True" in p for p in problems)


def test_validate_receipt_rejects_injected_fault_on_a_non_negative_control_case() -> None:
    rec = build_receipt(_clean_trial(injected_fault="payload_mismatch"), crashpoint_commit="d" * 40)
    problems = validate_receipt(rec)
    assert any("is not a negative-control case" in p for p in problems)


def test_validate_receipt_rejects_missing_injected_fault_on_payload_mismatch() -> None:
    action_id = "33333333-3333-3333-3333-333333333333"
    admitted = "a" * 64
    dispatched = "d" * 64
    rec = build_receipt(
        _clean_trial(
            case="payload_mismatch", action_id=action_id, admission_payload_digest=admitted,
            worker_a_attempt_id=f"{action_id}:worker-a",
            worker_a_dispatch_payload_digest=dispatched, injected_fault=None,
            effect_count=1, effect_attempt_ids=[f"{action_id}:worker-a"],
            effect_payload_digests=[dispatched], effect_ledger_classification="EXACTLY_ONCE",
            digests_match_admission=False,
            expected_result={
                "protocol_valid": True, "observation_availability": "FULL",
                "client_claim": "SUCCESS", "external_outcome": "ONE_EFFECT_MISMATCHED",
                "externally_verified": True,
            },
        ),
        crashpoint_commit="d" * 40,
    )
    problems = validate_receipt(rec)
    assert any("requires injected_fault='payload_mismatch'" in p for p in problems)


def test_validate_receipt_rejects_effect_attempt_ids_length_mismatch() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["effect_attempt_ids"] = []
    problems = validate_receipt(rec)
    assert any("effect_attempt_ids must be a list of length effect_count" in p for p in problems)


def test_validate_receipt_rejects_unknown_case() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["case"] = "not_a_real_case"
    problems = validate_receipt(rec)
    assert any("unknown case" in p for p in problems)


def test_validate_receipt_accepts_a_fully_valid_naive_retry_trial() -> None:
    action_id = "44444444-4444-4444-4444-444444444444"
    digest = "e" * 64
    rec = build_receipt(
        _clean_trial(
            case="naive_retry", action_id=action_id, admission_payload_digest=digest,
            worker_a_attempt_id=f"{action_id}:worker-a",
            worker_a_dispatch_payload_digest=digest, worker_a_killed=True,
            worker_a_exit_status=-9, worker_a_barrier_observed=True,
            worker_a_barrier_point="post_effect_pre_receipt", worker_a_local_receipt=None,
            worker_b_used=True, worker_b_pid=333,
            worker_b_attempt_id=f"{action_id}:worker-b-retry", worker_b_exit_status=0,
            worker_b_local_receipt={
                "outcome": "OK", "attempt_id": f"{action_id}:worker-b-retry"
            },
            effect_count=2,
            effect_attempt_ids=[f"{action_id}:worker-a", f"{action_id}:worker-b-retry"],
            effect_payload_digests=[digest, digest],
            effect_ledger_classification="DUPLICATED", digests_match_admission=True,
            expected_result={
                "protocol_valid": True, "observation_availability": "FULL",
                "client_claim": "SUCCESS", "external_outcome": "MULTIPLE_EFFECTS_MATCHING",
                "externally_verified": True,
            },
        ),
        crashpoint_commit="deadbeef" * 5,
    )
    assert rec["passed"] is True
    assert validate_receipt(rec) == []


# --------------------------------------------------------------------------------------------
# Round 5 self-audit: worker_b_used=False must mean no worker_b field was fabricated, regardless
# of case or pass/fail state - found on a non-naive_retry (clean) trial, where nothing previously
# checked worker_b_pid/worker_b_attempt_id/worker_b_exit_status/worker_b_local_receipt at all.
# --------------------------------------------------------------------------------------------


def test_validate_receipt_rejects_fabricated_worker_b_pid_when_unused() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    assert rec["worker_b_used"] is False
    rec["worker_b_pid"] = 999999
    problems = validate_receipt(rec)
    assert any("worker_b_used is False, but worker_b_pid=999999" in p for p in problems)


def test_validate_receipt_rejects_fabricated_worker_b_attempt_id_when_unused() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["worker_b_attempt_id"] = "some-action-id:worker-b-retry"
    problems = validate_receipt(rec)
    assert any("worker_b_used is False, but worker_b_attempt_id=" in p for p in problems)


def test_validate_receipt_rejects_fabricated_worker_b_local_receipt_when_unused() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["worker_b_local_receipt"] = {"outcome": "OK"}
    problems = validate_receipt(rec)
    assert any("worker_b_used is False, but worker_b_local_receipt=" in p for p in problems)


def test_validate_receipt_rejects_fabricated_worker_b_exit_status_when_unused() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    rec["worker_b_exit_status"] = 0
    problems = validate_receipt(rec)
    assert any("worker_b_used is False, but worker_b_exit_status=0" in p for p in problems)


# --------------------------------------------------------------------------------------------
# Round 6: external_outcome/observation_availability/client_claim were valid-enum-checked and
# used to derive externally_verified, but never themselves recomputed from the raw fields
# (effect_count, digests_match_admission, observer_raw_ledger_state, observer_exit_status,
# worker_a/b_local_receipt, worker_a_killed) that derive_external_outcome/
# derive_observation_availability/derive_client_claim actually take as input. A trial whose
# ledger-based cross-check never runs at all in the offline verifier (unavailable_readback has
# no ledger.jsonl retained) had nothing else constraining these raw fields to agree with
# observed_result - confirmed by fabricating a matching effect on an unavailable_readback trial
# while leaving observed_result/expected_result (both correctly INDETERMINATE) untouched, which
# both validate_receipt and the offline verifier previously accepted.
# --------------------------------------------------------------------------------------------


def test_validate_receipt_rejects_fabricated_effect_count_on_unavailable_readback() -> None:
    trial = _clean_trial(
        case="unavailable_readback", effect_count=None, effect_attempt_ids=[],
        effect_payload_digests=[], effect_ledger_classification="UNVERIFIED",
        digests_match_admission=None, observer_raw_ledger_state="READ_FAILED",
        observer_raw_ledger_sha256=None, injected_fault="observer_evidence_denied",
        expected_result={
            "protocol_valid": True, "observation_availability": "UNAVAILABLE",
            "client_claim": "SUCCESS", "external_outcome": "INDETERMINATE",
            "externally_verified": False,
        },
    )
    rec = build_receipt(trial, crashpoint_commit="deadbeef" * 5)
    assert validate_receipt(rec) == []
    action_id = rec["action_id"]
    digest = rec["admission_payload_digest"]
    # Fabricate a real-looking effect (raw fields only) without touching observed_result at all.
    rec["effect_count"] = 1
    rec["effect_attempt_ids"] = [f"{action_id}:worker-a"]
    rec["effect_payload_digests"] = [digest]
    rec["effect_ledger_classification"] = "EXACTLY_ONCE"
    rec["digests_match_admission"] = True
    problems = validate_receipt(rec)
    assert any(
        "observed_result['external_outcome']='INDETERMINATE' is not what the raw evidence "
        "fields imply ('ONE_EFFECT_MATCHING')" in p
        for p in problems
    )


def test_validate_receipt_recomputes_observation_availability_from_raw_ledger_state() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    assert validate_receipt(rec) == []
    # The raw read never happened, but observed_result still claims a FULL observation.
    rec["observer_raw_ledger_state"] = "NEVER_READ"
    problems = validate_receipt(rec)
    assert any(
        "observed_result['observation_availability']='FULL' is not what the raw evidence "
        "fields imply ('UNAVAILABLE')" in p
        for p in problems
    )


def test_validate_receipt_recomputes_client_claim_from_worker_local_receipt() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    assert validate_receipt(rec) == []
    # Worker A never wrote a local receipt and was not killed - real client_claim is FAILURE,
    # but observed_result still claims SUCCESS.
    rec["worker_a_local_receipt"] = None
    rec["worker_a_killed"] = False
    problems = validate_receipt(rec)
    assert any(
        "observed_result['client_claim']='SUCCESS' is not what the raw evidence fields imply "
        "('FAILURE')" in p
        for p in problems
    )


def test_validate_receipt_rejects_top_level_external_outcome_disagreeing_with_observed() -> None:
    """external_outcome is stored twice (flattened at the top level and nested inside
    observed_result) but the offline verifier only ever reads the nested copy - so without this
    check the top-level copy is a completely unconstrained duplicate."""
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    assert validate_receipt(rec) == []
    rec["external_outcome"] = "NO_EFFECT"
    problems = validate_receipt(rec)
    assert any(
        "top-level rec['external_outcome']='NO_EFFECT' disagrees with "
        "observed_result['external_outcome']='ONE_EFFECT_MATCHING'" in p
        for p in problems
    )


def test_validate_receipt_rejects_top_level_externally_verified_disagreeing_with_observed() -> None:
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    assert validate_receipt(rec) == []
    rec["externally_verified"] = False
    problems = validate_receipt(rec)
    assert any(
        "top-level rec['externally_verified']=False disagrees with "
        "observed_result['externally_verified']=True" in p
        for p in problems
    )


def test_validate_receipt_rejects_top_level_protocol_valid_disagreeing_with_observed() -> None:
    """protocol_valid is written from the same source to both the top level and observed_result
    (see build_receipt) - structurally identical to the other four duplicated fields, so it gets
    the same direct consistency check even though the passed=True contract check and the
    expected_result/externally_verified backstops already happen to constrain both directions."""
    rec = build_receipt(_clean_trial(), crashpoint_commit="deadbeef" * 5)
    assert validate_receipt(rec) == []
    rec["protocol_valid"] = False
    _observed(rec)["protocol_valid"] = True  # keep the nested copy True so only the top diverges
    problems = validate_receipt(rec)
    assert any(
        "top-level rec['protocol_valid']=False disagrees with "
        "observed_result['protocol_valid']=True" in p
        for p in problems
    )
