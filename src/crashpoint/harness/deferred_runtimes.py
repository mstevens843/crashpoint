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
        name="Vercel Workflow DevKit on the managed Vercel World",
        adapter_id_prefix="r_vwf_managed_",
        blocker=(
            "the Local World rows (r_vwf_*) are modeled and measured through "
            "crashpoint.harness.vercel_matrix, but the managed Vercel World "
            "(@workflow/world-vercel: Vercel Queues plus Vercel Functions) runs on infrastructure "
            "this sandbox cannot SIGKILL at a named barrier or inspect afterwards, so no faithful "
            "managed crash/recovery substrate has been validated"
        ),
        next_step=(
            "deploy the fixture to a Vercel project the author controls, crash the function from "
            "inside the step at the same barriers, read the ledger through a reachable invoke "
            "endpoint, and only then model managed-world rows separately from the Local World rows"
        ),
        source=(
            "Vercel Workflow docs: Worlds (Local, Postgres, Vercel); world-vercel requires "
            "VERCEL_DEPLOYMENT_ID and platform queues; Local World measured 2026-09-01 with "
            "workflow@5.0.0-beta.47 and @workflow/world-local@5.0.0-beta.41"
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
