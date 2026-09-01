"""LangGraph crash points outside the shared b0/b1/b2 matrix.

The main matrix deliberately defines b0 after the entry checkpoint is durable. This module measures
the separate pre-first-checkpoint case: process death before any checkpoint row exists. That is not
a b0 replacement and not part of the cross-runtime matrix; it has its own predicted rule and
evidence.
"""

from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

from ..canonical import receipt
from ..ledger.daemon import execute
from ..ledger.oracle import classify
from ..model.layers import Outcome
from .ledger_process import LedgerDaemon, LedgerHandle
from .wilson import wilson

HiddenBarrierId = Literal["lg_pre_first_checkpoint"]

_PAYLOAD = {"amount": 100, "to": "acct-attacker"}
_THREAD_ID = "accepted-run"
_PREDICTED: dict[HiddenBarrierId, Outcome] = {
    "lg_pre_first_checkpoint": Outcome.LOST,
}
_FIRST_PUT_SEEN = False


class _S(TypedDict):
    done: bool


@dataclass(frozen=True)
class HiddenTrial:
    barrier: HiddenBarrierId
    predicted: Outcome
    observed: Outcome
    subject_returncode: int
    recovery_returncode: int
    recovery_error_type: str
    recovery_error_message: str
    effect_count: int
    durable_checkpoints: int | str


def checkpoint_count(db: Path) -> int | str:
    try:
        with sqlite3.connect(db) as conn:
            row = conn.execute("select count(*) from checkpoints").fetchone()
            return int(row[0]) if row is not None else 0
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def _build_app(checkpoint: str, ledger: str, intent: str, crash_before_first_put: bool) -> Any:
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph

    class CrashBeforeFirstPut(SqliteSaver):
        def put(self, *args: Any, **kwargs: Any) -> Any:
            global _FIRST_PUT_SEEN
            if crash_before_first_put and not _FIRST_PUT_SEEN:
                _FIRST_PUT_SEEN = True
                import os

                os.kill(os.getpid(), signal.SIGKILL)
            return super().put(*args, **kwargs)

    def node(_state: _S) -> _S:
        execute(ledger, intent, None, _PAYLOAD)
        return {"done": True}

    graph: Any = StateGraph(_S)
    graph.add_node("node", node)
    graph.add_edge(START, "node")
    graph.add_edge("node", END)

    conn = sqlite3.connect(checkpoint, check_same_thread=False)
    saver = CrashBeforeFirstPut(conn) if crash_before_first_put else SqliteSaver(conn)
    saver.setup()
    return graph.compile(checkpointer=saver)


def subject(checkpoint: str, ledger: str, intent: str) -> int:
    global _FIRST_PUT_SEEN
    _FIRST_PUT_SEEN = False
    app = _build_app(checkpoint, ledger, intent, crash_before_first_put=True)
    cfg = {"configurable": {"thread_id": _THREAD_ID}}
    app.invoke({"done": False}, cfg, durability="sync")
    return 0


def recovery(checkpoint: str, ledger: str, intent: str) -> int:
    app = _build_app(checkpoint, ledger, intent, crash_before_first_put=False)
    cfg = {"configurable": {"thread_id": _THREAD_ID}}
    result: dict[str, object]
    try:
        out = app.invoke(None, cfg, durability="sync")
        result = {"ok": True, "returned": out}
    except Exception as exc:
        result = {"ok": False, "error_type": type(exc).__name__, "message": str(exc)}
    print(json.dumps(result, sort_keys=True))
    return 0


