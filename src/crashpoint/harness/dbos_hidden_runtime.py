"""DBOS-side definitions and crash hooks for ``crashpoint.harness.dbos_hidden``.

Only the subject and recovery subprocesses import this module, so the harness itself stays
importable without DBOS. The hooks are installed in the crash process only, after
``DBOS.launch()`` (when the system-database engine exists), and each one names the exact
persistence boundary it crashes on:

  * ``dbos_step_output_uncommitted`` wraps ``SystemDatabase._record_operation_result_txn``: the
    INSERT into ``operation_outputs`` for ``effect_step`` has executed on the open transaction, and
    the process dies before that transaction commits.
  * ``dbos_step_output_committed_before_resume`` uses DBOS's own
    ``DebugTriggers.DEBUG_TRIGGER_STEP_COMMIT``, which fires right after the step-output commit,
    before the workflow function resumes.
  * ``dbos_workflow_outcome_uncommitted`` listens on the SQLAlchemy engine: the UPDATE of
    ``workflow_status`` to SUCCESS has executed, and the ``commit`` event (dispatched before the
    DBAPI commit) kills the process.
  * ``dbos_duplicate_workflow_name_recovery`` needs no hook: two generated modules register a
    workflow named ``process``; the billing step crashes after its effect (the b1 point), and the
    recovery process imports the shipping module last so the registry dispatches to it.
"""

from __future__ import annotations

import importlib
import os
import signal
import sys
import time
from typing import Any

import sqlalchemy as sa
from dbos import DBOS, DBOSConfig, SetWorkflowID
from dbos._debug_trigger import DebugAction, DebugTriggers
from dbos._sys_db import SystemDatabase

from ..ledger.daemon import execute

_PAYLOAD = {"amount": 100, "to": "acct-attacker"}
APP_NAME = "crashpoint-hidden"
APP_VERSION = "crashpoint-hidden-v1"
EFFECT_STEP = "effect_step"
BILLING_MODULE = "cp_billing"
SHIPPING_MODULE = "cp_shipping"
DUPLICATE_NAME_BARRIER = "dbos_duplicate_workflow_name_recovery"
_TXN_METHOD = "_record_operation_result_txn"

# Process-level state set by the subject/recovery entrypoints.
LEDGER = ""
INTENT = ""
LAST_RECORDED: dict[str, str] = {"function_name": ""}


def _die() -> None:
    """SIGKILL this process and never return to the caller.

    The kill is issued from a worker thread. Process termination is not instantaneous for the
    thread that requested it: on macOS the calling thread can run a little further while the
    process is torn down, which is enough to send a COMMIT to Postgres or report a task to a
    server. Blocking here keeps the crash exactly where the barrier says it is.
    """
    os.kill(os.getpid(), signal.SIGKILL)
    while True:  # pragma: no cover - the process is dead before this loop matters
        time.sleep(1)


@DBOS.step()
def effect_step() -> str:
    execute(LEDGER, INTENT, None, dict(_PAYLOAD))
    return "ok"


@DBOS.step()
def sentinel_step() -> str:
    return "ok"


@DBOS.workflow()
def hidden_workflow() -> str:
    effect_step()
    sentinel_step()
    return "done"


def _config(db_url: str, executor_id: str) -> DBOSConfig:
    return DBOSConfig(
        name=APP_NAME,
        system_database_url=db_url,
        log_level="ERROR",
        executor_id=executor_id,
        application_version=APP_VERSION,
        run_admin_server=False,
    )


def _mentions_success(parameters: Any) -> bool:
    values = parameters.values() if isinstance(parameters, dict) else parameters
    try:
        return any(v == "SUCCESS" for v in values)
    except TypeError:
        return False


def install_hooks(dbos: DBOS, barrier: str) -> None:
    original = getattr(SystemDatabase, _TXN_METHOD)

    def record_txn(self: SystemDatabase, result: Any, completed_at_epoch_ms: int,
                   conn: Any) -> None:
        original(self, result, completed_at_epoch_ms, conn)
        LAST_RECORDED["function_name"] = str(result["function_name"])
        if barrier == "dbos_step_output_uncommitted" and result["function_name"] == EFFECT_STEP:
            _die()  # the INSERT ran on this transaction; it never commits

    setattr(SystemDatabase, _TXN_METHOD, record_txn)

    if barrier == "dbos_step_output_committed_before_resume":

        def after_step_commit() -> None:
            if LAST_RECORDED["function_name"] == EFFECT_STEP:
                _die()  # the output is committed; the workflow has not resumed

        DebugTriggers.set_debug_trigger(
            DebugTriggers.DEBUG_TRIGGER_STEP_COMMIT, DebugAction(callback=after_step_commit)
        )

    if barrier == "dbos_workflow_outcome_uncommitted":
        engine = dbos._sys_db.engine

        @sa.event.listens_for(engine, "after_cursor_execute")
        def flag_outcome_update(conn: Any, cursor: Any, statement: str, parameters: Any,
                                context: Any, executemany: bool) -> None:
            text = statement.lstrip().upper()
            if (
                text.startswith("UPDATE")
                and "WORKFLOW_STATUS" in text
                and _mentions_success(parameters)
            ):
                conn.info["cp_outcome_update"] = True

        @sa.event.listens_for(engine, "commit")
        def before_commit(conn: Any) -> None:
            if conn.info.pop("cp_outcome_update", False):
                _die()  # the UPDATE ran on this transaction; it never commits


def _duplicate_modules(modules_dir: str) -> tuple[Any, Any | None]:
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)
    billing = importlib.import_module(BILLING_MODULE)
    return billing, None


def subject(barrier: str, db_url: str, ledger: str, intent: str, wfid: str, executor_id: str,
            modules_dir: str) -> None:
    global LEDGER, INTENT
    LEDGER = ledger
    INTENT = intent
    os.environ["CP_LEDGER"] = ledger
    os.environ["CP_INTENT"] = intent
    os.environ["CP_CRASH"] = "1"
    target: Any = hidden_workflow
    if barrier == DUPLICATE_NAME_BARRIER:
        billing, _ = _duplicate_modules(modules_dir)
        target = billing.process
    dbos = DBOS(config=_config(db_url, executor_id))
    DBOS.launch()
    if barrier != DUPLICATE_NAME_BARRIER:
        install_hooks(dbos, barrier)
    with SetWorkflowID(wfid):
        DBOS.start_workflow(target)
    time.sleep(30)  # a hook or the billing step SIGKILLs this process first


def recovery(barrier: str, db_url: str, ledger: str, intent: str, wfid: str, executor_id: str,
             modules_dir: str) -> dict[str, object]:
    global LEDGER, INTENT
    LEDGER = ledger  # a re-run step crosses the same ledger; only the crash is gone
    INTENT = intent
    os.environ["CP_LEDGER"] = ledger
    os.environ["CP_INTENT"] = intent
    os.environ["CP_CRASH"] = "0"
    winner = ""
    if barrier == DUPLICATE_NAME_BARRIER:
        _duplicate_modules(modules_dir)
        # Registered last, so it wins the name `process` in this process's registry.
        importlib.import_module(SHIPPING_MODULE)
        from dbos import _dbos as dbos_internal

        registry = dbos_internal._dbos_global_registry
        assert registry is not None
        winner = str(registry.workflow_info_map["process"].__module__)
    DBOS(config=_config(db_url, executor_id))
    DBOS.launch()
    result: object = DBOS.retrieve_workflow(wfid).get_result()
    return {"result": str(result), "registry_winner": winner}
