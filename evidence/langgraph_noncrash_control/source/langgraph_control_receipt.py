"""Native receipt schema for the LangGraph non-crash positive control.

WHAT THIS MEASURES. One ordinary, non-crashed LangGraph execution: does the recorded evidence chain
(admission_id -> thread_id -> execution result -> external effect reference -> independently
rereadable state hash -> receipt) actually hold together, checked from retained bytes rather than
trusted from any single process's self-report. This is a positive control and evidence-portability
test, not a crash/recovery experiment - see ``langgraph_control.py`` and
``results/12-langgraph-noncrash-control.md`` for what it does and does not claim.

NO SABLE SCHEMA. This module is a versioned native Crashpoint record. ``SABLE_MAPPING`` is a
descriptive note only: how a native ``oracle_classification`` would read under external PASS/FAIL/
UNKNOWN vocabulary, kept separate from and never substituted into the native evidence above it.

THE NULL-VS-MISSING CAVEAT. ``crashpoint.canonical.receipt`` drops null dictionary members when it
canonicalizes a record (a null and an absent key hash the same way, by design - see
``canonical.py``). That is correct for the content hash but wrong for schema validation: a required
field that is legitimately null (e.g. ``effect.observed_count`` when the store was never read) must
still be distinguished from that field being missing entirely (a malformed receipt). So
``validate_receipt`` below checks key PRESENCE with ``in`` against the parsed JSON dict - which does
preserve explicit nulls - and never infers presence from a canonicalized round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA = "crashpoint.langgraph_control.receipt.v1"
EXPERIMENT_FAMILY = "application_noncrash_effect_binding"

SCOPE = (
    "One real, non-crashed LangGraph execution with a harmless external ledger effect, verified by "
    "a freshly spawned observer process reading the on-disk effect store independently of the "
    "worker and the daemon. This is a positive control and evidence-portability test. It does not "
    "demonstrate a LangGraph fix, crash recovery, a global exactly-once guarantee, provider "
    "idempotency, admission/dispatch fencing, SABLE certification, or an independent rerun."
)

LIMITATIONS = (
    "Local host only, one benign local-ledger fixture effect, not a real provider or network "
    "call. No crash, restart, or retry is exercised by this control - see "
    "langchain-ai/langgraph#8764 and results/10-langgraph-admission-ledger.md for the separate "
    "crash/recovery experiment this one deliberately does not repeat. n=1 by design: one "
    "recorded primary control, not a statistical rate, and no Wilson interval is reported "
    "because none is claimed. Does not certify a general exactly-once guarantee, "
    "admission/dispatch fencing, or SABLE admission. A process ID is diagnostic metadata, not "
    "cryptographic proof the observer was truly isolated from the worker; both run as the same "
    "OS user with no additional sandboxing. Offline verification establishes internal "
    "consistency of the retained evidence and what it supports, not an independent "
    "re-execution or authenticated real-world effect."
)

_VALID_CLASSIFICATIONS = frozenset(
    {"EXACTLY_ONCE", "DUPLICATED", "DIVERGED", "LOST", "VOID", "UNVERIFIED"}
)
_VALID_OBSERVER_STATUS = frozenset({"OBSERVED", "STORE_MISSING", "CORRUPT"})

SABLE_MAPPING: dict[str, object] = {
    "note": (
        "Descriptive only. This mapping does not alter, gate, or get folded into the native "
        "evidence above it; it exists so socksninja's mapping work has something concrete to read "
        "this record against. No SABLE schema or verifier is invoked by this repository."
    ),
    "EXACTLY_ONCE": "PASS",
    "LOST": "FAIL",
    "DUPLICATED": "FAIL",
    "DIVERGED": "FAIL",
    "VOID": "UNKNOWN",
    "UNVERIFIED": "UNKNOWN",
    "admission_independent_of_effect_count": (
        "admission.status_after_run is reported on its own axis and is never coerced into, or read "
        "as a substitute for, the effect observation's UNKNOWN status or null count."
    ),
}


def classify_control(
    *,
    worker_ok: bool,
    observer_status: str,
    effect_count: int | None,
    effect_digests: list[str],
) -> str:
    """Fail-closed classification, independent of ``ledger.oracle.classify``: that function trusts
    ``LedgerState.verify``'s path-based chain check, which treats a MISSING ledger file as a valid
    empty chain - exactly the conflation this control must not make (see ``ledger_readback.py``).
    This function never touches a path; it takes only already-determined facts."""
    if observer_status not in _VALID_OBSERVER_STATUS:
        raise ValueError(f"unknown observer_status: {observer_status!r}")
    if not worker_ok:
        return "UNVERIFIED"
    if observer_status != "OBSERVED":
        return "VOID"
    if effect_count is None:
        return "VOID"
    if effect_count == 0:
        return "LOST"
    if effect_count == 1:
        return "EXACTLY_ONCE"
    return "DUPLICATED" if len(set(effect_digests)) <= 1 else "DIVERGED"


def sable_status(classification: str) -> str:
    value = SABLE_MAPPING.get(classification)
    if not isinstance(value, str):
        raise ValueError(f"unknown oracle_classification for SABLE mapping: {classification!r}")
    return value


@dataclass(frozen=True)
class ControlObservation:
    """Everything the harness gathered about one primary-control run, already shaped into the
    receipt's nested sections. Built by ``langgraph_control.py`` (which does import LangGraph, "
    locally, to run the worker); this dataclass and ``build_receipt`` do not."""

    case: str
    identity: dict[str, object]
    admission: dict[str, object]
    runtime: dict[str, object]
    checkpoint: dict[str, object]
    observer: dict[str, object]
    effect: dict[str, object]
    contract: dict[str, object]
    manifest: dict[str, object]
    source: dict[str, object]
    expected: dict[str, object]


def build_receipt(obs: ControlObservation) -> dict[str, object]:
    """Assemble the native record body (everything except the outer ``receipt`` hash, which the
    caller adds with ``crashpoint.canonical.receipt`` once the body is final)."""
    worker_ok = bool(obs.runtime.get("worker_ok"))
    observer_status = str(obs.observer.get("status"))
    effect_count_raw = obs.effect.get("observed_count")
    effect_count = effect_count_raw if isinstance(effect_count_raw, int) else None
    digests_raw = obs.effect.get("effect_digests")
    effect_digests = (
        [d for d in digests_raw if isinstance(d, str)] if isinstance(digests_raw, list) else []
    )

    classification = classify_control(
        worker_ok=worker_ok,
        observer_status=observer_status,
        effect_count=effect_count,
        effect_digests=effect_digests,
    )
    measured = {
        "runtime_completion": "success" if worker_ok else "error",
        "observation_status": observer_status,
        "effect_count": effect_count,
        "admission_status_after": obs.admission.get("status_after_run"),
        "terminal_state": obs.runtime.get("worker_returned_state"),
    }
    agreement = (
        classification == "EXACTLY_ONCE"
        and obs.admission.get("status_after_run") == "completed"
        and measured["terminal_state"] == obs.expected.get("expected_terminal_state")
    )
    passed = agreement and bool(obs.observer.get("archive_matches_original"))

    return {
        "schema": SCHEMA,
        "case": obs.case,
        "scope": SCOPE,
        "experiment_family": EXPERIMENT_FAMILY,
        "framework_fix": False,
        "identity": obs.identity,
        "admission": obs.admission,
        "runtime": obs.runtime,
        "checkpoint": obs.checkpoint,
        "observer": obs.observer,
        "effect": obs.effect,
        "result": {
            "expected": obs.expected,
            "measured": measured,
            "oracle_classification": classification,
            "agreement": agreement,
            "passed": passed,
        },
        "contract": obs.contract,
        "manifest": obs.manifest,
        "source": obs.source,
        "sable_facing_mapping": SABLE_MAPPING,
        "limitations": LIMITATIONS,
    }


_TOP_REQUIRED: tuple[str, ...] = (
    "schema", "case", "scope", "experiment_family", "framework_fix", "identity", "admission",
    "runtime", "checkpoint", "observer", "effect", "result", "contract", "manifest", "source",
    "sable_facing_mapping", "limitations",
)
_IDENTITY_REQUIRED: tuple[str, ...] = ("admission_id", "thread_id", "intent_id")
_ADMISSION_REQUIRED: tuple[str, ...] = (
    "admission_db_path", "admission_db_sha256", "accepted_before_invoke", "accepted_at_utc",
    "original_input", "events_after_run", "status_after_run",
)
_RUNTIME_REQUIRED: tuple[str, ...] = (
    "langgraph_version", "langgraph_checkpoint_version", "langgraph_checkpoint_sqlite_version",
    "python_version", "platform", "durability_setting", "crash_injected", "invoke_count",
    "worker_exit_status", "worker_ok", "worker_error_type", "worker_error_message",
    "worker_returned_state",
)
_CHECKPOINT_REQUIRED: tuple[str, ...] = (
    "checkpoint_db_path", "checkpoint_db_sha256", "thread_scoped_checkpoint_count",
    "checkpoint_query",
)
_OBSERVER_REQUIRED: tuple[str, ...] = (
    "status", "pid", "pid_note", "exit_status", "stdout_path", "stderr_path", "report_path",
    "report_sha256", "archived_ledger_readback_path", "archived_ledger_readback_sha256",
    "original_readback_sha256", "archive_matches_original", "chain_valid", "first_broken_index",
    "record_count", "representation_note",
)
_EFFECT_REQUIRED: tuple[str, ...] = ("observed_count", "effect_digests", "effect_attempt_ids")
_RESULT_REQUIRED: tuple[str, ...] = (
    "expected", "measured", "oracle_classification", "agreement", "passed",
)
_CONTRACT_REQUIRED: tuple[str, ...] = ("path", "sha256")
_MANIFEST_REQUIRED: tuple[str, ...] = ("path", "sha256")
_SOURCE_REQUIRED: tuple[str, ...] = (
    "base_commit", "worktree_branch", "dirty", "new_source_files", "uv_lock_sha256",
)


def _missing(d: object, keys: tuple[str, ...], prefix: str) -> list[str]:
    if not isinstance(d, dict):
        return [f"{prefix} is not an object"]
    return [f"missing required field: {prefix}.{key}" for key in keys if key not in d]


def validate_receipt(rec: dict[str, Any]) -> list[str]:
    """Structural + cross-field validation. Fail-closed: returns violations, empty means valid.
    Presence is checked with ``in`` throughout, specifically so a nullable field that is present but
    ``None`` is never confused with that field being absent (see the module docstring)."""
    problems = [f"missing required field: {key}" for key in _TOP_REQUIRED if key not in rec]
    if problems:
        return problems

    problems += _missing(rec["identity"], _IDENTITY_REQUIRED, "identity")
    problems += _missing(rec["admission"], _ADMISSION_REQUIRED, "admission")
    problems += _missing(rec["runtime"], _RUNTIME_REQUIRED, "runtime")
    problems += _missing(rec["checkpoint"], _CHECKPOINT_REQUIRED, "checkpoint")
    problems += _missing(rec["observer"], _OBSERVER_REQUIRED, "observer")
    problems += _missing(rec["effect"], _EFFECT_REQUIRED, "effect")
    problems += _missing(rec["result"], _RESULT_REQUIRED, "result")
    problems += _missing(rec["contract"], _CONTRACT_REQUIRED, "contract")
    problems += _missing(rec["manifest"], _MANIFEST_REQUIRED, "manifest")
    problems += _missing(rec["source"], _SOURCE_REQUIRED, "source")
    if problems:
        return problems

    if rec["schema"] != SCHEMA:
        problems.append(f"unexpected schema: {rec['schema']!r}")

    identity = rec["identity"]
    admission_id = identity["admission_id"]
    thread_id = identity["thread_id"]
    intent_id = identity["intent_id"]
    id_fields = (("admission_id", admission_id), ("thread_id", thread_id), ("intent_id", intent_id))
    for name, value in id_fields:
        if not isinstance(value, str) or not value:
            problems.append(f"identity.{name} must be a non-empty string")
    if admission_id != thread_id or admission_id != intent_id:
        problems.append("identity.admission_id, thread_id, intent_id must agree in this control")

    observer = rec["observer"]
    status = observer["status"]
    if status not in _VALID_OBSERVER_STATUS:
        problems.append(
            f"observer.status must be one of {sorted(_VALID_OBSERVER_STATUS)}, got {status!r}"
        )

    chain_valid = observer["chain_valid"]
    if not isinstance(chain_valid, bool):
        problems.append("observer.chain_valid must be a bool")

    first_broken = observer["first_broken_index"]
    if not isinstance(first_broken, int) or isinstance(first_broken, bool):
        problems.append("observer.first_broken_index must be an int (not bool)")

    effect = rec["effect"]
    count = effect["observed_count"]
    if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0):
        problems.append(f"effect.observed_count must be a non-negative int or null, got {count!r}")
    if status == "OBSERVED" and count is None:
        problems.append("observer.status is OBSERVED but effect.observed_count is null")
    if status != "OBSERVED" and count is not None:
        problems.append(f"observer.status is {status!r} but effect.observed_count is non-null")

    digests = effect["effect_digests"]
    if not isinstance(digests, list) or not all(isinstance(d, str) for d in digests):
        problems.append("effect.effect_digests must be a list of strings")
    elif count is not None and len(digests) != count:
        problems.append(
            f"effect.effect_digests has {len(digests)} entries but observed_count is {count}"
        )

    result = rec["result"]
    classification = result["oracle_classification"]
    if classification not in _VALID_CLASSIFICATIONS:
        problems.append(f"unknown oracle_classification: {classification!r}")
    else:
        is_list = isinstance(digests, list)
        valid_digests = [d for d in digests if isinstance(d, str)] if is_list else []
        recomputed = classify_control(
            worker_ok=bool(rec["runtime"]["worker_ok"]),
            observer_status=str(status),
            effect_count=count if isinstance(count, int) else None,
            effect_digests=valid_digests,
        )
        if recomputed != classification:
            problems.append(
                f"oracle_classification {classification!r} does not match recomputed {recomputed!r}"
            )

    if not isinstance(result["passed"], bool):
        problems.append("result.passed must be a bool")
    if not isinstance(result["agreement"], bool):
        problems.append("result.agreement must be a bool")
    if result["passed"] and not result["agreement"]:
        problems.append("result.passed is True but result.agreement is False")

    admission = rec["admission"]
    if not isinstance(admission["accepted_before_invoke"], bool):
        problems.append("admission.accepted_before_invoke must be a bool")
    events = admission["events_after_run"]
    if not isinstance(events, list) or not all(isinstance(e, str) for e in events):
        problems.append("admission.events_after_run must be a list of strings")

    checkpoint = rec["checkpoint"]
    cp_count = checkpoint["thread_scoped_checkpoint_count"]
    if not isinstance(cp_count, int) or isinstance(cp_count, bool) or cp_count < 0:
        problems.append("checkpoint.thread_scoped_checkpoint_count must be a non-negative int")

    runtime = rec["runtime"]
    if not isinstance(runtime["worker_ok"], bool):
        problems.append("runtime.worker_ok must be a bool")
    if not isinstance(runtime["crash_injected"], bool):
        problems.append("runtime.crash_injected must be a bool")
    if runtime["crash_injected"] is not False:
        problems.append("runtime.crash_injected must be False: this is a non-crash control")

    source = rec["source"]
    new_files = source["new_source_files"]
    if not isinstance(new_files, list) or not all(
        isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("sha256"), str)
        for f in new_files
    ):
        problems.append("source.new_source_files must be a list of {path, sha256} objects")

    return problems
