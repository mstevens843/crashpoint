from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

SUBJECT = r"""
from __future__ import annotations

import os
import signal
import sqlite3
import sys
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph


class S(TypedDict):
    done: bool


seen_first_put = False


class CrashBeforeFirstPut(SqliteSaver):
    def put(self, *args: Any, **kwargs: Any) -> Any:
        global seen_first_put
        if not seen_first_put:
            seen_first_put = True
            os.kill(os.getpid(), signal.SIGKILL)
        return super().put(*args, **kwargs)


def node(state: S) -> S:
    with open(sys.argv[2], "a", encoding="utf-8") as f:
        f.write("effect\n")
    return {"done": True}


graph = StateGraph(S)
graph.add_node("node", node)
graph.add_edge(START, "node")
graph.add_edge("node", END)

conn = sqlite3.connect(sys.argv[1], check_same_thread=False)
saver = CrashBeforeFirstPut(conn)
saver.setup()
app = graph.compile(checkpointer=saver)
app.invoke({"done": False}, {"configurable": {"thread_id": "accepted-run"}}, durability="sync")
"""

RECOVERY = r"""
from __future__ import annotations

import sqlite3
import sys
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph


class S(TypedDict):
    done: bool


def node(state: S) -> S:
    with open(sys.argv[2], "a", encoding="utf-8") as f:
        f.write("effect\n")
    return {"done": True}


graph = StateGraph(S)
graph.add_node("node", node)
graph.add_edge(START, "node")
graph.add_edge("node", END)

conn = sqlite3.connect(sys.argv[1], check_same_thread=False)
saver = SqliteSaver(conn)
saver.setup()
app = graph.compile(checkpointer=saver)

try:
    out = app.invoke(None, {"configurable": {"thread_id": "accepted-run"}}, durability="sync")
    print(f"recovery returned: {out}")
except Exception as exc:
    print(f"recovery raised: {type(exc).__name__}: {exc}")
"""


def checkpoint_count(db: Path) -> int | str:
    try:
        with sqlite3.connect(db) as conn:
            return conn.execute("select count(*) from checkpoints").fetchone()[0]
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    db = root / "checkpoints.sqlite"
    effects = root / "effects.txt"
    subject = root / "subject.py"
    recovery = root / "recovery.py"
    subject.write_text(textwrap.dedent(SUBJECT), encoding="utf-8")
    recovery.write_text(textwrap.dedent(RECOVERY), encoding="utf-8")

    crashed = subprocess.run(
        [sys.executable, str(subject), str(db), str(effects)],
        text=True,
        capture_output=True,
    )
    print(f"subject exit code: {crashed.returncode}")

    recovered = subprocess.run(
        [sys.executable, str(recovery), str(db), str(effects)],
        text=True,
        capture_output=True,
    )
    print(recovered.stdout.strip())

    effect_count = effects.read_text(encoding="utf-8").count("effect") if effects.exists() else 0
    print(f"effect count: {effect_count}")
    print(f"durable checkpoints: {checkpoint_count(db)}")
