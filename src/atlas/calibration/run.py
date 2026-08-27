"""Run one §8.4 calibration cycle end to end.

    load Layer A + Layer B cells (loader)
      -> collapse to cell judgments by majority vote (D-045)
      -> compute agreement / kappa / Spearman (agreement)
      -> apply the §8.4 thresholds (gate)
      -> write calibration_results and emit the eligible-platform list (store)

D-042: Google AI has no Layer A leg, so it can never be paired and can never
pass. It is still carried through this pipeline — its consumer captures are
loaded and its (unpairable) result recorded as evidence-only — so that its
exclusion is a stored, dated fact rather than a silent absence from the table.
"""

from __future__ import annotations

from dataclasses import replace

from atlas.calibration.agreement import platform_agreement
from atlas.calibration.gate import evaluate_platform
from atlas.calibration.loader import load_cells
from atlas.calibration.store import write_calibration_run
from atlas.calibration.types import (
    LAYER_API,
    LAYER_CONSUMER,
    CalibrationRun,
    PlatformGateResult,
)

# D-042: structurally consumer-only in v1.0 — no API surface exists for
# Google Search AI Overviews, so the gate is undefined rather than failed.
CONSUMER_ONLY_PLATFORMS = frozenset({"google_ai"})


def run_gate(
    db,
    *,
    calibration_run_id: str,
    property_id: str,
    market_id: str,
    prompt_set_version: str,
    api_run_plan_ids: list[str],
    consumer_run_plan_ids: list[str],
    reviews: dict[str, tuple[str, bool]] | None = None,
    persist: bool = True,
) -> CalibrationRun:
    """Evaluate every platform present on either layer.

    `reviews` supplies the §8.4 documented-manual-review evidence for the
    fallback route, as {platform: (reviewer, approved)}. It is only consulted
    for platforms that actually need that route.
    """
    reviews = reviews or {}

    api_cells = load_cells(db, api_run_plan_ids, layer=LAYER_API)
    consumer_cells = load_cells(db, consumer_run_plan_ids, layer=LAYER_CONSUMER)

    platforms = sorted(set(api_cells) | set(consumer_cells))
    results: list[PlatformGateResult] = []

    for platform in platforms:
        agreement = platform_agreement(
            platform,
            api_cells.get(platform, []),
            consumer_cells.get(platform, []),
        )
        reviewer, approved = reviews.get(platform, (None, False))
        result = evaluate_platform(
            agreement, reviewer=reviewer, review_approved=approved
        )

        if platform in CONSUMER_ONLY_PLATFORMS:
            # Make the structural case unmistakable in the stored note. Without
            # this, an evidence-only verdict for Google AI reads as "it was
            # measured and failed", which is the wrong conclusion to leave in
            # the record.
            result = replace(
                result,
                notes=(
                    "D-042: no Layer A benchmark exists for this surface, so the "
                    "§8.4 gate is undefined for it rather than failed. Evidence-"
                    "only in v1.0 by structure, not by measured disagreement. "
                    f"[{result.notes}]"
                ),
                review_required=False,
            )
        results.append(result)

    run = CalibrationRun(
        calibration_run_id=calibration_run_id,
        property_id=property_id,
        market_id=market_id,
        prompt_set_version=prompt_set_version,
        results=tuple(results),
    )

    if persist:
        write_calibration_run(db, run)
    return run
