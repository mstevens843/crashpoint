"""One crash-and-recover trial against one runtime at one barrier.

The flow is the same every runtime: reset the ledger, spawn the adapter in a fresh process
that crashes at the barrier (an uncatchable SIGKILL), spawn it again in recovery mode where
it resumes and completes, then read the ledger and classify the outcome. The adapter runs in its own
subprocess so the SIGKILL kills it and not the harness, and the ledger - a separate process -
records the true side-effect count across the crash.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from ..ledger.oracle import classify
from ..model.layers import Outcome
from .ledger_process import LedgerHandle


@dataclass(frozen=True)
class AdapterSpec:
    module: str
    kind: str        # the --kind (controls) or --mode (real runtimes) value


class TrialExecutionError(RuntimeError):
    """An adapter process did not exit in the way the harness expected."""


_EXPECTED_CRASH_RETURNCODE = -int(signal.SIGKILL)
_MAX_CAPTURED_CHARS = 2_000


# runtime_id -> how to spawn its adapter
REGISTRY: dict[str, AdapterSpec] = {
    "r_null": AdapterSpec("crashpoint.adapters.controls", "null"),
    "r_dup": AdapterSpec("crashpoint.adapters.controls", "dup"),
    "r_lost": AdapterSpec("crashpoint.adapters.controls", "lost"),
    "r_idem": AdapterSpec("crashpoint.adapters.controls", "idem"),
    "r_diverge": AdapterSpec("crashpoint.adapters.controls", "diverge"),
    "r_twophase": AdapterSpec("crashpoint.adapters.controls", "twophase"),
    "r_lg_naive": AdapterSpec("crashpoint.adapters.langgraph_adapter", "naive"),
    "r_lg_idem": AdapterSpec("crashpoint.adapters.langgraph_adapter", "idem"),
    "r_tmp_naive": AdapterSpec("crashpoint.adapters.temporal_adapter", "naive"),
    "r_tmp_idem": AdapterSpec("crashpoint.adapters.temporal_adapter", "idem"),
    "r_dbos_naive": AdapterSpec("crashpoint.adapters.dbos_adapter", "naive"),
    "r_dbos_idem": AdapterSpec("crashpoint.adapters.dbos_adapter", "idem"),
    "r_lg_nondet": AdapterSpec("crashpoint.adapters.langgraph_adapter", "nondet"),
    "r_tmp_nondet": AdapterSpec("crashpoint.adapters.temporal_adapter", "nondet"),
    "r_dbos_nondet": AdapterSpec("crashpoint.adapters.dbos_adapter", "nondet"),
    "r_lg_twophase": AdapterSpec("crashpoint.adapters.langgraph_adapter", "twophase"),
    "r_tmp_twophase": AdapterSpec("crashpoint.adapters.temporal_adapter", "twophase"),
    "r_dbos_twophase": AdapterSpec("crashpoint.adapters.dbos_adapter", "twophase"),
}

_CONTROLS_MODULE = "crashpoint.adapters.controls"


def _argv(spec: AdapterSpec, ledger: str, marker: Path, checkpoint: Path, intent: str,
          barrier: str, recovery: bool) -> list[str]:
    base = [sys.executable, "-m", spec.module, "--ledger", ledger, "--intent", intent,
            "--barrier", barrier, "--recovery", "1" if recovery else "0"]
    # Branch on the MODULE, not the kind: a real runtime's mode ("idem") collides with a control's
    # kind ("idem"), so keying off the kind misroutes the idempotent runtime to the control adapter.
    if spec.module == _CONTROLS_MODULE:
        return [*base, "--kind", spec.kind, "--marker", str(marker)]
    return [*base, "--mode", spec.kind, "--checkpoint", str(checkpoint)]


def _stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _trim(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "<empty>"
    if len(stripped) <= _MAX_CAPTURED_CHARS:
        return stripped
    return stripped[:_MAX_CAPTURED_CHARS] + "\n...<truncated>"


def _describe_returncode(returncode: int | None) -> str:
    if returncode is None:
        return "timeout"
    if returncode < 0:
        signum = -returncode
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = "unknown"
        return f"signal {signum} ({name})"
    return f"exit {returncode}"


def _raise_process_error(
    *,
    runtime_id: str,
    barrier: str,
    phase: str,
    argv: list[str],
    expected_returncode: int,
    actual_returncode: int | None,
    stdout: str,
    stderr: str,
) -> NoReturn:
    cmd = " ".join(argv)
    raise TrialExecutionError(
        "\n".join(
            [
                f"adapter process failed during {phase} phase",
                f"runtime={runtime_id} barrier={barrier}",
                f"expected {_describe_returncode(expected_returncode)}, "
                f"got {_describe_returncode(actual_returncode)}",
                f"argv: {cmd}",
                f"stdout:\n{_trim(stdout)}",
                f"stderr:\n{_trim(stderr)}",
            ]
        )
    )


def _run_checked(
    argv: list[str],
    *,
    runtime_id: str,
    barrier: str,
    phase: str,
    timeout: float,
    expected_returncode: int,
) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _raise_process_error(
            runtime_id=runtime_id,
            barrier=barrier,
            phase=phase,
            argv=argv,
            expected_returncode=expected_returncode,
            actual_returncode=None,
            stdout=_stream_text(exc.stdout),
            stderr=_stream_text(exc.stderr),
        )
    if proc.returncode != expected_returncode:
        _raise_process_error(
            runtime_id=runtime_id,
            barrier=barrier,
            phase=phase,
            argv=argv,
            expected_returncode=expected_returncode,
            actual_returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    return proc


def run_trial(runtime_id: str, barrier: str, ledger: LedgerHandle, intent: str = "order-1",
              timeout: float = 30.0) -> Outcome:
    spec = REGISTRY[runtime_id]
    ledger.reset()
    with tempfile.TemporaryDirectory() as d:
        marker = Path(d) / "marker"
        checkpoint = Path(d) / "checkpoint.sqlite"
        # 1. the crash run
        crash_argv = _argv(
            spec, ledger.invoke_path, marker, checkpoint, intent, barrier, recovery=False
        )
        _run_checked(
            crash_argv,
            runtime_id=runtime_id,
            barrier=barrier,
            phase="crash",
            timeout=timeout,
            expected_returncode=_EXPECTED_CRASH_RETURNCODE if barrier != "none" else 0,
        )
        # 2. the recovery run (no crash)
        recovery_argv = _argv(
            spec, ledger.invoke_path, marker, checkpoint, intent, "none", recovery=True
        )
        _run_checked(
            recovery_argv,
            runtime_id=runtime_id,
            barrier=barrier,
            phase="recovery",
            timeout=timeout,
            expected_returncode=0,
        )
    ledger.seal()
    outcome = classify(intent, ledger.dump(), Path(ledger.store_path))
    return outcome
