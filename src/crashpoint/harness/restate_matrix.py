"""Run the Restate real-server crash matrix.

Prerequisites:
  1. Start a local Restate dev server, for example:
     docker run --rm --name crashpoint-restate -p 8080:8080 -p 9070:9070 -p 9071:9071 \
       --add-host=host.docker.internal:host-gateway docker.restate.dev/restatedev/restate:latest
  2. Run this module with the `restate` extra installed:
     uv run --extra restate python -m crashpoint.harness.restate_matrix --k 30 --name restate

The harness starts and restarts the Python ASGI worker itself. Registration uses the Restate CLI
Docker image so the command remains optional and outside baseline CI.
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ..canonical import receipt
from ..ledger.oracle import classify
from ..model.barriers import BARRIER_IDS
from ..model.layers import Outcome
from ..model.predict import PREDICTED
from ..model.runtimes import RUNTIMES_BY_ID
from .ledger_process import LedgerDaemon, LedgerHandle
from .wilson import wilson

RuntimeId = Literal[
    "r_restate_naive",
    "r_restate_idem",
    "r_restate_nondet",
    "r_restate_twophase",
]

RUNTIME_IDS: tuple[RuntimeId, ...] = (
    "r_restate_naive",
    "r_restate_idem",
    "r_restate_nondet",
    "r_restate_twophase",
)
_MODE_BY_RUNTIME: dict[RuntimeId, str] = {
    "r_restate_naive": "naive",
    "r_restate_idem": "idem",
    "r_restate_nondet": "nondet",
    "r_restate_twophase": "twophase",
}
_SYM = {
    Outcome.EXACTLY_ONCE: "ONCE",
    Outcome.DUPLICATED: "DUP",
    Outcome.LOST: "LOST",
    Outcome.DIVERGED: "DIVERGE",
    Outcome.VOID: "VOID",
}
_EXPECTED_CRASH = -int(signal.SIGKILL)
_SERVICE_NAME = "CrashpointRestate"
_CLI_IMAGE = "docker.restate.dev/restatedev/restate-cli:latest"


@dataclass
class ServiceProcess:
    proc: subprocess.Popen[str]
    port: int


def _http_json(url: str, payload: object | None = None, timeout: float = 10.0) -> object:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if payload is None else {"content-type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    if not body:
        return None
    return json.loads(body)


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Restate adapter did not listen on port {port}")


def start_service(port: int) -> ServiceProcess:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "crashpoint.adapters.restate_adapter",
            "--serve",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port(port)
    except Exception:
        _terminate(proc)
        raise
    return ServiceProcess(proc, port)


def _terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _process_tail(proc: subprocess.Popen[str]) -> str:
    if proc.poll() is None:
        return "<running>"
    try:
        out, err = proc.communicate(timeout=0.2)
    except subprocess.TimeoutExpired:
        return "<process exited; output unavailable>"
    return f"stdout={out.strip() or '<empty>'}; stderr={err.strip() or '<empty>'}"


def register_service(port: int, deployment_url: str | None = None) -> None:
    endpoint = deployment_url or f"http://host.docker.internal:{port}"
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network=host",
        _CLI_IMAGE,
        "deployments",
        "register",
        "--yes",
        "--use-http1.1",
        "--force",
        endpoint,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            "\n".join(
                [
                    "Restate deployment registration failed",
                    f"cmd={' '.join(cmd)}",
                    f"stdout={proc.stdout.strip() or '<empty>'}",
                    f"stderr={proc.stderr.strip() or '<empty>'}",
                ]
            )
        )


def _ensure_running(service: ServiceProcess | None, port: int) -> ServiceProcess:
    if service is not None and service.proc.poll() is None:
        return service
    return start_service(port)


def _wait_for_expected_crash(service: ServiceProcess, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = service.proc.poll()
        if rc is not None:
            if rc != _EXPECTED_CRASH:
                raise RuntimeError(
                    f"Restate worker exited unexpectedly: {rc}; {_process_tail(service.proc)}"
                )
            return
        time.sleep(0.1)
    raise RuntimeError(
        "Restate worker did not crash at the expected barrier; "
        f"{_process_tail(service.proc)}"
    )


def _wait_for_output(ingress: str, workflow_key: str, timeout: float) -> object:
    url = f"{ingress}/restate/workflow/{_SERVICE_NAME}/{workflow_key}/output"
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        try:
            out = _http_json(url, timeout=5)
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except urllib.error.URLError as exc:
            last = str(exc)
        else:
            last = out
            if out == "ok":
                return out
            if not (isinstance(out, dict) and out.get("message") == "not ready"):
                raise RuntimeError(f"unexpected Restate workflow output: {out!r}")
        time.sleep(0.25)
    raise RuntimeError(f"Restate workflow did not complete; last output={last!r}")


def _send_invocation(
    ingress: str,
    workflow_key: str,
    ledger: LedgerHandle,
    runtime_id: RuntimeId,
    barrier: str,
    marker_dir: Path,
) -> None:
    payload = {
        "ledger": ledger.invoke_path,
        "intent": workflow_key,
        "mode": _MODE_BY_RUNTIME[runtime_id],
        "barrier": barrier,
        "marker_dir": str(marker_dir),
    }
    url = f"{ingress}/{_SERVICE_NAME}/{workflow_key}/run/send"
    accepted = _http_json(url, payload, timeout=10)
    if not isinstance(accepted, dict) or accepted.get("status") != "Accepted":
        raise RuntimeError(f"Restate send did not return Accepted: {accepted!r}")


def run_trial(
    service: ServiceProcess,
    ledger: LedgerHandle,
    runtime_id: RuntimeId,
    barrier: str,
    index: int,
    name: str,
    ingress: str,
    marker_dir: Path,
    timeout: float,
) -> Outcome:
    safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:40]
    workflow_key = f"cp-{safe_name or 'restate'}-{runtime_id}-{barrier}-{index}"
    ledger.reset()
    _send_invocation(ingress, workflow_key, ledger, runtime_id, barrier, marker_dir)
    _wait_for_expected_crash(service, timeout)
    restarted = start_service(service.port)
    try:
        _wait_for_output(ingress, workflow_key, timeout)
    finally:
        _terminate(restarted.proc)
    ledger.seal()
    return classify(workflow_key, ledger.dump(), Path(ledger.store_path))


def run_cell(
    runtime_id: RuntimeId,
    barrier: str,
    k: int,
    name: str,
    ledger: LedgerHandle,
    port: int,
    ingress: str,
    marker_dir: Path,
    timeout: float,
) -> dict[str, object]:
    counts: Counter[str] = Counter()
    for i in range(k):
        service = start_service(port)
        try:
            outcome = run_trial(
                service, ledger, runtime_id, barrier, i, name, ingress, marker_dir, timeout
            )
        finally:
            _terminate(service.proc)
        counts[outcome.value] += 1
    modal, modal_n = counts.most_common(1)[0]
    lo, hi = wilson(modal_n, k)
    predicted = PREDICTED[runtime_id][barrier].outcome.value
    return {
        "runtime": runtime_id,
        "barrier": barrier,
        "k": k,
        "counts": dict(counts),
        "modal": modal,
        "modal_rate": round(modal_n / k, 4),
        "wilson95": [lo, hi],
        "predicted": predicted,
        "agrees": modal == predicted,
    }


def run(
    runtime_ids: tuple[RuntimeId, ...],
    barrier_ids: tuple[str, ...],
    k: int,
    name: str,
    port: int = 9080,
    ingress: str = "http://127.0.0.1:8080",
    deployment_url: str | None = None,
    timeout: float = 30.0,
) -> dict[str, object]:
    initial = start_service(port)
    try:
        register_service(port, deployment_url)
    finally:
        _terminate(initial.proc)

    cells: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerDaemon(Path(tmp) / "ledger") as ledger:
        marker_dir = Path(tmp) / "markers"
        for rid in runtime_ids:
            for bid in barrier_ids:
                cells.append(
                    run_cell(rid, bid, k, name, ledger, port, ingress, marker_dir, timeout)
                )
    disagreements = [c for c in cells if not c["agrees"]]
    record: dict[str, object] = {
        "name": name,
        "k": k,
        "runtimes": list(runtime_ids),
        "barriers": list(barrier_ids),
        "cells": cells,
        "disagreements": disagreements,
    }
    record["receipt"] = receipt(record)
    return record


def render(record: dict[str, object]) -> str:
    from ..model.barriers import BARRIERS_BY_ID

    cell_list = record["cells"]
    barriers = record["barriers"]
    runtimes = record["runtimes"]
    assert isinstance(cell_list, list) and isinstance(barriers, list) and isinstance(runtimes, list)
    cells = {(c["runtime"], c["barrier"]): c for c in cell_list}
    lines = [
        f"crashpoint Restate observed matrix - k={record['k']} per cell, "
        "predicted(P)/observed(O)"
    ]
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


def _parse_runtime_ids(raw: str) -> tuple[RuntimeId, ...]:
    ids = tuple(r for r in raw.split(",") if r)
    bad = [r for r in ids if r not in RUNTIME_IDS]
    if bad:
        raise ValueError(f"unknown Restate runtime ids: {bad}")
    return cast(tuple[RuntimeId, ...], ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--runtimes", default=",".join(RUNTIME_IDS))
    parser.add_argument("--name", default="restate")
    parser.add_argument("--port", type=int, default=9080)
    parser.add_argument("--ingress", default="http://127.0.0.1:8080")
    parser.add_argument("--deployment-url")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    record = run(
        _parse_runtime_ids(args.runtimes),
        BARRIER_IDS,
        args.k,
        args.name,
        port=args.port,
        ingress=args.ingress.rstrip("/"),
        deployment_url=args.deployment_url,
        timeout=args.timeout,
    )
    print(render(record))
    out = Path(__file__).resolve().parents[3] / "evidence" / f"{args.name}.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True))
    print(f"\nreceipt: {record['receipt']}\nwrote {out}")
    return 0 if not record["disagreements"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
