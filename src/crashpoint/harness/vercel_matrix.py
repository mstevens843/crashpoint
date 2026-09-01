"""Run the Vercel Workflow DevKit crash matrix against the Local World.

Prerequisites:
  1. Build the fixture once (Node 22):
       cd runtime/vercel-workflow && npm ci && npm run build
  2. Run this module; the Python side is stdlib only:
       uv run python -m crashpoint.harness.vercel_matrix --k 30 --name vercel

The harness starts and restarts the built Nitro server itself, with one fresh Local World data
directory per trial. Two environment knobs of the runtime under test are set, and no runtime code
is patched:

  * ``WORKFLOW_TARGET_WORLD=@workflow/world-local`` makes the runtime load the unbundled Local
    World package from ``node_modules`` through its documented custom-world path. The copy Nitro
    inlines into the server bundle cannot find its own ``package.json`` and falls back to the
    version string ``"bundled"``, which its data-directory initializer rejects with
    ``Invalid version string: "bundled"``. The unbundled copy reads its real version.
  * ``WORKFLOW_INLINE_OWNERSHIP_LEASE_SECONDS=1`` shortens the inline-step ownership lease from
    860 s to its documented minimum, so a step whose owning invocation died is re-dispatched after
    one second instead of fourteen minutes. The recovery path is unchanged.

The measured unit is the ``charge`` step of ``runtime/vercel-workflow/src/workflows/crashpoint.ts``:
  b0 - crash inside the step before the effect; recovery re-runs the step and the effect crosses
       once.
  b1 - crash inside the step after the effect but before ``step_completed`` is durable; recovery
       re-executes the whole step, so a naive effect DUPLICATES and an idempotent one dedups.
  b2 - crash inside the following ``sentinel`` step after ``charge`` completed; recovery replays the
       journaled result and does not re-run the effect.

The DevKit runs the first invocation eagerly inside ``start()``, so a crash trial normally kills the
server before ``/api/start`` can answer. The run id is therefore read from the trial's own data
directory (``runs/<runId>.json``), which holds exactly one run. Recovery is the documented Local
World behavior: a fresh server calls ``world.start()``, which re-enqueues every pending or running
run it finds in the data directory.

A recovery that never completes within the timeout is scored VOID, the fail-closed outcome: the
ledger's count is not the count of a completed recovery, so exactly-once cannot be certified. The
trial then records the run's durable state and the server log tail. The one shape seen so far is a
crash that lands after world-local has linked the lazy step-create claim
(``.locks/steps/<run>-<step>.created``) but before the step entity and ``step_created`` event are
written: the recovered run's lazy step start hits the stale claim, is mapped to ``skipped``, and
the flow delivery hangs until the local queue's header timeout, then again on redelivery.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import shutil
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

RuntimeId = Literal["r_vwf_naive", "r_vwf_idem", "r_vwf_nondet", "r_vwf_twophase"]

RUNTIME_IDS: tuple[RuntimeId, ...] = (
    "r_vwf_naive",
    "r_vwf_idem",
    "r_vwf_nondet",
    "r_vwf_twophase",
)
_MODE_BY_RUNTIME: dict[RuntimeId, str] = {
    "r_vwf_naive": "naive",
    "r_vwf_idem": "idem",
    "r_vwf_nondet": "nondet",
    "r_vwf_twophase": "twophase",
}
_SYM = {
    Outcome.EXACTLY_ONCE: "ONCE",
    Outcome.DUPLICATED: "DUP",
    Outcome.LOST: "LOST",
    Outcome.DIVERGED: "DIVERGE",
    Outcome.VOID: "VOID",
}
_EXPECTED_CRASH = -int(signal.SIGKILL)
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE_DIR = _ROOT / "runtime" / "vercel-workflow"
DEFAULT_TARGET_WORLD = "@workflow/world-local"
DEFAULT_LEASE_SECONDS = 1
DEFAULT_PORT = 4097
_SERVER_ENTRY = Path(".output") / "server" / "index.mjs"
_RUN_FILE = re.compile(r"^(wrun_[0-9A-Z]+)\.json$")
_LOG_TAIL_CHARS = 2_000


class RecoveryStalled(RuntimeError):
    """The recovered run stayed non-terminal for the whole timeout."""

    def __init__(self, message: str, detail: dict[str, object]) -> None:
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class FixtureConfig:
    fixture_dir: Path
    port: int = DEFAULT_PORT
    target_world: str = DEFAULT_TARGET_WORLD
    lease_seconds: int = DEFAULT_LEASE_SECONDS

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def server_entry(self) -> Path:
        return self.fixture_dir / _SERVER_ENTRY

    def env(self, data_dir: Path) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "NITRO_HOST": "127.0.0.1",
                "NITRO_PORT": str(self.port),
                "WORKFLOW_LOCAL_BASE_URL": self.base_url,
                "WORKFLOW_LOCAL_DATA_DIR": str(data_dir),
                "WORKFLOW_TARGET_WORLD": self.target_world,
                "WORKFLOW_INLINE_OWNERSHIP_LEASE_SECONDS": str(self.lease_seconds),
            }
        )
        return env


@dataclass
class ServerProcess:
    proc: subprocess.Popen[str]
    log_path: Path

    def log_tail(self) -> str:
        try:
            text = self.log_path.read_text(errors="replace")
        except OSError:
            return "<no log>"
        text = text.strip()
        if not text:
            return "<empty>"
        return text[-_LOG_TAIL_CHARS:]


@dataclass(frozen=True)
class DurableSnapshot:
    """What the Local World data directory holds for one run at one moment."""

    run_status: str
    events: int
    steps: int
    tmp_leftovers: int

    def as_dict(self) -> dict[str, object]:
        return {
            "run_status": self.run_status,
            "events": self.events,
            "steps": self.steps,
            "tmp_leftovers": self.tmp_leftovers,
        }


@dataclass(frozen=True)
class VercelTrial:
    runtime: RuntimeId
    barrier: str
    predicted: Outcome
    observed: Outcome
    run_id: str
    before_recovery: DurableSnapshot
    after_recovery: DurableSnapshot
    recovery_seconds: float
    stall: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        record: dict[str, object] = {
            "run_id": self.run_id,
            "predicted": self.predicted.value,
            "observed": self.observed.value,
            "before_recovery": self.before_recovery.as_dict(),
            "after_recovery": self.after_recovery.as_dict(),
            "recovery_seconds": self.recovery_seconds,
            "stalled": self.stall is not None,
        }
        if self.stall is not None:
            record["stall"] = self.stall
        return record


def _http(
    url: str, payload: object | None = None, timeout: float = 10.0
) -> tuple[int, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {} if payload is None else {"content-type": "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = int(response.status)
        body = response.read()
    return status, (json.loads(body) if body else None)


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.connect(("127.0.0.1", port))
        except OSError:
            return True
    return False


def discover_run_id(data_dir: Path) -> str:
    """The one run this trial's data directory holds. Crash leftovers (``*.json.tmp.*``) are
    ignored; anything other than exactly one run file is a harness error."""
    runs_dir = data_dir / "runs"
    ids = sorted(
        m.group(1)
        for p in (runs_dir.iterdir() if runs_dir.is_dir() else [])
        if (m := _RUN_FILE.match(p.name)) is not None
    )
    if len(ids) != 1:
        raise RuntimeError(f"expected exactly one run in {runs_dir}, found {ids}")
    return ids[0]


def durable_snapshot(data_dir: Path, run_id: str) -> DurableSnapshot:
    runs_dir = data_dir / "runs"
    status = "<missing>"
    run_file = runs_dir / f"{run_id}.json"
    if run_file.is_file():
        try:
            loaded = json.loads(run_file.read_text())
            status = str(loaded.get("status", "<unset>")) if isinstance(loaded, dict) else "<bad>"
        except (OSError, json.JSONDecodeError):
            status = "<unreadable>"

    def count(sub: str) -> int:
        d = data_dir / sub
        return sum(1 for p in d.glob(f"{run_id}-*.json")) if d.is_dir() else 0

    leftovers = (
        sum(1 for p in runs_dir.iterdir() if ".json.tmp." in p.name) if runs_dir.is_dir() else 0
    )
    return DurableSnapshot(status, count("events"), count("steps"), leftovers)


def durable_events(data_dir: Path, run_id: str) -> list[dict[str, object]]:
    """The run's durable events in id order: type, server timestamp, and inline-step owner."""
    events_dir = data_dir / "events"
    rows: list[dict[str, object]] = []
    if not events_dir.is_dir():
        return rows
    for path in sorted(events_dir.glob(f"{run_id}-*.json")):
        try:
            loaded = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            rows.append({"file": path.name, "error": "unreadable"})
            continue
        if not isinstance(loaded, dict):
            rows.append({"file": path.name, "error": "not an object"})
            continue
        data = loaded.get("eventData")
        rows.append(
            {
                "eventType": loaded.get("eventType"),
                "createdAt": loaded.get("createdAt"),
                "correlationId": loaded.get("correlationId"),
                "ownerMessageId": data.get("ownerMessageId") if isinstance(data, dict) else None,
            }
        )
    return rows


