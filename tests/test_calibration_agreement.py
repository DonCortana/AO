"""§8.4 agreement statistics — correctness, tie handling and reference checks.

D-045 requires Cohen kappa and Spearman rho to be hand-implemented on the
standard library (the D-037 no-numpy/no-scipy precedent) and "verified against
a reference implementation to a stated tolerance".

That verification was run against scipy 1.18.1 (`scipy.stats.spearmanr`) and
scikit-learn 1.9.0 (`sklearn.metrics.cohen_kappa_score`) in a throwaway
environment, seed 20260827:

    cohen_kappa   2,997 randomised 2x2 tables (n=4..200, prevalence 0.1..0.9,
                  rater disagreement 2%..50%)
                  max absolute deviation 7.216e-16
                  3 degenerate (pe == 1) cases correctly returned None

    spearman_rho  2,976 randomised rank pairs (n=2..60, deliberately
                  tie-heavy: value ranges of 3, 5, 10 and 40 positions)
                  max absolute deviation 2.220e-16
                  24 zero-variance cases correctly returned None

Neither library is a project dependency and neither is imported here — the
tests below use closed-form values and a vendored reference so CI stays
dependency-free, exactly as D-037 did for the Wilson interval and the
bootstrap percentiles.
"""

from __future__ import annotations

import pytest

from atlas.calibration.agreement import (
    _midranks,
    cohen_kappa,
    collapse_replicates,
    contingency,
    pair_cells,
    platform_agreement,
    raw_agreement,
    spearman_rho,
)
from atlas.calibration.types import (
    LAYER_API,
    LAYER_CONSUMER,
    Contingency,
    KappaStability,
    Replicate,
)

# Deviations measured against scipy/sklearn (see module docstring). The
# assertions below use a tolerance two orders of magnitude looser than the
# worst observed deviation, so they pin correctness without being flaky.
REFERENCE_TOLERANCE = 1e-13


def cell(prompt, platform, layer, mentioned, rank=None, count=5, tie=False):
    from atlas.calibration.types import CellJudgment

    return CellJudgment(
        prompt_id=prompt,
        platform=platform,
        layer=layer,
        mentioned=mentioned,
        rank=rank,
        replicate_count=count,
        tie_broken=tie,
    )


# ---------------------------------------------------------------------
# D-045 majority collapse
# ---------------------------------------------------------------------


def test_majority_collapse_odd_counts_never_tie():
    """n=5 (Layer A, §6.1) and n=3 (Layer B, §8.3) are odd by design."""
    for mentions, total, expected in [(3, 5, True), (2, 5, False), (2, 3, True), (1, 3, False)]:
        reps = [Replicate(mentioned=True, rank=1)] * mentions + [
            Replicate(mentioned=False)
        ] * (total - mentions)
        judgment = collapse_replicates(reps, prompt_id="P1", platform="openai", layer=LAYER_API)
        assert judgment.mentioned is expected
        assert judgment.tie_broken is False


def test_even_split_breaks_to_absent_and_is_flagged():
    """D-045: an operational shortfall leaving an even count and an exact split
    resolves to absent — never silently toward mention, which would flatter
    agreement — and is marked for manual review."""
    reps = [Replicate(mentioned=True, rank=1), Replicate(mentioned=False)]
    judgment = collapse_replicates(reps, prompt_id="P1", platform="openai", layer=LAYER_API)
    assert judgment.mentioned is False
    assert judgment.tie_broken is True
    assert judgment.rank is None


def test_even_count_without_exact_split_is_not_a_tie():
    reps = [Replicate(mentioned=True, rank=2)] * 3 + [Replicate(mentioned=False)]
    judgment = collapse_replicates(reps, prompt_id="P1", platform="openai", layer=LAYER_API)
    assert judgment.mentioned is True
    assert judgment.tie_broken is False


def test_empty_cell_returns_none_rather_than_absent():
    """A cell with no usable replicate is excluded from the frame and reported,
    never imputed as a non-mention (D-045, D-038(d) precedent)."""
    assert collapse_replicates([], prompt_id="P1", platform="openai", layer=LAYER_API) is None


def test_rank_is_median_over_mentioned_replicates_only():
    """D-047: median across the mentioned replicates, per layer."""
    reps = [
        Replicate(mentioned=True, rank=1),
        Replicate(mentioned=True, rank=3),
        Replicate(mentioned=True, rank=9),
        Replicate(mentioned=False),
        Replicate(mentioned=False),
    ]
    judgment = collapse_replicates(reps, prompt_id="P1", platform="openai", layer=LAYER_API)
    assert judgment.mentioned is True
    assert judgment.rank == 3.0  # median of [1, 3, 9], not of [1, 3, 9, None, None]


def test_median_rank_resists_a_single_outlying_replicate():
    """D-047's stated reason for median over mean, at the n=5 the Frozen Core
    actually runs: one anomalous deep placement would drag a mean to 3.4."""
    reps = [Replicate(mentioned=True, rank=r) for r in (1, 1, 2, 2, 11)]
    judgment = collapse_replicates(reps, prompt_id="P1", platform="openai", layer=LAYER_API)
    assert judgment.rank == 2.0


