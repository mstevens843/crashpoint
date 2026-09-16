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

v2. A publication review (recorded in
``handoff/langgraph-noncrash-control/CODEX-PUBLICATION-PROBE-RESULTS.json`` and answered in
``CODEX-CLOSEOUT-RESPONSE.md``) found that v1 accepted several internally contradictory bundles and
could not represent an honest observer-failure receipt. v2 adds: a shared ``compute_agreement`` so
the harness and the offline verifier can never independently drift on what "agreement" means;
unconditional coherence checks that do not depend on ``passed``; a nullable, explained checkpoint
count (``checkpoint_error``); a fixed, path-independent required set of executing-source modules
(``REQUIRED_EXECUTING_SOURCE_MODULES``) that a verifier must not silently skip; an
``OBSERVER_ERROR`` status that classifies as ``VOID`` instead of raising; and a labeled
parent-process fallback archive, distinct from the observer's own archive, for when the observer
fails after the ledger already holds real bytes. v1 bundles are rejected by schema mismatch, not
silently reinterpreted - see the early schema check in ``validate_receipt``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA = "crashpoint.langgraph_control.receipt.v2"
EXPERIMENT_FAMILY = "application_noncrash_effect_binding"

# The fixed set of modules whose code executes to produce this control's evidence. Repo-relative
# paths for hashing at run time; basenames are what a bundle retains under source/ and what a
# verifier requires present - see REQUIRED_EXECUTING_SOURCE_BASENAMES below. Defined here (the
# LangGraph-free schema module), not in langgraph_control.py, so both the harness and the offline
# verifier can import the same fixed list without an import cycle.
REQUIRED_EXECUTING_SOURCE_MODULES: tuple[str, ...] = (
    "src/crashpoint/harness/ledger_readback.py",
    "src/crashpoint/harness/langgraph_control.py",
    "src/crashpoint/harness/langgraph_control_receipt.py",
    "src/crashpoint/harness/langgraph_control_observer.py",
    "src/crashpoint/harness/langgraph_control_verify.py",
)
REQUIRED_EXECUTING_SOURCE_BASENAMES = frozenset(
    p.rsplit("/", 1)[-1] for p in REQUIRED_EXECUTING_SOURCE_MODULES
)

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
    "OS user with no additional sandboxing. A parent-process fallback archive of the ledger "
    "bytes, when present, is raw-byte preservation only - it does not prove the fresh observer "
    "succeeded and never substitutes for observer.status. Offline verification establishes "
    "internal consistency of the retained evidence and what it supports, not an independent "
    "re-execution or authenticated real-world effect."
)

