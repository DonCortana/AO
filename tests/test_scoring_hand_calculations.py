"""Hand-calculated fixtures the scoring engine must reproduce exactly.

Methodology §8.4: "v1.0 is frozen only after P0 defects are resolved or
formally owned, **the score engine reproduces hand calculations**, and
calibration results are documented." Execution Plan roadmap Week 6 restates
it as the acceptance criterion: "Hand calculations match engine."

Every case below carries its full arithmetic in the docstring so a human can
audit the expected value without running anything. These are not illustrative
examples — they are the freeze gate. If one of them changes, the methodology
changed, and that needs a decision-register row first.
"""

from __future__ import annotations

import pytest

from atlas.adapters.base import OutcomeType
from atlas.scoring import (
    PeriodObservations,
    ReplicateObservation,
    compute_ars,
    compute_avs,
    movement_verdict,
    readiness_band,
    visibility_band,
    wilson_interval,
)
from atlas.scoring.movement import MovementVerdict

EXACT = 1e-12


def obs(prompt, tier, platform, index, rpv, outcome=OutcomeType.RANKED, conflict=False):
    return ReplicateObservation(
        prompt_id=prompt,
        intent_tier=tier,
        platform=platform,
        replicate_index=index,
        rpv=rpv,
        outcome_type=outcome,
        entity_conflict=conflict,
    )


def constant_cell(prompt, tier, platform, rpv, n=5, outcome=OutcomeType.RANKED):
    return [obs(prompt, tier, platform, i, rpv, outcome) for i in range(n)]


# =====================================================================
# AVS — Methodology §4.3
# =====================================================================


def test_avs_single_prompt_single_platform():
    """One tier-A prompt, one platform, five replicates.

        replicate RPVs: 1.00, 0.80, 0.65, 0.00 (parsed absent), 0.45
        PVS           = (1.00 + 0.80 + 0.65 + 0.00 + 0.45) / 5
                      = 2.90 / 5
                      = 0.58
        PlatformScore = 0.58            (single prompt: the weight cancels)
        AVS           = 100 x 0.58      = 58.0
        band          = established     ([45, 65) per §4.4 / D-040)
    """
    observations = [
        obs("P1", "A", "openai", 0, 1.00),
        obs("P1", "A", "openai", 1, 0.80),
        obs("P1", "A", "openai", 2, 0.65),
        obs("P1", "A", "openai", 3, 0.00, OutcomeType.ABSENT),
        obs("P1", "A", "openai", 4, 0.45),
    ]
    result = compute_avs(observations, ["openai"])
    assert result.avs == pytest.approx(58.0, abs=EXACT)
    assert result.band == "established"
    assert result.prompt_visibility_scores[("P1", "openai")] == pytest.approx(0.58, abs=EXACT)


def test_avs_two_tiers_two_platforms():
    """Two prompts across two intent tiers, two platforms.

        P1 tier A -> weight 1.00      P2 tier C -> weight 0.60

        openai:
            PVS(P1) = (1.00 + 0.80) / 2 = 0.90
            PVS(P2) = (0.25 + 0.00) / 2 = 0.125
            PlatformScore = (1.00 x 0.90 + 0.60 x 0.125) / (1.00 + 0.60)
                          = (0.900 + 0.075) / 1.60
                          = 0.975 / 1.60
                          = 0.609375

        anthropic:
            PVS(P1) = (0.65 + 0.45) / 2 = 0.55
            PVS(P2) = (0.30 + 0.30) / 2 = 0.30
            PlatformScore = (1.00 x 0.55 + 0.60 x 0.30) / 1.60
                          = (0.55 + 0.18) / 1.60
                          = 0.73 / 1.60
                          = 0.45625

        AVS = 100 x (0.609375 + 0.45625) / 2
            = 100 x 1.065625 / 2
            = 100 x 0.5328125
            = 53.28125
        band = established
    """
    observations = [
        obs("P1", "A", "openai", 0, 1.00),
        obs("P1", "A", "openai", 1, 0.80),
        obs("P2", "C", "openai", 0, 0.25),
        obs("P2", "C", "openai", 1, 0.00, OutcomeType.ABSENT),
        obs("P1", "A", "anthropic", 0, 0.65),
        obs("P1", "A", "anthropic", 1, 0.45),
        obs("P2", "C", "anthropic", 0, 0.30, OutcomeType.UNORDERED_POSITIVE),
        obs("P2", "C", "anthropic", 1, 0.30, OutcomeType.UNORDERED_POSITIVE),
    ]
    result = compute_avs(observations, ["openai", "anthropic"])
    assert result.platform_scores["openai"] == pytest.approx(0.609375, abs=EXACT)
    assert result.platform_scores["anthropic"] == pytest.approx(0.45625, abs=EXACT)
    assert result.avs == pytest.approx(53.28125, abs=EXACT)
    assert result.band == "established"


