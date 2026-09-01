"""DBOS crash points outside the shared b0/b1/b2 matrix.

The main matrix crashes inside the step body (b0/b1) or inside a later step (b2). This module
measures four DBOS-only edges on the system database's own persistence boundaries. Each has its
own predicted rule and receipted evidence and stays disjoint from b0/b1/b2:

  dbos_step_output_uncommitted
      The effect step's output row has been INSERTed into ``dbos.operation_outputs`` on the open
      transaction, and the process dies before COMMIT. Predicted DUPLICATED: Postgres discards the
      uncommitted row when the connection drops, so recovery sees no output and re-runs the step.

  dbos_step_output_committed_before_resume
      The effect step's output has committed, and the process dies before the workflow function
      resumes (DBOS's own ``DEBUG_TRIGGER_STEP_COMMIT`` seam). Predicted EXACTLY_ONCE: recovery
      finds the committed output and replays it instead of re-running the step.

  dbos_workflow_outcome_uncommitted
      Both steps have committed, the final UPDATE of ``workflow_status`` to SUCCESS has executed
      on the open transaction, and the process dies before COMMIT. Predicted EXACTLY_ONCE: the
      workflow is still PENDING with every step output recorded, so recovery replays both steps
      and completes without re-running either.

  dbos_duplicate_workflow_name_recovery
      Two modules register a workflow named ``process``, each with one step that performs a
      module-tagged external effect. The billing step crashes after its effect and before its
      output commits (the b1 point). The recovery process imports the shipping module last, so its
      registry maps ``process`` to the shipping function. Predicted DIVERGED: DBOS resumes the
      workflow by stored name, dispatches to a different function body, and the second crossing is
      a different action.

All four use a NAIVE effect: the question is whether the framework re-runs the unit at that edge,
not whether an idempotent boundary would hide it. Beside the ledger outcome, each trial records
the ``workflow_status`` row and the ``operation_outputs`` names before and after recovery, read
by the harness with SQLAlchemy, so the mechanism is evidence too.

Run against the documented Postgres container:
    docker start cp-postgres
    export CRASHPOINT_DBOS_URL=postgresql://cpuser:dbos@localhost:5433/cpdbos
    uv run --extra dbos python -m crashpoint.harness.dbos_hidden --k 30 \\
        --barrier dbos_step_output_uncommitted --name dbos_hidden_uncommitted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ..canonical import receipt
from ..ledger.oracle import classify
from ..model.layers import Outcome
from .ledger_process import LedgerDaemon, LedgerHandle
from .wilson import wilson

HiddenBarrierId = Literal[
    "dbos_step_output_uncommitted",
    "dbos_step_output_committed_before_resume",
    "dbos_workflow_outcome_uncommitted",
    "dbos_duplicate_workflow_name_recovery",
]
HIDDEN_BARRIERS: tuple[HiddenBarrierId, ...] = (
    "dbos_step_output_uncommitted",
    "dbos_step_output_committed_before_resume",
    "dbos_workflow_outcome_uncommitted",
    "dbos_duplicate_workflow_name_recovery",
)
_DEFAULT_URL = "postgresql://cpuser:dbos@localhost:5433/cpdbos"
_EXPECTED_CRASH = -int(signal.SIGKILL)
_PREDICTED: dict[HiddenBarrierId, Outcome] = {
    "dbos_step_output_uncommitted": Outcome.DUPLICATED,
    "dbos_step_output_committed_before_resume": Outcome.EXACTLY_ONCE,
    "dbos_workflow_outcome_uncommitted": Outcome.EXACTLY_ONCE,
    "dbos_duplicate_workflow_name_recovery": Outcome.DIVERGED,
}
_PREDICTION_RULES: dict[HiddenBarrierId, str] = {
    "dbos_step_output_uncommitted": (
        "the step output INSERT ran on an uncommitted transaction; Postgres discards it when the "
        "connection dies, so recovery finds no output for the step and re-runs it, and the naive "
        "effect crosses twice"
    ),
    "dbos_step_output_committed_before_resume": (
        "the step output is committed before the workflow resumes; recovery finds the recorded "
        "output and replays it without re-running the step, so the effect crosses once"
    ),
    "dbos_workflow_outcome_uncommitted": (
        "every step output is committed but the SUCCESS update never commits; recovery replays "
        "both recorded outputs and completes the workflow without re-running a step, so the "
        "effect crosses once"
    ),
    "dbos_duplicate_workflow_name_recovery": (
        "the workflow_status row stores only the function name; when two registered workflows "
        "share it, recovery dispatches the pending workflow to whichever registration won in the "
        "recovery process, so the uncommitted step is re-run by a different function body and "
        "the two crossings are different actions"
    ),
}
_DB_RULES: dict[HiddenBarrierId, str] = {
    "dbos_step_output_uncommitted": (
        "before recovery: PENDING with no operation_outputs; after: SUCCESS with effect_step and "
        "sentinel_step recorded"
    ),
    "dbos_step_output_committed_before_resume": (
        "before recovery: PENDING with only effect_step recorded; after: SUCCESS with both steps"
    ),
    "dbos_workflow_outcome_uncommitted": (
        "before recovery: PENDING with both steps recorded; after: SUCCESS with the same two rows"
    ),
    "dbos_duplicate_workflow_name_recovery": (
        "before recovery: PENDING with no operation_outputs; after: SUCCESS with shipping_charge "
        "recorded, registry winner cp_shipping, result 'shipping'"
    ),
}

_BILLING_SOURCE = '''
from __future__ import annotations

import os
import signal

from dbos import DBOS

from crashpoint.ledger.daemon import execute


@DBOS.step()
def billing_charge() -> str:
    execute(
        os.environ["CP_LEDGER"],
        os.environ["CP_INTENT"],
        None,
        {"amount": 100, "to": "acct-attacker", "module": "billing"},
    )
    if os.environ.get("CP_CRASH") == "1":
        os.kill(os.getpid(), signal.SIGKILL)  # after the effect, before the output commits
    return "billing"


@DBOS.workflow()
def process() -> str:
    return billing_charge()
'''
_SHIPPING_SOURCE = '''
from __future__ import annotations

import os

from dbos import DBOS

from crashpoint.ledger.daemon import execute


@DBOS.step()
def shipping_charge() -> str:
    execute(
        os.environ["CP_LEDGER"],
        os.environ["CP_INTENT"],
        None,
        {"amount": 100, "to": "acct-attacker", "module": "shipping"},
    )
    return "shipping"


@DBOS.workflow()
def process() -> str:
    return shipping_charge()
'''


@dataclass(frozen=True)
class DbSnapshot:
    status: str
    name: str
    recovery_attempts: int
    outputs: tuple[str, ...]  # operation_outputs function names in function_id order

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "name": self.name,
            "recovery_attempts": self.recovery_attempts,
            "outputs": list(self.outputs),
        }


@dataclass(frozen=True)
class HiddenTrial:
    barrier: HiddenBarrierId
    predicted: Outcome
    observed: Outcome
    subject_returncode: int
    recovery_returncode: int
    effect_count: int
    distinct_digests: int
    recovery_result: str
    registry_winner: str
    before: DbSnapshot
    after: DbSnapshot
    db_agrees: bool


def db_agrees(barrier: HiddenBarrierId, before: DbSnapshot, after: DbSnapshot,
              result: str, registry_winner: str) -> bool:
    """Does the system database show the mechanism the barrier's rule names?"""
    pending_before = before.status == "PENDING"
    success_after = after.status == "SUCCESS"
    both = ("effect_step", "sentinel_step")
    if barrier == "dbos_step_output_uncommitted":
        return pending_before and before.outputs == () and success_after and after.outputs == both
    if barrier == "dbos_step_output_committed_before_resume":
        return (
            pending_before
            and before.outputs == ("effect_step",)
            and success_after
            and after.outputs == both
        )
    if barrier == "dbos_workflow_outcome_uncommitted":
        return pending_before and before.outputs == both and success_after and after.outputs == both
    return (
        pending_before
        and before.outputs == ()
        and success_after
        and after.outputs == ("shipping_charge",)
        and registry_winner == "cp_shipping"
        and result == "shipping"
    )


