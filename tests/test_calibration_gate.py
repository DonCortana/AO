"""§8.4 gate — thresholds, the two pass routes, and what cannot promote.

The rules under test, quoting §8.4:

  "Platform eligibility requires raw mention agreement >=80% and Cohen kappa
   >=0.60. If prevalence makes kappa unstable, >=85% raw agreement plus
   documented manual review is required."
  "If sample size is lower, rank agreement is descriptive and cannot rescue a
   failed mention-agreement gate."
  "A platform failing the gate remains evidence-only and is excluded from AVS."
"""

from __future__ import annotations

import pytest

from atlas.calibration.gate import evaluate_platform
from atlas.calibration.types import (
    Contingency,
    KappaStability,
    PassRoute,
    PlatformAgreement,
    Verdict,
)


def agreement(
    *,
    platform="openai",
    n=40,
    raw=0.90,
    kappa=0.75,
    stability=KappaStability.STABLE,
    note="pe=0.5, n=40",
    co_mentions=20,
    rho=0.80,
):
    return PlatformAgreement(
        platform=platform,
        n_paired_units=n,
        raw_agreement=raw,
        cohen_kappa=kappa,
        kappa_stability=stability,
        kappa_note=note,
        co_mention_count=co_mentions,
        spearman_rho=rho,
        contingency=Contingency(both_yes=18, api_only=2, consumer_only=2, both_no=18),
    )


# ---------------------------------------------------------------------
# Route 1 — automatic
# ---------------------------------------------------------------------


def test_clean_pass_on_kappa_route():
    result = evaluate_platform(agreement())
    assert result.verdict is Verdict.ELIGIBLE
    assert result.pass_route is PassRoute.KAPPA
    assert result.review_required is False


def test_thresholds_are_inclusive_at_the_boundary():
    result = evaluate_platform(agreement(raw=0.80, kappa=0.60))
    assert result.verdict is Verdict.ELIGIBLE
    assert result.pass_route is PassRoute.KAPPA


@pytest.mark.parametrize(
    "raw,kappa",
    [(0.79, 0.75), (0.90, 0.59), (0.79, 0.59)],
)
def test_just_below_either_threshold_fails(raw, kappa):
    result = evaluate_platform(agreement(raw=raw, kappa=kappa))
    assert result.verdict is Verdict.EVIDENCE_ONLY
    assert result.pass_route is None


# ---------------------------------------------------------------------
# Route 2 — never automatic
# ---------------------------------------------------------------------


def test_fallback_route_refused_without_a_reviewer():
    """§8.4 requires 'documented manual review'. Code does not get to decide
    that prevalence excused a failed kappa."""
    result = evaluate_platform(
        agreement(raw=0.90, kappa=0.42, stability=KappaStability.UNSTABLE_PREVALENCE)
    )
    assert result.verdict is Verdict.EVIDENCE_ONLY
    assert result.review_required is True
    assert "NOT yet documented" in result.notes


def test_fallback_route_granted_with_documented_review():
    result = evaluate_platform(
        agreement(raw=0.90, kappa=0.42, stability=KappaStability.UNSTABLE_PREVALENCE),
        reviewer="Doud",
        review_approved=True,
    )
    assert result.verdict is Verdict.ELIGIBLE
    assert result.pass_route is PassRoute.RAW_AGREEMENT_MANUAL_REVIEW
    assert result.reviewer == "Doud"


def test_fallback_needs_the_higher_85_percent_bar():
    """84% is above the 80% base bar but below the fallback's 85%."""
    result = evaluate_platform(
        agreement(raw=0.84, kappa=0.42, stability=KappaStability.UNSTABLE_PREVALENCE),
        reviewer="Doud",
        review_approved=True,
    )
    assert result.verdict is Verdict.EVIDENCE_ONLY
    assert result.review_required is False


def test_undefined_kappa_routes_to_the_fallback():
    """pe == 1 — both layers agreed on everything. Perfect raw agreement, no
    kappa. Without the fallback this would fail despite total agreement."""
    result = evaluate_platform(
        agreement(
            raw=1.0,
            kappa=None,
            stability=KappaStability.UNDEFINED_DEGENERATE,
            note="kappa undefined: both layers assigned all 40 cells to one category (pe=1)",
        ),
        reviewer="Doud",
        review_approved=True,
    )
    assert result.verdict is Verdict.ELIGIBLE
    assert result.pass_route is PassRoute.RAW_AGREEMENT_MANUAL_REVIEW


def test_review_is_ignored_when_the_kappa_route_already_passed():
    """A platform that clears kappa passes on its own terms and is never
    recorded as having leaned on manual review."""
    result = evaluate_platform(agreement(), reviewer="Doud", review_approved=True)
    assert result.pass_route is PassRoute.KAPPA


# ---------------------------------------------------------------------
# Spearman cannot promote or demote
# ---------------------------------------------------------------------


def test_strong_rho_cannot_rescue_a_failed_mention_gate():
    result = evaluate_platform(agreement(raw=0.60, kappa=0.20, rho=0.99, co_mentions=30))
    assert result.verdict is Verdict.EVIDENCE_ONLY


def test_low_rho_does_not_demote_a_passing_platform_but_is_recorded():
    result = evaluate_platform(agreement(rho=0.20, co_mentions=30))
    assert result.verdict is Verdict.ELIGIBLE
    assert "CAVEAT" in result.notes
    assert "0.2000" in result.notes


def test_below_floor_rank_agreement_is_named_as_descriptive_only():
    result = evaluate_platform(agreement(co_mentions=7, rho=None))
    assert result.verdict is Verdict.ELIGIBLE
    assert "descriptive only" in result.notes
    assert "cannot rescue" in result.notes


# ---------------------------------------------------------------------
# The unpairable case (D-042)
# ---------------------------------------------------------------------


def test_no_paired_cells_is_undefined_not_failed():
    result = evaluate_platform(
        PlatformAgreement(
            platform="google_ai",
            n_paired_units=0,
            raw_agreement=None,
            cohen_kappa=None,
            kappa_stability=KappaStability.UNSTABLE_SMALL_SAMPLE,
            kappa_note="no paired cells",
            co_mention_count=0,
            spearman_rho=None,
            contingency=Contingency(0, 0, 0, 0),
        )
    )
    assert result.verdict is Verdict.EVIDENCE_ONLY
    assert "undefined" in result.notes
    assert "D-042" in result.notes
