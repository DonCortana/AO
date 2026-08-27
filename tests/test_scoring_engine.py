"""Scoring engine behaviour: the guards, exclusions and reproducibility rules
that the hand-calculation fixtures do not exercise directly.

Covers decision-register D-036 (eligible platforms are explicit), D-037
(seeded, reproducible bootstrap), D-038 (the four §6.2 resolutions) and
D-041 (rank beyond the §4.1 table).
"""

from __future__ import annotations

import pytest

from atlas.adapters.base import OutcomeType
from atlas.scoring import (
    INTENT_WEIGHTS,
    PeriodObservations,
    ReplicateObservation,
    compute_ars,
    compute_avs,
    mention_rate,
    movement_verdict,
    paired_bootstrap_deltas,
    percentile,
    platform_score,
    prompt_visibility_score,
    recommendation_stability,
    rpv_for,
    top_3_rate,
)

EXACT = 1e-12


def obs(prompt, tier, platform, index, rpv, outcome=OutcomeType.RANKED, conflict=False):
    return ReplicateObservation(prompt, tier, platform, index, rpv, outcome, conflict)


def period(label, observations, planned=None, scoreable=None):
    return PeriodObservations(
        label=label,
        observations=tuple(observations),
        planned_observation_count=planned if planned is not None else len(observations),
        scoreable_observation_count=scoreable if scoreable is not None else len(observations),
    )


def constant_cell(prompt, tier, platform, rpv, n=5):
    return [obs(prompt, tier, platform, i, rpv) for i in range(n)]


# ---------------------------------------------------------------------
# §4.1 RPV table
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "rank,expected",
    [(1, 1.00), (2, 0.80), (3, 0.65), (4, 0.45), (5, 0.45), (6, 0.25), (10, 0.25)],
)
def test_rpv_table_ranks(rank, expected):
    assert rpv_for(OutcomeType.RANKED, rank) == expected


def test_rpv_beyond_rank_ten_is_zero():
    """D-041: §4.1 stops at rank 10; 11+ scores 0.00 rather than
    extrapolating a band the methodology never wrote."""
    assert rpv_for(OutcomeType.RANKED, 11) == 0.00
    assert rpv_for(OutcomeType.RANKED, 50) == 0.00


def test_rpv_unordered_positive_and_zero_outcomes():
    assert rpv_for(OutcomeType.UNORDERED_POSITIVE, None) == 0.30
    assert rpv_for(OutcomeType.ABSENT, None) == 0.00
    assert rpv_for(OutcomeType.SOURCE_ONLY_MENTION, None) == 0.00
    assert rpv_for(OutcomeType.NEGATIVE_MENTION, None) == 0.00


def test_rpv_entity_conflict_is_excluded_not_scored():
    """§4.1 marks entity conflict "Excluded", which is not a value."""
    assert rpv_for(OutcomeType.ENTITY_CONFLICT, None) is None


def test_ranked_without_rank_is_rejected():
    with pytest.raises(ValueError, match="requires a rank"):
        rpv_for(OutcomeType.RANKED, None)


def test_intent_weights_match_methodology():
    assert INTENT_WEIGHTS == {"A": 1.00, "B": 0.80, "C": 0.60, "D": 0.30}


def test_rpv_outside_range_is_rejected():
    with pytest.raises(ValueError, match="outside 0.00-1.00"):
        obs("P1", "A", "openai", 0, 1.5)


def test_unknown_intent_tier_is_rejected():
    with pytest.raises(ValueError, match="unknown intent tier"):
        obs("P1", "Z", "openai", 0, 0.5)


# ---------------------------------------------------------------------
# D-036 — eligible platforms are explicit
# ---------------------------------------------------------------------


def test_eligible_platforms_is_required():
    with pytest.raises(ValueError, match="eligible_platforms is required"):
        compute_avs(constant_cell("P1", "A", "openai", 0.5), [])