def start_server(config: FixtureConfig, data_dir: Path, log_path: Path,
                 timeout: float = 30.0) -> ServerProcess:
    log = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            ["node", str(_SERVER_ENTRY)],
            cwd=config.fixture_dir,
            env=config.env(data_dir),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
    finally:
        log.close()
    server = ServerProcess(proc, log_path)
    try:
        wait_healthy(server, config, timeout)
    except Exception:
        _terminate(proc)
        raise
    return server


def wait_healthy(server: ServerProcess, config: FixtureConfig, timeout: float) -> None:
    """Block until ``/api/health`` answers. The fixture starts the Local World inside this handler,
    which is what re-enqueues the crashed run on a recovery server."""
    deadline = time.monotonic() + timeout
    last = "<never answered>"
    while time.monotonic() < deadline:
        if server.proc.poll() is not None:
            raise RuntimeError(
                f"fixture server exited with {server.proc.returncode} before becoming healthy; "
                f"log tail:\n{server.log_tail()}"
            )
        try:
            status, body = _http(f"{config.base_url}/api/health", timeout=3)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last = repr(exc)
        else:
            if status == 200 and isinstance(body, dict) and body.get("ok") is True:
                return
            last = f"HTTP {status} {body!r}"
        time.sleep(0.1)
    raise RuntimeError(f"fixture server not healthy after {timeout}s: {last}")


