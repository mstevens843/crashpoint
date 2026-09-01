"""Restate adapter service.

Restate is not a single-process checkpoint file like the local control and LangGraph adapters. The
real-server harness starts this module as an ASGI worker, registers it with a Restate dev server,
sends workflow invocations through Restate ingress, kills the worker at a named barrier, restarts
it, and then reads the ledger after Restate completes recovery.

The measured unit is a workflow with one durable operation:
  b0 - crash before the ctx.run_typed action starts; retry runs the action once.
  b1 - crash inside the action after the ledger effect but before the action returns to Restate, so
       the durable step result is not journaled and recovery retries the action.
  b2 - crash after ctx.run_typed returns; recovery replays the journaled action result and does not
       re-run the external effect.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import signal
from datetime import timedelta
from pathlib import Path
from typing import Literal, TypedDict, cast

import restate

from .base import effect, two_phase_key

Mode = Literal["naive", "idem", "nondet", "twophase"]
Barrier = Literal["b0", "b1", "b2", "none"]

WORKFLOW_NAME = "CrashpointRestate"


class Request(TypedDict):
    ledger: str
    intent: str
    mode: Mode
    barrier: Barrier
    marker_dir: str


_RETRY = restate.InvocationRetryPolicy(
    initial_interval=timedelta(seconds=1),
    max_interval=timedelta(seconds=1),
    exponentiation_factor=1.0,
)
_RUN_OPTIONS = restate.RunOptions(
    type_hint=str,
    initial_retry_interval=timedelta(seconds=1),
    max_retry_interval=timedelta(seconds=1),
    retry_interval_factor=1.0,
)
_WORKFLOW = restate.Workflow(WORKFLOW_NAME, invocation_retry_policy=_RETRY)


def _validate_request(req: object) -> Request:
    if not isinstance(req, dict):
        raise ValueError("Restate request must be a JSON object")
    out = cast(dict[str, object], req)
    mode = out.get("mode")
    barrier = out.get("barrier")
    if mode not in ("naive", "idem", "nondet", "twophase"):
        raise ValueError(f"invalid mode: {mode!r}")
    if barrier not in ("b0", "b1", "b2", "none"):
        raise ValueError(f"invalid barrier: {barrier!r}")
    for key in ("ledger", "intent", "marker_dir"):
        if not isinstance(out.get(key), str) or not out[key]:
            raise ValueError(f"{key} must be a non-empty string")
    return {
        "ledger": cast(str, out["ledger"]),
        "intent": cast(str, out["intent"]),
        "mode": mode,
        "barrier": barrier,
        "marker_dir": cast(str, out["marker_dir"]),
    }


def _marker(req: Request) -> Path:
    digest = hashlib.sha256(
        f"{req['intent']}:{req['mode']}:{req['barrier']}".encode()
    ).hexdigest()[:16]
    return Path(req["marker_dir"]) / f"crashed-{digest}"


def _crash_once(req: Request) -> None:
    marker = _marker(req)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if marker.exists():
        return
    marker.write_text("crashed")
    os.kill(os.getpid(), signal.SIGKILL)
    raise SystemExit(1)  # pragma: no cover


def _charge(req: Request, key_override: str | None) -> str:
    effect(
        req["ledger"],
        req["intent"],
        req["mode"] in ("idem", "nondet", "twophase"),
        req["mode"] in ("nondet", "twophase"),
        key_override=key_override,
    )
    if req["barrier"] == "b1":
        _crash_once(req)
    return "effect-complete"


@_WORKFLOW.main("run")
async def run(ctx: restate.WorkflowContext, raw_req: object) -> str:
    req = _validate_request(raw_req)
    if req["barrier"] == "b0":
        _crash_once(req)

    key_override = None
    if req["mode"] == "twophase":
        key_override = two_phase_key(req["intent"])
        ctx.set("effect_key", key_override)

    await ctx.run_typed("ledger-effect", _charge, _RUN_OPTIONS, req, key_override)

    if req["barrier"] == "b2":
        _crash_once(req)
    return "ok"


app = restate.app([_WORKFLOW])


def serve(host: str, port: int) -> None:
    import hypercorn.asyncio
    import hypercorn.config

    config = hypercorn.config.Config()
    config.bind = [f"{host}:{port}"]
    config.use_reloader = False
    config.loglevel = "warning"
    asyncio.run(hypercorn.asyncio.serve(app, config))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9080)
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("Restate adapter only supports --serve")
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
