"""Scoring inputs — the typed values the engine aggregates, and nothing more.

Decision-register D-034 fixes the scoring engine's input boundary at the
`recommendations` table: RPV arrives already assigned, and how it was
assigned (a human today, a parser later once it clears Execution Plan §10's
>=95% accuracy gate) is deliberately invisible here. Nothing in this package
reads `observations.raw_response` or `Outcome.parsed_recommendations`.

The distinction this module exists to keep sharp — D-034, load-bearing for
the §6.2 movement verdict — is between:

  * a replicate that was *parsed and found absent*  -> a scoreable RPV 0.00
  * a replicate that was *never parsed at all*      -> not scoreable; it
    counts against completeness and is never read as a zero

Collapsing those two is how a measurement gap silently becomes a reported
regression, which Operating System §1 forbids ("a technical failure is never
scored as zero").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from atlas.adapters.base import (  # single source of truth for the §4.1 vocabulary
    RPV_BY_RANK,
    RPV_UNORDERED_POSITIVE,
    OutcomeType,
)

# Methodology §4.2 commercial intent weights. Immutable for the life of a
# prompt version (§4.2, §7) — a tier change is a new prompt version.
INTENT_WEIGHTS: dict[str, float] = {"A": 1.00, "B": 0.80, "C": 0.60, "D": 0.30}

# Methodology §4.1: outcomes that are scoreable, positive contributions.
POSITIVE_OUTCOMES = frozenset({OutcomeType.RANKED, OutcomeType.UNORDERED_POSITIVE})

# Methodology §4.1: scoreable, but contribute 0.00. Tracked distinctly from
# each other and from "absent" (D-034) — a source-only citation is valuable
# intelligence and a negative mention is a different problem entirely, even
# though both score the same.
ZERO_SCORING_OUTCOMES = frozenset(
    {OutcomeType.SOURCE_ONLY_MENTION, OutcomeType.ABSENT, OutcomeType.NEGATIVE_MENTION}
)


class ScoreModelVersion(str, Enum):
    """Stamped on every score row (Methodology §9). Historical scores are
    never recalculated under a later methodology."""

    V1_0_RC = "1.0-rc"


def rpv_for(outcome_type: OutcomeType, rank: int | None) -> float | None:
    """The Methodology §4.1 RPV table, as a lookup.

    This does not *assign* RPV to an observation — D-034 puts that outside
    the engine. It exists so the loader can check that a stored `rpv` agrees
    with the stored `outcome_type`/`rank` that accompany it, catching a bad
    manual parse at the boundary rather than in a client report.

    Returns None for ENTITY_CONFLICT, which §4.1 excludes rather than scores.
    """
    if outcome_type is OutcomeType.ENTITY_CONFLICT:
        return None
    if outcome_type is OutcomeType.RANKED:
        if rank is None:
            raise ValueError("outcome_type 'ranked' requires a rank")
        # §4.1 tabulates ranks 1-10. Beyond rank 10 the table stops; Atlas
        # treats a mention past position 10 as carrying no visibility value
        # rather than extrapolating a band the methodology never wrote.
        return RPV_BY_RANK.get(rank, 0.00)
    if outcome_type is OutcomeType.UNORDERED_POSITIVE:
        return RPV_UNORDERED_POSITIVE
    return 0.00


@dataclass(frozen=True)
class ReplicateObservation:
    """One replicate's scored outcome for one prompt on one platform.

    Built by atlas.scoring.loader from a `recommendations` row joined to its
    `observations` row. `rpv` is what the table holds — not recomputed here.
    """

    prompt_id: str
    intent_tier: str
    platform: str
    replicate_index: int
    rpv: float
    outcome_type: OutcomeType
    entity_conflict: bool = False
    observation_id: str | None = None

    def __post_init__(self) -> None:
        if self.intent_tier not in INTENT_WEIGHTS:
            raise ValueError(f"unknown intent tier {self.intent_tier!r}")
        if not 0.0 <= self.rpv <= 1.0:
            raise ValueError(f"rpv {self.rpv} outside 0.00-1.00 (§4.1)")

    @property
    def is_scoreable(self) -> bool:
        """§4.1: entity conflicts are *excluded*, not scored as zero. They
        feed P3 Business Information Consistency instead."""
        return not (self.entity_conflict or self.outcome_type is OutcomeType.ENTITY_CONFLICT)

    @property
    def intent_weight(self) -> float:
        return INTENT_WEIGHTS[self.intent_tier]

    @property
    def cell(self) -> tuple[str, str]:
        """The prompt-platform cell this replicate belongs to (§4.3)."""
        return (self.prompt_id, self.platform)


@dataclass(frozen=True)
class PeriodObservations:
    """One measurement period's scoreable input, plus the completeness facts
    the §6.2 Incomplete verdict needs.

    `planned_observation_count` comes from the run plan's task list, which
    the planner writes before any provider call (Technical Lane step 3), so
    the completeness denominator is fixed in advance and cannot shrink to
    flatter the ratio (D-039).
    """

    label: str
    observations: tuple[ReplicateObservation, ...]
    planned_observation_count: int
    scoreable_observation_count: int

    @property
    def completeness_pct(self) -> float:
        """D-039: scoreable observations / planned observations, as a %."""
        if self.planned_observation_count <= 0:
            raise ValueError(
                f"{self.label}: planned_observation_count must be positive — "
                "the run plan's task list is the completeness denominator (D-039)"
            )
        return 100.0 * self.scoreable_observation_count / self.planned_observation_count


@dataclass(frozen=True)
class ExcludedCounts:
    """What was dropped and why. Reported, never silently discarded."""

    entity_conflicts: int = 0
    empty_cells: int = 0
    unpaired_prompts: tuple[str, ...] = field(default_factory=tuple)
    ineligible_platforms: tuple[str, ...] = field(default_factory=tuple)
