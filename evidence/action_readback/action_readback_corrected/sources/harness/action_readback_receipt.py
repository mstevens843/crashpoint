"""Strict machine-readable receipt schema for the pre-dispatch action identity / external
readback experiment.

WHAT THIS IS. One receipt per trial, keeping four questions separate, exactly as the experiment's
own scope demands, instead of collapsing them into one verdict:

  1. the intended action and its expected payload digest (``admission_payload_digest``, committed
     to a caller-owned SQLite store BEFORE dispatch, confirmed via an independent post-commit
     read-back - never inferred from a timestamp alone)
  2. the client/worker's own claim, exit state, and local receipt (``worker_a_*``/``worker_b_*``,
     ``client_claim`` - what the dispatching process believed happened, and whether it ever got to
     say so)
  3. what the receiver (the out-of-process ledger) actually retains (``effect_count``,
     ``effect_payload_digests``, ``effect_attempt_ids`` - read by a fresh observer process with
     its own file handle, never the worker's self-report or a live in-memory dump)
  4. whether the observation was complete enough to compare those things at all
     (``observation_availability``, ``externally_verified`` - deliberately separate from
     ``passed``: a trial predicted to have unavailable readback can agree with its prediction
     while still never being externally verified)

``external_outcome`` is derived, not a fifth source of truth: a pure function of effect_count and
digest agreement against the ADMITTED payload (see ``derive_external_outcome``), so a reader can
recompute it and an effect recorded under the right action_id but the wrong payload can never be
folded into "the intended action succeeded" (see ``ONE_EFFECT_MISMATCHED``). This is the same
fail-closed discipline as ``safeagent_ttl_receipt``/``crewai_retry_receipt``/``ledger.oracle``.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any, Final

SCHEMA: Final = "crashpoint.action_readback.receipt.v1"

CASES: Final[tuple[str, ...]] = (
    "clean",
    "effect_before_lost_receipt",
    "stopped_before_effect",
    "naive_retry",
    "payload_mismatch",
    "unavailable_readback",
)

ACTION_TYPE: Final = "action_readback_local_action"

# The complete set of source/lock/prediction files this experiment's bundle must embed -
# independent of whatever a (possibly tampered) manifest.embedded_source_sha256 dict happens to
# list. Both the runner (which writes these) and the verifier (which requires exactly these to be
# present and correctly hashed) import this same list, so omitting a source and its manifest entry
# together cannot go unnoticed. Unlike the TTL experiment, there is no third-party package under
# test here, so provenance is bounded by crashpoint's own source tree plus its dependency lock.
REQUIRED_EMBEDDED_SOURCE_FILES: Final[tuple[str, ...]] = (
    "harness/action_readback.py",
    "harness/action_readback_runtime.py",
    "harness/action_readback_receipt.py",
    "harness/action_readback_verify.py",
    "ledger/core.py",
    "ledger/daemon.py",
    "canonical.py",
)
REQUIRED_EMBEDDED_LOCK_FILES: Final[tuple[str, ...]] = ("pyproject.toml", "uv.lock")

SOURCE_REFERENCES: Final[tuple[str, ...]] = (
    "crashpoint.ledger.daemon.execute(invoke_path, intent_id, key, payload, attempt_id=...) - "
    "the receiver; a keyless (key=None) call is always a distinct recorded side effect, never "
    "silently deduped, per crashpoint.ledger.core.LedgerState._crossed",
    "crashpoint.ledger.core.LedgerState records payload_digest and attempt_id per record; it "
    "does NOT retain raw payload bytes, a generic receiver key, or a wall-clock timestamp - the "
    "comparison below is built only from what LedgerState.dump()/the raw JSONL actually store",
    "the admission store is this experiment's own SQLite table (schema below), not a third-party "
    "package - there is no external claim/sweep API under test here",
)

LIMITATIONS: Final = (
    "Same-host, same-user process separation (SIGKILL across local subprocesses on one "
    "macOS/POSIX host), not a sandbox, a container, or an independent organization boundary. The "
    "receiver ledger is authoritative only for this local fixture's harmless effect - it is not a "
    "payment provider or proof about an unobserved system. A hash proves consistency with "
    "retained bytes, not independent authorship, wall-clock truth, or tamper-proof publication. "
    "No global exactly-once claim, no distributed fencing, no host/power-loss durability test "
    "(the admission store's durability configuration is recorded per trial and the claim is "
    "restricted to a killed WORKER process, never a killed host). A zero-effect readback at the "
    "terminal fixture boundary is not general permission to replay an ambiguous real-world "
    "operation. A small, deterministic, predeclared trial count (3 per cell, 18 total): no "
    "statistical reliability-rate claim. No CrewAI, LangGraph, SafeAgent, payment provider, or "
    "SABLE integration - this is a bounded fixture for a future native-vs-SafeAgent-Control field "
    "comparison, not that comparison itself."
)

# --------------------------------------------------------------------------------------------
# Result vocabulary - small, explicit, and kept in separate fields on purpose (see module
# docstring). Every set below is closed: an unrecognized value is a schema violation, not a
# best-effort guess.
# --------------------------------------------------------------------------------------------

_VALID_CLIENT_CLAIM = frozenset({"SUCCESS", "FAILURE", "LOST"})
_VALID_OBSERVATION_AVAILABILITY = frozenset({"FULL", "PARTIAL", "UNAVAILABLE"})
_VALID_EXTERNAL_OUTCOME = frozenset(
    {
        "NO_EFFECT",
        "ONE_EFFECT_MATCHING",
        "ONE_EFFECT_MISMATCHED",
        "MULTIPLE_EFFECTS_MATCHING",
        "MULTIPLE_EFFECTS_DIVERGED",
        "INDETERMINATE",
    }
)
_VALID_LEDGER_CLASSIFICATIONS = frozenset(
    {"EXACTLY_ONCE", "DUPLICATED", "DIVERGED", "VOID", "ZERO", "UNVERIFIED"}
)
_VALID_RAW_LEDGER_STATES = frozenset(
    {"NEVER_READ", "READ_FAILED", "READ_EMPTY_CONFIRMED", "READ_OK"}
)
_VALID_INJECTED_FAULTS = frozenset({"payload_mismatch", "observer_evidence_denied"})


def derive_external_outcome(
    effect_count: int | None, digests_match_admission: bool | None
) -> str:
    """Pure function: effect_count and whether every retained effect digest for this action_id
    equals the ADMITTED canonical payload's digest (never "agrees with each other" alone - an
    action_id can be reused for a materially different payload, which is exactly the fault
    ``payload_mismatch`` injects). Never reads a worker's claim."""
    if effect_count is None:
        return "INDETERMINATE"
    if effect_count == 0:
        return "NO_EFFECT"
    if digests_match_admission is None:
        return "INDETERMINATE"
    if effect_count == 1:
        return "ONE_EFFECT_MATCHING" if digests_match_admission else "ONE_EFFECT_MISMATCHED"
    return "MULTIPLE_EFFECTS_MATCHING" if digests_match_admission else "MULTIPLE_EFFECTS_DIVERGED"