def test_avs_entity_conflict_is_excluded_not_zeroed():
    """§4.1: a wrong-entity / name-collision observation is *excluded*, not
    scored 0.00.

        replicates: 1.00, 0.80, ENTITY_CONFLICT
        PVS = (1.00 + 0.80) / 2 = 0.90      <- conflict dropped entirely
        AVS = 90.0, band = leading

    Had the conflict been scored as a zero the answer would be
    (1.00 + 0.80 + 0.00) / 3 = 0.60 -> AVS 60.0, a 30-point error in the
    direction that invents a visibility problem the venue does not have.
    """
    observations = [
        obs("P1", "A", "openai", 0, 1.00),
        obs("P1", "A", "openai", 1, 0.80),
        obs("P1", "A", "openai", 2, 0.00, OutcomeType.ENTITY_CONFLICT, conflict=True),
    ]
    result = compute_avs(observations, ["openai"])
    assert result.avs == pytest.approx(90.0, abs=EXACT)
    assert result.avs != pytest.approx(60.0, abs=EXACT)
    assert result.band == "leading"
    assert result.excluded.entity_conflicts == 1


def test_avs_zero_outcomes_score_zero_but_stay_distinct():
    """§4.1: source-only, negative and absent all score 0.00, and all three
    are genuine observations that belong in the PVS denominator.

        replicates: 1.00, SOURCE_ONLY (0.00), NEGATIVE (0.00), ABSENT (0.00)
        PVS = 1.00 / 4 = 0.25  ->  AVS = 25.0, band = emerging
    """
    observations = [
        obs("P1", "A", "openai", 0, 1.00),
        obs("P1", "A", "openai", 1, 0.00, OutcomeType.SOURCE_ONLY_MENTION),
        obs("P1", "A", "openai", 2, 0.00, OutcomeType.NEGATIVE_MENTION),
        obs("P1", "A", "openai", 3, 0.00, OutcomeType.ABSENT),
    ]
    result = compute_avs(observations, ["openai"])
    assert result.avs == pytest.approx(25.0, abs=EXACT)
    assert result.band == "emerging"
    # Distinct in the evidence trail even though they score identically.
    kinds = {o.outcome_type for o in observations}
    assert len(kinds) == 4


# =====================================================================
# ARS — Methodology §3.1
# =====================================================================


@pytest.mark.parametrize(
    "p2,p3,p4,p5,expected,band",
    [
        # (20x80 + 15x60 + 20x70 + 15x50) / 70
        # = (1600 + 900 + 1400 + 750) / 70 = 4650 / 70 = 66.428571428571...
        (80, 60, 70, 50, 4650 / 70, "established"),
        # All pillars perfect: (20+15+20+15) x 100 / 70 = 7000 / 70 = 100.0
        (100, 100, 100, 100, 100.0, "advanced"),
        # All pillars zero.
        (0, 0, 0, 0, 0.0, "fragile"),
        # (20x50 + 15x20 + 20x45 + 15x30) / 70
        # = (1000 + 300 + 900 + 450) / 70 = 2650 / 70 = 37.857142857142...
        (50, 20, 45, 30, 2650 / 70, "fragile"),
        # Uniform pillars normalise to themselves: 70 x 40 / 70 = 40.0 exactly,
        # which is the Developing lower bound (D-040: bands are half-open).
        (40, 40, 40, 40, 40.0, "developing"),
        # 39.5 sits in the gap between the printed "0-39" and "40-59" ranges.
        (39.5, 39.5, 39.5, 39.5, 39.5, "fragile"),
    ],
)
def test_ars_hand_calculations(p2, p3, p4, p5, expected, band):
    result = compute_ars(p2, p3, p4, p5)
    assert result.ars == pytest.approx(expected, abs=EXACT)
    assert result.band == band


