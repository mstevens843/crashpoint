"""Spawn the out-of-process ledger daemon as a subprocess and expose its sockets. This is how the
harness gets an independent ledger the system under test cannot forge: a separate process, its own
storage, and only the invoke socket handed to the SUT.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..ledger import daemon


@dataclass
class LedgerHandle:
    invoke_path: str
    control_path: str
    store_path: str
    proc: subprocess.Popen[str]

    def dump(self) -> dict[str, object]:
        resp = daemon.control(self.control_path, "dump")
        d = resp.get("dump")
        return d if isinstance(d, dict) else {}

    def reset(self) -> None:
        daemon.control(self.control_path, "reset")

    def seal(self) -> None:
        daemon.control(self.control_path, "seal")


class LedgerDaemon:
    """Context manager: starts the daemon, waits for LEDGER_READY, stops it on exit."""

    def __init__(self, work: Path) -> None:
        self.work = work
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / "ctl").mkdir(exist_ok=True)
        self.invoke = str(self.work / "inv.sock")
        self.control = str(self.work / "ctl" / "ctl.sock")
        self.store = str(self.work / "ledger.jsonl")
        self.proc: subprocess.Popen[str] | None = None

    def __enter__(self) -> LedgerHandle:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "crashpoint.ledger.daemon",
             "--invoke", self.invoke, "--control", self.control, "--store", self.store],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        assert self.proc.stdout is not None
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            line = self.proc.stdout.readline()
            if "LEDGER_READY" in line:
                break
        else:  # pragma: no cover
            raise RuntimeError("ledger daemon did not become ready")
        return LedgerHandle(self.invoke, self.control, self.store, self.proc)

    def __exit__(self, *exc: object) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()