def test_ineligible_platform_is_excluded_and_reported():
    """§8.4: a platform failing the calibration gate stays evidence-only."""
    observations = constant_cell("P1", "A", "openai", 1.00) + constant_cell(
        "P1", "A", "perplexity", 0.00
    )
    result = compute_avs(observations, ["openai"])
    # perplexity's zeros must not drag the AVS down.
    assert result.avs == pytest.approx(100.0, abs=EXACT)
    assert result.excluded.ineligible_platforms == ("perplexity",)


def test_eligible_platform_with_no_data_is_refused():
    """D-036: refuse to average over a missing term rather than treating an
    absent platform as zero visibility."""
    with pytest.raises(ValueError, match="no scoreable observation"):
        compute_avs(constant_cell("P1", "A", "openai", 0.5), ["openai", "gemini"])


def test_duplicate_eligible_platform_is_rejected():
    with pytest.raises(ValueError, match="duplicate platform"):
        compute_avs(constant_cell("P1", "A", "openai", 0.5), ["openai", "openai"])


# ---------------------------------------------------------------------
# D-038c — empty cells are dropped, never imputed as zero
# ---------------------------------------------------------------------


def test_cell_of_only_entity_conflicts_has_no_pvs():
    replicates = [obs("P1", "A", "openai", i, 0.0, OutcomeType.ENTITY_CONFLICT, True) for i in range(3)]
    assert prompt_visibility_score(replicates) is None


def test_empty_cell_drops_its_weight_from_the_denominator():
    """P1 (tier A, w=1.00) scores 0.80. P2 (tier C, w=0.60) is entirely
    entity conflicts, so it is dropped along with its weight:

        PlatformScore = (1.00 x 0.80) / 1.00 = 0.80   (not 0.80/1.60 = 0.50)
    """
    observations = [
        obs("P1", "A", "openai", 0, 0.80),
        obs("P2", "C", "openai", 0, 0.00, OutcomeType.ENTITY_CONFLICT, True),
        obs("P2", "C", "openai", 1, 0.00, OutcomeType.ENTITY_CONFLICT, True),
    ]
    assert platform_score(observations, "openai") == pytest.approx(0.80, abs=EXACT)
    result = compute_avs(observations, ["openai"])
    assert result.avs == pytest.approx(80.0, abs=EXACT)
    assert result.excluded.empty_cells == 1


# ---------------------------------------------------------------------
# D-037 — reproducibility
# ---------------------------------------------------------------------


def _two_prompt_periods():
    baseline = period(
        "baseline",
        constant_cell("P1", "A", "openai", 0.25)
        + [obs("P2", "B", "openai", i, v) for i, v in enumerate([0.45, 0.25, 0.65, 0.00, 0.45])],
    )
    validation = period(
        "validation",
        constant_cell("P1", "A", "openai", 0.65)
        + [obs("P2", "B", "openai", i, v) for i, v in enumerate([0.80, 0.65, 0.45, 0.65, 1.00])],
    )
    return baseline, validation


def test_same_seed_reproduces_the_interval_exactly():
    """A client-facing interval must be reproducible from stored inputs
    (§9, §8.4). The seed is stored with the result for exactly this."""
    baseline, validation = _two_prompt_periods()
    first = movement_verdict(baseline, validation, ["openai"], seed=20260827)
    second = movement_verdict(baseline, validation, ["openai"], seed=20260827)

    assert first.ci_lower == second.ci_lower
    assert first.ci_upper == second.ci_upper
    assert first.delta == second.delta
    assert first.seed == 20260827