def derive_observation_availability(
    raw_ledger_state: str, observer_exit_ok: bool | None
) -> str:
    """Pure function from the raw-ledger read state and the observer process's own exit code.
    A confirmed genuine empty read (a real, established-empty baseline, still empty at
    observation time) is FULL - it is a complete observation of zero effects, not a partial one.
    NEVER_READ (no attempt was made) and READ_FAILED (an attempt was made and failed - the file
    is missing, corrupt, or unreadable) are both UNAVAILABLE: knowing *why* the read failed does
    not make the required evidence any more present. PARTIAL is reserved for a read that DID
    return bytes but whose observer process exited nonzero - a worker claim of success must never
    substitute for readback, and neither may an observer's own unchecked exit code."""
    if raw_ledger_state in {"NEVER_READ", "READ_FAILED"}:
        return "UNAVAILABLE"
    if raw_ledger_state in {"READ_OK", "READ_EMPTY_CONFIRMED"}:
        return "FULL" if observer_exit_ok is True else "PARTIAL"
    return "PARTIAL"  # pragma: no cover - unreachable given the closed enum above


def derive_client_claim(
    *, final_local_receipt: dict[str, object] | None, worker_killed_before_receipt: bool
) -> str:
    """Pure function from whichever worker ran LAST in the trial (Worker B if a retry happened,
    else Worker A). A killed worker that never wrote its local receipt is LOST, not FAILURE - the
    task may well have succeeded at the receiver; only the client's own bookkeeping is missing."""
    if final_local_receipt is not None:
        return "SUCCESS" if final_local_receipt.get("outcome") == "OK" else "FAILURE"
    return "LOST" if worker_killed_before_receipt else "FAILURE"


