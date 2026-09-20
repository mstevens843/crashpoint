"""Real-process subject for the pre-dispatch action identity / external readback experiment.

Three short-lived processes the parent harness (``action_readback.py``) spawns and either waits
on to completion or kills outright:

``dispatch``  Worker A (the original attempt) or Worker B (a fresh retry). Reads its own action
              record back from the caller-owned admission SQLite store through a fresh connection
              - never accepts a payload/action-id override from argv - then, unless stopped at the
              pre-dispatch barrier, records one harmless effect through crashpoint's out-of-process
              ledger and writes its own local completion receipt. ``--fault payload-mismatch``
              deliberately dispatches a different payload than the one it just read, under the
              SAME admitted action_id, to exercise the "effect identity substituted for
              pre-dispatch identity" case explicitly, never silently.
``observe``   A fresh, independent process with its own file handle that reads the receiver
              ledger's raw on-disk bytes (never a live daemon dump, never a worker's exit code),
              archives them verbatim, and parses/verifies the actual chain and per-intent record
              order itself. Every expected failure mode (missing file, unreadable file, a broken
              chain) is caught and reported as a structured event with THIS process still exiting
              0 - only a genuinely unexpected bug in the observer's own logic is allowed to raise
              and exit nonzero, so the harness can tell "the evidence is honestly absent/broken"
              apart from "the observer itself crashed and its report cannot be trusted".

The admission store (``admissions``/``completions`` tables) is this experiment's own fixture, not
a third-party package - there is nothing here analogous to SafeAgent's SQLiteExecutionStore to
isolate into a separate venv.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

from ..canonical import canonicalize, sha256_hex

PREFIX = "CRASHPOINT_ACTION_READBACK "
ACTION_TYPE = "action_readback_local_action"


def emit(event: str, **fields: Any) -> None:
    print(PREFIX + json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


# --------------------------------------------------------------------------------------------
# Admission store: this experiment's own caller-owned SQLite fixture. Every connection opened
# here is a FRESH one (never a handle reused across processes), matching "the harness's own read
# must be its own file handle" and "the worker must read the committed action record, not accept
# a later replacement ID/payload from command-line arguments".
# --------------------------------------------------------------------------------------------

_ADMISSIONS_DDL = """
CREATE TABLE IF NOT EXISTS admissions (
    action_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    trial_id TEXT NOT NULL,
    case_name TEXT NOT NULL,
    action_type TEXT NOT NULL,
    receiver_ref TEXT NOT NULL,
    canonical_payload TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    admitted_at_utc TEXT NOT NULL
)
"""
_COMPLETIONS_DDL = """
CREATE TABLE IF NOT EXISTS completions (
    action_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    PRIMARY KEY (action_id, attempt_id)
)
"""


def connect_admission_db(db_path: str) -> sqlite3.Connection:
    """A fresh connection with an explicit, recorded durability configuration - WAL journaling,
    FULL synchronous commits - so the claim below is bounded to what was actually configured,
    not left to whatever sqlite3's platform default happens to be."""
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; commits are explicit below
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def admission_durability_config(conn: sqlite3.Connection) -> tuple[str, str]:
    """Read back the ACTUAL pragma values in effect on this connection (not the values we asked
    for - a platform without WAL support would silently fall back, and this must catch that)."""
    journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
    synchronous = str(conn.execute("PRAGMA synchronous").fetchone()[0])
    return journal_mode, synchronous


def init_admission_db(db_path: str) -> None:
    conn = connect_admission_db(db_path)
    try:
        conn.execute(_ADMISSIONS_DDL)
        conn.execute(_COMPLETIONS_DDL)
    finally:
        conn.close()


def write_admission(
    db_path: str, *, action_id: str, run_id: str, trial_id: str, case_name: str,
    receiver_ref: str, payload: dict[str, object], admitted_at_utc: str,
) -> str:
    """Commit one admission row through a connection this call owns start to finish, then close
    it - the harness confirms durability separately, through ANOTHER fresh connection, after this
    returns. Returns the payload digest that was written."""
    canonical_payload = canonicalize(payload)
    digest = sha256_hex(canonical_payload)
    conn = connect_admission_db(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO admissions (action_id, run_id, trial_id, case_name, action_type, "
            "receiver_ref, canonical_payload, payload_digest, admitted_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (action_id, run_id, trial_id, case_name, ACTION_TYPE, receiver_ref,
             canonical_payload, digest, admitted_at_utc),
        )
        conn.commit()
    finally:
        conn.close()
    return digest


