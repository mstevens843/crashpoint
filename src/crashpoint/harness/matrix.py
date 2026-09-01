"""Run k crash-and-recover trials per (runtime, barrier) cell, beside the prediction.

For each cell it records the distribution of observed outcomes over k trials, the modal outcome, and
a Wilson interval on the modal fraction - so a deterministic cell reads ~1.0 and a racy one
(the LangGraph race) reads its real rate with error bars. Emits the observed matrix, the
predicted-vs-observed diff, and evidence/<name>.json with a canonical-JSON SHA-256 receipt.

`uv run python -m crashpoint.harness.matrix [--k 20] [--runtimes r_dup,r_lost,...]`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from ..canonical import receipt
from ..model.barriers import BARRIER_IDS
from ..model.layers import Outcome
from ..model.predict import PREDICTED
from ..model.runtimes import RUNTIME_IDS, RUNTIMES_BY_ID
from .ledger_process import LedgerDaemon, LedgerHandle
from .trial import run_trial
from .wilson import wilson

_SYM = {Outcome.EXACTLY_ONCE: "ONCE", Outcome.DUPLICATED: "DUP", Outcome.LOST: "LOST",
        Outcome.DIVERGED: "DIVERGE", Outcome.VOID: "VOID"}


def _dotenv_value_present(name: str) -> bool:
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if not env_path.exists():
        return False
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        if sep and key.strip() == name and value.strip().strip("'\""):
            return True
    return False


def _sampler_metadata() -> dict[str, object] | None:
    source = os.environ.get("CRASHPOINT_NONDET_SOURCE", "uuid").strip().lower()
    if source != "model":
        return None
    prompt = os.environ.get("CRASHPOINT_MODEL_PROMPT", "")
    return {
        "nondeterministic_source": "model",
        "sampler_cmd": os.environ.get("CRASHPOINT_MODEL_SAMPLER_CMD", ""),
        "model": os.environ.get("ANTHROPIC_MODEL", ""),
        "workspace_id_present": bool(os.environ.get("ANTHROPIC_WORKSPACE_ID"))
        or _dotenv_value_present("ANTHROPIC_WORKSPACE_ID"),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest() if prompt else "",
    }


def run_cell(
    runtime_id: str, barrier: str, k: int, ledger: LedgerHandle, name: str = "matrix"
) -> dict[str, object]:
    counts: Counter[str] = Counter()
    for _ in range(k):
        outcome = run_trial(runtime_id, barrier, ledger)
        counts[outcome.value] += 1
    modal, modal_n = counts.most_common(1)[0]
    lo, hi = wilson(modal_n, k)
    predicted = PREDICTED[runtime_id][barrier].outcome.value
    return {
        "runtime": runtime_id, "barrier": barrier, "k": k,
        "counts": dict(counts), "modal": modal, "modal_rate": round(modal_n / k, 4),
        "wilson95": [lo, hi], "predicted": predicted,
        "agrees": modal == predicted,
    }


def run(runtime_ids: tuple[str, ...], barrier_ids: tuple[str, ...], k: int,
        name: str) -> dict[str, object]:
    cells: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as d, LedgerDaemon(Path(d) / "led") as ledger:
        for rid in runtime_ids:
            for bid in barrier_ids:
                cells.append(run_cell(rid, bid, k, ledger, name))
    disagreements = [c for c in cells if not c["agrees"]]
    record: dict[str, object] = {
        "name": name, "k": k, "runtimes": list(runtime_ids), "barriers": list(barrier_ids),
        "cells": cells, "disagreements": disagreements,
    }
    sampler = _sampler_metadata()
    if sampler is not None:
        record["sampler"] = sampler
    record["receipt"] = receipt(record)
    return record


def render(record: dict[str, object]) -> str:
    from ..model.barriers import BARRIERS_BY_ID

    cell_list = record["cells"]
    barriers = record["barriers"]
    runtimes = record["runtimes"]
    assert isinstance(cell_list, list) and isinstance(barriers, list) and isinstance(runtimes, list)
    cells = {(c["runtime"], c["barrier"]): c for c in cell_list}
    lines = [f"crashpoint observed matrix - k={record['k']} per cell, predicted(P)/observed(O)"]
    head = f"{'runtime':18}" + "".join(f"{BARRIERS_BY_ID[b].slug:>18}" for b in barriers)
    lines.append(head)
    lines.append("-" * len(head))
    for rid in runtimes:
        row = f"{RUNTIMES_BY_ID[rid].slug:18}"
        for b in barriers:
            c = cells[(rid, b)]
            p = _SYM[Outcome(c["predicted"])]
            o = _SYM[Outcome(c["modal"])]
            rate = c["modal_rate"]
            cellstr = f"{p}/{o}" if c["agrees"] else f"{p}!{o}"
            if rate != 1.0:
                cellstr += f"@{rate}"
            row += f"{cellstr:>18}"
        lines.append(row)
    lines.append("")
    dis = record["disagreements"]
    assert isinstance(dis, list)
    names = [(d["runtime"], d["barrier"]) for d in dis]
    lines.append(f"disagreements (model wrong): {names}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--runtimes", default=",".join(RUNTIME_IDS))
    ap.add_argument("--name", default="matrix")
    args = ap.parse_args(argv)
    rids = tuple(r for r in args.runtimes.split(",") if r)
    record = run(rids, BARRIER_IDS, args.k, args.name)
    print(render(record))
    out = Path(__file__).resolve().parents[3] / "evidence" / f"{args.name}.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True))
    print(f"\nreceipt: {record['receipt']}\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