def derive_externally_verified(
    *, observation_availability: str, external_outcome: str, protocol_valid: bool
) -> bool:
    """A trial is externally verified only when the observation was FULL, the derived outcome is
    not INDETERMINATE, and the trial's own protocol ran as designed. Deliberately separate from
    ``passed``: ``unavailable_readback`` is DESIGNED to have externally_verified=False and can
    still match its prediction (passed=True) - matching a prediction of "we could not verify" is
    not the same claim as "we verified success", and the schema must not let the two merge."""
    return (
        protocol_valid
        and observation_availability == "FULL"
        and external_outcome != "INDETERMINATE"
    )


REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "schema",
    "python_version",
    "crashpoint_version",
    "crashpoint_commit",
    "run_id",
    "case",
    "trial_id",
    "action_id",
    "action_type",
    "receiver_ref",
    "admitted_at_utc",
    "admission_payload_digest",
    "admission_journal_mode",
    "admission_synchronous",
    "admission_commit_confirmed",
    "worker_a_pid",
    "worker_a_attempt_id",
    "worker_a_read_admission_confirmed",
    "worker_a_dispatch_payload_digest",
    "worker_a_killed",
    "worker_a_exit_status",
    "worker_a_barrier_observed",
    "worker_a_barrier_point",
    "worker_a_local_receipt",
    "worker_b_used",
    "worker_b_pid",
    "worker_b_attempt_id",
    "worker_b_exit_status",
    "worker_b_local_receipt",
    "injected_fault",
    "receiver_baseline_established",
    "receiver_seal_dump_count",
    "receiver_seal_dump_head",
    "effect_count",
    "effect_attempt_ids",
    "effect_payload_digests",
    "effect_ledger_classification",
    "digests_match_admission",
    "observer_pid",
    "observer_exit_status",
    "observer_raw_ledger_state",
    "observer_raw_ledger_sha256",
    "protocol_valid",
    "observation_availability",
    "client_claim",
    "external_outcome",
    "externally_verified",
    "observation_complete",
    "invalid_reason",
    "expected_result",
    "observed_result",
    "passed",
    "source_references",
    "limitations",
)


@dataclass(frozen=True)
class ActionReadbackTrial:
    """One completed trial's observations, before it is stamped into a receipt."""

    run_id: str
    case: str
    trial_id: str
    action_id: str
    receiver_ref: str
    admitted_at_utc: str | None
    admission_payload_digest: str | None
    admission_journal_mode: str | None
    admission_synchronous: str | None
    admission_commit_confirmed: bool
    worker_a_pid: int | None
    worker_a_attempt_id: str
    worker_a_read_admission_confirmed: bool
    worker_a_dispatch_payload_digest: str | None
    worker_a_killed: bool
    worker_a_exit_status: int | None
    worker_a_barrier_observed: bool
    worker_a_barrier_point: str | None
    worker_a_local_receipt: dict[str, object] | None
    worker_b_used: bool
    worker_b_pid: int | None
    worker_b_attempt_id: str | None
    worker_b_exit_status: int | None
    worker_b_local_receipt: dict[str, object] | None
    injected_fault: str | None
    receiver_baseline_established: bool
    receiver_seal_dump_count: int | None
    receiver_seal_dump_head: str | None
    effect_count: int | None
    effect_attempt_ids: list[str]
    effect_payload_digests: list[str]
    effect_ledger_classification: str
    digests_match_admission: bool | None
    observer_pid: int | None
    observer_exit_status: int | None
    observer_raw_ledger_state: str
    observer_raw_ledger_sha256: str | None
    protocol_valid: bool
    observation_complete: bool
    invalid_reason: str | None
    expected_result: dict[str, object]


