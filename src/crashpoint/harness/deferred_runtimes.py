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
        name="Vercel Workflow DevKit",
        adapter_id_prefix="r_vercel_workflow_",
        blocker=(
            "current docs include JS/TS and Python Workflow support, but this repo has no faithful "
            "worker/backend crash/recovery adapter yet"
        ),
        next_step=(
            "choose the current JS/TS or Python substrate, expose the same ledger boundary, "
            "validate local or managed-backend recovery semantics, then model and measure rows"
        ),
        source=(
            "Vercel Workflow docs: durable workflows/steps are available through JS/TS and Python"
        ),
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
