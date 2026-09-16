"""One ordinary, non-crashed LangGraph execution, bound end-to-end to a caller-owned admission
record and verified by a freshly spawned observer process reading the on-disk effect ledger.

WHAT THIS IS. A positive control, not a crash/recovery experiment. ``langgraph_admission.py``
measures what a caller-owned acceptance record can recover after LangGraph is killed before its
first checkpoint; that experiment's results are unchanged by this module and this module never
calls its ``recovery()``. This module asks a different, narrower question: when nothing crashes,
does the evidence chain admission_id -> thread_id -> execution result -> external effect reference
-> independently rereadable state hash -> receipt actually hold together end to end, checked from
retained bytes rather than trusted from any single process's self-report?

WHY THE WORKER READS ITS INPUT FROM THE ADMISSION LEDGER, NOT A CLI ARGUMENT. The admission record
is written before the worker is dispatched (see ``run_primary_control``, step order below); the
worker's ``--admission-db``/``--thread-id`` arguments only let it look that record up. This makes
"connected to prior caller-owned admission" a structural fact rather than a claim: if the row were
missing or bound to a different thread, the worker could not obtain the input it invokes with.

WHY THE OBSERVER IS A SEPARATE MODULE, NOT A FUNCTION CALLED HERE. See
``langgraph_control_observer.py``. This module imports LangGraph (locally, inside ``_build_app``,
never at module scope, matching ``langgraph_admission.py``'s own pattern) and is therefore never
imported by the offline verifier.

Identity: ``admission_id == thread_id == intent_id``, one fresh ``uuid4``-derived value per run,
generated here and never reused across runs or derived from mutable arguments (see
``_fresh_identity``). The existing admission experiment's fixed ``"accepted-run"`` thread ID is
deliberately not reused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast

from ..canonical import receipt as canonical_receipt
from ..ledger.daemon import execute
from .langgraph_admission import (
    accept_run,
    append_admission_event,
    initialize_admission_ledger,
    read_admission,
)
from .langgraph_control_receipt import (
    REQUIRED_EXECUTING_SOURCE_MODULES,
    VALID_OBSERVER_STATUSES,
    ControlObservation,
    build_receipt,
    validate_receipt,
)
from .ledger_process import LedgerDaemon

_ROOT = Path(__file__).resolve().parents[3]
_PAYLOAD: dict[str, object] = {
    "kind": "langgraph_noncrash_control",
    "note": "harmless local-ledger fixture effect; not a real external system",
}
_ORIGINAL_INPUT: dict[str, object] = {"done": False}


class _S(TypedDict):
    done: bool


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fresh_identity() -> str:
    """One fresh identity, used unchanged as admission_id, thread_id, and the ledger's intent_id.
    uuid4 is not derived from any caller-supplied or mutable argument, and is never reused: the
    collision probability across runs is astronomically small, and ``run_primary_control``
    additionally refuses to reuse an existing output directory (see its first line)."""
    return f"lgnc-{uuid.uuid4().hex}"


def _build_app(
    checkpoint: str, effect_ledger_invoke_path: str, intent_id: str
) -> tuple[Any, sqlite3.Connection]:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph

    def node(_state: _S) -> _S:
        # key=None deliberately: this positive control has no retry loop, crash injection, or
        # idempotency guard to hide a duplicate crossing. See the module docstring and
        # results/12-langgraph-noncrash-control.md.
        execute(
            effect_ledger_invoke_path, intent_id, None, _PAYLOAD,
            attempt_id=f"{intent_id}:attempt-1",
        )
        return {"done": True}

    graph: Any = StateGraph(_S)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)

    conn = sqlite3.connect(checkpoint, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return graph.compile(checkpointer=saver), conn


def worker(
    checkpoint: str, effect_ledger_invoke_path: str, admission_db: str,
    thread_id: str, intent_id: str,
) -> int:
    """The real separate worker process. Reads its invoke input from the admission ledger row
    written before dispatch - never from its own CLI arguments - constructs the START -> node ->
    END graph, and invokes it exactly once with ``durability=\"sync\"``. Always exits 0: the exit
    code means \"this process ran and reported\", not \"the measured outcome was a PASS\" - the
    same convention ``langgraph_admission.recovery`` uses, and for the same reason (a nonzero exit
    here would be indistinguishable from a real subprocess crash to the harness's caller)."""
    admission = read_admission(Path(admission_db), thread_id)
    if not admission.accepted or admission.original_input is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "returned": None,
                    "error_type": "AdmissionNotFound",
                    "message": (
                        f"no accepted admission row for thread_id={thread_id!r} in {admission_db}"
                    ),
                },
                sort_keys=True,
            )
        )
        return 0

    app, conn = _build_app(checkpoint, effect_ledger_invoke_path, intent_id)
    config = {"configurable": {"thread_id": thread_id}}
    try:
        returned = app.invoke(cast(_S, admission.original_input), config, durability="sync")
        result: dict[str, object] = {
            "ok": True, "returned": returned, "error_type": None, "message": None,
        }
    except Exception as exc:
        result = {
            "ok": False, "returned": None,
            "error_type": type(exc).__name__, "message": str(exc),
        }
    finally:
        try:
            # Fold the WAL back into the single checkpoint file so a later fresh reader sees every
            # durable row without needing the -wal/-shm sidecars to still be present.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    print(json.dumps(result, sort_keys=True))
    return 0


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=True
    )
    return proc.stdout.strip()


