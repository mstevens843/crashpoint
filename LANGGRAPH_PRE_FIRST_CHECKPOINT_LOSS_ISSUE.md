# Crash before first durable checkpoint can silently drop an accepted run with no durable failure record

### Related Issues / PRs

Related but separate from #8039 and #8753.

This is also adjacent to #5672, but not the same case: #5672 is about cancellation losing streamed
state after a run has begun streaming. This issue is about process death before the first durable
checkpoint exists.

### Reproduction Steps / Example Code (Python)

Save this as `repro_pre_first_checkpoint_loss.py` and run it with LangGraph plus the sqlite
checkpointer installed:

```bash
python -m pip install langgraph langgraph-checkpoint-sqlite
python repro_pre_first_checkpoint_loss.py
```

```python
from __future__ import annotations

import os
import signal
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
```

On my machine this prints:

```text
subject exit code: -9
recovery raised: EmptyInputError: Received no input for __start__
effect count: 0
durable checkpoints: 0
```

### Error Message and Stack Trace (if applicable)

Fresh-process recovery raises:

```text
EmptyInputError: Received no input for __start__
```

### Description

A LangGraph run that dies before its first durable checkpoint leaves recovery with nothing to
resume. That part is expected mechanically: if nothing was persisted, there is no state to replay.

The issue is the visibility boundary. For fire-and-forget / background invocation shapes, the run
can be accepted by the caller's system, die before the first checkpoint, and then leave no durable
record that the run ever existed or failed. A later recovery attempt has no checkpoint and raises
`EmptyInputError`, but a system that is not synchronously waiting on the killed process has no
durable failure marker to observe.

This came out while calibrating crashpoint's LangGraph #8039 probe. The original "before effect"
crash point sometimes landed before LangGraph's first durable checkpoint. Those runs recovered as
`LOST`: zero effects crossed, and fresh-process recovery had nothing to resume. I then refined
crashpoint's b0 barrier to crash after the entry checkpoint was durable, because this pre-checkpoint
loss is a separate failure mode from #8039's `put_writes` / `put` ordering race.

Recorded here:
https://github.com/mstevens843/crashpoint/blob/main/results/03-langgraph-and-controls.md

Crashpoint note:

> a crash before LangGraph's FIRST durable checkpoint leaves recovery nothing to resume, so the whole
> workflow is silently dropped

### Expected Behavior

One of these should be available or documented clearly:

- a durable "accepted / started" record before user node execution;
- a durable failed/aborted marker when recovery sees a known run with no resumable checkpoint;
- or explicit documentation that callers must provide their own acceptance ledger if they need
  fire-and-forget run admission to be durable.

### Actual Behavior

If the process dies before the first checkpoint is persisted:

- recovery has no checkpoint for the thread;
- `invoke(None, config, durability="sync")` raises `EmptyInputError: Received no input for
  __start__`;
- no user effect crosses;
- and, absent an external acceptance ledger, there is no durable record that the run was ever
  accepted.

### Why This Matters

This is a different shape from duplicate external effects. The duplicate case teaches the operator
something happened twice. This case can teach nothing happened at all: a background run disappears
before the runtime has a durable record for it.

### System Info

Observed locally with:

```text
OS: macOS 15.6 arm64
Python: 3.12.13
langgraph: 1.2.11
langgraph-checkpoint-sqlite: 3.1.1
langchain-core: 1.6.1
langgraph-sdk: 0.4.3
```
