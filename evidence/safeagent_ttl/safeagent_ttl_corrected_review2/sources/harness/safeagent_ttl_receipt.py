"""Strict machine-readable receipt schema for the SafeAgent TTL / sweep / fresh-client experiment.

WHAT THIS IS. One receipt per trial. It keeps three questions separate, exactly as the
experiment's own scope demands, instead of collapsing them into one verdict:

  1. did another claim succeed (``retry_claim_admitted``)
  2. how many external effects actually occurred (``effect_count``, read from the independent
     ledger, never from a claim return value or a worker's self-report)
  3. did the logical task reach a settled result (``final_claim_row``, read from SafeAgent's own
     ``get()``)

``task_completion_label`` is a label, not a fourth source of truth: it is a pure function of the
three fields above (see ``derive_label``), so a reader can recompute it and it can never smuggle
in a judgment the raw fields do not support. A denied retry is never labelled a completed
recovery, and a PENDING row with zero effects is labelled unperformed/unresolved, never success -
this is the check ``validate_receipt`` enforces structurally, the same fail-closed discipline as
``crewai_retry_receipt`` and ``ledger.oracle``.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any, Final

SCHEMA: Final = "crashpoint.safeagent_ttl.receipt.v1"

CASES: Final[tuple[str, ...]] = (
    "settled_control",
    "pending_before_ttl",
    "pending_expired_no_sweep",
    "pending_expired_swept",
    "pre_effect_expired_swept",
)
RELEASES: Final[tuple[str, ...]] = ("0.1.23", "0.1.24")

# Shared timing contract. Both the runner (safeagent_ttl.py, which actually times trials) and
# this module's own validate_receipt import these, so a trial's self-reported
# pending_ttl_seconds can be checked against the one true config instead of trusting the
# receipt's own copy of it, and so the two modules cannot silently drift apart.
TTL_EXPIRED_SECONDS: Final[float] = 1.5
TTL_BEFORE_CONTROL_SECONDS: Final[float] = 30.0


def expected_ttl_for_case(case: str) -> float:
    return TTL_BEFORE_CONTROL_SECONDS if case == "pending_before_ttl" else TTL_EXPIRED_SECONDS


def case_requires_sweep(case: str) -> bool:
    """Whether this case's protocol invokes sweep_stale_pending() at all. Every case except
    pending_expired_no_sweep does, including settled_control (a harmless no-op cross-check
    there, since sweep only ever touches PENDING rows)."""
    return case != "pending_expired_no_sweep"


# The complete set of source files this experiment's bundle must embed - independent of whatever
# a (possibly tampered) manifest.embedded_source_sha256 dict happens to list. Both the runner
# (which writes these) and the verifier (which requires exactly these to be present and
# correctly hashed) import this same list, so omitting a source and its manifest entry together
# cannot go unnoticed.
REQUIRED_EMBEDDED_SOURCE_FILES: Final[tuple[str, ...]] = (
    "harness/safeagent_ttl.py",
    "harness/safeagent_ttl_runtime.py",
    "harness/safeagent_ttl_receipt.py",
    "harness/safeagent_ttl_verify.py",
    "ledger/core.py",
    "ledger/daemon.py",
    "canonical.py",
    "safeagent_exec_guard/0.1.23/sqlite_store.py",
    "safeagent_exec_guard/0.1.24/sqlite_store.py",
)

SOURCE_REFERENCES: Final[tuple[str, ...]] = (
    "safeagent_exec_guard/sqlite_store.py SQLiteExecutionStore - read directly out of each "
    "isolated venv's installed site-packages copy, confirmed byte-identical to the PyPI wheel "
    "by sha256, not read from GitHub main",
    "claim(request_id, action, agent_id=None) -> bool - INSERT with request_id as PRIMARY KEY; "
    "returns False on sqlite3.IntegrityError regardless of the existing row's status (PENDING "
    "or COMMITTED)",
    "settle(request_id, result) -> None - UPDATE ... WHERE request_id=? AND status='PENDING'; "
    "a no-op against an already-COMMITTED or nonexistent row",
    "get(request_id) -> dict | None - a fresh, non-mutating read of the row, or None if no row "
    "exists (CLAIMABLE)",
    "0.1.23 sweep_stale_pending() -> int - DELETEs PENDING rows with claimed_at older than "
    "pending_ttl_seconds and returns the deleted row count; a swept request_id becomes claimable "
    "again",
    "0.1.24 sweep_stale_pending() -> int - unconditionally returns 0 and deletes no rows "
    "('Deprecated fail-closed compatibility method; modifies no rows')",
    "0.1.24 count_stale_pending() -> int - new in this release; counts PENDING rows older than "
    "pending_ttl_seconds without changing them; absent in 0.1.23",
)

LIMITATIONS: Final = (
    "SQLiteExecutionStore only: no /sweep HTTP route, no PostgresExecutionStore, no MCP/HTTP "
    "server layer, no payment/x402/agent_id gating (agent_id=None throughout), no n8n or SABLE "
    "integration, no CrewAI integration. Same-host, same-user process separation (SIGKILL across "
    "local subprocesses and isolated venvs on one macOS/POSIX host), not a sandbox, a container, "
    "or an independent organization boundary, and not a test of stale-owner overlap (Worker A is "
    "confirmed dead before Worker B starts). No distributed fencing, no PostgreSQL, no production "
    "deployment or credentials. A small, deterministic, predeclared trial count (3 per cell, 30 "
    "total): no statistical reliability-rate claim is made from this batch. 'Retry denied' is "
    "reported as exactly that - it is not equated with 'the provider-side action was reconciled' "
    "or 'the task will ever complete'; SafeAgent's own 0.1.24 docstring says reconciliation is "
    "the caller's responsibility, and this experiment does not implement or evaluate one."
)

REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "schema",
    "safeagent_version",
    "safeagent_dist_sha256",
    "safeagent_sqlite_store_sha256",
    "python_version",
    "crashpoint_version",
    "crashpoint_commit",
    "case",
    "release",
    "trial_id",
    "action_id",
    "pending_ttl_seconds",
    "reset_confirmed",
    "worker_a_pid",
    "worker_a_killed",
    "worker_a_exit_status",
    "barrier_observed",
    "barrier_point",
    "sweep_invoked",
    "sweep_return_value",
    "has_count_stale_pending",
    "stale_count_before_sweep",
    "claimed_at",
    "measured_age_at_decision",
    "ttl_boundary_confirmed",
    "retry_claim_admitted",
    "retry_performed_effect",
    "effect_count",
    "effect_ledger_classification",
    "ledger_dump_count_before_seal",
    "final_claim_row",
    "task_completion_label",
    "observation_complete",
    "invalid_reason",
    "expected_result",
    "observed_result",
    "passed",
    "source_references",
    "limitations",
)

_VALID_LEDGER_CLASSIFICATIONS = frozenset(
    {"EXACTLY_ONCE", "DUPLICATED", "DIVERGED", "VOID", "ZERO", "UNVERIFIED"}
)

_VALID_LABELS = frozenset(
    {
        "settled_once_retry_blocked",
        "blocked_pending_effect_already_recorded",
        "duplicated_effect_after_reclaim",
        "reclaimed_and_settled_once",
        "blocked_pending_task_unperformed",
        "UNEXPECTED_SHAPE",
        "UNVERIFIED",
    }
)


def derive_label(
    *,
    observation_complete: bool,
    effect_count: int | None,
    retry_claim_admitted: bool | None,
    final_status: str | None,
) -> str:
    """Pure function from the three separated observations to a human-readable label. Never
    reads the prediction, never guesses: an unrecognized combination is UNEXPECTED_SHAPE, not a
    best-effort label, so a real surprise cannot hide inside a plausible-sounding name."""
    if not observation_complete or effect_count is None or retry_claim_admitted is None:
        return "UNVERIFIED"
    if effect_count == 1 and retry_claim_admitted is False and final_status == "COMMITTED":
        return "settled_once_retry_blocked"
    if effect_count == 1 and retry_claim_admitted is False and final_status == "PENDING":
        return "blocked_pending_effect_already_recorded"
    if effect_count == 2 and retry_claim_admitted is True and final_status == "COMMITTED":
        return "duplicated_effect_after_reclaim"
    if effect_count == 1 and retry_claim_admitted is True and final_status == "COMMITTED":
        return "reclaimed_and_settled_once"
    if effect_count == 0 and retry_claim_admitted is False and final_status == "PENDING":
        return "blocked_pending_task_unperformed"
    return "UNEXPECTED_SHAPE"


@dataclass(frozen=True)
class SafeAgentTTLTrial:
    """One completed trial's observations, before it is stamped into a receipt."""

    case: str
    release: str
    trial_id: str
    action_id: str
    pending_ttl_seconds: float
    safeagent_version: str
    safeagent_dist_sha256: str
    worker_a_pid: int | None
    worker_a_killed: bool
    worker_a_exit_status: int | None
    barrier_observed: bool
    barrier_point: str | None
    sweep_invoked: bool
    sweep_return_value: int | None
    has_count_stale_pending: bool | None
    stale_count_before_sweep: int | None
    claimed_at: float | None
    measured_age_at_decision: float | None
    ttl_boundary_confirmed: bool
    retry_claim_admitted: bool | None
    retry_performed_effect: bool | None
    effect_count: int | None
    effect_ledger_classification: str
    ledger_dump_count_before_seal: int | None
    final_claim_row: dict[str, object] | None
    observation_complete: bool
    invalid_reason: str | None
    expected_result: dict[str, object]
    observed_result: dict[str, object]
    safeagent_sqlite_store_sha256: str
    reset_confirmed: bool