def read_admission(db_path: str, action_id: str) -> dict[str, object] | None:
    """A fresh connection, a fresh read. Returns None if no row exists (never a fabricated
    default), so a caller that reads its own admission back and finds nothing can tell that
    apart from a row with different content."""
    conn = connect_admission_db(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM admissions WHERE action_id = ?", (action_id,)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def write_completion(
    db_path: str, *, action_id: str, attempt_id: str, outcome: str, completed_at_utc: str
) -> None:
    conn = connect_admission_db(db_path)
    try:
        conn.execute(
            "INSERT INTO completions (action_id, attempt_id, outcome, completed_at_utc) "
            "VALUES (?, ?, ?, ?)",
            (action_id, attempt_id, outcome, completed_at_utc),
        )
        conn.commit()
    finally:
        conn.close()


def read_completion(db_path: str, action_id: str, attempt_id: str) -> dict[str, object] | None:
    conn = connect_admission_db(db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM completions WHERE action_id = ? AND attempt_id = ?",
            (action_id, attempt_id),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def utc_now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat()


# --------------------------------------------------------------------------------------------
# dispatch role: Worker A or Worker B.
# --------------------------------------------------------------------------------------------

_PRE_DISPATCH_CASES = frozenset({"stopped_before_effect"})
_LOST_RECEIPT_CASES = frozenset({"effect_before_lost_receipt", "naive_retry"})


def dispatch_role(
    *, admission_db: str, invoke: str, action_id: str, attempt_id: str, case: str,
    fault: str | None,
) -> int:
    emit("worker_started", pid=os.getpid(), action_id=action_id, attempt_id=attempt_id, case=case)

    admitted = read_admission(admission_db, action_id)
    if admitted is None:
        emit("fatal", reason="admission row not found for this action_id", action_id=action_id)
        return 2
    admitted_payload = json.loads(str(admitted["canonical_payload"]))
    admitted_digest = str(admitted["payload_digest"])
    emit(
        "admission_read", action_id=action_id, admission_payload_digest=admitted_digest,
        receiver_ref=admitted["receiver_ref"],
    )

    is_worker_a = attempt_id.endswith(":worker-a")
    if is_worker_a and case in _PRE_DISPATCH_CASES:
        emit("barrier", point="pre_dispatch", pid=os.getpid(), action_id=action_id)
        sys.stdout.flush()
        threading.Event().wait()  # SIGKILL only; never returns on its own
        return 1  # pragma: no cover - unreachable

    dispatch_payload = dict(admitted_payload)
    if fault == "payload-mismatch":
        dispatch_payload["marker"] = "MUTATED_BY_INJECTED_FAULT"
    dispatch_digest = sha256_hex(canonicalize(dispatch_payload))

    from ..ledger.daemon import execute

    resp = execute(invoke, action_id, None, dispatch_payload, attempt_id=attempt_id)
    ok = resp.get("outcome") == "OK" and resp.get("ok") is True
    emit(
        "effect_ack" if ok else "effect_nack", action_id=action_id, attempt_id=attempt_id,
        dispatch_payload_digest=dispatch_digest, response=resp,
    )
    if not ok:
        emit("fatal", reason="ledger execute() was not acknowledged", action_id=action_id)
        return 1

    if is_worker_a and case in _LOST_RECEIPT_CASES:
        emit("barrier", point="post_effect_pre_receipt", pid=os.getpid(), action_id=action_id)
        sys.stdout.flush()
        threading.Event().wait()  # SIGKILL only; never returns on its own
        return 1  # pragma: no cover - unreachable

    write_completion(
        admission_db, action_id=action_id, attempt_id=attempt_id, outcome="OK",
        completed_at_utc=utc_now_iso(),
    )
    emit("local_receipt_written", action_id=action_id, attempt_id=attempt_id)
    emit("worker_complete", action_id=action_id, attempt_id=attempt_id)
    return 0


# --------------------------------------------------------------------------------------------
# observe role: a fresh, independent reader of the receiver ledger's raw bytes.
# --------------------------------------------------------------------------------------------

_GENESIS = "crashpoint-ledger-genesis-cp1"


def _parse_ledger_bytes(raw: bytes) -> dict[str, object]:
    """Independently re-derive facts from raw ledger JSONL bytes: this process's own parse, not
    a call into the daemon or a reuse of any in-process LedgerState. Mirrors the shape the
    offline verifier will separately, independently redo later against the archived copy."""
    from ..canonical import chain

    head = _GENESIS
    attempts: dict[str, int] = {}
    side_effects: dict[str, int] = {}
    effect_digests: dict[str, list[str]] = {}
    attempt_ids_by_intent: dict[str, list[Any]] = {}
    chain_valid = True
    broken_at_index: int | None = None
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return {
            "chain_valid": False, "broken_at_index": 0, "attempts": attempts,
            "side_effects": side_effects, "effect_digests": effect_digests,
            "attempt_ids_by_intent": attempt_ids_by_intent,
        }
    for idx, line in enumerate(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            chain_valid = False
            broken_at_index = idx
            break
        if not isinstance(entry, dict) or entry.get("prev") != head:
            chain_valid = False
            broken_at_index = idx
            break
        record = entry.get("record", {})
        if not isinstance(record, dict):
            chain_valid = False
            broken_at_index = idx
            break
        expected_hash = chain(head, record)
        if entry.get("hash") != expected_hash:
            chain_valid = False
            broken_at_index = idx
            break
        head = expected_hash
        if record.get("op") == "execute":
            intent = str(record.get("intent_id", ""))
            attempts[intent] = attempts.get(intent, 0) + 1
            attempt_ids_by_intent.setdefault(intent, []).append(record.get("attempt_id"))
            if not record.get("deduped", False):
                digest = record.get("payload_digest")
                side_effects[intent] = side_effects.get(intent, 0) + 1
                if isinstance(digest, str):
                    effect_digests.setdefault(intent, []).append(digest)
    return {
        "chain_valid": chain_valid,
        "broken_at_index": broken_at_index,
        "attempts": attempts,
        "side_effects": side_effects,
        "effect_digests": effect_digests,
        "attempt_ids_by_intent": attempt_ids_by_intent,
    }


def observe_role(*, ledger_path: str, archive_out: str, action_id: str) -> int:
    emit("observer_started", pid=os.getpid(), action_id=action_id)
    path = Path(ledger_path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        emit(
            "readback_result", action_id=action_id, raw_state="READ_FAILED",
            error="ledger file does not exist", raw_sha256=None, byte_length=None,
        )
        return 0
    except OSError as exc:
        emit(
            "readback_result", action_id=action_id, raw_state="READ_FAILED",
            error=repr(exc), raw_sha256=None, byte_length=None,
        )
        return 0

    Path(archive_out).write_bytes(raw)
    raw_sha256 = hashlib.sha256(raw).hexdigest()  # byte-exact digest of the raw file content
    if len(raw) == 0:
        emit(
            "readback_result", action_id=action_id, raw_state="READ_EMPTY_CONFIRMED",
            raw_sha256=raw_sha256, byte_length=0, chain_valid=True, broken_at_index=None,
            attempts={}, side_effects={}, effect_digests={}, attempt_ids_by_intent={},
        )
        return 0

    parsed = _parse_ledger_bytes(raw)
    emit(
        "readback_result", action_id=action_id, raw_state="READ_OK", raw_sha256=raw_sha256,
        byte_length=len(raw), **parsed,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="role", required=True)

    p_dispatch = sub.add_parser("dispatch")
    p_dispatch.add_argument("--admission-db", required=True)
    p_dispatch.add_argument("--invoke", required=True)
    p_dispatch.add_argument("--action-id", required=True)
    p_dispatch.add_argument("--attempt-id", required=True)
    p_dispatch.add_argument("--case", required=True)
    p_dispatch.add_argument("--fault", default=None, choices=["payload-mismatch"])

    p_observe = sub.add_parser("observe")
    p_observe.add_argument("--ledger-path", required=True)
    p_observe.add_argument("--archive-out", required=True)
    p_observe.add_argument("--action-id", required=True)

    args = ap.parse_args(argv)
    if args.role == "dispatch":
        return dispatch_role(
            admission_db=args.admission_db, invoke=args.invoke, action_id=args.action_id,
            attempt_id=args.attempt_id, case=args.case, fault=args.fault,
        )
    if args.role == "observe":
        return observe_role(
            ledger_path=args.ledger_path, archive_out=args.archive_out, action_id=args.action_id,
        )
    raise AssertionError(f"unhandled role {args.role!r}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
