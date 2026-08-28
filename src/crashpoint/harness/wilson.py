"""The Wilson score interval for a binomial proportion. Stays inside [0, 1] and is asymmetric near
the boundaries, where per-cell pass rates cluster (a deterministic control is ~1.0, a naive runtime
under the lethal barrier is ~0.0), which is exactly where the normal/Wald interval misbehaves.
"""

from __future__ import annotations


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% (z=1.96) Wilson interval for k successes in n trials."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = p + z2 / (2 * n)
    half = z * ((p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5)
    return (round((centre - half) / denom, 4), round((centre + half) / denom, 4))
