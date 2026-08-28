"""Shared adapter primitives: the external effect (a call to the out-of-process ledger), the
idempotency key for the idempotent arm, and the deterministic crash.

The crash is `os.kill(os.getpid(), SIGKILL)` - an uncatchable kill of the whole process
at a named barrier, the fault the langgraph#8039 probe uses. Because it kills the process, every
adapter runs in its own subprocess spawned by the harness; the out-of-process ledger survives it.
"""

from __future__ import annotations

import os
import signal

from ..ledger.daemon import execute
from ..ledger.idempotency import derive_idempotency_key

_PAYLOAD = {"amount": 100, "to": "acct-attacker"}


def effect(invoke_path: str, intent_id: str, idempotent: bool) -> None:
    """Perform the external side effect by recording it in the ledger. Naive passes no key (each
    call is a distinct effect); idempotent derives a stable key so a re-run dedups."""
    key = derive_idempotency_key("charge", intent_id, 1, _PAYLOAD) if idempotent else None
    execute(invoke_path, intent_id, key, _PAYLOAD)


def crash() -> None:
    """Kill this process now, uncatchably, at a barrier. Never returns."""
    os.kill(os.getpid(), signal.SIGKILL)
    raise SystemExit(1)  # pragma: no cover - unreachable, for the type checker