def build_receipt(trial: ActionReadbackTrial, *, crashpoint_commit: str) -> dict[str, object]:
    """Stamp one completed trial into a self-describing receipt. Pure given its input."""
    observer_exit_ok = (
        trial.observer_exit_status == 0 if trial.observer_exit_status is not None else None
    )
    observation_availability = derive_observation_availability(
        trial.observer_raw_ledger_state, observer_exit_ok
    )
    external_outcome = derive_external_outcome(trial.effect_count, trial.digests_match_admission)
    final_local_receipt = (
        trial.worker_b_local_receipt if trial.worker_b_used else trial.worker_a_local_receipt
    )
    worker_killed_before_receipt = (
        trial.worker_b_used and trial.worker_b_exit_status != 0
    ) or (not trial.worker_b_used and trial.worker_a_killed)
    client_claim = derive_client_claim(
        final_local_receipt=final_local_receipt,
        worker_killed_before_receipt=worker_killed_before_receipt,
    )
    externally_verified = derive_externally_verified(
        observation_availability=observation_availability,
        external_outcome=external_outcome,
        protocol_valid=trial.protocol_valid,
    )
    observed_result: dict[str, object] = {
        "protocol_valid": trial.protocol_valid,
        "observation_availability": observation_availability,
        "client_claim": client_claim,
        "external_outcome": external_outcome,
        "externally_verified": externally_verified,
    }
    passed = (
        trial.observation_complete
        and trial.invalid_reason is None
        and observed_result == trial.expected_result
    )
    return {
        "schema": SCHEMA,
        "python_version": platform.python_version(),
        "crashpoint_version": "0.0.0",
        "crashpoint_commit": crashpoint_commit,
        "run_id": trial.run_id,
        "case": trial.case,
        "trial_id": trial.trial_id,
        "action_id": trial.action_id,
        "action_type": ACTION_TYPE,
        "receiver_ref": trial.receiver_ref,
        "admitted_at_utc": trial.admitted_at_utc,
        "admission_payload_digest": trial.admission_payload_digest,
        "admission_journal_mode": trial.admission_journal_mode,
        "admission_synchronous": trial.admission_synchronous,
        "admission_commit_confirmed": trial.admission_commit_confirmed,
        "worker_a_pid": trial.worker_a_pid,
        "worker_a_attempt_id": trial.worker_a_attempt_id,
        "worker_a_read_admission_confirmed": trial.worker_a_read_admission_confirmed,
        "worker_a_dispatch_payload_digest": trial.worker_a_dispatch_payload_digest,
        "worker_a_killed": trial.worker_a_killed,
        "worker_a_exit_status": trial.worker_a_exit_status,
        "worker_a_barrier_observed": trial.worker_a_barrier_observed,
        "worker_a_barrier_point": trial.worker_a_barrier_point,
        "worker_a_local_receipt": trial.worker_a_local_receipt,
        "worker_b_used": trial.worker_b_used,
        "worker_b_pid": trial.worker_b_pid,
        "worker_b_attempt_id": trial.worker_b_attempt_id,
        "worker_b_exit_status": trial.worker_b_exit_status,
        "worker_b_local_receipt": trial.worker_b_local_receipt,
        "injected_fault": trial.injected_fault,
        "receiver_baseline_established": trial.receiver_baseline_established,
        "receiver_seal_dump_count": trial.receiver_seal_dump_count,
        "receiver_seal_dump_head": trial.receiver_seal_dump_head,
        "effect_count": trial.effect_count,
        "effect_attempt_ids": list(trial.effect_attempt_ids),
        "effect_payload_digests": list(trial.effect_payload_digests),
        "effect_ledger_classification": trial.effect_ledger_classification,
        "digests_match_admission": trial.digests_match_admission,
        "observer_pid": trial.observer_pid,
        "observer_exit_status": trial.observer_exit_status,
        "observer_raw_ledger_state": trial.observer_raw_ledger_state,
        "observer_raw_ledger_sha256": trial.observer_raw_ledger_sha256,
        "protocol_valid": trial.protocol_valid,
        "observation_availability": observation_availability,
        "client_claim": client_claim,
        "external_outcome": external_outcome,
        "externally_verified": externally_verified,
        "observation_complete": trial.observation_complete,
        "invalid_reason": trial.invalid_reason,
        "expected_result": trial.expected_result,
        "observed_result": observed_result,
        "passed": passed,
        "source_references": list(SOURCE_REFERENCES),
        "limitations": LIMITATIONS,
    }