def test_different_seed_moves_the_interval_but_not_the_point_estimate():
    """The point estimate comes from the observed data, not the bootstrap
    distribution (D-038), so it is seed-independent."""
    baseline, validation = _two_prompt_periods()
    first = movement_verdict(baseline, validation, ["openai"], seed=1)
    second = movement_verdict(baseline, validation, ["openai"], seed=2)

    assert first.delta == pytest.approx(second.delta, abs=EXACT)
    assert (first.ci_lower, first.ci_upper) != (second.ci_lower, second.ci_upper)
    # Same data, so the intervals should still agree closely.
    assert first.ci_lower == pytest.approx(second.ci_lower, abs=2.0)


def test_percentile_matches_known_values():
    """Linear interpolation between closest ranks — matches
    numpy.percentile(method="linear"), verified in D-037."""
    values = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0.0) == 0.0
    assert percentile(values, 100.0) == 4.0
    assert percentile(values, 50.0) == 2.0
    # position = 0.25 * 4 = 1.0 -> exactly the element at index 1
    assert percentile(values, 25.0) == 1.0
    # position = 0.1 * 4 = 0.4 -> 0.0 + (1.0 - 0.0) * 0.4
    assert percentile(values, 10.0) == pytest.approx(0.4, abs=EXACT)


def test_percentile_of_single_value():
    assert percentile([7.5], 2.5) == 7.5


# ---------------------------------------------------------------------
# D-038d / §6.1 / §4.2 — frame and eligibility guards
# ---------------------------------------------------------------------


def test_unpaired_prompt_is_excluded_and_reported():
    """A prompt seen in only one period cannot carry a paired delta."""
    baseline = period(
        "baseline",
        constant_cell("P1", "A", "openai", 0.25) + constant_cell("P_ONLY_BASE", "A", "openai", 1.00),
    )
    validation = period("validation", constant_cell("P1", "A", "openai", 0.65))
    result = movement_verdict(baseline, validation, ["openai"], seed=1)

    assert result.excluded.unpaired_prompts == ("P_ONLY_BASE",)
    # The delta itself is still the observed AVS difference, which does
    # include the unpaired prompt in the baseline AVS — only the bootstrap
    # frame drops it.
    assert result.verdict is not None


def test_no_shared_prompt_is_an_error_not_a_verdict():
    baseline = period("baseline", constant_cell("P1", "A", "openai", 0.25))
    validation = period("validation", constant_cell("P2", "A", "openai", 0.65))
    with pytest.raises(ValueError, match="no prompt is observed in both periods"):
        paired_bootstrap_deltas(baseline, validation, ["openai"], seed=1, resamples=10)


def test_intent_tier_change_between_periods_is_rejected():
    """§4.2: "Intent tier is immutable for the life of a prompt version.\""""
    baseline = period("baseline", constant_cell("P1", "A", "openai", 0.25))
    validation = period("validation", constant_cell("P1", "C", "openai", 0.65))
    with pytest.raises(ValueError, match="changed intent tier"):
        paired_bootstrap_deltas(baseline, validation, ["openai"], seed=1, resamples=10)


@pytest.mark.parametrize("run_type", ["sentinel", "benchmark_monthly", "discovery"])
def test_run_types_that_may_not_support_a_verdict(run_type):
    """§6.1 marks these "may support movement verdict: No\"."""
    baseline, validation = _two_prompt_periods()
    with pytest.raises(ValueError, match="may not support a movement verdict"):
        movement_verdict(baseline, validation, ["openai"], seed=1, run_type=run_type)


def test_quarterly_benchmark_is_flagged_secondary_evidence():
    """§6.1 rates Quarterly Benchmark validation "Secondary evidence"."""
    baseline, validation = _two_prompt_periods()
    result = movement_verdict(
        baseline, validation, ["openai"], seed=1, run_type="benchmark_quarterly"
    )
    assert result.is_secondary_evidence is True

    frozen = movement_verdict(baseline, validation, ["openai"], seed=1)
    assert frozen.is_secondary_evidence is False


def test_zero_planned_observations_is_rejected():
    empty_plan = period("baseline", constant_cell("P1", "A", "openai", 0.5), planned=0)
    with pytest.raises(ValueError, match="planned_observation_count must be positive"):
        _ = empty_plan.completeness_pct


