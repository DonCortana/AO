"""AI Visibility Score — Methodology §4.3, and the §4.4 visibility bands.

    PVS(prompt, platform) = mean RPV across valid replicates
    PlatformScore        = weighted mean of PVS by intent weight
    AVS                  = 100 x mean PlatformScore across eligible
                           calibrated platforms

Equal platform weighting is intentional (§4.3): "Equal weighting is
intentionally preferred to unsupported pseudo-precision about market share."

`compute_avs` is a pure function and takes its eligible-platform list
explicitly, because G2 requires hand-calculated AVS values to be reproduced
against the engine and that verification needs a kernel with no database in
it. The *production* path is
`atlas.calibration.scoring.compute_avs_for_property`, which sources the list
from `calibration_results` per D-044. A hand-typed list is for verification,
not for a client-facing score.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from atlas.scoring.types import ExcludedCounts, ReplicateObservation

# D-042: Google Search AI Overviews has no API surface, so no Layer A leg can
# exist for it and the §8.4 gate is undefined rather than failed. Duplicated
# from atlas.calibration.run (rather than imported) to keep scoring free of
# any dependency on the calibration package.
CONSUMER_ONLY_PLATFORMS = frozenset({"google_ai"})

# Methodology §4.4. Half-open intervals — see decision-register D-040 for why
# the printed integer ranges are read this way. (lower_inclusive, label)
VISIBILITY_BANDS: tuple[tuple[float, str], ...] = (
    (85.0, "leading"),
    (65.0, "strong"),
    (45.0, "established"),
    (25.0, "emerging"),
    (10.0, "detectable"),
    (0.0, "not_observed"),
)


def visibility_band(avs: float) -> str:
    """§4.4 visibility band for an AVS. Bands are half-open (D-040)."""
    if not 0.0 <= avs <= 100.0:
        raise ValueError(f"AVS {avs} outside 0-100")
    for lower, label in VISIBILITY_BANDS:
        if avs >= lower:
            return label
    raise AssertionError("unreachable: bands cover [0, 100]")


@dataclass(frozen=True)
class AVSResult:
    avs: float
    band: str
    platform_scores: dict[str, float]
    prompt_visibility_scores: dict[tuple[str, str], float]
    excluded: ExcludedCounts


def prompt_visibility_score(replicates: Iterable[ReplicateObservation]) -> float | None:
    """PVS = mean RPV across *valid* replicates (§4.3).

    Valid excludes entity conflicts (§4.1 excludes them rather than scoring
    them). It includes parsed-absent, source-only and negative replicates,
    which are genuine observations that happen to score 0.00. Returns None
    when the cell holds no valid replicate at all — the caller drops the cell
    rather than imputing a zero (D-038c).
    """
    valid = [r.rpv for r in replicates if r.is_scoreable]
    if not valid:
        return None
    return sum(valid) / len(valid)


def _cells(
    observations: Iterable[ReplicateObservation],
) -> dict[tuple[str, str], list[ReplicateObservation]]:
    cells: dict[tuple[str, str], list[ReplicateObservation]] = defaultdict(list)
    for obs in observations:
        cells[obs.cell].append(obs)
    return dict(cells)


def platform_score(
    observations: Iterable[ReplicateObservation],
    platform: str,
) -> float | None:
    """Intent-weighted mean of this platform's PVS values (§4.3).

    A cell with no valid replicate is dropped and its intent weight removed
    from the denominator (D-038c), so a parsing or technical gap never enters
    the mean as a zero.
    """
    observations = [o for o in observations if o.platform == platform]
    numerator = denominator = 0.0
    for replicates in _cells(observations).values():
        pvs = prompt_visibility_score(replicates)
        if pvs is None:
            continue
        weight = replicates[0].intent_weight
        numerator += weight * pvs
        denominator += weight
    if denominator == 0.0:
        return None
    return numerator / denominator


def compute_avs(
    observations: Sequence[ReplicateObservation],
    eligible_platforms: Sequence[str],
) -> AVSResult:
    """AVS = 100 x mean PlatformScore across eligible calibrated platforms.

    `eligible_platforms` is required and is never inferred from the data
    (D-036): §8.4 makes eligibility the output of a calibration gate, and
    defaulting to "every platform present" would fold evidence-only platforms
    into a client-facing AVS, which §8.4 explicitly forbids.
    """
    if not eligible_platforms:
        raise ValueError(
            "eligible_platforms is required — AVS averages over eligible "
            "calibrated platforms only (§4.3, §8.4; D-036)"
        )
    if len(set(eligible_platforms)) != len(eligible_platforms):
        raise ValueError(f"duplicate platform in eligible_platforms: {eligible_platforms!r}")
    structural = sorted(set(eligible_platforms) & CONSUMER_ONLY_PLATFORMS)
    if structural:
        # D-042: these surfaces have no Layer A benchmark, so §8.4's gate is
        # undefined for them and they can never be calibrated in v1.0. Enforced
        # in the kernel as well as the gate, so a hand-typed verification list
        # cannot reintroduce what the methodology structurally excludes.
        raise ValueError(
            f"platform(s) {structural} are consumer-surface only and have no "
            "Layer A benchmark, so they can never pass the §8.4 calibration "
            "gate and are excluded from AVS by structure (D-042)"
        )

    eligible = set(eligible_platforms)
    scoreable = [o for o in observations if o.platform in eligible]

    entity_conflicts = sum(1 for o in observations if not o.is_scoreable)
    empty_cells = sum(
        1 for reps in _cells(scoreable).values() if prompt_visibility_score(reps) is None
    )
    ineligible = tuple(sorted({o.platform for o in observations} - eligible))

    platform_scores: dict[str, float] = {}
    for platform in eligible_platforms:
        score = platform_score(scoreable, platform)
        if score is None:
            # D-036: refuse to average over a missing term. A platform named
            # eligible but carrying no scoreable observation is a data or
            # configuration fault, not a zero-visibility result.
            raise ValueError(
                f"platform {platform!r} was named eligible but has no scoreable "
                "observation — refusing to average over a missing term (D-036)"
            )
        platform_scores[platform] = score

    avs = 100.0 * sum(platform_scores.values()) / len(platform_scores)

    pvs_map = {
        cell: pvs
        for cell, reps in _cells(scoreable).items()
        if (pvs := prompt_visibility_score(reps)) is not None
    }

    return AVSResult(
        avs=avs,
        band=visibility_band(avs),
        platform_scores=platform_scores,
        prompt_visibility_scores=pvs_map,
        excluded=ExcludedCounts(
            entity_conflicts=entity_conflicts,
            empty_cells=empty_cells,
            ineligible_platforms=ineligible,
        ),
    )
