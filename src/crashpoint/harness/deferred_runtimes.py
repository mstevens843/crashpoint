"""Deferred runtime adapters with precise blockers.

These are not model rows and not evidence. A runtime only enters ``runtimes.py`` after there is a
faithful crash/recovery substrate and a predicted row family to test. This inventory keeps deferred
work visible without letting an unavailable adapter look like an unmeasured regression.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model.runtimes import RUNTIME_IDS


@dataclass(frozen=True)
class DeferredRuntime:
    name: str
    adapter_id_prefix: str
    blocker: str
    next_step: str
    source: str


DEFERRED_RUNTIMES: tuple[DeferredRuntime, ...] = (
    DeferredRuntime(
        name="Restate",
        adapter_id_prefix="r_restate_",
        blocker=(
            "no local Restate server/CLI is installed in this workspace, and no faithful "
            "crash/recovery adapter has been validated against Restate's service journal"
        ),
        next_step=(
            "install a local Restate substrate, model durable-step replay rules, then add naive, "
            "idempotent, nondeterministic, and two-phase rows before measuring"
        ),
        source="Restate Python durable steps docs: ctx.run journals operation results",
    ),
    DeferredRuntime(
        name="Vercel Workflow DevKit",
        adapter_id_prefix="r_vercel_workflow_",
        blocker=(
            "the runtime is TypeScript/JavaScript-first and uses a Workflow Development Server; "
            "this Python harness has no faithful Node worker crash/recovery adapter yet"
        ),
        next_step=(
            "add a minimal Node harness that exposes the same ledger boundary, validate local "
            "development-server recovery semantics, then model and measure rows"
        ),
        source="Vercel Workflow docs and local WDK docs: workflows and steps run in Node",
    ),
)


def deferred_prefixes() -> tuple[str, ...]:
    return tuple(r.adapter_id_prefix for r in DEFERRED_RUNTIMES)


def render() -> str:
    lines = ["deferred runtime adapter inventory"]
    for runtime in DEFERRED_RUNTIMES:
        lines.append(
            f"- {runtime.name}: blocker: {runtime.blocker}; next: {runtime.next_step}"
        )
    return "\n".join(lines)


def main() -> int:
    bad = [
        prefix
        for prefix in deferred_prefixes()
        if any(runtime_id.startswith(prefix) for runtime_id in RUNTIME_IDS)
    ]
    if bad:
        print(f"INVALID deferred runtime prefix already modeled: {bad}")
        return 1
    print(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