def _terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def send_start(config: FixtureConfig, ledger: LedgerHandle, intent: str, mode: str,
               barrier: str, marker_dir: Path) -> str | None:
    """POST the run. In a crash trial the server usually dies before answering, because the first
    invocation runs eagerly inside ``start()``; that is reported as ``None`` and the run id is
    recovered from the data directory instead."""
    payload = {
        "ledger": ledger.invoke_path,
        "intent": intent,
        "mode": mode,
        "barrier": barrier,
        "markerDir": str(marker_dir),
    }
    try:
        status, body = _http(f"{config.base_url}/api/start", payload, timeout=10)
    except (urllib.error.URLError, http.client.HTTPException, OSError):
        return None
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("runId"), str):
        raise RuntimeError(f"unexpected /api/start answer: HTTP {status} {body!r}")
    return cast(str, body["runId"])


def wait_for_expected_crash(server: ServerProcess, timeout: float) -> None:
    try:
        rc = server.proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "fixture server did not crash at the expected barrier; log tail:\n"
            f"{server.log_tail()}"
        ) from None
    if rc != _EXPECTED_CRASH:
        raise RuntimeError(
            f"fixture server exited with {rc}, expected SIGKILL; log tail:\n{server.log_tail()}"
        )


def wait_for_completion(server: ServerProcess, config: FixtureConfig, run_id: str,
                        timeout: float, data_dir: Path | None = None) -> float:
    """Poll ``/api/output/<runId>`` until the run is ``completed``. Returns elapsed seconds."""
    started = time.monotonic()
    deadline = started + timeout
    last: object = None
    while time.monotonic() < deadline:
        if server.proc.poll() is not None:
            raise RuntimeError(
                f"recovery server exited with {server.proc.returncode} before the run completed; "
                f"log tail:\n{server.log_tail()}"
            )
        try:
            status, body = _http(f"{config.base_url}/api/output/{run_id}", timeout=5)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            last = repr(exc)
        else:
            last = body
            run_status = body.get("status") if isinstance(body, dict) else None
            if status == 200 and run_status == "completed":
                return round(time.monotonic() - started, 3)
            if run_status in ("failed", "cancelled"):
                raise RuntimeError(f"run {run_id} ended {run_status}: {body!r}")
        time.sleep(0.2)
    detail: dict[str, object] = {
        "reason": f"run did not complete within {timeout}s",
        "last_status": repr(last),
        "server_log_tail": server.log_tail(),
    }
    if data_dir is not None:
        detail["durable_events"] = durable_events(data_dir, run_id)
    raise RecoveryStalled(f"run {run_id} did not complete within {timeout}s", detail)