def validate_receipt(rec: dict[str, Any]) -> list[str]:
    """Return violations; empty means valid. Fail-closed: an ill-shaped or self-contradicting
    receipt cannot PASS, and an invalid trial cannot be silently relabeled valid."""
    problems: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in rec:
            problems.append(f"missing required field: {field}")
    if problems:
        return problems

    if rec["schema"] != SCHEMA:
        problems.append(f"unexpected schema: {rec['schema']!r}")
    if rec["case"] not in CASES:
        problems.append(f"unknown case: {rec['case']!r}")
    if rec["action_type"] != ACTION_TYPE:
        problems.append(f"unexpected action_type: {rec['action_type']!r}")

    injected_fault = rec["injected_fault"]
    if injected_fault is not None and injected_fault not in _VALID_INJECTED_FAULTS:
        problems.append(f"unknown injected_fault: {injected_fault!r}")
    # injected_fault must be case-consistent, not a free-floating label a mutation could attach
    # to any trial to explain away a real anomaly.
    if rec["case"] == "payload_mismatch" and injected_fault != "payload_mismatch":
        problems.append(
            f"case 'payload_mismatch' requires injected_fault='payload_mismatch', got "
            f"{injected_fault!r}"
        )
    if rec["case"] == "unavailable_readback" and injected_fault != "observer_evidence_denied":
        problems.append(
            f"case 'unavailable_readback' requires injected_fault='observer_evidence_denied', "
            f"got {injected_fault!r}"
        )
    negative_control_cases = {"payload_mismatch", "unavailable_readback"}
    if rec["case"] not in negative_control_cases and injected_fault is not None:
        problems.append(
            f"case {rec['case']!r} is not a negative-control case but injected_fault="
            f"{injected_fault!r}"
        )

    classification = rec["effect_ledger_classification"]
    if classification not in _VALID_LEDGER_CLASSIFICATIONS:
        problems.append(f"unknown effect_ledger_classification: {classification!r}")

    raw_state = rec["observer_raw_ledger_state"]
    if raw_state not in _VALID_RAW_LEDGER_STATES:
        problems.append(f"unknown observer_raw_ledger_state: {raw_state!r}")

    observed = rec.get("observed_result")
    if isinstance(observed, dict):
        for key, valid_set in (
            ("client_claim", _VALID_CLIENT_CLAIM),
            ("observation_availability", _VALID_OBSERVATION_AVAILABILITY),
            ("external_outcome", _VALID_EXTERNAL_OUTCOME),
        ):
            value = observed.get(key)
            if value not in valid_set:
                problems.append(f"observed_result[{key!r}]={value!r} is not a recognized value")
    else:
        problems.append("observed_result must be an object")

    effect_count = rec["effect_count"]
    if effect_count is None:
        # passed is NOT required to be False here: unavailable_readback is DESIGNED to have a
        # null effect_count (evidence deliberately denied) and can legitimately pass by matching
        # its own prediction of an unverified/indeterminate outcome - "passed" means "agreed with
        # the prediction", not "achieved a successful effect". What IS required is that such a
        # trial can never claim to be externally verified.
        if isinstance(observed, dict) and observed.get("externally_verified") is True:
            problems.append(
                "effect_count is null: observed_result['externally_verified'] must be False"
            )
        if classification not in {"VOID", "UNVERIFIED"}:
            problems.append(
                f"effect_count is null: effect_ledger_classification must be VOID or "
                f"UNVERIFIED, got {classification!r}"
            )
    else:
        # Type-checked before any numeric comparison: a malformed value (a string, a bool, a
        # float) must produce a structured problem here, never an unhandled TypeError/ValueError
        # from a downstream `>=`/`==` on a value that was never confirmed to be a number.
        invalid_count = (
            not isinstance(effect_count, int)
            or isinstance(effect_count, bool)
            or effect_count < 0
        )
        if invalid_count:
            problems.append(
                f"effect_count must be a non-negative int or null, got {effect_count!r}"
            )
        else:
            if effect_count == 0 and classification not in {"ZERO", "VOID", "UNVERIFIED"}:
                problems.append(
                    f"effect_count is 0 but effect_ledger_classification is {classification!r}: "
                    "zero effects must never be reported as EXACTLY_ONCE"
                )
            if effect_count >= 1 and classification == "ZERO":
                problems.append("effect_count is >=1 but effect_ledger_classification is ZERO")
            attempt_ids = rec.get("effect_attempt_ids")
            digests = rec.get("effect_payload_digests")
            if not isinstance(attempt_ids, list) or len(attempt_ids) != effect_count:
                problems.append(
                    f"effect_attempt_ids must be a list of length effect_count "
                    f"({effect_count!r}), got {attempt_ids!r}"
                )
            if not isinstance(digests, list) or len(digests) != effect_count:
                problems.append(
                    f"effect_payload_digests must be a list of length effect_count "
                    f"({effect_count!r}), got {digests!r}"
                )

    if rec["invalid_reason"] is not None and rec["passed"] is not False:
        problems.append("invalid_reason is set: passed must be False")

    # A receipt's own passed/observed/expected/observation_complete/invalid_reason fields must be
    # internally consistent WITHOUT needing any external file.
    expected = rec.get("expected_result")
    recomputed_passed = (
        rec["observation_complete"] is True
        and rec["invalid_reason"] is None
        and observed == expected
    )
    if bool(rec["passed"]) is not recomputed_passed:
        problems.append(
            f"passed={rec['passed']!r} is not what observation_complete/invalid_reason/"
            f"observed_result==expected_result imply ({recomputed_passed!r}): "
            f"observed_result={observed!r} expected_result={expected!r}"
        )

    # externally_verified must itself be the pure function of the other three fields it is
    # derived from - not an independently-settable boolean a mutation could flip on its own.
    if isinstance(observed, dict):
        recomputed_verified = derive_externally_verified(
            observation_availability=observed.get("observation_availability"),  # type: ignore[arg-type]
            external_outcome=observed.get("external_outcome"),  # type: ignore[arg-type]
            protocol_valid=bool(observed.get("protocol_valid")),
        )
        if observed.get("externally_verified") is not recomputed_verified:
            problems.append(
                f"observed_result['externally_verified']="
                f"{observed.get('externally_verified')!r} is not what protocol_valid/"
                f"observation_availability/external_outcome imply ({recomputed_verified!r})"
            )

    # Protocol-as-a-contract: for a PASSING trial, every one of these fields is required to take
    # the ONE value the case structurally implies.
    if rec["passed"] is True:
        case = rec["case"]
        if rec["admission_commit_confirmed"] is not True:
            problems.append("a passing trial must have admission_commit_confirmed=True")
        if rec["worker_a_read_admission_confirmed"] is not True:
            problems.append("a passing trial must have worker_a_read_admission_confirmed=True")
        if rec["receiver_baseline_established"] is not True:
            problems.append("a passing trial must have receiver_baseline_established=True")
        if rec["protocol_valid"] is not True:
            problems.append("a passing trial must have protocol_valid=True")

        killed = rec["worker_a_killed"]
        exit_status = rec["worker_a_exit_status"]
        # unavailable_readback's fault is injected by the HARNESS (deleting the ledger file)
        # AFTER Worker A completes normally - Worker A's own lifecycle is identical to clean's.
        not_killed_cases = {"clean", "payload_mismatch", "unavailable_readback"}
        if case in not_killed_cases:
            if killed is not False:
                problems.append(
                    f"case {case!r} never kills Worker A, but worker_a_killed is not False"
                )
            if exit_status != 0:
                problems.append(
                    f"case {case!r}'s Worker A must exit 0, got "
                    f"worker_a_exit_status={exit_status!r}"
                )
        else:
            if killed is not True:
                problems.append(f"case {case!r} requires Worker A to be killed")
            if exit_status != -9:
                problems.append(
                    f"case {case!r} requires worker_a_exit_status=-9 (SIGKILL), got "
                    f"{exit_status!r}"
                )

        expected_worker_b_used = case == "naive_retry"
        if rec["worker_b_used"] is not expected_worker_b_used:
            problems.append(
                f"case {case!r} requires worker_b_used={expected_worker_b_used!r}, got "
                f"{rec['worker_b_used']!r}"
            )

    if not isinstance(rec["action_id"], str) or not rec["action_id"]:
        problems.append("action_id must be a non-empty string")
    if not isinstance(rec["trial_id"], str) or not rec["trial_id"]:
        problems.append("trial_id must be a non-empty string")
    if not isinstance(rec["run_id"], str) or not rec["run_id"]:
        problems.append("run_id must be a non-empty string")

    refs = rec["source_references"]
    if not isinstance(refs, list) or not refs:
        problems.append("source_references must be a non-empty list")

    return problems
