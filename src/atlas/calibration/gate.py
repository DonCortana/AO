"""The §8.4 calibration gate — turning agreement statistics into eligibility.

    "Platform eligibility requires raw mention agreement >=80% and Cohen
     kappa >=0.60. If prevalence makes kappa unstable, >=85% raw agreement
     plus documented manual review is required."

    "Rank agreement is reported with Spearman correlation where at least 10
     co-mentioned observations exist; rho >=0.50 is the working acceptance
     threshold. If sample size is lower, rank agreement is descriptive and
     cannot rescue a failed mention-agreement gate."

    "A platform failing the gate remains evidence-only and is excluded from
     AVS."

Two deliberate design rules here:

1. The **kappa route is fully automatic** — both of its thresholds are stated
   numerically in §8.4, so no judgment is added. The **fallback route is
   never automatic**: §8.4 requires "documented manual review", so this module
   will not grant it without a named reviewer. Code does not get to decide
   that prevalence excused a failed kappa; it can only surface that the case
   applies and wait for a human. That is what `review_required` means.

2. Spearman **cannot promote**. §8.4 says a low sample size means rank
   agreement "cannot rescue a failed mention-agreement gate", and the
   symmetric reading is that a strong rho cannot rescue one either. rho is
   reported, stored and — where it is computable and falls short — recorded
   as a caveat on an otherwise-passing platform, but it never flips a verdict
   by itself.
"""

from __future__ import annotations

from atlas.calibration.types import (
    MIN_CO_MENTIONS_FOR_SPEARMAN,
    MIN_COHEN_KAPPA,
    MIN_RAW_AGREEMENT,
    MIN_RAW_AGREEMENT_FALLBACK,
    MIN_SPEARMAN_RHO,
    KappaStability,
    PassRoute,
    PlatformAgreement,
    PlatformGateResult,
    Verdict,
)


def evaluate_platform(
    agreement: PlatformAgreement,
    *,
    reviewer: str | None = None,
    review_approved: bool = False,
) -> PlatformGateResult:
    """Apply §8.4 to one platform's statistics.

    `review_approved` + `reviewer` are the documented-manual-review evidence
    for the fallback route. They are ignored unless the platform actually
    needs that route: a platform that clears kappa passes on its own terms and
    is never recorded as having used manual review.
    """
    notes: list[str] = []
    raw = agreement.raw_agreement
    kappa = agreement.cohen_kappa

    if raw is None or agreement.n_paired_units == 0:
        return PlatformGateResult(
            agreement=agreement,
            verdict=Verdict.EVIDENCE_ONLY,
            pass_route=None,
            reviewer=None,
            notes="no paired cells — the gate is undefined for this platform, "
            "which is not the same as failing it (see D-042 for the "
            "structural case)",
        )

    # ---- Route 1: raw agreement >=80% AND kappa >=0.60. Fully automatic. ----
    if raw >= MIN_RAW_AGREEMENT and kappa is not None and kappa >= MIN_COHEN_KAPPA:
        notes.append(
            f"passed on kappa route: raw agreement {raw:.4f} >= "
            f"{MIN_RAW_AGREEMENT}, kappa {kappa:.4f} >= {MIN_COHEN_KAPPA}"
        )
        notes.append(_rank_note(agreement))
        if agreement.kappa_stability is not KappaStability.STABLE:
            notes.append(f"kappa stability advisory: {agreement.kappa_note}")
        return PlatformGateResult(
            agreement=agreement,
            verdict=Verdict.ELIGIBLE,
            pass_route=PassRoute.KAPPA,
            reviewer=reviewer,
            notes="; ".join(n for n in notes if n),
        )

    # ---- Route 2: the prevalence fallback. Never automatic. ----
    kappa_unusable = kappa is None or agreement.kappa_stability in (
        KappaStability.UNDEFINED_DEGENERATE,
        KappaStability.UNSTABLE_PREVALENCE,
        KappaStability.UNSTABLE_SMALL_SAMPLE,
    )
    fallback_available = raw >= MIN_RAW_AGREEMENT_FALLBACK and kappa_unusable

    if fallback_available:
        notes.append(
            f"fallback route available: raw agreement {raw:.4f} >= "
            f"{MIN_RAW_AGREEMENT_FALLBACK} and kappa is not usable at face "
            f"value ({agreement.kappa_note})"
        )
        notes.append(_rank_note(agreement))
        if review_approved and reviewer:
            notes.append(f"manual review documented by {reviewer}")
            return PlatformGateResult(
                agreement=agreement,
                verdict=Verdict.ELIGIBLE,
                pass_route=PassRoute.RAW_AGREEMENT_MANUAL_REVIEW,
                reviewer=reviewer,
                notes="; ".join(n for n in notes if n),
            )
        # §8.4 requires the review to exist. Until it does, the platform is
        # evidence-only — the safe direction, and reversible by re-running
        # the gate with the review recorded.
        notes.append(
            "manual review NOT yet documented — platform stays evidence-only "
            "until a named reviewer signs off (§8.4)"
        )
        return PlatformGateResult(
            agreement=agreement,
            verdict=Verdict.EVIDENCE_ONLY,
            pass_route=None,
            reviewer=None,
            notes="; ".join(n for n in notes if n),
            review_required=True,
        )

    # ---- Failed. ----
    if raw < MIN_RAW_AGREEMENT:
        notes.append(f"raw agreement {raw:.4f} below {MIN_RAW_AGREEMENT}")
    if kappa is not None and kappa < MIN_COHEN_KAPPA:
        notes.append(f"kappa {kappa:.4f} below {MIN_COHEN_KAPPA}")
    if raw >= MIN_RAW_AGREEMENT and raw < MIN_RAW_AGREEMENT_FALLBACK and kappa_unusable:
        notes.append(
            f"kappa unusable ({agreement.kappa_note}) and raw agreement "
            f"{raw:.4f} is below the {MIN_RAW_AGREEMENT_FALLBACK} fallback "
            "threshold"
        )
    notes.append(_rank_note(agreement))
    notes.append("evidence-only: retained and reported separately, excluded from AVS (§8.4)")

    return PlatformGateResult(
        agreement=agreement,
        verdict=Verdict.EVIDENCE_ONLY,
        pass_route=None,
        reviewer=None,
        notes="; ".join(n for n in notes if n),
    )


def _rank_note(agreement: PlatformAgreement) -> str:
    """Spearman is always *reported*, never decisive (§8.4)."""
    n = agreement.co_mention_count
    if n < MIN_CO_MENTIONS_FOR_SPEARMAN:
        return (
            f"rank agreement descriptive only: {n} co-mentioned cells is "
            f"below the {MIN_CO_MENTIONS_FOR_SPEARMAN}-observation floor, so "
            "Spearman is not computed and cannot rescue or qualify the "
            "mention gate (§8.4)"
        )
    rho = agreement.spearman_rho
    if rho is None:
        return (
            f"rank agreement not computable at {n} co-mentioned cells: rank "
            "variance is zero on at least one layer (every co-mention at the "
            "same position), so the correlation is undefined rather than zero"
        )
    if rho >= MIN_SPEARMAN_RHO:
        return f"rank agreement rho {rho:.4f} >= {MIN_SPEARMAN_RHO} over {n} co-mentions"
    return (
        f"CAVEAT: rank agreement rho {rho:.4f} is below the working "
        f"{MIN_SPEARMAN_RHO} threshold over {n} co-mentions — mention "
        "presence agrees but rank behaviour does not; review before relying "
        "on this platform's rank-sensitive output"
    )