def _hash_executing_source_files(root: Path) -> list[dict[str, str]]:
    """Hash the FIXED set of modules whose code executes to produce this evidence
    (``REQUIRED_EXECUTING_SOURCE_MODULES``), unconditionally - never derived from ``git status``.
    A fully committed, clean checkout must record exactly these same hashes as a dirty one; git
    dirty-state is tracked separately below (``dirty``, informational provenance only) and never
    gates whether these are recorded. Raises if a required module is missing from disk: that is a
    genuine setup error, not something to silently skip (see the publication-review finding on
    ``source/`` requiring all five module copies)."""
    files: list[dict[str, str]] = []
    for rel in REQUIRED_EXECUTING_SOURCE_MODULES:
        path = root / rel
        if not path.is_file():
            raise RuntimeError(f"required executing-source module missing from disk: {rel}")
        files.append({"path": rel, "sha256": _sha256_file(path)})
    return sorted(files, key=lambda f: f["path"])


def _build_manifest(root: Path, contract_path: Path, contract_sha256: str) -> dict[str, object]:
    base_commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    dirty = bool(_git(root, "status", "--porcelain=v1"))
    executing_files = _hash_executing_source_files(root)
    return {
        "schema": "crashpoint.langgraph_control.manifest.v2",
        "created_at_utc": _utc_now_iso(),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "base_commit": base_commit,
        "worktree_branch": branch,
        "dirty": dirty,
        "executing_source_files": executing_files,
        "uv_lock_sha256": _sha256_file(root / "uv.lock"),
        "uv_lock_note": (
            "hash recorded from the repository's tracked uv.lock at run time; this bundle does not "
            "retain a full copy, so cross-checking it requires the original repository checkout"
        ),
        "contract_path": contract_path.name,
        "contract_sha256": contract_sha256,
    }


def _contract_body() -> dict[str, object]:
    return {
        "schema": "crashpoint.langgraph_control.contract.v1",
        "description": (
            "Pre-declared expected outcome for the LangGraph non-crash positive control, written "
            "and hashed before the worker is invoked. Not adjusted after execution."
        ),
        "crash_injected": False,
        "durability_setting": "sync",
        "invoke_count": 1,
        "intended_effect_count": 1,
        "expected_worker_exit_status": 0,
        "expected_runtime_completion": "success",
        "expected_observation_method": "fresh_process_read_of_on_disk_ledger_bytes",
        "expected_observation_status": "OBSERVED",
        "expected_effect_count": 1,
        "expected_chain_valid": True,
        "expected_admission_status_after": "completed",
        "expected_terminal_state": {"done": True},
    }