# ---------------------------------------------------------------------
# §6.2 supporting rates
# ---------------------------------------------------------------------


def test_mention_rate_excludes_source_only_from_the_numerator():
    """§4.1: source-only citations "are not recommendations, so they cannot
    raise AVS" — they belong in the denominator, not the numerator."""
    observations = [
        obs("P1", "A", "openai", 0, 1.00),
        obs("P1", "A", "openai", 1, 0.30, OutcomeType.UNORDERED_POSITIVE),
        obs("P1", "A", "openai", 2, 0.00, OutcomeType.SOURCE_ONLY_MENTION),
        obs("P1", "A", "openai", 3, 0.00, OutcomeType.ABSENT),
    ]
    rate = mention_rate(observations)
    assert (rate.successes, rate.n) == (2, 4)
    assert rate.proportion == 0.5


def test_mention_rate_excludes_entity_conflicts_from_both_sides():
    observations = [
        obs("P1", "A", "openai", 0, 1.00),
        obs("P1", "A", "openai", 1, 0.00, OutcomeType.ENTITY_CONFLICT, True),
    ]
    rate = mention_rate(observations)
    assert (rate.successes, rate.n) == (1, 1)


def test_top_3_rate_counts_ranks_one_to_three():
    observations = [
        obs("P1", "A", "openai", 0, 1.00),  # rank 1
        obs("P1", "A", "openai", 1, 0.80),  # rank 2
        obs("P1", "A", "openai", 2, 0.65),  # rank 3
        obs("P1", "A", "openai", 3, 0.45),  # rank 4-5
        obs("P1", "A", "openai", 4, 0.30, OutcomeType.UNORDERED_POSITIVE),
    ]
    rate = top_3_rate(observations)
    assert (rate.successes, rate.n) == (3, 5)


def test_stability_is_one_when_every_replicate_agrees():
    result = recommendation_stability(constant_cell("P1", "A", "openai", 0.80))
    assert result.stability == pytest.approx(1.0, abs=EXACT)
    assert result.is_volatile is False


def test_stability_below_threshold_is_flagged_volatile():
    """§6.2: "Stability below 0.60 is explicitly flagged as volatile."

    Five replicates, four distinct RPVs: modal bucket holds 2 of 5 = 0.40.
    """
    observations = [
        obs("P1", "A", "openai", 0, 1.00),
        obs("P1", "A", "openai", 1, 1.00),
        obs("P1", "A", "openai", 2, 0.80),
        obs("P1", "A", "openai", 3, 0.65),
        obs("P1", "A", "openai", 4, 0.45),
    ]
    result = recommendation_stability(observations)
    assert result.stability == pytest.approx(0.40, abs=EXACT)
    assert result.is_volatile is True
    assert result.modal_rpv_by_cell[("P1", "openai")] == 1.00


def test_stability_averages_across_cells():
    """1.0 on one cell and 0.6 on another averages to 0.8."""
    observations = constant_cell("P1", "A", "openai", 0.80) + [
        obs("P2", "A", "openai", 0, 0.25),
        obs("P2", "A", "openai", 1, 0.25),
        obs("P2", "A", "openai", 2, 0.25),
        obs("P2", "A", "openai", 3, 1.00),
        obs("P2", "A", "openai", 4, 0.80),
    ]
    result = recommendation_stability(observations)
    assert result.stability == pytest.approx(0.8, abs=EXACT)


# ---------------------------------------------------------------------
# ARS guards
# ---------------------------------------------------------------------


def test_ars_requires_every_pillar():
    with pytest.raises(ValueError, match="pillar P3 is required"):
        compute_ars(50, None, 50, 50)


def test_ars_rejects_out_of_range_pillar():
    with pytest.raises(ValueError, match="outside 0-100"):
        compute_ars(50, 50, 120, 50)
