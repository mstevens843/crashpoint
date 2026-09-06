"""Application-level containment for LangGraph's pre-first-checkpoint admission gap.

This is not a LangGraph fix. Both arms die immediately before the first checkpointer ``put`` and
therefore leave LangGraph with no durable state. The treatment arm differs only in one caller-owned
fact: before dispatch, an external SQLite event ledger commits the run identity and original input.
Recovery can then distinguish an admitted run from an unknown thread and explicitly replay it.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from ..canonical import receipt
from ..ledger.daemon import execute
from .langgraph_hidden import checkpoint_count
from .ledger_process import LedgerDaemon, LedgerHandle
from .wilson import wilson

AdmissionArm = Literal["runtime_only", "external_admission_ledger"]
ADMISSION_ARMS: tuple[AdmissionArm, ...] = (
    "runtime_only",
    "external_admission_ledger",
)

_THREAD_ID = "accepted-run"
_ORIGINAL_INPUT: dict[str, object] = {"done": False}
_PAYLOAD = {"amount": 100, "to": "acct-attacker"}
_STATE = {"first_put_seen": False}


class _S(TypedDict):
    done: bool


@dataclass(frozen=True)
class AdmissionSnapshot:
    accepted: bool
    original_input: dict[str, object] | None
    events: tuple[str, ...]

    @property
    def status(self) -> str:
        return self.events[-1] if self.events else "missing"


@dataclass(frozen=True)
class AdmissionTrial:
    arm: AdmissionArm
    agrees: bool
    decision: str
    subject_returncode: int
    recovery_returncode: int
    recovery_error_type: str
    recovery_error_message: str
    checkpoints_before_recovery: int | str
    checkpoints_after_recovery: int | str
    effects_before_recovery: int
    effects_after_recovery: int
    admission_before_recovery: AdmissionSnapshot
    admission_after_recovery: AdmissionSnapshot
    replayed: bool
    replay_result: dict[str, object] | None


def initialize_admission_ledger(path: Path) -> None:
    """Create the caller-owned admission ledger without claiming that a run was accepted."""
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            create table if not exists accepted_runs (
                thread_id text primary key,
                input_json text not null
            );
            create table if not exists admission_events (
                sequence integer primary key autoincrement,
                thread_id text not null,
                event text not null check (event in ('accepted', 'recovery_required', 'completed')),
                foreign key (thread_id) references accepted_runs(thread_id)
            );
            """
        )


def accept_run(path: Path, thread_id: str, original_input: dict[str, object]) -> None:
    """Durably bind acceptance and replay input before the runtime is dispatched."""
    encoded = json.dumps(original_input, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(path) as conn:
        conn.execute("pragma foreign_keys = on")
        conn.execute(
            "insert into accepted_runs(thread_id, input_json) values (?, ?)",
            (thread_id, encoded),
        )
        conn.execute(
            "insert into admission_events(thread_id, event) values (?, 'accepted')",
            (thread_id,),
        )


def append_admission_event(path: Path, thread_id: str, event: str) -> None:
    if event not in {"recovery_required", "completed"}:
        raise ValueError(f"invalid post-acceptance event: {event}")
    with sqlite3.connect(path) as conn:
        conn.execute("pragma foreign_keys = on")
        conn.execute(
            "insert into admission_events(thread_id, event) values (?, ?)",
            (thread_id, event),
        )


def read_admission(path: Path, thread_id: str) -> AdmissionSnapshot:
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "select input_json from accepted_runs where thread_id = ?", (thread_id,)
        ).fetchone()
        events = tuple(
            str(event_row[0])
            for event_row in conn.execute(
                "select event from admission_events where thread_id = ? order by sequence",
                (thread_id,),
            ).fetchall()
        )
    if row is None:
        return AdmissionSnapshot(False, None, events)
    decoded = json.loads(str(row[0]))
    original_input = cast(dict[str, object], decoded) if isinstance(decoded, dict) else None
    return AdmissionSnapshot(True, original_input, events)


def _effect_count(dump: dict[str, object], intent: str) -> int:
    side_effects = dump.get("side_effects", {})
    return int(side_effects.get(intent, 0)) if isinstance(side_effects, dict) else -1


def _reset_state() -> None:
    _STATE["first_put_seen"] = False


def _build_app(
    checkpoint: str,
    effect_ledger: str,
    intent: str,
    *,
    crash_enabled: bool,
) -> Any:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph

    class CrashBeforeFirstPut(SqliteSaver):
        def put(self, *args: Any, **kwargs: Any) -> Any:
            if crash_enabled and not _STATE["first_put_seen"]:
                _STATE["first_put_seen"] = True
                os.kill(os.getpid(), signal.SIGKILL)
            return super().put(*args, **kwargs)

    def node(_state: _S) -> _S:
        execute(effect_ledger, intent, None, _PAYLOAD)
        return {"done": True}

    graph: Any = StateGraph(_S)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)

    conn = sqlite3.connect(checkpoint, check_same_thread=False)
    saver = CrashBeforeFirstPut(conn)
    saver.setup()
    return graph.compile(checkpointer=saver)


