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
            "an optional JS/TS Nitro fixture was added against workflow@5.0.0-beta.47, but both "
            "nitro build/start and nitro dev fail before any run because the bundled Local World "
            'reports Invalid version string: "bundled" while initializing its data directory; '
            "there is still no faithful local crash/recovery substrate to measure"
        ),
        next_step=(
            "rerun the fixture when Vercel Workflow Local World exposes a valid package version in "
            "Nitro bundles, or switch to a supported managed/backend substrate whose "
            "crash/recovery semantics can be controlled and observed"
        ),
        source=(
            "Vercel Workflow docs: JS/TS and beta Python support; npm workflow@5.0.0-beta.47 "
            "Local World/Nitro probe on 2026-09-01"
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
