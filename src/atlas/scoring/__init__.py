"""Atlas scoring engine — AVS (§4.3) and Atlas Readiness Score (§3.1).

Execution Plan Technical Lane step 9: "Add scoring engine only after raw
observations are trustworthy." Built after the four provider adapters (steps
4-7) and retry/idempotency/reconciliation (step 8) were proven.

Scope note: this package computes AVS from observed recommendation outcomes
and normalises P2-P5 into ARS. *Measuring* P2-P5 — the crawler, entity,
reputation and authority engines — is Technical Lane step 10 and is
deliberately not here. compute_ars takes the four pillar scores as given.

Input boundary: the `recommendations` table, and nothing upstream of it
(decision-register D-034). See atlas.scoring.loader.
"""

from atlas.scoring.ars import ARSResult, compute_ars, readiness_band
from atlas.scoring.avs import (
    AVSResult,
    compute_avs,
    platform_score,
    prompt_visibility_score,
    visibility_band,
)
from atlas.scoring.intervals import (
    Stability,
    WilsonInterval,
    mention_rate,
    recommendation_stability,
    top_3_rate,
    wilson_interval,
)
from atlas.scoring.loader import load_period
from atlas.scoring.movement import (
    BOOTSTRAP_RESAMPLES,
    COMPLETENESS_THRESHOLD_PCT,
    MINIMUM_REPORTABLE_CHANGE,
    MovementResult,
    MovementVerdict,
    movement_verdict,
    paired_bootstrap_deltas,
    percentile,
)
from atlas.scoring.types import (
    INTENT_WEIGHTS,
    PeriodObservations,
    ReplicateObservation,
    ScoreModelVersion,
    rpv_for,
)

__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "COMPLETENESS_THRESHOLD_PCT",
    "INTENT_WEIGHTS",
    "MINIMUM_REPORTABLE_CHANGE",
    "ARSResult",
    "AVSResult",
    "MovementResult",
    "MovementVerdict",
    "PeriodObservations",
    "ReplicateObservation",
    "ScoreModelVersion",
    "Stability",
    "WilsonInterval",
    "compute_ars",
    "compute_avs",
    "load_period",
    "mention_rate",
    "movement_verdict",
    "paired_bootstrap_deltas",
    "percentile",
    "platform_score",
    "prompt_visibility_score",
    "readiness_band",
    "recommendation_stability",
    "rpv_for",
    "top_3_rate",
    "visibility_band",
    "wilson_interval",
]
