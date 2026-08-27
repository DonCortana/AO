"""Atlas Readiness Score — Methodology §3.1.

    ARS = (20 x P2 + 15 x P3 + 20 x P4 + 15 x P5) / 70

The four readiness pillars keep their original relative weights, normalised
from 70 total points to a 0-100 readiness score.

Computing P2-P5 themselves is Execution Plan Technical Lane step 10 (crawler,
entity, reputation and authority engines) and is deliberately not done here.
This module takes the four pillar scores as given and normalises them, which
is the whole of §3.1.

ARS is a *controllable readiness* measure and is reported separately from
AVS, never combined with it — decision-register D-028 retired the AVI
composite specifically so a readiness improvement can never be mistaken for
an observed recommendation outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

# Methodology §3.1. Point weights, not percentages — the divisor is their sum.
PILLAR_WEIGHTS: dict[str, int] = {"P2": 20, "P3": 15, "P4": 20, "P5": 15}
ARS_DIVISOR = sum(PILLAR_WEIGHTS.values())  # 70

# Methodology §3.1 bands, half-open (decision-register D-040).
READINESS_BANDS: tuple[tuple[float, str], ...] = (
    (90.0, "advanced"),
    (75.0, "strong"),
    (60.0, "established"),
    (40.0, "developing"),
    (0.0, "fragile"),
)


def readiness_band(ars: float) -> str:
    """§3.1 readiness band for an ARS. Bands are half-open (D-040)."""
    if not 0.0 <= ars <= 100.0:
        raise ValueError(f"ARS {ars} outside 0-100")
    for lower, label in READINESS_BANDS:
        if ars >= lower:
            return label
    raise AssertionError("unreachable: bands cover [0, 100]")


@dataclass(frozen=True)
class ARSResult:
    ars: float
    band: str
    pillars: dict[str, float]


def compute_ars(p2: float, p3: float, p4: float, p5: float) -> ARSResult:
    """ARS = (20*P2 + 15*P3 + 20*P4 + 15*P5) / 70, per §3.1.

    Each pillar score is itself on 0-100. Every pillar is required: there is
    no partial ARS, because dropping a pillar would silently reweight the
    other three and change what the number means.
    """
    pillars = {"P2": p2, "P3": p3, "P4": p4, "P5": p5}
    for name, value in pillars.items():
        if value is None:
            raise ValueError(f"pillar {name} is required — ARS has no partial form (§3.1)")
        if not 0.0 <= value <= 100.0:
            raise ValueError(f"pillar {name} = {value} outside 0-100")

    ars = sum(PILLAR_WEIGHTS[name] * value for name, value in pillars.items()) / ARS_DIVISOR
    return ARSResult(ars=ars, band=readiness_band(ars), pillars=pillars)
