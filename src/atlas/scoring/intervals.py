"""Wilson intervals and the §6.2 supporting rates.

Methodology §6.2: "Mention Rate and Top-3 Rate use Wilson intervals.
Recommendation Stability remains the mean proportion of replicates matching
the modal RPV bucket. Stability below 0.60 is explicitly flagged as
volatile."

The Wilson implementation here was verified against
statsmodels.stats.proportion.proportion_confint(method="wilson") 0.14.6 to
1.1e-16 across 19 cases including the 0/n, n/n and n=1 boundaries — see
decision-register D-037. statsmodels is not a dependency; the check was run
against a throwaway install so no scipy/numpy stack enters the pipeline.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from atlas.adapters.base import OutcomeType
from atlas.scoring.types import POSITIVE_OUTCOMES, ReplicateObservation

# The standard normal 0.975 quantile, to full double precision. Pinned rather
# than rounded to 1.96 (a ~6e-6 difference on the bound) so the interval is
# exactly the standard quantile and matches any reference implementation
# (D-037).
Z_975 = 1.959963984540054

# §6.2: stability below this is explicitly flagged as volatile.
VOLATILE_STABILITY_THRESHOLD = 0.60

# §4.1 RPV values that count as "Top 3": ranks 1, 2 and 3.
TOP_3_RPV = frozenset({1.00, 0.80, 0.65})


@dataclass(frozen=True)
class WilsonInterval:
    proportion: float
    lower: float
    upper: float
    successes: int
    n: int


def wilson_interval(successes: int, n: int, z: float = Z_975) -> WilsonInterval:
    """Wilson score interval, no continuity correction.

    n == 0 returns the full [0, 1] range with a proportion of 0.0: no
    observations means no evidence, not a rate of zero.
    """
    if successes < 0 or n < 0 or successes > n:
        raise ValueError(f"invalid counts: {successes}/{n}")
    if n == 0:
        return WilsonInterval(proportion=0.0, lower=0.0, upper=1.0, successes=0, n=0)

    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half_width = (z / denominator) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return WilsonInterval(
        proportion=p,
        lower=max(0.0, centre - half_width),
        upper=min(1.0, centre + half_width),
        successes=successes,
        n=n,
    )


def mention_rate(observations: Iterable[ReplicateObservation]) -> WilsonInterval:
    """Share of valid replicates carrying a positive recommendation (§6.2).

    §4.1: "Only positive recommendations contribute to AVS. Source-only
    citations are valuable intelligence but are not recommendations" — so a
    SOURCE_ONLY_MENTION counts in the denominator, never the numerator.
    Entity conflicts are excluded from both (§4.1).
    """
    valid = [o for o in observations if o.is_scoreable]
    hits = sum(1 for o in valid if o.outcome_type in POSITIVE_OUTCOMES)
    return wilson_interval(hits, len(valid))


def top_3_rate(observations: Iterable[ReplicateObservation]) -> WilsonInterval:
    """Share of valid replicates recommended in positions 1-3 (§6.2)."""
    valid = [o for o in observations if o.is_scoreable]
    hits = sum(
        1
        for o in valid
        if o.outcome_type is OutcomeType.RANKED and o.rpv in TOP_3_RPV
    )
    return wilson_interval(hits, len(valid))


@dataclass(frozen=True)
class Stability:
    stability: float
    is_volatile: bool
    modal_rpv_by_cell: dict[tuple[str, str], float]


def recommendation_stability(observations: Sequence[ReplicateObservation]) -> Stability:
    """Mean proportion of replicates matching the modal RPV bucket (§6.2).

    Computed per prompt-platform cell, then averaged across cells — the
    "mean proportion" in §6.2 is over cells, since stability is a property of
    how consistently one prompt behaves on one platform. Ties for the mode
    resolve to the highest RPV, which is the conservative direction: it can
    only lower the matching proportion, never inflate it.
    """
    cells: dict[tuple[str, str], list[ReplicateObservation]] = {}
    for obs in observations:
        if obs.is_scoreable:
            cells.setdefault(obs.cell, []).append(obs)

    if not cells:
        return Stability(stability=0.0, is_volatile=True, modal_rpv_by_cell={})

    proportions: list[float] = []
    modes: dict[tuple[str, str], float] = {}
    for cell, replicates in cells.items():
        counts = Counter(o.rpv for o in replicates)
        top = max(counts.values())
        modal_rpv = max(rpv for rpv, c in counts.items() if c == top)
        modes[cell] = modal_rpv
        proportions.append(counts[modal_rpv] / len(replicates))

    stability = sum(proportions) / len(proportions)
    return Stability(
        stability=stability,
        is_volatile=stability < VOLATILE_STABILITY_THRESHOLD,
        modal_rpv_by_cell=modes,
    )