def build_receipt(trial: SafeAgentTTLTrial, *, crashpoint_commit: str) -> dict[str, object]:
    """Stamp one completed trial into a self-describing receipt. Pure given its input."""
    final_status = None
    if trial.final_claim_row is not None:
        final_status = trial.final_claim_row.get("status")
    label = derive_label(
        observation_complete=trial.observation_complete,
        effect_count=trial.effect_count,
        retry_claim_admitted=trial.retry_claim_admitted,
        final_status=final_status if isinstance(final_status, str) else None,
    )
    passed = (
        trial.observation_complete
        and trial.invalid_reason is None
        and trial.observed_result == trial.expected_result
    )
    return {
        "schema": SCHEMA,
        "safeagent_version": trial.safeagent_version,
        "safeagent_dist_sha256": trial.safeagent_dist_sha256,
        "safeagent_sqlite_store_sha256": trial.safeagent_sqlite_store_sha256,
        "python_version": platform.python_version(),
        "crashpoint_version": "0.0.0",
        "crashpoint_commit": crashpoint_commit,
        "case": trial.case,
        "release": trial.release,
        "trial_id": trial.trial_id,
        "action_id": trial.action_id,
        "pending_ttl_seconds": trial.pending_ttl_seconds,
        "reset_confirmed": trial.reset_confirmed,
        "worker_a_pid": trial.worker_a_pid,
        "worker_a_killed": trial.worker_a_killed,
        "worker_a_exit_status": trial.worker_a_exit_status,
        "barrier_observed": trial.barrier_observed,
        "barrier_point": trial.barrier_point,
        "sweep_invoked": trial.sweep_invoked,
        "sweep_return_value": trial.sweep_return_value,
        "has_count_stale_pending": trial.has_count_stale_pending,
        "stale_count_before_sweep": trial.stale_count_before_sweep,
        "claimed_at": trial.claimed_at,
        "measured_age_at_decision": trial.measured_age_at_decision,
        "ttl_boundary_confirmed": trial.ttl_boundary_confirmed,
        "retry_claim_admitted": trial.retry_claim_admitted,
        "retry_performed_effect": trial.retry_performed_effect,
        "effect_count": trial.effect_count,
        "effect_ledger_classification": trial.effect_ledger_classification,
        "ledger_dump_count_before_seal": trial.ledger_dump_count_before_seal,
        "final_claim_row": trial.final_claim_row,
        "task_completion_label": label,
        "observation_complete": trial.observation_complete,
        "invalid_reason": trial.invalid_reason,
        "expected_result": trial.expected_result,
        "observed_result": trial.observed_result,
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
    if rec["release"] not in RELEASES:
        problems.append(f"unknown release: {rec['release']!r}")

    classification = rec["effect_ledger_classification"]
    if classification not in _VALID_LEDGER_CLASSIFICATIONS:
        problems.append(f"unknown effect_ledger_classification: {classification!r}")

    label = rec["task_completion_label"]
    if label not in _VALID_LABELS:
        problems.append(f"unknown task_completion_label: {label!r}")

    effect_count = rec["effect_count"]
    if effect_count is None:
        if rec["observation_complete"] is not False:
            problems.append("effect_count is null: observation_complete must be False")
        if rec["passed"] is not False:
            problems.append("effect_count is null: passed must be False, the trial must not PASS")
        if classification not in {"VOID", "UNVERIFIED"}:
            problems.append(
                f"effect_count is null: effect_ledger_classification must be VOID or "
                f"UNVERIFIED, got {classification!r}"
            )
        if label != "UNVERIFIED":
            problems.append("effect_count is null: task_completion_label must be UNVERIFIED")
    else:
        # Type-checked before any numeric comparison: a malformed value (a string, a bool, a
        # float) must produce a structured problem here, never an unhandled TypeError/ValueError
        # from a downstream `>=` or `==` on a value that was never confirmed to be a number.
        invalid_count = (
            not isinstance(effect_count, int) or isinstance(effect_count, bool) or effect_count < 0
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
            if effect_count >= 1 and classification in {"ZERO"}:
                problems.append("effect_count is >=1 but effect_ledger_classification is ZERO")

    if rec["invalid_reason"] is not None and rec["passed"] is not False:
        problems.append("invalid_reason is set: passed must be False")

    if rec["observation_complete"] is False and rec["task_completion_label"] not in {
        "UNVERIFIED",
        "UNEXPECTED_SHAPE",
    }:
        problems.append(
            "observation_complete is False: task_completion_label must be UNVERIFIED "
            "(or UNEXPECTED_SHAPE if the raw fields themselves disagree)"
        )

    # A receipt's own passed/observed/expected/observation_complete/invalid_reason fields must be
    # internally consistent WITHOUT needing any external file - this is exactly the contradiction
    # a receipt could otherwise smuggle through (passed=True with observed != expected, or with
    # observation_complete=False) if only the deeper, evidence-file-reading checks caught it.
    observed = rec.get("observed_result")
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

    sweep_return_value = rec.get("sweep_return_value")
    if sweep_return_value is not None:
        if not isinstance(sweep_return_value, int) or isinstance(sweep_return_value, bool):
            problems.append(
                f"sweep_return_value must be an int or null, got {sweep_return_value!r}"
            )
        elif sweep_return_value not in (0, 1):
            # At most one row is ever eligible to be swept in this experiment's design (one
            # claim per trial): any other value cannot be a genuine sweep_stale_pending() result.
            problems.append(
                f"sweep_return_value={sweep_return_value!r} is impossible for this experiment "
                "(at most one row can ever be swept per trial)"
            )

    measured_age = rec.get("measured_age_at_decision")
    if measured_age is not None:
        if not isinstance(measured_age, (int, float)) or isinstance(measured_age, bool):
            problems.append(
                f"measured_age_at_decision must be numeric or null, got {measured_age!r}"
            )
        elif measured_age < 0:
            problems.append(
                f"measured_age_at_decision={measured_age!r} is negative: an elapsed time cannot "
                "be negative"
            )

    # Protocol-as-a-contract: for a PASSING trial, every one of these fields is required to take
    # the ONE value the case structurally implies - not merely "not an impossible value" in
    # isolation. A trial claiming to pass cannot simultaneously claim the protocol that would
    # produce a pass did not actually happen.
    if rec["passed"] is True:
        case = rec["case"]
        killed = rec["worker_a_killed"]
        exit_status = rec["worker_a_exit_status"]
        if case == "settled_control":
            if killed is not False:
                problems.append(
                    "settled_control never kills Worker A, but worker_a_killed is not False"
                )
            if exit_status != 0:
                problems.append(
                    f"settled_control's Worker A must exit 0 (clean completion), got "
                    f"worker_a_exit_status={exit_status!r}"
                )
        else:
            if killed is not True:
                problems.append(
                    f"case {case!r} requires Worker A to be killed, but worker_a_killed is not "
                    "True"
                )
            if exit_status != -9:
                problems.append(
                    f"case {case!r} requires Worker A's recorded exit status to be -9 (SIGKILL), "
                    f"got worker_a_exit_status={exit_status!r}"
                )

        if rec["reset_confirmed"] is not True:
            problems.append("a passing trial must have reset_confirmed=True")
        if rec["ttl_boundary_confirmed"] is not True:
            problems.append("a passing trial must have ttl_boundary_confirmed=True")
        if measured_age is None:
            problems.append(
                "a passing trial must have a non-null measured_age_at_decision - the TTL "
                "boundary cannot be confirmed without one"
            )

        expected_sweep = case_requires_sweep(case) if isinstance(case, str) else None
        if expected_sweep is not None and rec["sweep_invoked"] is not expected_sweep:
            problems.append(
                f"case {case!r} requires sweep_invoked={expected_sweep!r}, got "
                f"{rec['sweep_invoked']!r}"
            )

        expected_ttl = expected_ttl_for_case(case) if isinstance(case, str) else None
        ttl_field = rec["pending_ttl_seconds"]
        if expected_ttl is not None and ttl_field != expected_ttl:
            problems.append(
                f"case {case!r} requires pending_ttl_seconds={expected_ttl!r}, got "
                f"{ttl_field!r}"
            )

    if not isinstance(rec["action_id"], str) or not rec["action_id"]:
        problems.append("action_id must be a non-empty string")
    if not isinstance(rec["trial_id"], str) or not rec["trial_id"]:
        problems.append("trial_id must be a non-empty string")

    refs = rec["source_references"]
    if not isinstance(refs, list) or not refs:
        problems.append("source_references must be a non-empty list")

    return problems