def subject(checkpoint: str, effect_ledger: str, intent: str) -> int:
    _reset_state()
    app = _build_app(checkpoint, effect_ledger, intent, crash_enabled=True)
    config = {"configurable": {"thread_id": _THREAD_ID}}
    app.invoke(cast(_S, _ORIGINAL_INPUT), config, durability="sync")
    return 0


def recovery(
    checkpoint: str,
    effect_ledger: str,
    admission_db: str,
    intent: str,
) -> int:
    _reset_state()
    config = {"configurable": {"thread_id": _THREAD_ID}}
    app = _build_app(checkpoint, effect_ledger, intent, crash_enabled=False)
    runtime_report: dict[str, object]
    try:
        returned = app.invoke(None, config, durability="sync")
        runtime_report = {"ok": True, "returned": returned}
    except Exception as exc:
        runtime_report = {
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }

    admission = read_admission(Path(admission_db), _THREAD_ID)
    decision = "unverified"
    replayed = False
    replay_result: dict[str, object] | None = None
    if admission.accepted and admission.original_input is not None:
        decision = "admitted_runtime_evidence_missing"
        append_admission_event(Path(admission_db), _THREAD_ID, "recovery_required")
        replayed_raw = app.invoke(
            cast(_S, admission.original_input), config, durability="sync"
        )
        replay_result = cast(dict[str, object], replayed_raw)
        replayed = True
        append_admission_event(Path(admission_db), _THREAD_ID, "completed")

    print(
        json.dumps(
            {
                "runtime": runtime_report,
                "decision": decision,
                "replayed": replayed,
                "replay_result": replay_result,
            },
            sort_keys=True,
        )
    )
    return 0