def test_ars_weights_are_the_methodology_weights():
    """P2 and P4 carry 20 points, P3 and P5 carry 15, divisor is 70 (§3.1).

    Moving 10 points onto P2 (weight 20) must raise ARS by exactly
    20 x 10 / 70 = 2.857142857...; the same 10 points on P3 (weight 15)
    raises it by 15 x 10 / 70 = 2.142857142...
    """
    base = compute_ars(50, 50, 50, 50).ars
    assert base == pytest.approx(50.0, abs=EXACT)
    assert compute_ars(60, 50, 50, 50).ars - base == pytest.approx(200 / 70, abs=EXACT)
    assert compute_ars(50, 60, 50, 50).ars - base == pytest.approx(150 / 70, abs=EXACT)


# =====================================================================
# Bands — §3.1, §4.4, half-open per D-040
# =====================================================================


@pytest.mark.parametrize(
    "avs,band",
    [
        (0.0, "not_observed"), (9.999, "not_observed"),
        (10.0, "detectable"), (24.999, "detectable"),
        (25.0, "emerging"), (44.999, "emerging"),
        (45.0, "established"), (64.999, "established"),
        (65.0, "strong"), (84.999, "strong"),
        (85.0, "leading"), (100.0, "leading"),
    ],
)
def test_visibility_band_boundaries(avs, band):
    assert visibility_band(avs) == band


@pytest.mark.parametrize(
    "ars,band",
    [
        (0.0, "fragile"), (39.999, "fragile"),
        (40.0, "developing"), (59.999, "developing"),
        (60.0, "established"), (74.999, "established"),
        (75.0, "strong"), (89.999, "strong"),
        (90.0, "advanced"), (100.0, "advanced"),
    ],
)
def test_readiness_band_boundaries(ars, band):
    assert readiness_band(ars) == band


# =====================================================================
# Wilson intervals — §6.2
# =====================================================================


def test_wilson_is_symmetric_about_one_half():
    """For p = 0.5 the Wilson centre is exactly 0.5 by symmetry, so the
    interval must be symmetric about 0.5 — checkable without a reference."""
    interval = wilson_interval(5, 10)
    assert interval.proportion == 0.5
    assert (interval.lower + interval.upper) / 2 == pytest.approx(0.5, abs=EXACT)


@pytest.mark.parametrize(
    "successes,n,lower,upper",
    [
        # Verified against statsmodels.stats.proportion.proportion_confint(
        #   method="wilson") 0.14.6 to 1.1e-16 (decision-register D-037).
        (0, 10, 0.0, 0.2775327998628892),
        (1, 10, 0.017876213095072868, 0.4041500267952385),
        (5, 10, 0.236593090512564, 0.7634069094874361),
        (10, 10, 0.7224672001371107, 1.0),
        (1, 100, 0.0017674320637892, 0.0544861961792669),
        (123, 456, 0.2310496275616356, 0.3122712366796194),
    ],
)
def test_wilson_reference_values(successes, n, lower, upper):
    interval = wilson_interval(successes, n)
    assert interval.lower == pytest.approx(lower, abs=1e-12)
    assert interval.upper == pytest.approx(upper, abs=1e-12)


def test_wilson_no_observations_is_full_range_not_zero_rate():
    """No observations is no evidence, not a rate of zero."""
    interval = wilson_interval(0, 0)
    assert (interval.proportion, interval.lower, interval.upper) == (0.0, 0.0, 1.0)


# =====================================================================
# Movement verdict — §6.2 five-way table
# =====================================================================


def period(label, observations, planned=None):
    scoreable = len(observations)
    return PeriodObservations(
        label=label,
        observations=tuple(observations),
        planned_observation_count=planned if planned is not None else scoreable,
        scoreable_observation_count=scoreable,
    )


