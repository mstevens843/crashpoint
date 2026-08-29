"""One crash-and-recover trial against one runtime at one barrier.

The flow is the same every runtime: reset the ledger, spawn the adapter in a fresh process
that crashes at the barrier (an uncatchable SIGKILL), spawn it again in recovery mode where
it resumes and completes, then read the ledger and classify the outcome. The adapter runs in its own
subprocess so the SIGKILL kills it and not the harness, and the ledger - a separate process -
records the true side-effect count across the crash.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..ledger.oracle import classify
from ..model.layers import Outcome
from .ledger_process import LedgerHandle


@dataclass(frozen=True)
class AdapterSpec:
    module: str
    kind: str        # the --kind (controls) or --mode (real runtimes) value


# runtime_id -> how to spawn its adapter
REGISTRY: dict[str, AdapterSpec] = {
    "r_null": AdapterSpec("crashpoint.adapters.controls", "null"),
    "r_dup": AdapterSpec("crashpoint.adapters.controls", "dup"),
    "r_lost": AdapterSpec("crashpoint.adapters.controls", "lost"),
    "r_idem": AdapterSpec("crashpoint.adapters.controls", "idem"),
    "r_diverge": AdapterSpec("crashpoint.adapters.controls", "diverge"),
    "r_lg_naive": AdapterSpec("crashpoint.adapters.langgraph_adapter", "naive"),
    "r_lg_idem": AdapterSpec("crashpoint.adapters.langgraph_adapter", "idem"),
    "r_tmp_naive": AdapterSpec("crashpoint.adapters.temporal_adapter", "naive"),
    "r_tmp_idem": AdapterSpec("crashpoint.adapters.temporal_adapter", "idem"),
    "r_dbos_naive": AdapterSpec("crashpoint.adapters.dbos_adapter", "naive"),
    "r_dbos_idem": AdapterSpec("crashpoint.adapters.dbos_adapter", "idem"),
    "r_lg_nondet": AdapterSpec("crashpoint.adapters.langgraph_adapter", "nondet"),
    "r_tmp_nondet": AdapterSpec("crashpoint.adapters.temporal_adapter", "nondet"),
    "r_dbos_nondet": AdapterSpec("crashpoint.adapters.dbos_adapter", "nondet"),
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


def run_trial(runtime_id: str, barrier: str, ledger: LedgerHandle, intent: str = "order-1",
              timeout: float = 30.0) -> Outcome:
    spec = REGISTRY[runtime_id]
    ledger.reset()
    with tempfile.TemporaryDirectory() as d:
        marker = Path(d) / "marker"
        checkpoint = Path(d) / "checkpoint.sqlite"
        # 1. the crash run
        subprocess.run(
            _argv(spec, ledger.invoke_path, marker, checkpoint, intent, barrier, recovery=False),
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        # 2. the recovery run (no crash)
        subprocess.run(
            _argv(spec, ledger.invoke_path, marker, checkpoint, intent, "none", recovery=True),
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    ledger.seal()
    outcome = classify(intent, ledger.dump(), Path(ledger.store_path))
    return outcome