def _run_hidden_process(
    args: list[str], expected_returncode: int, timeout: float
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != expected_returncode:
        raise RuntimeError(
            "\n".join(
                [
                    "hidden LangGraph subprocess failed",
                    f"expected={expected_returncode} actual={proc.returncode}",
                    f"argv={' '.join(args)}",
                    f"stdout={proc.stdout.strip() or '<empty>'}",
                    f"stderr={proc.stderr.strip() or '<empty>'}",
                ]
            )
        )
    return proc


def run_trial(ledger: LedgerHandle, root: Path, index: int, timeout: float = 30.0) -> HiddenTrial:
    barrier: HiddenBarrierId = "lg_pre_first_checkpoint"
    predicted = _PREDICTED[barrier]
    intent = f"{_THREAD_ID}-{index}"
    checkpoint = root / f"checkpoint-{index}.sqlite"
    ledger.reset()

    subject_proc = _run_hidden_process(
        [
            sys.executable,
            "-m",
            "crashpoint.harness.langgraph_hidden",
            "--subject",
            "--checkpoint",
            str(checkpoint),
            "--ledger",
            ledger.invoke_path,
            "--intent",
            intent,
        ],
        -int(signal.SIGKILL),
        timeout,
    )
    recovery_proc = _run_hidden_process(
        [
            sys.executable,
            "-m",
            "crashpoint.harness.langgraph_hidden",
            "--recovery",
            "--checkpoint",
            str(checkpoint),
            "--ledger",
            ledger.invoke_path,
            "--intent",
            intent,
        ],
        0,
        timeout,
    )

    try:
        recovery_report = json.loads(recovery_proc.stdout)
    except json.JSONDecodeError:
        recovery_report = {"ok": False, "error_type": "BadRecoveryOutput", "message": ""}

    ledger.seal()
    dump = ledger.dump()
    observed = classify(intent, dump, Path(ledger.store_path))
    side_effects = dump.get("side_effects", {})
    effect_count = int(side_effects.get(intent, 0)) if isinstance(side_effects, dict) else -1
    return HiddenTrial(
        barrier=barrier,
        predicted=predicted,
        observed=observed,
        subject_returncode=subject_proc.returncode,
        recovery_returncode=recovery_proc.returncode,
        recovery_error_type=str(recovery_report.get("error_type", "")),
        recovery_error_message=str(recovery_report.get("message", "")),
        effect_count=effect_count,
        durable_checkpoints=checkpoint_count(checkpoint),
    )


def run(k: int, name: str) -> dict[str, object]:
    trials: list[HiddenTrial] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerDaemon(Path(tmp) / "ledger") as ledger:
        root = Path(tmp)
        for i in range(k):
            trials.append(run_trial(ledger, root, i))

    counts: Counter[str] = Counter(t.observed.value for t in trials)
    modal, modal_n = counts.most_common(1)[0]
    predicted = _PREDICTED["lg_pre_first_checkpoint"].value
    record: dict[str, object] = {
        "name": name,
        "runtime": "langgraph",
        "barrier": "lg_pre_first_checkpoint",
        "barrier_family": "hidden",
        "prediction_rule": (
            "death before the first durable checkpoint leaves no resumable state; a recovery "
            "invoke with no input raises and no external effect crosses"
        ),
        "k": k,
        "counts": dict(counts),
        "modal": modal,
        "modal_rate": round(modal_n / k, 4),
        "wilson95": list(wilson(modal_n, k)),
        "predicted": predicted,
        "agrees": modal == predicted,
        "trials": [
            {
                "barrier": t.barrier,
                "predicted": t.predicted.value,
                "observed": t.observed.value,
                "subject_returncode": t.subject_returncode,
                "recovery_returncode": t.recovery_returncode,
                "recovery_error_type": t.recovery_error_type,
                "recovery_error_message": t.recovery_error_message,
                "effect_count": t.effect_count,
                "durable_checkpoints": t.durable_checkpoints,
            }
            for t in trials
        ],
    }
    record["receipt"] = receipt(record)
    return record


def render(record: dict[str, object]) -> str:
    return "\n".join(
        [
            f"langgraph hidden crash-point evidence - k={record['k']}",
            f"barrier: {record['barrier']}",
            f"predicted: {record['predicted']}",
            f"observed modal: {record['modal']} @ {record['modal_rate']}",
            f"counts: {record['counts']}",
            f"disagreement: {not record['agrees']}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--name", default="langgraph_hidden")
    ap.add_argument("--subject", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--recovery", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--checkpoint", help=argparse.SUPPRESS)
    ap.add_argument("--ledger", help=argparse.SUPPRESS)
    ap.add_argument("--intent", default=_THREAD_ID, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.subject or args.recovery:
        if args.checkpoint is None or args.ledger is None:
            ap.error("--subject/--recovery require --checkpoint and --ledger")
        if args.subject:
            return subject(args.checkpoint, args.ledger, args.intent)
        return recovery(args.checkpoint, args.ledger, args.intent)

    record = run(args.k, args.name)
    print(render(record))
    out = Path(__file__).resolve().parents[3] / "evidence" / f"{args.name}.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True))
    print(f"\nreceipt: {record['receipt']}\nwrote {out}")
    return 0 if record["agrees"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
