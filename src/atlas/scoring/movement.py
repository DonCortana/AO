"""Movement verdict — Methodology §6.2.

Hierarchical paired bootstrap, 10,000 resamples: prompt IDs are resampled
within intent tier, all eligible platforms are retained, replicate
observations are resampled within each prompt-platform cell, and baseline and
validation are recomputed within the same bootstrap draw. The 2.5th and
97.5th percentiles form the 95% interval for the paired delta.

§6.2 states that in one sentence, which leaves four things undetermined that
change the reported interval. Decision-register D-038 resolves them:

  (a) the prompt draw is shared between the two periods (that *is* the
      pairing); replicate resampling is independent within each period,
      because replicate 3 of a baseline run and replicate 3 of a validation
      run are not the same unit.
  (b) a prompt drawn k times gets k independent replicate resamples.
  (c) a cell with no scoreable replicate in a draw is dropped and its intent
      weight removed from the denominator, never imputed as zero.
  (d) the resampling frame is the intersection of prompts observed in both
      periods; prompts seen in only one are excluded and reported.

The RNG is the standard library's seeded random.Random, and the seed is
required and returned for storage with the score row (D-037) — a
client-facing interval has to be exactly reproducible from stored inputs.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from atlas.scoring.avs import compute_avs
from atlas.scoring.types import ExcludedCounts, PeriodObservations, ReplicateObservation

# §6.2: "MRC is fixed at 5.0 AVS points for v1.0 RC. It is a reporting
# threshold, not a power-analysis claim."
MINIMUM_REPORTABLE_CHANGE = 5.0

# §6.2: "a hierarchical paired bootstrap with 10,000 resamples".
BOOTSTRAP_RESAMPLES = 10_000

# §6.2 / Operating System §11: completeness below this issues no verdict.
COMPLETENESS_THRESHOLD_PCT = 90.0

# §6.1: which run types may support a movement verdict at all.
MOVEMENT_RUN_TYPES = frozenset({"frozen_core"})
SECONDARY_EVIDENCE_RUN_TYPES = frozenset({"benchmark_quarterly"})


class MovementVerdict(str, Enum):
    """§6.2 five-way verdict table."""

    IMPROVEMENT = "improvement"
    REGRESSION = "regression"
    NO_MEANINGFUL_MOVEMENT = "no_meaningful_movement"
    INCONCLUSIVE = "inconclusive"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class MovementResult:
    verdict: MovementVerdict
    delta: float | None
    ci_lower: float | None
    ci_upper: float | None
    baseline_avs: float | None
    validation_avs: float | None
    completeness_pct: float
    baseline_completeness_pct: float
    validation_completeness_pct: float
    seed: int
    resamples: int
    is_secondary_evidence: bool
    excluded: ExcludedCounts


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks.

    Matches numpy.percentile(..., method="linear") — the default — to 7.1e-15
    over 200 random arrays x 5 quantiles (D-037). numpy is not a dependency;
    the comparison was run against a throwaway install.
    """
    if not sorted_values:
        raise ValueError("percentile of an empty distribution")
    if not 0.0 <= q <= 100.0:
        raise ValueError(f"percentile {q} outside 0-100")
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    position = (q / 100.0) * (n - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def _index(
    observations: Sequence[ReplicateObservation],
    eligible: frozenset[str],
) -> dict[str, dict[str, list[float]]]:
    """platform -> prompt_id -> [scoreable RPV, ...]."""
    index: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for obs in observations:
        if obs.platform in eligible and obs.is_scoreable:
            index[obs.platform][obs.prompt_id].append(obs.rpv)
    return {platform: dict(prompts) for platform, prompts in index.items()}


def _avs_for_draw(
    index: dict[str, dict[str, list[float]]],
    draw: Sequence[tuple[str, float]],
    platforms: Sequence[str],
    rng: random.Random,
) -> float | None:
    """AVS for one bootstrap draw, resampling replicates within each cell."""
    total = 0.0
    scored_platforms = 0
    for platform in platforms:
        prompts = index.get(platform)
        if not prompts:
            continue
        numerator = denominator = 0.0
        for prompt_id, weight in draw:
            replicates = prompts.get(prompt_id)
            if not replicates:
                continue  # D-038c: drop the cell, drop its weight
            resampled = rng.choices(replicates, k=len(replicates))
            numerator += weight * (sum(resampled) / len(resampled))
            denominator += weight
        if denominator == 0.0:
            continue
        total += numerator / denominator
        scored_platforms += 1
    if scored_platforms == 0:
        return None
    return 100.0 * total / scored_platforms


def paired_bootstrap_deltas(
    baseline: PeriodObservations,
    validation: PeriodObservations,
    eligible_platforms: Sequence[str],
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> tuple[list[float], tuple[str, ...]]:
    """The bootstrap distribution of the paired AVS delta, and the prompts
    excluded from the frame for being present in only one period (D-038d)."""
    eligible = frozenset(eligible_platforms)
    baseline_index = _index(baseline.observations, eligible)
    validation_index = _index(validation.observations, eligible)

    def prompt_tiers(period: PeriodObservations) -> dict[str, str]:
        return {
            o.prompt_id: o.intent_tier
            for o in period.observations
            if o.platform in eligible and o.is_scoreable
        }

    baseline_tiers = prompt_tiers(baseline)
    validation_tiers = prompt_tiers(validation)

    paired = set(baseline_tiers) & set(validation_tiers)
    unpaired = tuple(sorted((set(baseline_tiers) | set(validation_tiers)) - paired))
    if not paired:
        raise ValueError(
            "no prompt is observed in both periods — a paired delta needs a "
            "shared resampling frame (§6.2; D-038d)"
        )

    for prompt_id in paired:
        if baseline_tiers[prompt_id] != validation_tiers[prompt_id]:
            # §4.2: "Intent tier is immutable for the life of a prompt version."
            raise ValueError(
                f"prompt {prompt_id!r} changed intent tier between periods "
                f"({baseline_tiers[prompt_id]} -> {validation_tiers[prompt_id]}); "
                "intent tier is immutable for the life of a prompt version (§4.2)"
            )

    # §6.2: prompt IDs are resampled *within intent tier*. Drawing each tier
    # to its own original size preserves the tier composition, so the intent
    # weighting of the frame is unchanged from draw to draw.
    from atlas.scoring.types import INTENT_WEIGHTS

    by_tier: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for prompt_id in sorted(paired):
        tier = baseline_tiers[prompt_id]
        by_tier[tier].append((prompt_id, INTENT_WEIGHTS[tier]))

    rng = random.Random(seed)
    platforms = list(eligible_platforms)
    deltas: list[float] = []

    for _ in range(resamples):
        draw: list[tuple[str, float]] = []
        for tier_prompts in by_tier.values():
            draw.extend(rng.choices(tier_prompts, k=len(tier_prompts)))
        # D-038a: one shared prompt draw, independent replicate resampling.
        baseline_avs = _avs_for_draw(baseline_index, draw, platforms, rng)
        validation_avs = _avs_for_draw(validation_index, draw, platforms, rng)
        if baseline_avs is None or validation_avs is None:
            continue
        deltas.append(validation_avs - baseline_avs)

    if not deltas:
        raise ValueError("bootstrap produced no usable resample")
    return deltas, unpaired


def movement_verdict(
    baseline: PeriodObservations,
    validation: PeriodObservations,
    eligible_platforms: Sequence[str],
    seed: int,
    run_type: str = "frozen_core",
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> MovementResult:
    """The §6.2 five-way verdict.

    Completeness is checked first: below 90% in *either* period the cycle is
    Incomplete and no verdict is issued (D-039), so no bootstrap is run and
    no delta is reported at all.
    """
    if run_type in SECONDARY_EVIDENCE_RUN_TYPES:
        is_secondary = True
    elif run_type in MOVEMENT_RUN_TYPES:
        is_secondary = False
    else:
        # §6.1: Weekly Sentinel, Monthly Benchmark and Discovery Set are
        # explicitly "may support movement verdict: No".
        raise ValueError(
            f"run_type {run_type!r} may not support a movement verdict (§6.1) — "
            f"only {sorted(MOVEMENT_RUN_TYPES | SECONDARY_EVIDENCE_RUN_TYPES)} may"
        )

    baseline_completeness = baseline.completeness_pct
    validation_completeness = validation.completeness_pct
    # D-039: the lower of the two, so a well-covered baseline cannot mask a
    # thin validation run.
    completeness = min(baseline_completeness, validation_completeness)

    if completeness < COMPLETENESS_THRESHOLD_PCT:
        return MovementResult(
            verdict=MovementVerdict.INCOMPLETE,
            delta=None,
            ci_lower=None,
            ci_upper=None,
            baseline_avs=None,
            validation_avs=None,
            completeness_pct=completeness,
            baseline_completeness_pct=baseline_completeness,
            validation_completeness_pct=validation_completeness,
            seed=seed,
            resamples=0,
            is_secondary_evidence=is_secondary,
            excluded=ExcludedCounts(),
        )

    baseline_result = compute_avs(baseline.observations, eligible_platforms)
    validation_result = compute_avs(validation.observations, eligible_platforms)
    # §6.2 point estimate comes from the observed data, not the mean of the
    # bootstrap distribution (D-038).
    delta = validation_result.avs - baseline_result.avs

    deltas, unpaired = paired_bootstrap_deltas(
        baseline, validation, eligible_platforms, seed=seed, resamples=resamples
    )
    deltas.sort()
    ci_lower = percentile(deltas, 2.5)
    ci_upper = percentile(deltas, 97.5)

    # §6.2 verdict table. "CI excludes 0" is strict: a bound of exactly 0.0
    # includes 0, so the verdict is Inconclusive (D-038).
    if abs(delta) < MINIMUM_REPORTABLE_CHANGE:
        verdict = MovementVerdict.NO_MEANINGFUL_MOVEMENT
    elif delta >= MINIMUM_REPORTABLE_CHANGE and ci_lower > 0.0:
        verdict = MovementVerdict.IMPROVEMENT
    elif delta <= -MINIMUM_REPORTABLE_CHANGE and ci_upper < 0.0:
        verdict = MovementVerdict.REGRESSION
    else:
        verdict = MovementVerdict.INCONCLUSIVE

    return MovementResult(
        verdict=verdict,
        delta=delta,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        baseline_avs=baseline_result.avs,
        validation_avs=validation_result.avs,
        completeness_pct=completeness,
        baseline_completeness_pct=baseline_completeness,
        validation_completeness_pct=validation_completeness,
        seed=seed,
        resamples=len(deltas),
        is_secondary_evidence=is_secondary,
        excluded=ExcludedCounts(
            entity_conflicts=baseline_result.excluded.entity_conflicts
            + validation_result.excluded.entity_conflicts,
            empty_cells=baseline_result.excluded.empty_cells
            + validation_result.excluded.empty_cells,
            unpaired_prompts=unpaired,
            ineligible_platforms=baseline_result.excluded.ineligible_platforms,
        ),
    )