def _sqlalchemy_url(db_url: str) -> str:
    if db_url.startswith("postgresql://"):
        return db_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return db_url


def db_snapshot(db_url: str, wfid: str) -> DbSnapshot:
    import sqlalchemy as sa

    engine = sa.create_engine(_sqlalchemy_url(db_url))
    try:
        with engine.begin() as conn:
            row = conn.execute(
                sa.text(
                    "select status, name, recovery_attempts from dbos.workflow_status "
                    "where workflow_uuid = :id"
                ),
                {"id": wfid},
            ).mappings().first()
            outputs = conn.execute(
                sa.text(
                    "select function_name from dbos.operation_outputs "
                    "where workflow_uuid = :id order by function_id"
                ),
                {"id": wfid},
            ).scalars().all()
    finally:
        engine.dispose()
    if row is None:
        return DbSnapshot("<missing>", "", 0, tuple(str(o) for o in outputs))
    return DbSnapshot(
        str(row["status"]),
        str(row["name"]),
        int(row["recovery_attempts"] or 0),
        tuple(str(o) for o in outputs),
    )


def write_duplicate_modules(modules_dir: Path) -> None:
    modules_dir.mkdir(parents=True, exist_ok=True)
    (modules_dir / "cp_billing.py").write_text(textwrap.dedent(_BILLING_SOURCE).lstrip())
    (modules_dir / "cp_shipping.py").write_text(textwrap.dedent(_SHIPPING_SOURCE).lstrip())


