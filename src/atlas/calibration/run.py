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


def _check_prompt_set_version(db, run_plan_ids: list[str], claimed: str) -> str:
    """D-085: derive the prompt-set version from the plans and refuse on
    disagreement with the operator-supplied argument.

    `prompt_set_version` arrives here as a hand-typed keyword and is written
    straight through to `calibration_results.prompt_set_version` (store.py) —
    NOT NULL, append-only, score-bearing, and required by §9 to travel with
    every score row. Nothing used to check it against the plans passed in the
    same call, so a typo became a permanent wrong provenance stamp on a
    calibration result with no symptom at write time.

    Refuses four ways, all fail-closed and none silent:

      - a run_plan_id that does not exist;
      - a plan with no recorded prompt_set_version (D-084's column is
        nullable for run types that have no prompt set, and a calibration gate
        run over such a plan is exactly the provenance hole this closes — the
        derivation cannot be performed, so the guarantee cannot be given);
      - plans that disagree with each other;
      - plans that agree with each other but not with `claimed`.

    Returns the derived version, which equals `claimed` whenever it returns.
    """
    rows = (
        db.table("run_plans")
        .select("id,prompt_set_version")
        .in_("id", run_plan_ids)
        .execute()
        .data
    )
    by_id = {r["id"]: r for r in rows}

    missing = sorted(set(run_plan_ids) - set(by_id))
    if missing:
        raise ValueError(
            f"run plans do not exist: {missing}. Refusing to gate against plans "
            "that cannot be read (D-085)."
        )

    # Explicit rather than falsy: NULL and '' are different mistakes — nobody
    # recorded a version, versus something wrote an empty one — and both must
    # fail here, but a bare `not` would also swallow any future non-string
    # falsy value without anyone noticing which case applied.
    unrecorded = sorted(
        plan_id
        for plan_id, row in by_id.items()
        if row.get("prompt_set_version") is None
        or not str(row["prompt_set_version"]).strip()
    )
    if unrecorded:
        raise ValueError(
            f"run plans record no prompt_set_version: {unrecorded}. D-085 "
            "requires the gate to derive the version from its plans rather "
            "than trust the argument; a null column makes that impossible, and "
            "§9 requires the version to travel with the score row. Backfill the "
            "plan (migration 0011's pattern) before gating."
        )

    derived = {r["prompt_set_version"] for r in by_id.values()}
    if len(derived) > 1:
        raise ValueError(
            f"run plans span more than one prompt_set_version: "
            f"{sorted(derived)}. A single §8.4 gate result is meaningful only "
            "against one prompt set (§9), so this is a refusal rather than a "
            "choice between them (D-085)."
        )

    actual = derived.pop()
    if actual != claimed:
        raise ValueError(
            f"prompt_set_version={claimed!r} was supplied, but the run plans "
            f"record {actual!r}. Refusing to stamp a calibration result with a "
            "version its own plans contradict (D-085)."
        )
    return actual


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

    `prompt_set_version` is cross-checked against the plans it is passed
    alongside and the call is refused on disagreement (D-085) — see
    `_check_prompt_set_version`. It remains an argument rather than becoming
    purely derived so that the caller's intent is stated and can be
    contradicted; a silently derived value would record whatever the plans
    happened to say.
    """
    reviews = reviews or {}

    # D-085 — before anything is computed, let alone written. Both layers'
    # plans are checked together: a Layer A and a Layer B plan measuring
    # different prompt sets cannot produce a meaningful paired comparison,
    # which is the whole point of the §8.4 gate.
    prompt_set_version = _check_prompt_set_version(
        db, list(api_run_plan_ids) + list(consumer_run_plan_ids), prompt_set_version
    )

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
