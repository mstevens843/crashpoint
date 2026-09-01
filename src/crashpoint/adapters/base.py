"""Shared adapter primitives: the external effect (a call to the out-of-process ledger), the
idempotency key for the idempotent arm, nondeterministic memo sources, and the deterministic crash.

The crash is `os.kill(os.getpid(), SIGKILL)` - an uncatchable kill of the whole process
at a named barrier, the fault the langgraph#8039 probe uses. Because it kills the process, every
adapter runs in its own subprocess spawned by the harness; the out-of-process ledger survives it.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import uuid

from ..ledger.daemon import execute
from ..ledger.idempotency import derive_idempotency_key

_PAYLOAD: dict[str, object] = {"amount": 100, "to": "acct-attacker"}
_MODEL_PROMPT = "Write one short payment memo. Return only the memo text."
_MAX_MEMO_CHARS = 240


class ModelSamplerUnavailable(RuntimeError):
    """The optional real-model sampler was requested but is unavailable."""


def two_phase_key(intent_id: str) -> str:
    """The identity prepared before a nondeterministic call.

    It is derived only from durable pre-call inputs, not from the eventual memo/content produced
    during the step. The two-phase adapters persist or pass this identity before the draw and then
    reuse it at the external boundary.
    """
    return derive_idempotency_key("charge-prepared", intent_id, 1, dict(_PAYLOAD))


def _uuid_draw() -> str:
    """The one thing no durable runtime can replay: a value produced DURING the step that is not in
    the step's durable inputs, and so does not exist until after the step has already run.

    A model call is the motivating case (@vasilisnasopoulos on langgraph#8039: "a step that calls a
    model is not deterministic given its inputs"), but the model is not the property - the
    irreproducibility is. A uuid draw has exactly that property, costs nothing, needs no API key,
    and diverges on EVERY trial instead of only when a sampler happens to. Using a model here would
    make the measurement more expensive, slower, and less reliable at showing the same thing.
    """
    return uuid.uuid4().hex[:12]


def _model_draw() -> str:
    """Call an operator-supplied sampler command for the optional real-model arm.

    The command receives the prompt on stdin and must print the sampled memo to stdout. This keeps
    crashpoint provider-neutral and secret-free: the command can wrap a local model, a provider SDK,
    or a CLI already configured outside this repo.
    """
    raw_cmd = os.environ.get("CRASHPOINT_MODEL_SAMPLER_CMD")
    if not raw_cmd:
        raise ModelSamplerUnavailable(
            "CRASHPOINT_NONDET_SOURCE=model requires CRASHPOINT_MODEL_SAMPLER_CMD"
        )
    try:
        timeout = float(os.environ.get("CRASHPOINT_MODEL_SAMPLER_TIMEOUT", "30"))
    except ValueError as exc:
        raise ModelSamplerUnavailable("CRASHPOINT_MODEL_SAMPLER_TIMEOUT must be numeric") from exc
    prompt = os.environ.get("CRASHPOINT_MODEL_PROMPT", _MODEL_PROMPT)
    proc = subprocess.run(
        shlex.split(raw_cmd),
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "<no output>"
        raise ModelSamplerUnavailable(
            f"model sampler command exited {proc.returncode}: {err[:_MAX_MEMO_CHARS]}"
        )
    memo = proc.stdout.strip()
    if not memo:
        raise ModelSamplerUnavailable("model sampler command produced no stdout")
    return memo[:_MAX_MEMO_CHARS]


def draw_memo() -> str:
    """Return the nondeterministic memo for `*_nondet` rows.

    Default `uuid` is the cheap deterministic-property control. Optional `model` uses an actual
    sampler command when the operator configures one. If that sampler happens to return the same
    value on retry, the matrix should show a model disagreement rather than forcing DIVERGED.
    """
    source = os.environ.get("CRASHPOINT_NONDET_SOURCE", "uuid").strip().lower()
    if source == "uuid":
        return _uuid_draw()
    if source == "model":
        return _model_draw()
    raise ModelSamplerUnavailable(
        "CRASHPOINT_NONDET_SOURCE must be 'uuid' or 'model', got " f"{source!r}"
    )


def effect(
    invoke_path: str,
    intent_id: str,
    idempotent: bool,
    nondeterministic: bool = False,
    key_override: str | None = None,
) -> None:
    """Perform the external side effect by recording it in the ledger. Naive passes no key (each
    call is a distinct effect); idempotent derives a stable key so a deterministic re-run dedups.

    `nondeterministic` puts a drawn value in the payload - the memo line an agent would have a model
    write. It is ordinary semantic content, so it is legitimately part of what the action IS and
    therefore part of the key. That is what makes it lethal: the key stays content-derived and the
    forbidden-field guard stays satisfied, and the dedup still fails, because the content itself is
    not reproducible from the step's durable inputs.
    """
    payload = dict(_PAYLOAD)
    if nondeterministic:
        payload["memo"] = draw_memo()
    if key_override is not None:
        key = key_override
    elif idempotent:
        key = derive_idempotency_key("charge", intent_id, 1, payload)
    else:
        key = None
    execute(invoke_path, intent_id, key, payload)


def crash() -> None:
    """Kill this process now, uncatchably, at a barrier. Never returns."""
    os.kill(os.getpid(), signal.SIGKILL)
    raise SystemExit(1)  # pragma: no cover - unreachable, for the type checker