def test_verdict_improvement_degenerate_bootstrap():
    """One prompt, one platform, zero within-cell variance.

        baseline   replicates all 0.25 -> PVS 0.25 -> AVS 25.0
        validation replicates all 0.65 -> PVS 0.65 -> AVS 65.0
        delta = 65.0 - 25.0 = +40.0

    With a single prompt the prompt resample always redraws that same prompt,
    and resampling a constant list of replicates always yields the same mean,
    so *every one* of the 10,000 bootstrap deltas is exactly +40.0 and the
    95% interval is exactly (40.0, 40.0).

        delta >= +5.0 and CI excludes 0 on the positive side -> Improvement
    """
    baseline = period("baseline", constant_cell("P1", "A", "openai", 0.25))
    validation = period("validation", constant_cell("P1", "A", "openai", 0.65))
    result = movement_verdict(baseline, validation, ["openai"], seed=1)

    assert result.baseline_avs == pytest.approx(25.0, abs=EXACT)
    assert result.validation_avs == pytest.approx(65.0, abs=EXACT)
    assert result.delta == pytest.approx(40.0, abs=EXACT)
    assert result.ci_lower == pytest.approx(40.0, abs=EXACT)
    assert result.ci_upper == pytest.approx(40.0, abs=EXACT)
    assert result.verdict is MovementVerdict.IMPROVEMENT
    assert result.resamples == 10_000


def test_verdict_regression_degenerate_bootstrap():
    """The mirror image: 0.65 -> 0.25, delta -40.0, CI exactly (-40, -40).

        delta <= -5.0 and CI excludes 0 on the negative side -> Regression
    """
    baseline = period("baseline", constant_cell("P1", "A", "openai", 0.65))
    validation = period("validation", constant_cell("P1", "A", "openai", 0.25))
    result = movement_verdict(baseline, validation, ["openai"], seed=1)

    assert result.delta == pytest.approx(-40.0, abs=EXACT)
    assert result.ci_lower == pytest.approx(-40.0, abs=EXACT)
    assert result.ci_upper == pytest.approx(-40.0, abs=EXACT)
    assert result.verdict is MovementVerdict.REGRESSION


def test_verdict_no_meaningful_movement_below_mrc():
    """A real but sub-MRC change.

        baseline   replicates: 0.45 x 5      -> PVS 0.45 -> AVS 45.0
        validation replicates: 0.45 x 4, 0.65 -> PVS (1.80 + 0.65)/5
                                              = 2.45/5 = 0.49 -> AVS 49.0
        delta = +4.0, and |4.0| < 5.0 -> No meaningful movement

    The CI is irrelevant here: §6.2 gates this verdict on the delta alone.
    """
    baseline = period("baseline", constant_cell("P1", "A", "openai", 0.45))
    validation = period(
        "validation",
        constant_cell("P1", "A", "openai", 0.45, n=4)
        + [obs("P1", "A", "openai", 4, 0.65)],
    )
    result = movement_verdict(baseline, validation, ["openai"], seed=1)

    assert result.validation_avs == pytest.approx(49.0, abs=EXACT)
    assert result.delta == pytest.approx(4.0, abs=EXACT)
    assert result.verdict is MovementVerdict.NO_MEANINGFUL_MOVEMENT


