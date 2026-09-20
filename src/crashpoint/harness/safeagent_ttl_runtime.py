"""Real-package subject for the SafeAgent TTL / sweep / fresh-client experiment.

Runs inside one of two isolated per-release virtualenvs (0.1.23 or 0.1.24 of
``safeagent-exec-guard``), never inside crashpoint's own shared dev venv, which never has
SafeAgent installed at all. Each role below is a short-lived process the parent harness
(``safeagent_ttl.py``) spawns and either waits on to completion or kills outright:

``claim-run``  Worker A: claims durably, records the harmless effect through crashpoint's
               out-of-process ledger when the case calls for it, emits a structured barrier
               event, then blocks forever so the parent - never the subject - decides when it
               dies. ``settled_control`` is the one case that never blocks: it claims, effects,
               settles, and exits 0 on its own.
``sweep``      A fresh client opening the same durable claim database, calling the installed
               ``sweep_stale_pending()`` (and, where present, ``count_stale_pending()``) once.
``inspect``    A fresh, non-mutating client reading back ``get(action_id)``, optionally also
               writing a WAL-consistent snapshot of the claim database via the stdlib
               ``sqlite3`` backup API (not SafeAgent's own connection).
``retry``      Worker B: a fresh OS process, fresh store client, that attempts the same logical
               claim and either performs exactly one effect and settles it (claim admitted) or
               performs none and just reads back the existing claim (claim denied).

WHAT COUNTS AS OBSERVED lives in the parent, not here: this module only emits facts (claim
booleans, ledger acknowledgements, the row SafeAgent's own ``get()`` returns) and never itself
decides EXACTLY_ONCE/DUPLICATED/PENDING-is-fine/PENDING-is-stuck. The payload passed to the
ledger is constant across every attempt of a trial - the worker-A/worker-B distinction lives
only in the ``attempt_id`` field, which the ledger keeps outside the payload digest, so two
authorized crossings of one logical action are recognized as the same charge twice, not two
different charges.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
from typing import Any

PREFIX = "CRASHPOINT_SAFEAGENT_TTL "
ACTION_NAME = "safeagent_ttl_local_action"
PAYLOAD: dict[str, object] = {"operation": "safeagent_ttl_local_action", "marker": "harmless"}
CASES = (
    "settled_control",
    "pending_before_ttl",
    "pending_expired_no_sweep",
    "pending_expired_swept",
    "pre_effect_expired_swept",
)


def emit(event: str, **fields: Any) -> None:
    print(PREFIX + json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def _store(db_path: str, ttl: float) -> Any:
    # Imported lazily so this module can be imported (e.g. by mypy or by the parent harness for
    # its constants) in an environment that never has safeagent-exec-guard installed - only the
    # two isolated per-release venvs do.
    from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

    return SQLiteExecutionStore(db_path, pending_ttl_seconds=ttl)


def _snapshot(db_path: str, out_path: str) -> None:
    """A WAL-consistent copy taken with the stdlib's own backup API, independent of any
    SafeAgent connection or checkpoint state, so retained evidence cannot lose committed WAL
    content the way a raw file copy could."""
    src = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(out_path)
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()


def claim_run(invoke: str, db_path: str, ttl: float, action_id: str, case: str) -> int:
    if case not in CASES:
        emit("fatal", reason=f"unknown case {case!r}")
        return 2
    from crashpoint.ledger.daemon import execute

    emit("worker_started", case=case, action_id=action_id, pid=os.getpid(), ttl=ttl)
    store = _store(db_path, ttl)
    claimed = store.claim(action_id, ACTION_NAME, agent_id=None)
    emit("claimed", action_id=action_id, claim_result=bool(claimed))
    if not claimed:
        # Every trial uses a fresh db and a fresh action_id; a first claim being refused means
        # the fixture itself is broken, not that the experiment observed anything.
        emit("fatal", reason="initial claim on a fresh action_id was refused")
        return 1

    def do_effect(attempt: str) -> bool:
        resp = execute(
            invoke, action_id, None, dict(PAYLOAD), attempt_id=f"{action_id}:{attempt}"
        )
        ok = resp.get("outcome") == "OK" and resp.get("ok") is True
        emit(
            "effect_ack" if ok else "effect_nack", action_id=action_id, attempt=attempt,
            response=resp,
        )
        return ok

    if case == "settled_control":
        if not do_effect("worker-a"):
            return 1
        store.settle(action_id, {"outcome": "worker-a-settled", "action_id": action_id})
        emit("settled", action_id=action_id, attempt="worker-a")
        emit("worker_a_complete", action_id=action_id)
        return 0

    if case == "pre_effect_expired_swept":
        emit("barrier", point="post_claim_pre_effect", pid=os.getpid(), action_id=action_id)
    else:
        if not do_effect("worker-a"):
            return 1
        emit("barrier", point="post_effect_pending", pid=os.getpid(), action_id=action_id)

    sys.stdout.flush()
    # Blocks forever. The parent kills this process with SIGKILL after independently verifying
    # claim state and ledger effect count; nothing in this branch ever exits on its own.
    threading.Event().wait()
    return 1  # pragma: no cover - unreachable; SIGKILL never lets this return.


def sweep_role(db_path: str, ttl: float) -> int:
    store = _store(db_path, ttl)
    has_count = hasattr(store, "count_stale_pending")
    stale_before: int | None = None
    if has_count:
        try:
            stale_before = store.count_stale_pending()
        except Exception as exc:  # report, do not hide, an unexpected method failure
            emit("sweep_count_error", error=repr(exc))
    swept = store.sweep_stale_pending()
    emit(
        "sweep_result",
        has_count_stale_pending=has_count,
        stale_count_before_sweep=stale_before,
        sweep_return_value=swept,
    )
    return 0


def inspect_role(db_path: str, action_id: str, snapshot_out: str | None) -> int:
    # A fresh, independent connection to the same durable claim database; distinct from the
    # connection any worker in this trial holds, matching "the harness's own read must be its
    # own file handle."
    from safeagent_exec_guard.sqlite_store import SQLiteExecutionStore

    store = SQLiteExecutionStore(db_path, pending_ttl_seconds=1.0)  # ttl irrelevant to get()
    row = store.get(action_id)
    queried_at = time.time()
    if snapshot_out:
        _snapshot(db_path, snapshot_out)
    emit("inspect_result", action_id=action_id, row=row, queried_at=queried_at)
    return 0


def retry_role(invoke: str, db_path: str, ttl: float, action_id: str) -> int:
    from crashpoint.ledger.daemon import execute

    store = _store(db_path, ttl)
    emit("retry_started", action_id=action_id, pid=os.getpid())
    admitted = store.claim(action_id, ACTION_NAME, agent_id=None)
    emit("retry_claim", action_id=action_id, admitted=bool(admitted))
    performed_effect = False
    if admitted:
        resp = execute(
            invoke, action_id, None, dict(PAYLOAD), attempt_id=f"{action_id}:worker-b-retry"
        )
        ok = resp.get("outcome") == "OK" and resp.get("ok") is True
        emit("effect_ack" if ok else "effect_nack", action_id=action_id, attempt="worker-b-retry",
             response=resp)
        if not ok:
            return 1
        performed_effect = True
        store.settle(action_id, {"outcome": "retry-settled", "action_id": action_id})
        emit("settled", action_id=action_id, attempt="worker-b-retry")
    row = store.get(action_id)
    emit(
        "retry_complete",
        action_id=action_id,
        admitted=bool(admitted),
        performed_effect=performed_effect,
        final_row=row,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="role", required=True)

    p_claim = sub.add_parser("claim-run")
    p_claim.add_argument("--invoke", required=True)
    p_claim.add_argument("--db", required=True)
    p_claim.add_argument("--ttl", type=float, required=True)
    p_claim.add_argument("--action-id", required=True)
    p_claim.add_argument("--case", required=True, choices=CASES)

    p_sweep = sub.add_parser("sweep")
    p_sweep.add_argument("--db", required=True)
    p_sweep.add_argument("--ttl", type=float, required=True)

    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("--db", required=True)
    p_inspect.add_argument("--action-id", required=True)
    p_inspect.add_argument("--snapshot-out", default=None)

    p_retry = sub.add_parser("retry")
    p_retry.add_argument("--invoke", required=True)
    p_retry.add_argument("--db", required=True)
    p_retry.add_argument("--ttl", type=float, required=True)
    p_retry.add_argument("--action-id", required=True)

    args = ap.parse_args(argv)
    if args.role == "claim-run":
        return claim_run(args.invoke, args.db, args.ttl, args.action_id, args.case)
    if args.role == "sweep":
        return sweep_role(args.db, args.ttl)
    if args.role == "inspect":
        return inspect_role(args.db, args.action_id, args.snapshot_out)
    if args.role == "retry":
        return retry_role(args.invoke, args.db, args.ttl, args.action_id)
    raise AssertionError(f"unhandled role {args.role!r}")  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