def _run_process(
    args: list[str], expected_returncode: int, timeout: float
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != expected_returncode:
        raise RuntimeError(
            "\n".join(
                [
                    "LangGraph admission subprocess failed",
                    f"expected={expected_returncode} actual={proc.returncode}",
                    f"argv={' '.join(args)}",
                    f"stdout={proc.stdout.strip() or '<empty>'}",
                    f"stderr={proc.stderr.strip() or '<empty>'}",
                ]
            )
        )
    return proc


def run_trial(
    effect_ledger: LedgerHandle,
    root: Path,
    index: int,
    arm: AdmissionArm,
    timeout: float = 30.0,
) -> AdmissionTrial:
    intent = f"{_THREAD_ID}-{arm}-{index}"
    checkpoint = root / f"checkpoint-{arm}-{index}.sqlite"
    admission_db = root / f"admission-{arm}-{index}.sqlite"
    initialize_admission_ledger(admission_db)
    if arm == "external_admission_ledger":
        accept_run(admission_db, _THREAD_ID, _ORIGINAL_INPUT)

    effect_ledger.reset()
    subject_proc = _run_process(
        [
            sys.executable,
            "-m",
            "crashpoint.harness.langgraph_admission",
            "--subject",
            "--checkpoint",
            str(checkpoint),
            "--effect-ledger",
            effect_ledger.invoke_path,
            "--intent",
            intent,
        ],
        -int(signal.SIGKILL),
        timeout,
    )

    checkpoints_before = checkpoint_count(checkpoint)
    effects_before = _effect_count(effect_ledger.dump(), intent)
    admission_before = read_admission(admission_db, _THREAD_ID)

    recovery_proc = _run_process(
        [
            sys.executable,
            "-m",
            "crashpoint.harness.langgraph_admission",
            "--recovery",
            "--checkpoint",
            str(checkpoint),
            "--effect-ledger",
            effect_ledger.invoke_path,
            "--admission-db",
            str(admission_db),
            "--intent",
            intent,
        ],
        0,
        timeout,
    )
    try:
        report = json.loads(recovery_proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid admission recovery report: {recovery_proc.stdout!r}") from exc
    if not isinstance(report, dict):
        raise RuntimeError(f"admission recovery report is not an object: {report!r}")

    runtime = report.get("runtime", {})
    runtime_report = runtime if isinstance(runtime, dict) else {}
    decision = str(report.get("decision", ""))
    replayed = bool(report.get("replayed", False))
    raw_replay_result = report.get("replay_result")
    replay_result = (
        cast(dict[str, object], raw_replay_result)
        if isinstance(raw_replay_result, dict)
        else None
    )
    checkpoints_after = checkpoint_count(checkpoint)
    admission_after = read_admission(admission_db, _THREAD_ID)
    effect_ledger.seal()
    effects_after = _effect_count(effect_ledger.dump(), intent)

    common = (
        subject_proc.returncode == -int(signal.SIGKILL)
        and checkpoints_before == 0
        and effects_before == 0
        and runtime_report.get("error_type") == "EmptyInputError"
    )
    if arm == "runtime_only":
        agrees = (
            common
            and not admission_before.accepted
            and decision == "unverified"
            and not replayed
            and checkpoints_after == 0
            and effects_after == 0
            and admission_after.status == "missing"
        )
    else:
        agrees = (
            common
            and admission_before.status == "accepted"
            and decision == "admitted_runtime_evidence_missing"
            and replayed
            and isinstance(checkpoints_after, int)
            and checkpoints_after > 0
            and effects_after == 1
            and admission_after.events == ("accepted", "recovery_required", "completed")
            and replay_result == {"done": True}
        )

    return AdmissionTrial(
        arm=arm,
        agrees=agrees,
        decision=decision,
        subject_returncode=subject_proc.returncode,
        recovery_returncode=recovery_proc.returncode,
        recovery_error_type=str(runtime_report.get("error_type", "")),
        recovery_error_message=str(runtime_report.get("message", "")),
        checkpoints_before_recovery=checkpoints_before,
        checkpoints_after_recovery=checkpoints_after,
        effects_before_recovery=effects_before,
        effects_after_recovery=effects_after,
        admission_before_recovery=admission_before,
        admission_after_recovery=admission_after,
        replayed=replayed,
        replay_result=replay_result,
    )


def _snapshot_json(snapshot: AdmissionSnapshot) -> dict[str, object]:
    return {
        "accepted": snapshot.accepted,
        "status": snapshot.status,
        "events": list(snapshot.events),
        "original_input": snapshot.original_input,
    }


def run(k: int, name: str) -> dict[str, object]:
    trials: list[AdmissionTrial] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerDaemon(Path(tmp) / "effect-ledger") as ledger:
        root = Path(tmp)
        for arm in ADMISSION_ARMS:
            for index in range(k):
                trials.append(run_trial(ledger, root, index, arm))

    arms: dict[str, object] = {}
    for arm in ADMISSION_ARMS:
        arm_trials = [trial for trial in trials if trial.arm == arm]
        passing = sum(trial.agrees for trial in arm_trials)
        decisions = Counter(trial.decision for trial in arm_trials)
        arms[arm] = {
            "k": k,
            "passing": passing,
            "pass_rate": round(passing / k, 4),
            "wilson95": list(wilson(passing, k)),
            "decisions": dict(decisions),
        }

    record: dict[str, object] = {
        "name": name,
        "runtime": "langgraph",
        "experiment_family": "application_admission_containment",
        "framework_fix": False,
        "claim": (
            "a caller-owned acceptance record committed before dispatch distinguishes an admitted "
            "run from an unknown thread and supplies the original input for explicit replay"
        ),
        "limitation": (
            "this does not make arbitrary external effects exactly-once; safe replay still needs "
            "authorization, stable idempotency, attempt records, and destination reconciliation"
        ),
        "k_per_arm": k,
        "arms": arms,
        "all_agree": all(trial.agrees for trial in trials),
        "trials": [
            {
                "arm": trial.arm,
                "agrees": trial.agrees,
                "decision": trial.decision,
                "subject_returncode": trial.subject_returncode,
                "recovery_returncode": trial.recovery_returncode,
                "recovery_error_type": trial.recovery_error_type,
                "recovery_error_message": trial.recovery_error_message,
                "checkpoints_before_recovery": trial.checkpoints_before_recovery,
                "checkpoints_after_recovery": trial.checkpoints_after_recovery,
                "effects_before_recovery": trial.effects_before_recovery,
                "effects_after_recovery": trial.effects_after_recovery,
                "admission_before_recovery": _snapshot_json(trial.admission_before_recovery),
                "admission_after_recovery": _snapshot_json(trial.admission_after_recovery),
                "replayed": trial.replayed,
                "replay_result": trial.replay_result,
            }
            for trial in trials
        ],
    }
    record["receipt"] = receipt(record)
    return record


def render(record: dict[str, object]) -> str:
    arms = cast(dict[str, dict[str, object]], record["arms"])
    lines = [
        f"LangGraph admission containment evidence - k={record['k_per_arm']} per arm",
        f"all trials agree: {record['all_agree']}",
    ]
    for arm in ADMISSION_ARMS:
        arm_record = arms[arm]
        lines.append(
            f"{arm}: {arm_record['passing']}/{arm_record['k']} pass; "
            f"decisions={arm_record['decisions']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--name", default="langgraph_admission")
    parser.add_argument("--subject", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recovery", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--checkpoint", help=argparse.SUPPRESS)
    parser.add_argument("--effect-ledger", help=argparse.SUPPRESS)
    parser.add_argument("--admission-db", help=argparse.SUPPRESS)
    parser.add_argument("--intent", default=_THREAD_ID, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.subject or args.recovery:
        if args.checkpoint is None or args.effect_ledger is None:
            parser.error("--subject/--recovery require --checkpoint and --effect-ledger")
        if args.subject:
            return subject(args.checkpoint, args.effect_ledger, args.intent)
        if args.admission_db is None:
            parser.error("--recovery requires --admission-db")
        return recovery(args.checkpoint, args.effect_ledger, args.admission_db, args.intent)

    if args.k < 1:
        parser.error("--k must be positive")
    record = run(args.k, args.name)
    print(render(record))
    output = Path(__file__).resolve().parents[3] / "evidence" / f"{args.name}.json"
    output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"\nreceipt: {record['receipt']}\nwrote {output}")
    return 0 if record["all_agree"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