def test_verdict_inconclusive_large_delta_ci_includes_zero():
    """A large delta driven entirely by one of two prompts.

        Both prompts tier A (equal weight 1.00), one platform.
        baseline:   P1 all 0.00, P2 all 0.00  -> AVS 0.0
        validation: P1 all 1.00, P2 all 0.00
                    PlatformScore = (1.00 x 1.00 + 1.00 x 0.00) / 2.00 = 0.50
                                                                  -> AVS 50.0
        delta = +50.0

    The tier-A frame holds 2 prompts, so each resample draws 2 with
    replacement. Every cell has zero within-cell variance, so the draw alone
    determines the delta:

        {P1, P1}  p = 1/4  -> validation AVS 100.0 -> delta 100.0
        {P1, P2}  p = 1/2  -> validation AVS  50.0 -> delta  50.0
        {P2, P2}  p = 1/4  -> validation AVS   0.0 -> delta   0.0

    The bottom 25% of the distribution is exactly 0.0, so the 2.5th
    percentile is 0.0 and the 97.5th is 100.0: CI = (0.0, 100.0).

        |delta| >= 5.0 but the CI includes 0 -> Inconclusive

    "Excludes 0" is strict (D-038): a bound of exactly 0.0 includes 0.
    """
    baseline = period(
        "baseline",
        constant_cell("P1", "A", "openai", 0.00, outcome=OutcomeType.ABSENT)
        + constant_cell("P2", "A", "openai", 0.00, outcome=OutcomeType.ABSENT),
    )
    validation = period(
        "validation",
        constant_cell("P1", "A", "openai", 1.00)
        + constant_cell("P2", "A", "openai", 0.00, outcome=OutcomeType.ABSENT),
    )
    result = movement_verdict(baseline, validation, ["openai"], seed=1)

    assert result.baseline_avs == pytest.approx(0.0, abs=EXACT)
    assert result.validation_avs == pytest.approx(50.0, abs=EXACT)
    assert result.delta == pytest.approx(50.0, abs=EXACT)
    assert result.ci_lower == pytest.approx(0.0, abs=EXACT)
    assert result.ci_upper == pytest.approx(100.0, abs=EXACT)
    assert result.verdict is MovementVerdict.INCONCLUSIVE


def test_verdict_incomplete_below_ninety_percent():
    """§6.2 / Operating System §11: completeness < 90% issues no verdict.

        planned 100, scoreable 89 -> 89.0% -> Incomplete

    No delta, no interval, no AVS is reported at all — the cycle did not
    produce a measurement, so there is nothing to report a movement on.
    """
    observations = constant_cell("P1", "A", "openai", 0.25, n=5)
    baseline = PeriodObservations(
        label="baseline",
        observations=tuple(observations),
        planned_observation_count=100,
        scoreable_observation_count=89,
    )
    validation = period("validation", constant_cell("P1", "A", "openai", 0.65))
    result = movement_verdict(baseline, validation, ["openai"], seed=1)

    assert result.verdict is MovementVerdict.INCOMPLETE
    assert result.completeness_pct == pytest.approx(89.0, abs=EXACT)
    assert result.delta is None
    assert result.ci_lower is None and result.ci_upper is None
    assert result.baseline_avs is None and result.validation_avs is None
    assert result.resamples == 0


def test_verdict_exactly_ninety_percent_is_complete_enough():
    """The threshold is "< 90%", so exactly 90.0% still issues a verdict."""
    baseline = PeriodObservations(
        label="baseline",
        observations=tuple(constant_cell("P1", "A", "openai", 0.25)),
        planned_observation_count=100,
        scoreable_observation_count=90,
    )
    validation = period("validation", constant_cell("P1", "A", "openai", 0.65))
    result = movement_verdict(baseline, validation, ["openai"], seed=1)

    assert result.completeness_pct == pytest.approx(90.0, abs=EXACT)
    assert result.verdict is MovementVerdict.IMPROVEMENT


def test_completeness_reports_the_lower_of_the_two_periods():
    """D-039: a well-covered baseline must not mask a thin validation run."""
    baseline = PeriodObservations(
        label="baseline",
        observations=tuple(constant_cell("P1", "A", "openai", 0.25)),
        planned_observation_count=100,
        scoreable_observation_count=100,
    )
    validation = PeriodObservations(
        label="validation",
        observations=tuple(constant_cell("P1", "A", "openai", 0.65)),
        planned_observation_count=100,
        scoreable_observation_count=80,
    )
    result = movement_verdict(baseline, validation, ["openai"], seed=1)

    assert result.baseline_completeness_pct == pytest.approx(100.0, abs=EXACT)
    assert result.validation_completeness_pct == pytest.approx(80.0, abs=EXACT)
    assert result.completeness_pct == pytest.approx(80.0, abs=EXACT)
    assert result.verdict is MovementVerdict.INCOMPLETE