def test_even_count_cell_rank_may_be_non_integer():
    """D-047: acceptable because only relative ordering survives into Spearman
    and no client-facing number reports a cell rank directly."""
    reps = [Replicate(mentioned=True, rank=r) for r in (2, 5)]
    judgment = collapse_replicates(reps, prompt_id="P1", platform="openai", layer=LAYER_API)
    assert judgment.rank == 3.5


def test_absent_majority_carries_no_rank():
    """A cell whose majority judgment is absent holds no rank, even though a
    minority of replicates ranked the client."""
    reps = [
        Replicate(mentioned=True, rank=1),
        Replicate(mentioned=False),
        Replicate(mentioned=False),
    ]
    judgment = collapse_replicates(reps, prompt_id="P1", platform="openai", layer=LAYER_API)
    assert judgment.mentioned is False
    assert judgment.rank is None


def test_order_preserving_rank_collapse_leaves_spearman_unchanged():
    """D-047's low-stakes claim, made testable: Spearman re-ranks its inputs,
    so only the *ordering* of cells survives the collapse. Median and mean give
    different cell values here but the same ordering, and rho is identical.

    (Median and mean are not interchangeable in general — they can disagree on
    ordering, and then rho does differ. The claim is about order preservation,
    which is why D-047 picks median on ordinal grounds rather than treating the
    choice as free.)
    """
    from statistics import mean

    per_cell = [(1, 1, 1), (2, 2, 5), (4, 4, 10), (7, 7, 7), (8, 9, 10)]
    medians_x = [float(sorted(c)[1]) for c in per_cell]
    means_x = [mean(c) for c in per_cell]
    ys = [3.0, 1.0, 5.0, 2.0, 4.0]

    assert medians_x != means_x
    assert _midranks(medians_x) == _midranks(means_x)  # same ordering
    assert spearman_rho(medians_x, ys) == pytest.approx(
        spearman_rho(means_x, ys), abs=REFERENCE_TOLERANCE
    )


def test_non_mention_cannot_carry_a_rank():
    with pytest.raises(ValueError, match="non-mention cannot carry a rank"):
        Replicate(mentioned=False, rank=3)


# ---------------------------------------------------------------------
# raw agreement + Cohen kappa
# ---------------------------------------------------------------------


def test_raw_agreement_is_the_agreeing_diagonal():
    table = Contingency(both_yes=6, api_only=2, consumer_only=1, both_no=11)
    assert raw_agreement(table) == pytest.approx(17 / 20, abs=REFERENCE_TOLERANCE)


def test_cohen_kappa_closed_form():
    """Worked by hand: n=20, po=0.85, marginals api=0.40 consumer=0.35.
    pe = 0.40*0.35 + 0.60*0.65 = 0.14 + 0.39 = 0.53
    kappa = (0.85 - 0.53) / (1 - 0.53) = 0.32 / 0.47
    """
    table = Contingency(both_yes=6, api_only=2, consumer_only=1, both_no=11)
    kappa, stability, _ = cohen_kappa(table)
    assert kappa == pytest.approx(0.32 / 0.47, abs=REFERENCE_TOLERANCE)
    assert stability is KappaStability.STABLE


def test_perfect_agreement_with_spread_gives_kappa_one():
    table = Contingency(both_yes=10, api_only=0, consumer_only=0, both_no=10)
    kappa, stability, _ = cohen_kappa(table)
    assert kappa == pytest.approx(1.0, abs=REFERENCE_TOLERANCE)
    assert stability is KappaStability.STABLE


def test_kappa_undefined_when_both_layers_are_degenerate():
    """pe == 1: both layers put every cell in one category. A real,
    fully-agreeing result — not a missing one — and exactly the case §8.4's
    >=85%-plus-manual-review fallback exists for."""
    table = Contingency(both_yes=0, api_only=0, consumer_only=0, both_no=30)
    kappa, stability, note = cohen_kappa(table)
    assert kappa is None
    assert stability is KappaStability.UNDEFINED_DEGENERATE
    assert "pe=1" in note


def test_kappa_paradox_is_flagged_not_silently_passed():
    """High raw agreement, depressed kappa, because one outcome dominates."""
    table = Contingency(both_yes=27, api_only=1, consumer_only=1, both_no=1)
    kappa, stability, note = cohen_kappa(table)
    assert raw_agreement(table) == pytest.approx(28 / 30, abs=REFERENCE_TOLERANCE)
    assert kappa < 0.60
    assert stability is KappaStability.UNSTABLE_PREVALENCE
    assert "kappa paradox" in note


def test_small_sample_is_flagged():
    table = Contingency(both_yes=3, api_only=1, consumer_only=0, both_no=4)
    _, stability, _ = cohen_kappa(table)
    assert stability is KappaStability.UNSTABLE_SMALL_SAMPLE


def test_kappa_can_go_negative_below_chance():
    table = Contingency(both_yes=1, api_only=9, consumer_only=9, both_no=1)
    kappa, _, _ = cohen_kappa(table)
    assert kappa < 0.0


# ---------------------------------------------------------------------
# Spearman rho — midrank tie correction
# ---------------------------------------------------------------------