def run_trial(
    config: FixtureConfig,
    ledger: LedgerHandle,
    runtime_id: RuntimeId,
    barrier: str,
    index: int,
    root: Path,
    timeout: float,
    keep_dir: Path | None = None,
) -> VercelTrial:
    """One crash-and-recover trial. A failed trial's directory (server log, Local World data dir,
    markers) is copied under ``keep_dir`` before the error propagates, so a stall can be read."""
    trial_dir = root / f"{runtime_id}-{barrier}-{index}"
    try:
        return _run_trial(config, ledger, runtime_id, barrier, index, trial_dir, timeout)
    except Exception:
        if keep_dir is not None and trial_dir.is_dir():
            keep_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(trial_dir, keep_dir / trial_dir.name, dirs_exist_ok=True)
        raise


def _run_trial(
    config: FixtureConfig,
    ledger: LedgerHandle,
    runtime_id: RuntimeId,
    barrier: str,
    index: int,
    trial_dir: Path,
    timeout: float,
) -> VercelTrial:
    intent = f"{runtime_id}-{barrier}-{index}"
    data_dir = trial_dir / "world"
    marker_dir = trial_dir / "markers"
    log_path = trial_dir / "server.log"
    trial_dir.mkdir(parents=True)
    ledger.reset()

    crash_server = start_server(config, data_dir, log_path)
    try:
        send_start(config, ledger, intent, _MODE_BY_RUNTIME[runtime_id], barrier, marker_dir)
        wait_for_expected_crash(crash_server, timeout)
    finally:
        _terminate(crash_server.proc)
    run_id = discover_run_id(data_dir)
    before = durable_snapshot(data_dir, run_id)

    recovery_server = start_server(config, data_dir, log_path)
    stall: dict[str, object] | None = None
    try:
        elapsed = wait_for_completion(recovery_server, config, run_id, timeout, data_dir)
    except RecoveryStalled as exc:
        elapsed = round(timeout, 3)
        stall = exc.detail
    finally:
        _terminate(recovery_server.proc)
    after = durable_snapshot(data_dir, run_id)

    ledger.seal()
    # A recovery that never finished cannot be certified: fail closed rather than read the
    # ledger count of an incomplete recovery as exactly-once.
    observed = (
        Outcome.VOID if stall is not None
        else classify(intent, ledger.dump(), Path(ledger.store_path))
    )
    return VercelTrial(
        runtime_id,
        barrier,
        PREDICTED[runtime_id][barrier].outcome,
        observed,
        run_id,
        before,
        after,
        elapsed,
        stall,
    )


def run_cell(
    config: FixtureConfig,
    ledger: LedgerHandle,
    runtime_id: RuntimeId,
    barrier: str,
    k: int,
    root: Path,
    timeout: float,
    keep_dir: Path | None = None,
) -> dict[str, object]:
    started = time.monotonic()
    trials = [
        run_trial(config, ledger, runtime_id, barrier, i, root, timeout, keep_dir)
        for i in range(k)
    ]
    print(
        f"cell {runtime_id} {barrier}: {dict(Counter(t.observed.value for t in trials))} "
        f"in {time.monotonic() - started:.1f}s",
        file=sys.stderr,
        flush=True,
    )
    counts: Counter[str] = Counter(t.observed.value for t in trials)
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
        "trials": [t.as_dict() for t in trials],
    }