@dataclass(frozen=True)
class _WorkerRun:
    exit_status: int | None
    ok: bool
    error_type: str | None
    error_message: str | None
    returned_state: dict[str, object] | None
    stdout: str
    stderr: str
    timed_out: bool


def _run_worker_subprocess(
    *, checkpoint: Path, invoke_path: str, admission_db: Path, thread_id: str, intent_id: str,
    timeout: float,
) -> _WorkerRun:
    argv = [
        sys.executable, "-m", "crashpoint.harness.langgraph_control", "--worker",
        "--checkpoint", str(checkpoint), "--effect-ledger", invoke_path,
        "--admission-db", str(admission_db), "--thread-id", thread_id, "--intent", intent_id,
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        return _WorkerRun(
            exit_status=None, ok=False, error_type="TimeoutExpired",
            error_message=f"worker did not exit within {timeout}s", returned_state=None,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "", timed_out=True,
        )

    report: dict[str, Any] | None = None
    for line in reversed(proc.stdout.splitlines()):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            break
        if isinstance(parsed, dict):
            report = parsed
        break

    if proc.returncode != 0 or report is None:
        return _WorkerRun(
            exit_status=proc.returncode, ok=False, error_type="WorkerProcessFailed",
            error_message=f"worker exited {proc.returncode} with no parseable report",
            returned_state=None, stdout=proc.stdout, stderr=proc.stderr, timed_out=False,
        )

    returned = report.get("returned")
    return _WorkerRun(
        exit_status=proc.returncode,
        ok=bool(report.get("ok")),
        error_type=cast(str | None, report.get("error_type")),
        error_message=cast(str | None, report.get("message")),
        returned_state=(
            cast(dict[str, object] | None, returned) if isinstance(returned, dict) else None
        ),
        stdout=proc.stdout, stderr=proc.stderr, timed_out=False,
    )


def _safe_thread_checkpoint_count(
    checkpoint_db: Path, thread_id: str
) -> tuple[int | None, str | None]:
    """A fresh sqlite3 connection distinct from the worker's own (which is closed and gone by the
    time the harness calls this): plain stdlib SQLite access to a file LangGraph happens to have
    created, not a LangGraph API call. ``immutable=1`` because the worker already folded the WAL
    back into the main file (see ``worker``'s ``PRAGMA wal_checkpoint(TRUNCATE)``) and a plain
    connect would otherwise create fresh ``-shm``/``-wal`` sidecar files next to a file this
    bundle intends to retain as a single frozen artifact.

    Returns ``(count, error)``. A checkpoint database that exists but was never fully set up (the
    worker died between opening the sqlite connection and ``SqliteSaver.setup()`` - reproduced
    under real disk pressure during the publication review) raises ``sqlite3.OperationalError: no
    such table: checkpoints``. That must not crash the whole run and lose every other piece of
    already-written evidence; it is reported as an explicit, explained null instead, the same
    missing-vs-unreadable discipline ``ledger_readback.py`` applies to the effect ledger."""
    try:
        with sqlite3.connect(f"file:{checkpoint_db}?immutable=1", uri=True) as conn:
            row = conn.execute(
                "select count(*) from checkpoints where thread_id = ?", (thread_id,)
            ).fetchone()
            return (int(row[0]) if row is not None else 0), None
    except sqlite3.Error as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _validate_observer_report_shape(parsed: object) -> str | None:
    """Return ``None`` if the parsed observer report is well-formed enough to trust for building
    the receipt; otherwise a diagnostic string describing what is wrong. Never raises - this
    exists specifically so a malformed report (``[]``, ``null``, ``{}``, or an ``OBSERVED`` report
    missing/mistyping a field that status requires) cannot crash ``run_primary_control`` with an
    ``AttributeError``/``KeyError`` after the worker has already run and the receipt is the only
    remaining place left to honestly record what happened. ``cast()`` alone (the bug this fixes)
    performs no runtime check at all - it is purely a type-checker hint, so ``json.loads("[]")``
    cast to ``dict[str, Any]`` is still a list at runtime and crashes on the first ``.get()``."""
    if not isinstance(parsed, dict):
        return f"observer report is valid JSON but not an object (found {type(parsed).__name__})"
    status = parsed.get("status")
    if not isinstance(status, str) or status not in VALID_OBSERVER_STATUSES:
        return f"observer report has a missing or invalid 'status' field: {status!r}"
    if status != "OBSERVED":
        return None
    count = parsed.get("observed_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return f"observer report status is OBSERVED but 'observed_count' is invalid: {count!r}"
    digests = parsed.get("effect_digests")
    if not isinstance(digests, list) or not all(isinstance(d, str) for d in digests):
        return "observer report status is OBSERVED but 'effect_digests' is not a list of strings"
    if not isinstance(parsed.get("effect_attempt_ids"), list):
        return "observer report status is OBSERVED but 'effect_attempt_ids' is not a list"
    if not isinstance(parsed.get("chain_valid"), bool):
        return "observer report status is OBSERVED but 'chain_valid' is not a bool"
    first_broken = parsed.get("first_broken_index")
    if not isinstance(first_broken, int) or isinstance(first_broken, bool):
        return "observer report status is OBSERVED but 'first_broken_index' is not an int"
    for field in (
        "archived_ledger_readback_path", "archived_ledger_readback_sha256",
        "original_readback_sha256",
    ):
        value = parsed.get(field)
        if not isinstance(value, str) or not value:
            return f"observer report status is OBSERVED but {field!r} is not a non-empty string"
    if not isinstance(parsed.get("archive_matches_original"), bool):
        return "observer report status is OBSERVED but 'archive_matches_original' is not a bool"
    return None


def run_primary_control(
    output_dir: Path,
    *,
    name: str = "langgraph_noncrash_control",
    worker_timeout: float = 30.0,
    observer_timeout: float = 30.0,
    root: Path = _ROOT,
) -> dict[str, object]:
    """Run the whole control once into a new, exclusive ``output_dir`` and return the native
    receipt body (already containing the outer ``receipt`` hash). Raises ``FileExistsError`` if
    ``output_dir`` already exists - checked first, before any other side effect - and
    ``RuntimeError`` if the assembled receipt fails its own structural validation."""
    if output_dir.exists():
        raise FileExistsError(f"control output directory already exists: {output_dir}")

    identity = _fresh_identity()  # admission_id == thread_id == intent_id, see module docstring
    output_dir.mkdir(parents=True)

    # 1. Admission record, persisted and durably bound BEFORE the worker is ever invoked.
    admission_db = output_dir / "admission.sqlite"
    initialize_admission_ledger(admission_db)
    accepted_at_utc = _utc_now_iso()
    accept_run(admission_db, identity, _ORIGINAL_INPUT)

    # 2. Expected-result contract, saved and hashed before the primary run; never edited after.
    contract_path = output_dir / "contract.json"
    contract_body = _contract_body()
    contract_path.write_text(json.dumps(contract_body, indent=2, sort_keys=True) + "\n")
    contract_sha256 = _sha256_file(contract_path)

    # 3. Run manifest: installed versions, Python/platform, base commit AND actual uncommitted
    #    source hashes/dirty state (this work is not contained in the base commit - see module
    #    docstring), dependency lock hash, and the contract's own hash.
    manifest_path = output_dir / "manifest.json"
    manifest_body = _build_manifest(root, contract_path, contract_sha256)
    manifest_path.write_text(json.dumps(manifest_body, indent=2, sort_keys=True) + "\n")

    # Retain copies of the REQUIRED executing-source modules INSIDE the bundle so their hashes are
    # checkable after the bundle is moved off this machine, without needing the original repository
    # checkout. Every required module must exist and copy successfully - a missing one is a setup
    # error (raised by _hash_executing_source_files above, which runs first), not something silently
    # skipped here.
    source_dir = output_dir / "source"
    source_dir.mkdir()
    for rel in REQUIRED_EXECUTING_SOURCE_MODULES:
        src_path = root / rel
        dest = source_dir / Path(rel).name
        dest.write_bytes(src_path.read_bytes())

    checkpoint_db = output_dir / "checkpoint.sqlite"

    with tempfile.TemporaryDirectory() as ephemeral:
        # Short system-temp path for the ledger's Unix sockets, deliberately outside the (possibly
        # long) worktree path.
        ledger_work = Path(ephemeral) / "effect-ledger"
        with LedgerDaemon(ledger_work) as effect_ledger:
            # 4. Known-fresh ledger/store: actually confirmed empty by a real read over the control
            #    socket, not inferred from a missing file after a failed read.
            effect_ledger.reset()
            baseline = effect_ledger.dump()
            if baseline.get("side_effects"):
                raise RuntimeError(f"newly reset ledger is not empty: {baseline}")

            worker_run = _run_worker_subprocess(
                checkpoint=checkpoint_db, invoke_path=effect_ledger.invoke_path,
                admission_db=admission_db, thread_id=identity, intent_id=identity,
                timeout=worker_timeout,
            )
            (output_dir / "worker-stdout.log").write_text(worker_run.stdout)
            (output_dir / "worker-stderr.log").write_text(worker_run.stderr)

            if worker_run.ok:
                append_admission_event(admission_db, identity, "completed")

            effect_ledger.seal()
            store_path_at_seal_time = Path(effect_ledger.store_path)
        # LedgerDaemon.__exit__ has now terminated and reaped the daemon subprocess. The ephemeral
        # temp dir (and the original ledger store file inside it) still exists until this whole
        # `with tempfile.TemporaryDirectory()` block exits, below.

        observer_report_path = output_dir / "observer-report.json"
        archive_path = output_dir / "ledger-readback.jsonl"
        observer_argv = [
            sys.executable, "-m", "crashpoint.harness.langgraph_control_observer",
            "--ledger-store", str(store_path_at_seal_time), "--intent", identity,
            "--archive-to", str(archive_path), "--report-out", str(observer_report_path),
        ]
        try:
            observer_proc = subprocess.run(
                observer_argv, capture_output=True, text=True, timeout=observer_timeout, check=False
            )
            observer_stdout, observer_stderr, observer_exit = (
                observer_proc.stdout, observer_proc.stderr, observer_proc.returncode,
            )
        except subprocess.TimeoutExpired as exc:
            observer_stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            observer_stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            observer_exit = None
        (output_dir / "observer-stdout.log").write_text(observer_stdout)
        (output_dir / "observer-stderr.log").write_text(observer_stderr)

        # Parse the observer's own report now, while the ephemeral store is still readable, so a
        # PARENT-PROCESS fallback archive can be taken if the observer failed after the worker had
        # already written real ledger bytes - preserved for forensics, never claimed as proof the
        # fresh observer succeeded (see the observer/parent_fallback_* fields below and
        # LIMITATIONS in langgraph_control_receipt.py).
        observer_report: dict[str, Any]
        if not observer_report_path.exists():
            observer_report = {
                "status": "OBSERVER_ERROR", "pid": None,
                "pid_note": "diagnostic metadata only, not cryptographic proof of independence",
                "error": (
                    "observer process produced no report file (timeout or crash before writing)"
                ),
            }
        else:
            try:
                parsed_report = json.loads(observer_report_path.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                observer_report = {
                    "status": "OBSERVER_ERROR", "pid": None,
                    "pid_note": "diagnostic metadata only, not cryptographic proof of independence",
                    "error": (
                        f"observer report file exists but could not be parsed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                }
            else:
                # json.loads succeeding does not mean the result is a usable dict - `[]`, `null`,
                # a bare number/string, or `{}` all parse fine. cast() alone (the original bug)
                # performs no runtime check, so an unvalidated report crashes the FIRST .get() call
                # below with an AttributeError, after the worker has already run successfully and
                # with no receipt ever written. Validate shape/types before trusting anything in it.
                shape_problem = _validate_observer_report_shape(parsed_report)
                if shape_problem is not None:
                    observer_report = {
                        "status": "OBSERVER_ERROR", "pid": None,
                        "pid_note": (
                            "diagnostic metadata only, not cryptographic proof of independence"
                        ),
                        "error": f"observer report file exists but is unusable: {shape_problem}",
                    }
                else:
                    observer_report = cast(dict[str, Any], parsed_report)

        observer_archived_ok = bool(
            observer_report.get("archived_ledger_readback_path")
            and observer_report.get("archive_matches_original")
        )
        parent_fallback_path: str | None = None
        parent_fallback_sha256: str | None = None
        if not observer_archived_ok and store_path_at_seal_time.exists():
            fallback_dest = output_dir / "parent-fallback-ledger-readback.jsonl"
            fallback_bytes = store_path_at_seal_time.read_bytes()
            fallback_dest.write_bytes(fallback_bytes)
            parent_fallback_path = fallback_dest.name
            parent_fallback_sha256 = hashlib.sha256(fallback_bytes).hexdigest()
    # tempfile.TemporaryDirectory cleanup happens here; the archived copy (or, if the observer could
    # not produce one, the parent-fallback copy above) already preserves what is needed, written
    # while the ephemeral directory still existed.

    checkpoint_count, checkpoint_error = (
        _safe_thread_checkpoint_count(checkpoint_db, identity)
        if checkpoint_db.exists()
        else (None, "checkpoint database file was never created")
    )
    admission_after = read_admission(admission_db, identity)

    obs = ControlObservation(
        case=name,
        identity={
            "admission_id": identity,
            "thread_id": identity,
            "intent_id": identity,
            "identity_binding_note": (
                "admission_id, thread_id, and the ledger intent_id are the same value in this "
                "control: one uuid4-derived identity generated once, never reused across runs"
            ),
        },
        admission={
            "admission_db_path": admission_db.name,
            "admission_db_sha256": _sha256_file(admission_db),
            "accepted_before_invoke": True,
            "accepted_at_utc": accepted_at_utc,
            "original_input": _ORIGINAL_INPUT,
            "original_input_source": (
                "read back by the worker from the admission ledger row itself before invoking "
                "LangGraph, not passed to the worker as a separate argument"
            ),
            "events_after_run": list(admission_after.events),
            "status_after_run": admission_after.status,
        },
        runtime={
            "langgraph_version": _installed_version("langgraph"),
            "langgraph_checkpoint_version": _installed_version("langgraph-checkpoint"),
            "langgraph_checkpoint_sqlite_version": (
                _installed_version("langgraph-checkpoint-sqlite")
            ),
            "python_version": sys.version.split()[0],
            "platform": sys.platform,
            "durability_setting": "sync",
            "crash_injected": False,
            "invoke_count": 1,
            "worker_exit_status": worker_run.exit_status,
            "worker_ok": worker_run.ok,
            "worker_error_type": worker_run.error_type,
            "worker_error_message": worker_run.error_message,
            "worker_returned_state": worker_run.returned_state,
        },
        checkpoint={
            "checkpoint_db_path": checkpoint_db.name,
            "checkpoint_db_sha256": _sha256_file(checkpoint_db) if checkpoint_db.exists() else "",
            "thread_scoped_checkpoint_count": checkpoint_count,
            "checkpoint_query": "select count(*) from checkpoints where thread_id = ?",
            "checkpoint_error": checkpoint_error,
        },
        observer={
            "status": observer_report.get("status"),
            "pid": observer_report.get("pid"),
            "pid_note": "diagnostic metadata only, not cryptographic proof of independence",
            "exit_status": observer_exit,
            "stdout_path": "observer-stdout.log",
            "stderr_path": "observer-stderr.log",
            "report_path": observer_report_path.name if observer_report_path.exists() else None,
            "report_sha256": (
                _sha256_file(observer_report_path) if observer_report_path.exists() else None
            ),
            "archived_ledger_readback_path": (
                archive_path.name if observer_report.get("archived_ledger_readback_path") else None
            ),
            "archived_ledger_readback_sha256": (
                observer_report.get("archived_ledger_readback_sha256")
            ),
            "original_readback_sha256": observer_report.get("original_readback_sha256"),
            "archive_matches_original": bool(observer_report.get("archive_matches_original")),
            "chain_valid": bool(observer_report.get("chain_valid")),
            "first_broken_index": observer_report.get("first_broken_index", -1),
            "record_count": observer_report.get("record_count"),
            "representation_note": observer_report.get(
                "representation_note",
                "each JSONL record stores intent_id directly and the payload only as a "
                "SHA-256 digest",
            ),
            "parent_fallback_archive_path": parent_fallback_path,
            "parent_fallback_archive_sha256": parent_fallback_sha256,
            "error": observer_report.get("error"),
        },
        effect={
            "observed_count": observer_report.get("observed_count"),
            "effect_digests": observer_report.get("effect_digests", []),
            "effect_attempt_ids": observer_report.get("effect_attempt_ids", []),
        },
        contract={"path": contract_path.name, "sha256": contract_sha256},
        manifest={"path": manifest_path.name, "sha256": _sha256_file(manifest_path)},
        source={
            "base_commit": manifest_body["base_commit"],
            "worktree_branch": manifest_body["worktree_branch"],
            "dirty": manifest_body["dirty"],
            "executing_source_files": manifest_body["executing_source_files"],
            "uv_lock_sha256": manifest_body["uv_lock_sha256"],
        },
        expected=json.loads(contract_path.read_text()),
    )

    record = build_receipt(obs)
    record["receipt"] = canonical_receipt(record)

    problems = validate_receipt(record)
    if problems:
        raise RuntimeError(f"assembled receipt failed its own validation: {problems}")

    (output_dir / "receipt.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def _installed_version(dist_name: str) -> str:
    import importlib.metadata as m

    try:
        return m.version(dist_name)
    except m.PackageNotFoundError:
        return "unknown"


def render(record: dict[str, object]) -> str:
    result = cast(dict[str, object], record["result"])
    observer = cast(dict[str, object], record["observer"])
    return "\n".join(
        [
            f"LangGraph non-crash control - case={record['case']}",
            f"oracle_classification: {result['oracle_classification']}",
            f"passed: {result['passed']}  agreement: {result['agreement']}",
            f"observer status: {observer['status']}  chain_valid: {observer['chain_valid']}",
            f"receipt: {record.get('receipt')}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--checkpoint", help=argparse.SUPPRESS)
    parser.add_argument("--effect-ledger", help=argparse.SUPPRESS)
    parser.add_argument("--admission-db", help=argparse.SUPPRESS)
    parser.add_argument("--thread-id", help=argparse.SUPPRESS)
    parser.add_argument("--intent", help=argparse.SUPPRESS)
    parser.add_argument(
        "--output", type=Path, help="exclusive new directory for the recorded bundle"
    )
    parser.add_argument("--name", default="langgraph_noncrash_control")
    parser.add_argument("--worker-timeout", type=float, default=30.0)
    parser.add_argument("--observer-timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    if args.worker:
        missing = [
            flag for flag, val in (
                ("--checkpoint", args.checkpoint), ("--effect-ledger", args.effect_ledger),
                ("--admission-db", args.admission_db), ("--thread-id", args.thread_id),
                ("--intent", args.intent),
            ) if val is None
        ]
        if missing:
            parser.error(f"--worker requires {', '.join(missing)}")
        return worker(
            args.checkpoint, args.effect_ledger, args.admission_db, args.thread_id, args.intent
        )

    if args.output is None:
        parser.error("--output is required")
    record = run_primary_control(
        args.output, name=args.name,
        worker_timeout=args.worker_timeout, observer_timeout=args.observer_timeout,
    )
    print(render(record))
    result = cast(dict[str, object], record["result"])
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