_VALID_CLASSIFICATIONS = frozenset(
    {"EXACTLY_ONCE", "DUPLICATED", "DIVERGED", "LOST", "VOID", "UNVERIFIED"}
)
# Public: langgraph_control.py imports this too, so a malformed observer report can be rejected
# there (before it is ever trusted) against the exact same set the schema validates against here.
VALID_OBSERVER_STATUSES = frozenset({"OBSERVED", "STORE_MISSING", "CORRUPT", "OBSERVER_ERROR"})

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
    This function never touches a path; it takes only already-determined facts. ``OBSERVER_ERROR``
    (the observer crashed or never ran - timeout, exception, no report) classifies the same as
    ``STORE_MISSING``/``CORRUPT``: VOID, an honest "cannot certify", never raised as an exception -
    the whole point is that an observer failure must still produce a retained receipt."""
    if observer_status not in VALID_OBSERVER_STATUSES:
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


def compute_agreement(
    *,
    classification: str,
    admission_status_after: object,
    admission_accepted_before_invoke: object,
    worker_exit_status: object,
    observer_exit_status: object,
    terminal_state: object,
    expected_terminal_state: object,
    invoke_count: object,
    expected_invoke_count: object,
    checkpoint_count: object,
) -> bool:
    """The one place "the positive control's declared outcome agrees with its declared evidence" is
    decided. Both ``build_receipt`` (the harness, from freshly gathered facts) and
    ``langgraph_control_verify.verify_bundle`` (from independently recomputed facts) call this same
    function, so the two sides cannot independently drift on what agreement means - a bundle that
    the harness marked ``passed`` must satisfy exactly the same test the verifier recomputes.

    ``observer_exit_status == 0`` is required even when the observer's own report is a perfectly
    well-formed, byte-verified ``OBSERVED`` finding: a fresh-process observation whose own process
    did not exit cleanly is not a clean positive control, whatever its report says. This does not
    discard or null the report's actual readback data - classification and the effect count/digests
    are still computed from the retained bytes regardless of exit status, exactly as before; only
    ``agreement``/``passed`` are gated on a clean exit, so a nonzero-exit-but-genuinely-observed run
    still honestly reports what it found without also claiming a clean PASS."""
    checkpoint_ok = (
        isinstance(checkpoint_count, int)
        and not isinstance(checkpoint_count, bool)
        and checkpoint_count > 0
    )
    return (
        classification == "EXACTLY_ONCE"
        and admission_status_after == "completed"
        and admission_accepted_before_invoke is True
        and worker_exit_status == 0
        and observer_exit_status == 0
        and terminal_state == expected_terminal_state
        and invoke_count == expected_invoke_count
        and checkpoint_ok
    )


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
    agreement = compute_agreement(
        classification=classification,
        admission_status_after=obs.admission.get("status_after_run"),
        admission_accepted_before_invoke=obs.admission.get("accepted_before_invoke"),
        worker_exit_status=obs.runtime.get("worker_exit_status"),
        observer_exit_status=obs.observer.get("exit_status"),
        terminal_state=measured["terminal_state"],
        expected_terminal_state=obs.expected.get("expected_terminal_state"),
        invoke_count=obs.runtime.get("invoke_count"),
        expected_invoke_count=obs.expected.get("invoke_count"),
        checkpoint_count=obs.checkpoint.get("thread_scoped_checkpoint_count"),
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
    "checkpoint_query", "checkpoint_error",
)
_OBSERVER_REQUIRED: tuple[str, ...] = (
    "status", "pid", "pid_note", "exit_status", "stdout_path", "stderr_path", "report_path",
    "report_sha256", "archived_ledger_readback_path", "archived_ledger_readback_sha256",
    "original_readback_sha256", "archive_matches_original", "chain_valid", "first_broken_index",
    "record_count", "representation_note", "parent_fallback_archive_path",
    "parent_fallback_archive_sha256", "error",
)
_EFFECT_REQUIRED: tuple[str, ...] = ("observed_count", "effect_digests", "effect_attempt_ids")
_RESULT_REQUIRED: tuple[str, ...] = (
    "expected", "measured", "oracle_classification", "agreement", "passed",
)
_CONTRACT_REQUIRED: tuple[str, ...] = ("path", "sha256")
_MANIFEST_REQUIRED: tuple[str, ...] = ("path", "sha256")
_SOURCE_REQUIRED: tuple[str, ...] = (
    "base_commit", "worktree_branch", "dirty", "executing_source_files", "uv_lock_sha256",
)


def _missing(d: object, keys: tuple[str, ...], prefix: str) -> list[str]:
    if not isinstance(d, dict):
        return [f"{prefix} is not an object"]
    return [f"missing required field: {prefix}.{key}" for key in keys if key not in d]


def validate_receipt(rec: dict[str, Any]) -> list[str]:
    """Structural + cross-field validation. Fail-closed: returns violations, empty means valid.
    Presence is checked with ``in`` throughout, specifically so a nullable field that is present but
    ``None`` is never confused with that field being absent (see the module docstring).

    Every check below is either UNCONDITIONAL (a receipt with this problem is malformed/incoherent
    regardless of what ``passed`` claims - e.g. a worker that exited nonzero cannot coherently have
    self-reported ``ok``) or folded into ``compute_agreement`` (a positive-control invariant: a
    receipt CAN validly represent a failed run missing one of these, as long as it then honestly
    declares ``agreement``/``passed`` False)."""
    problems = [f"missing required field: {key}" for key in _TOP_REQUIRED if key not in rec]
    if problems:
        return problems

    if rec["schema"] != SCHEMA:
        return [f"unexpected schema: {rec['schema']!r}, expected {SCHEMA!r}"]

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
    if status not in VALID_OBSERVER_STATUSES:
        problems.append(
            f"observer.status must be one of {sorted(VALID_OBSERVER_STATUSES)}, got {status!r}"
        )

    chain_valid = observer["chain_valid"]
    if not isinstance(chain_valid, bool):
        problems.append("observer.chain_valid must be a bool")

    first_broken = observer["first_broken_index"]
    if not isinstance(first_broken, int) or isinstance(first_broken, bool):
        problems.append("observer.first_broken_index must be an int (not bool)")

    observer_exit_status = observer["exit_status"]
    if observer_exit_status is not None and (
        not isinstance(observer_exit_status, int) or isinstance(observer_exit_status, bool)
    ):
        problems.append("observer.exit_status must be an int or null (not bool)")

    observer_error = observer["error"]
    if observer_error is not None and (not isinstance(observer_error, str) or not observer_error):
        problems.append("observer.error must be null or a non-empty string")
    if status != "OBSERVED" and observer_error is None:
        problems.append(
            f"observer.status is {status!r} (not OBSERVED) but observer.error does not explain why"
        )
    if status == "OBSERVED" and observer_error is not None:
        problems.append("observer.status is OBSERVED but observer.error is also set")

    report_path = observer["report_path"]
    report_sha = observer["report_sha256"]
    if (report_path is None) != (report_sha is None):
        problems.append("observer.report_path and report_sha256 must both be null or both be set")
    elif report_path is not None and (
        not isinstance(report_path, str) or not isinstance(report_sha, str)
    ):
        problems.append("observer.report_path/report_sha256 must be strings when set")
    if report_path is None and status != "OBSERVER_ERROR":
        # A report file is null only when the observer process never produced one at all - a
        # true timeout/crash before writing anything. Legitimate only for OBSERVER_ERROR: every
        # other status (including STORE_MISSING and CORRUPT) is a normal return from observe()
        # that always writes a report before exiting.
        problems.append(
            f"observer.report_path is null but observer.status is {status!r}, not OBSERVER_ERROR"
        )

    fallback_path = observer["parent_fallback_archive_path"]
    fallback_sha = observer["parent_fallback_archive_sha256"]
    if (fallback_path is None) != (fallback_sha is None):
        problems.append(
            "observer.parent_fallback_archive_path and _sha256 must both be null or both be set"
        )
    elif fallback_path is not None and (
        not isinstance(fallback_path, str) or not isinstance(fallback_sha, str)
    ):
        problems.append("observer.parent_fallback_archive_path/_sha256 must be strings when set")

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
    expected = result["expected"]
    measured = result["measured"]
    if not isinstance(expected, dict):
        problems.append("result.expected must be an object")
    if not isinstance(measured, dict):
        problems.append("result.measured must be an object")

    classification = result["oracle_classification"]
    if classification not in _VALID_CLASSIFICATIONS:
        problems.append(f"unknown oracle_classification: {classification!r}")
    else:
        is_list = isinstance(digests, list)
        valid_digests = [d for d in digests if isinstance(d, str)] if is_list else []
        recomputed_classification = classify_control(
            worker_ok=bool(rec["runtime"]["worker_ok"]),
            observer_status=str(status),
            effect_count=count if isinstance(count, int) else None,
            effect_digests=valid_digests,
        )
        if recomputed_classification != classification:
            problems.append(
                f"oracle_classification {classification!r} does not match recomputed "
                f"{recomputed_classification!r}"
            )

    if not isinstance(result["passed"], bool):
        problems.append("result.passed must be a bool")
    if not isinstance(result["agreement"], bool):
        problems.append("result.agreement must be a bool")

    admission = rec["admission"]
    if not isinstance(admission["accepted_before_invoke"], bool):
        problems.append("admission.accepted_before_invoke must be a bool")
    events = admission["events_after_run"]
    if not isinstance(events, list) or not all(isinstance(e, str) for e in events):
        problems.append("admission.events_after_run must be a list of strings")

    checkpoint = rec["checkpoint"]
    cp_count = checkpoint["thread_scoped_checkpoint_count"]
    cp_error = checkpoint["checkpoint_error"]
    if cp_count is not None and (
        not isinstance(cp_count, int) or isinstance(cp_count, bool) or cp_count < 0
    ):
        problems.append(
            f"checkpoint.thread_scoped_checkpoint_count must be a non-negative int or null, "
            f"got {cp_count!r}"
        )
    if cp_error is not None and (not isinstance(cp_error, str) or not cp_error):
        problems.append("checkpoint.checkpoint_error must be null or a non-empty string")
    if cp_count is None and cp_error is None:
        problems.append(
            "checkpoint.thread_scoped_checkpoint_count is null but checkpoint_error does not "
            "explain why"
        )
    if cp_count is not None and cp_error is not None:
        problems.append(
            "checkpoint.thread_scoped_checkpoint_count is non-null but checkpoint_error is also "
            "set"
        )

    runtime = rec["runtime"]
    worker_ok = runtime["worker_ok"]
    worker_exit_status = runtime["worker_exit_status"]
    if not isinstance(worker_ok, bool):
        problems.append("runtime.worker_ok must be a bool")
    elif worker_ok is True and worker_exit_status != 0:
        problems.append(
            f"runtime.worker_ok is True but runtime.worker_exit_status is {worker_exit_status!r}, "
            "not 0: a worker that exited nonzero cannot coherently have self-reported ok"
        )
    if not isinstance(runtime["crash_injected"], bool):
        problems.append("runtime.crash_injected must be a bool")
    if runtime["crash_injected"] is not False:
        problems.append("runtime.crash_injected must be False: this is a non-crash control")
    if isinstance(expected, dict) and runtime["invoke_count"] != expected.get("invoke_count"):
        problems.append(
            f"runtime.invoke_count {runtime['invoke_count']!r} does not match "
            f"result.expected.invoke_count {expected.get('invoke_count')!r}"
        )

    source = rec["source"]
    exec_files = source["executing_source_files"]
    if not isinstance(exec_files, list) or not all(
        isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("sha256"), str)
        for f in exec_files
    ):
        problems.append("source.executing_source_files must be a list of {path, sha256} objects")
    else:
        basenames = {f["path"].rsplit("/", 1)[-1] for f in exec_files}
        missing_modules = REQUIRED_EXECUTING_SOURCE_BASENAMES - basenames
        extra_modules = basenames - REQUIRED_EXECUTING_SOURCE_BASENAMES
        if missing_modules:
            problems.append(
                f"source.executing_source_files is missing required module(s): "
                f"{sorted(missing_modules)}"
            )
        if extra_modules:
            problems.append(
                f"source.executing_source_files has unexpected module(s): {sorted(extra_modules)}"
            )
        if len(basenames) != len(exec_files):
            problems.append("source.executing_source_files has duplicate module entries")

    if not problems:
        recomputed_agreement = compute_agreement(
            classification=classification,
            admission_status_after=admission.get("status_after_run"),
            admission_accepted_before_invoke=admission.get("accepted_before_invoke"),
            worker_exit_status=worker_exit_status,
            observer_exit_status=observer_exit_status,
            terminal_state=measured.get("terminal_state") if isinstance(measured, dict) else None,
            expected_terminal_state=(
                expected.get("expected_terminal_state") if isinstance(expected, dict) else None
            ),
            invoke_count=runtime.get("invoke_count"),
            expected_invoke_count=(
                expected.get("invoke_count") if isinstance(expected, dict) else None
            ),
            checkpoint_count=cp_count,
        )
        if recomputed_agreement != result["agreement"]:
            problems.append(
                f"result.agreement {result['agreement']!r} does not match recomputed "
                f"{recomputed_agreement!r}"
            )
        if result["passed"] and not recomputed_agreement:
            problems.append("result.passed is True but recomputed agreement is False")
        if result["passed"] and not bool(observer.get("archive_matches_original")):
            problems.append(
                "result.passed is True but observer.archive_matches_original is not True"
            )

    return problems
