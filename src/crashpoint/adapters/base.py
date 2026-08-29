"""Shared adapter primitives: the external effect (a call to the out-of-process ledger), the
idempotency key for the idempotent arm, the nondeterministic draw, and the deterministic crash.

The crash is `os.kill(os.getpid(), SIGKILL)` - an uncatchable kill of the whole process
at a named barrier, the fault the langgraph#8039 probe uses. Because it kills the process, every
adapter runs in its own subprocess spawned by the harness; the out-of-process ledger survives it.
"""

from __future__ import annotations

import os
import signal
import uuid

from ..ledger.daemon import execute
from ..ledger.idempotency import derive_idempotency_key

_PAYLOAD: dict[str, object] = {"amount": 100, "to": "acct-attacker"}


def _draw() -> str:
    """The one thing no durable runtime can replay: a value produced DURING the step that is not in
    the step's durable inputs, and so does not exist until after the step has already run.

    A model call is the motivating case (@vasilisnasopoulos on langgraph#8039: "a step that calls a
    model is not deterministic given its inputs"), but the model is not the property - the
    irreproducibility is. A uuid draw has exactly that property, costs nothing, needs no API key,
    and diverges on EVERY trial instead of only when a sampler happens to. Using a model here would
    make the measurement more expensive, slower, and less reliable at showing the same thing.
    """
    return uuid.uuid4().hex[:12]


def effect(
    invoke_path: str, intent_id: str, idempotent: bool, nondeterministic: bool = False
) -> None:
    """Perform the external side effect by recording it in the ledger. Naive passes no key (each
    call is a distinct effect); idempotent derives a stable key so a re-run dedups.

    `nondeterministic` puts a drawn value in the payload - the memo line an agent would have a model
    write. It is ordinary semantic content, so it is legitimately part of what the action IS and
    therefore part of the key. That is what makes it lethal: the key stays content-derived and the
    forbidden-field guard stays satisfied, and the dedup still fails, because the content itself is
    not reproducible from the step's durable inputs.
    """
    payload = dict(_PAYLOAD)
    if nondeterministic:
        payload["memo"] = _draw()
    key = derive_idempotency_key("charge", intent_id, 1, payload) if idempotent else None
    execute(invoke_path, intent_id, key, payload)


def crash() -> None:
    """Kill this process now, uncatchably, at a barrier. Never returns."""
    os.kill(os.getpid(), signal.SIGKILL)
    raise SystemExit(1)  # pragma: no cover - unreachable, for the type checker