def _run_process(args: list[str], expected_returncode: int, timeout: float,
                 env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False, env=env
    )
    if proc.returncode != expected_returncode:
        raise RuntimeError(
            "\n".join(
                [
                    "hidden DBOS subprocess failed",
                    f"expected={expected_returncode} actual={proc.returncode}",
                    f"argv={' '.join(args)}",
                    f"stdout={proc.stdout.strip() or '<empty>'}",
                    f"stderr={proc.stderr.strip()[-2000:] or '<empty>'}",
                ]
            )
        )
    return proc


def run_trial(
    ledger: LedgerHandle,
    index: int,
    barrier: HiddenBarrierId,
    db_url: str,
    modules_dir: Path,
    timeout: float = 60.0,
) -> HiddenTrial:
    predicted = _PREDICTED[barrier]
    digest = hashlib.sha256(
        f"{barrier}:{index}:{os.getpid()}:{time.time_ns()}".encode()
    ).hexdigest()[:16]
    intent = f"{barrier}-{index}"
    wfid = f"cp-hidden-{digest}"
    executor_id = f"cp-hidden-exec-{digest}"
    ledger.reset()
    env = dict(os.environ)
    env.update({"CP_LEDGER": ledger.invoke_path, "CP_INTENT": intent})
    common = [
        sys.executable,
        "-m",
        "crashpoint.harness.dbos_hidden",
        "--barrier",
        barrier,
        "--db-url",
        db_url,
        "--ledger",
        ledger.invoke_path,
        "--intent",
        intent,
        "--wfid",
        wfid,
        "--executor-id",
        executor_id,
        "--modules-dir",
        str(modules_dir),
    ]
    subject_proc = _run_process([*common, "--subject"], _EXPECTED_CRASH, timeout, env)
    before = db_snapshot(db_url, wfid)
    recovery_proc = _run_process([*common, "--recovery"], 0, timeout, env)
    after = db_snapshot(db_url, wfid)
    try:
        report = json.loads(recovery_proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        report = {"result": "<bad recovery output>", "registry_winner": ""}
    result = str(report.get("result", "")) if isinstance(report, dict) else ""
    winner = str(report.get("registry_winner", "")) if isinstance(report, dict) else ""

    ledger.seal()
    dump = ledger.dump()
    observed = classify(intent, dump, Path(ledger.store_path))
    side_effects = dump.get("side_effects", {})
    effect_count = int(side_effects.get(intent, 0)) if isinstance(side_effects, dict) else -1
    digests_map = dump.get("effect_digests", {})
    digests = digests_map.get(intent, []) if isinstance(digests_map, dict) else []
    distinct = len(set(digests)) if isinstance(digests, list) else -1
    return HiddenTrial(
        barrier=barrier,
        predicted=predicted,
        observed=observed,
        subject_returncode=subject_proc.returncode,
        recovery_returncode=recovery_proc.returncode,
        effect_count=effect_count,
        distinct_digests=distinct,
        recovery_result=result,
        registry_winner=winner,
        before=before,
        after=after,
        db_agrees=db_agrees(barrier, before, after, result, winner),
    )


def run(k: int, name: str, barrier: HiddenBarrierId, db_url: str = _DEFAULT_URL,
        timeout: float = 60.0) -> dict[str, object]:
    trials: list[HiddenTrial] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerDaemon(Path(tmp) / "ledger") as ledger:
        modules_dir = Path(tmp) / "modules"
        write_duplicate_modules(modules_dir)
        for i in range(k):
            trials.append(run_trial(ledger, i, barrier, db_url, modules_dir, timeout))

    counts: Counter[str] = Counter(t.observed.value for t in trials)
    modal, modal_n = counts.most_common(1)[0]
    predicted = _PREDICTED[barrier].value
    record: dict[str, object] = {
        "name": name,
        "runtime": "dbos",
        "barrier": barrier,
        "barrier_family": "hidden",
        "prediction_rule": _PREDICTION_RULES[barrier],
        "db_rule": _DB_RULES[barrier],
        "k": k,
        "counts": dict(counts),
        "modal": modal,
        "modal_rate": round(modal_n / k, 4),
        "wilson95": list(wilson(modal_n, k)),
        "predicted": predicted,
        "agrees": modal == predicted,
        "db_agrees_count": sum(1 for t in trials if t.db_agrees),
        "trials": [
            {
                "barrier": t.barrier,
                "predicted": t.predicted.value,
                "observed": t.observed.value,
                "subject_returncode": t.subject_returncode,
                "recovery_returncode": t.recovery_returncode,
                "effect_count": t.effect_count,
                "distinct_digests": t.distinct_digests,
                "recovery_result": t.recovery_result,
                "registry_winner": t.registry_winner,
                "before_recovery": t.before.as_dict(),
                "after_recovery": t.after.as_dict(),
                "db_agrees": t.db_agrees,
            }
            for t in trials
        ],
    }
    record["receipt"] = receipt(record)
    return record


def render(record: dict[str, object]) -> str:
    return "\n".join(
        [
            f"dbos hidden crash-point evidence - k={record['k']}",
            f"barrier: {record['barrier']}",
            f"predicted: {record['predicted']}",
            f"observed modal: {record['modal']} @ {record['modal_rate']}",
            f"counts: {record['counts']}",
            f"db agrees: {record['db_agrees_count']}/{record['k']}",
            f"disagreement: {not record['agrees']}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--name", default="dbos_hidden")
    ap.add_argument("--barrier", choices=HIDDEN_BARRIERS, default=HIDDEN_BARRIERS[0])
    ap.add_argument("--db-url", default=os.environ.get("CRASHPOINT_DBOS_URL", _DEFAULT_URL))
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--subject", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--recovery", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--ledger", help=argparse.SUPPRESS)
    ap.add_argument("--intent", help=argparse.SUPPRESS)
    ap.add_argument("--wfid", help=argparse.SUPPRESS)
    ap.add_argument("--executor-id", help=argparse.SUPPRESS)
    ap.add_argument("--modules-dir", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    barrier = cast(HiddenBarrierId, args.barrier)

    if args.subject or args.recovery:
        if args.wfid is None or args.executor_id is None or args.modules_dir is None:
            ap.error("--subject/--recovery require --wfid, --executor-id, and --modules-dir")
        from . import dbos_hidden_runtime as rt

        if args.ledger is None or args.intent is None:
            ap.error("--subject/--recovery require --ledger and --intent")
        if args.subject:
            rt.subject(
                barrier, args.db_url, args.ledger, args.intent, args.wfid, args.executor_id,
                args.modules_dir,
            )
            return 1  # pragma: no cover - the subject SIGKILLs itself before this
        report = rt.recovery(
            barrier, args.db_url, args.ledger, args.intent, args.wfid, args.executor_id,
            args.modules_dir,
        )
        print(json.dumps(report, sort_keys=True))
        return 0

    record = run(args.k, args.name, barrier, args.db_url, args.timeout)
    print(render(record))
    out = Path(__file__).resolve().parents[3] / "evidence" / f"{args.name}.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True))
    print(f"\nreceipt: {record['receipt']}\nwrote {out}")
    return 0 if record["agrees"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