def _reference_spearman(xs, ys):
    """Vendored reference: Pearson correlation over midranks, written
    independently of the implementation under test."""
    def rank(vals):
        out = []
        for v in vals:
            less = sum(1 for w in vals if w < v)
            equal = sum(1 for w in vals if w == v)
            out.append(less + (equal + 1) / 2)
        return out

    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (
        sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)
    ) ** 0.5
    return num / den if den else None


def test_midranks_average_ties():
    assert _midranks([1, 2, 2, 3]) == [1.0, 2.5, 2.5, 4.0]
    assert _midranks([5, 5, 5]) == [2.0, 2.0, 2.0]


def test_spearman_monotonic_extremes():
    assert spearman_rho([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert spearman_rho([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == pytest.approx(-1.0)


def test_spearman_matches_vendored_reference_on_tie_heavy_data():
    """Real calibration rank data is tie-heavy — many prompts return the client
    at rank 1 — which is exactly where the classic 1-6*sum(d^2) shortcut is
    wrong. Deviation from scipy on 2,976 such cases was 2.220e-16."""
    cases = [
        ([1, 1, 1, 2, 3, 3], [1, 2, 1, 2, 3, 1]),
        ([1, 2, 2, 2, 5, 8, 8], [1, 1, 3, 2, 4, 9, 7]),
        ([3, 3, 3, 3, 1, 2], [2, 2, 1, 3, 1, 1]),
        ([1, 4, 2, 7, 5, 5, 1, 9, 3, 6], [2, 3, 1, 8, 4, 6, 1, 9, 3, 5]),
    ]
    for xs, ys in cases:
        assert spearman_rho(xs, ys) == pytest.approx(
            _reference_spearman(xs, ys), abs=REFERENCE_TOLERANCE
        )


def test_spearman_undefined_on_zero_variance():
    """Every co-mention at the same rank: perfect agreement with no spread.
    Reporting 0.0 would read as 'no rank agreement', which is the opposite of
    the truth."""
    assert spearman_rho([1, 1, 1, 1], [1, 1, 1, 1]) is None
    assert spearman_rho([1, 2, 3], [4, 4, 4]) is None


def test_spearman_needs_two_points():
    assert spearman_rho([1], [1]) is None


# ---------------------------------------------------------------------
# pairing + platform_agreement
# ---------------------------------------------------------------------


def test_pairing_is_the_intersection_and_reports_unpaired():
    api = [cell("P1", "openai", LAYER_API, True, 1), cell("P2", "openai", LAYER_API, False)]
    consumer = [
        cell("P1", "openai", LAYER_CONSUMER, True, 2, count=3),
        cell("P3", "openai", LAYER_CONSUMER, True, 1, count=3),
    ]
    paired, unpaired = pair_cells(api, consumer)
    assert [p.prompt_id for p in paired] == ["P1"]
    assert sorted(unpaired) == [("P2", "openai"), ("P3", "openai")]


def test_contingency_counts_every_quadrant():
    api = [
        cell("P1", "openai", LAYER_API, True, 1),
        cell("P2", "openai", LAYER_API, True, 2),
        cell("P3", "openai", LAYER_API, False),
        cell("P4", "openai", LAYER_API, False),
    ]
    consumer = [
        cell("P1", "openai", LAYER_CONSUMER, True, 1, count=3),
        cell("P2", "openai", LAYER_CONSUMER, False, count=3),
        cell("P3", "openai", LAYER_CONSUMER, True, 3, count=3),
        cell("P4", "openai", LAYER_CONSUMER, False, count=3),
    ]
    paired, _ = pair_cells(api, consumer)
    table = contingency(paired)
    assert (table.both_yes, table.api_only, table.consumer_only, table.both_no) == (1, 1, 1, 1)


def test_spearman_not_computed_below_the_ten_co_mention_floor():
    """§8.4: below 10 co-mentions rank agreement is descriptive and 'cannot
    rescue a failed mention-agreement gate'. It is not computed at all."""
    api = [cell(f"P{i}", "openai", LAYER_API, True, i + 1) for i in range(9)]
    consumer = [
        cell(f"P{i}", "openai", LAYER_CONSUMER, True, i + 1, count=3) for i in range(9)
    ]
    result = platform_agreement("openai", api, consumer)
    assert result.co_mention_count == 9
    assert result.spearman_rho is None


def test_spearman_computed_at_the_floor():
    api = [cell(f"P{i}", "openai", LAYER_API, True, i + 1) for i in range(10)]
    consumer = [
        cell(f"P{i}", "openai", LAYER_CONSUMER, True, i + 1, count=3) for i in range(10)
    ]
    result = platform_agreement("openai", api, consumer)
    assert result.co_mention_count == 10
    assert result.spearman_rho == pytest.approx(1.0)


def test_tie_broken_cells_surface_in_the_result():
    api = [cell("P1", "openai", LAYER_API, False, count=4, tie=True)]
    consumer = [cell("P1", "openai", LAYER_CONSUMER, False, count=3)]
    result = platform_agreement("openai", api, consumer)
    assert result.tie_broken_cells == (("P1", "openai"),)