def _package_version(fixture_dir: Path, package: str) -> str:
    manifest = fixture_dir / "node_modules" / package / "package.json"
    try:
        loaded = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return "<unknown>"
    return str(loaded.get("version", "<unknown>")) if isinstance(loaded, dict) else "<unknown>"


def substrate_metadata(config: FixtureConfig) -> dict[str, object]:
    try:
        node = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        node = "<unknown>"
    return {
        "fixture": str(config.fixture_dir.relative_to(_ROOT))
        if config.fixture_dir.is_relative_to(_ROOT)
        else str(config.fixture_dir),
        "node": node,
        "workflow": _package_version(config.fixture_dir, "workflow"),
        "world_local": _package_version(config.fixture_dir, "@workflow/world-local"),
        "nitro": _package_version(config.fixture_dir, "nitro"),
        "target_world": config.target_world,
        "inline_ownership_lease_seconds": config.lease_seconds,
    }


def check_fixture(config: FixtureConfig) -> None:
    if not config.server_entry.is_file():
        raise RuntimeError(
            f"built fixture not found at {config.server_entry}; run "
            "`cd runtime/vercel-workflow && npm ci && npm run build` first"
        )
    if not _port_is_free(config.port):
        raise RuntimeError(f"port {config.port} is already in use; pass --port")


def run(
    runtime_ids: tuple[RuntimeId, ...],
    barrier_ids: tuple[str, ...],
    k: int,
    name: str,
    config: FixtureConfig,
    timeout: float = 30.0,
    keep_dir: Path | None = None,
) -> dict[str, object]:
    check_fixture(config)
    cells: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp, LedgerDaemon(Path(tmp) / "led") as ledger:
        root = Path(tmp) / "trials"
        for rid in runtime_ids:
            for bid in barrier_ids:
                cells.append(run_cell(config, ledger, rid, bid, k, root, timeout, keep_dir))
    disagreements = [
        {key: value for key, value in c.items() if key != "trials"}
        for c in cells
        if not c["agrees"]
    ]
    record: dict[str, object] = {
        "name": name,
        "k": k,
        "runtimes": list(runtime_ids),
        "barriers": list(barrier_ids),
        "cells": cells,
        "disagreements": disagreements,
        "substrate": substrate_metadata(config),
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
        f"crashpoint Vercel Workflow observed matrix - k={record['k']} per cell, "
        "predicted(P)/observed(O)"
    ]
    head = f"{'runtime':24}" + "".join(f"{BARRIERS_BY_ID[b].slug:>18}" for b in barriers)
    lines.append(head)
    lines.append("-" * len(head))
    for rid in runtimes:
        row = f"{RUNTIMES_BY_ID[rid].slug:24}"
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
        raise ValueError(f"unknown Vercel Workflow runtime ids: {bad}")
    return cast(tuple[RuntimeId, ...], ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--runtimes", default=",".join(RUNTIME_IDS))
    parser.add_argument("--name", default="vercel")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--fixture-dir", default=str(DEFAULT_FIXTURE_DIR))
    parser.add_argument("--target-world", default=DEFAULT_TARGET_WORLD)
    parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    parser.add_argument("--keep-dir", help="copy a failed trial's directory here for inspection")
    args = parser.parse_args(argv)
    config = FixtureConfig(
        Path(args.fixture_dir).resolve(), args.port, args.target_world, args.lease_seconds
    )
    record = run(
        _parse_runtime_ids(args.runtimes),
        BARRIER_IDS,
        args.k,
        args.name,
        config,
        timeout=args.timeout,
        keep_dir=Path(args.keep_dir).resolve() if args.keep_dir else None,
    )
    print(render(record))
    out = _ROOT / "evidence" / f"{args.name}.json"
    out.write_text(json.dumps(record, indent=2, sort_keys=True))
    print(f"\nreceipt: {record['receipt']}\nwrote {out}")
    return 0 if not record["disagreements"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
