"""The production AVS entry point — eligibility sourced from the §8.4 gate.

D-044: the eligible-platform list is read from `calibration_results`, not
typed by hand into a call. `atlas.scoring.avs.compute_avs` remains a pure
function taking an explicit list, because Execution Plan §4 and the G2 gate
require hand-calculated AVS values to be reproduced against the engine, and
that verification needs a kernel with no database in it. This module is the
wired path; the pure one is for verification.

Lives in the calibration package rather than the scoring package so the
dependency runs one way only: calibration knows about scoring, scoring never
imports calibration.
"""

from __future__ import annotations

from atlas.calibration.store import eligible_platforms
from atlas.scoring.avs import AVSResult, compute_avs
from atlas.scoring.loader import load_period


def compute_avs_for_property(
    db,
    *,
    run_plan_id: str,
    property_id: str,
    market_id: str,
    label: str = "baseline",
) -> tuple[AVSResult, tuple[str, ...]]:
    """Load one run plan's Layer A observations and score them against the
    platforms the §8.4 gate found eligible.

    Returns the result alongside the eligible list it was computed over, so a
    caller storing a score row can record both (§9 requires every score row
    carry its evidence references).

    Raises if no calibration exists for this property/market — D-036's rule
    that AVS is never computed over an inferred platform set is unchanged by
    moving the list into a table.
    """
    eligible = eligible_platforms(db, property_id, market_id)
    period = load_period(db, run_plan_id, label)
    return compute_avs(period.observations, list(eligible)), eligible
