"""Print the predicted outcome matrix. `python -m crashpoint.model`.

This is the model talking to a human. It reads nothing, crashes nothing, reaches no runtime; it
renders the pure prediction in ``predict.PREDICTED`` so a reader can see, before any runtime is
crashed, what each engine is predicted to do to an external side effect at each crash barrier. The
harness later prints the same shape with an observed column beside the predicted one.
"""

from __future__ import annotations

from .barriers import BARRIERS
from .layers import Outcome
from .predict import PREDICTED, summarize
from .runtimes import RUNTIMES

_SYM = {Outcome.EXACTLY_ONCE: "ONCE", Outcome.DUPLICATED: "DUP ", Outcome.LOST: "LOST",
        Outcome.DIVERGED: "DIVERGED", Outcome.VOID: "VOID"}


def render() -> str:
    lines: list[str] = []
    head = f"{'runtime':22}" + "".join(f"{b.slug:>16}" for b in BARRIERS)
    lines.append(head)
    lines.append("-" * len(head))
    for rt in RUNTIMES:
        row = f"{rt.slug:22}"
        for b in BARRIERS:
            row += f"{_SYM[PREDICTED[rt.id][b.id].outcome]:>16}"
        tag = "  [TARGET]" if rt.is_target else "  [REF]" if rt.is_reference else \
            ("  real" if rt.is_real else "  control")
        lines.append(row + tag)
    lines.append("")
    for rt in RUNTIMES:
        s = summarize(rt)
        verdict = "clean" if s.is_clean else (
            f"dup={list(s.duplicated)} lost={list(s.lost)} diverged={list(s.diverged)}")
        lines.append(f"{rt.slug:22} exactly_once={len(s.exactly_once)}  {verdict}")
    lines.append("")
    lines.append("Legend: ONCE exactly-once, DUP duplicated (charged twice), DIVERGED crossed "
                 "twice with DIFFERENT content (two different charges), LOST never crossed, VOID "
                 "cannot certify. The BETWEEN column is the finding.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render())
