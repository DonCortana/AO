"""Typed inputs and outputs for the Methodology §8.4 calibration gate.

The gate compares Layer A (§8.1 controlled API benchmark) against Layer B
(§8.3 human-initiated consumer-surface capture), per platform, on
recommendation presence and rank behaviour.

Decision-register D-045 fixes the unit of analysis as the **prompt-platform
cell**, not the replicate: Layer A (n=5) and Layer B (n=3 target) are each
collapsed to one cell-level mention judgment by majority vote before any
statistic is computed. Replicate-level pairing has no natural 1:1
correspondence and would pseudo-replicate one measurement into many,
inflating kappa's apparent precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Methodology §8.1 Layer A vs §8.3 Layer B. Matches observations.surface_layer
# (migration 0005, D-043).
LAYER_API = "api"
LAYER_CONSUMER = "consumer"

# §8.4 thresholds, stated numerically in the methodology and never inferred.
MIN_RAW_AGREEMENT = 0.80
MIN_COHEN_KAPPA = 0.60
MIN_RAW_AGREEMENT_FALLBACK = 0.85
MIN_SPEARMAN_RHO = 0.50
# "Rank agreement is reported with Spearman correlation where at least 10
# co-mentioned observations exist" (§8.4).
MIN_CO_MENTIONS_FOR_SPEARMAN = 10


class KappaStability(str, Enum):
    """Whether Cohen kappa can be read at face value for this platform.

    §8.4 allows a fallback route "if prevalence makes kappa unstable" but does
    not define unstable. This enum is a *diagnostic*, never a gate: the
    fallback route requires a named human reviewer (see gate.py), so nothing
    here auto-routes a platform past a failed kappa.
    """

    STABLE = "stable"
    # pe == 1: both layers put every cell in the same single category, so
    # (1 - pe) is zero and kappa is arithmetically undefined. Not a missing
    # result — a real and fully-agreeing one.
    UNDEFINED_DEGENERATE = "undefined_degenerate"
    # The kappa paradox: high raw agreement with a depressed kappa because
    # one outcome dominates both marginals.
    UNSTABLE_PREVALENCE = "unstable_prevalence"
    # Too few paired cells for kappa to mean much regardless of its value.
    UNSTABLE_SMALL_SAMPLE = "unstable_small_sample"


class Verdict(str, Enum):
    ELIGIBLE = "eligible"
    EVIDENCE_ONLY = "evidence_only"


class PassRoute(str, Enum):
    """Which §8.4 route a platform passed by. Stored, because a clean kappa
    pass and a prevalence-degraded manual-review pass are not equivalent
    evidence (D-044)."""

    KAPPA = "kappa"
    RAW_AGREEMENT_MANUAL_REVIEW = "raw_agreement_manual_review"


@dataclass(frozen=True)
class Replicate:
    """One replicate's presence/rank outcome for one prompt on one platform.

    `mentioned` is a positive recommendation of the client entity — §4.1's
    RANKED or UNORDERED_POSITIVE. A source-only citation is not a mention:
    §4.1 is explicit that source-only citations "are not recommendations".
    `rank` is None for an unordered positive and for a non-mention.
    """

    mentioned: bool
    rank: int | None = None

    def __post_init__(self) -> None:
        if self.rank is not None:
            if not self.mentioned:
                raise ValueError("a non-mention cannot carry a rank")
            if self.rank < 1:
                raise ValueError(f"rank {self.rank} must be >= 1")


@dataclass(frozen=True)
class CellJudgment:
    """One prompt-platform cell on one layer, collapsed to a single judgment.

    D-045: mention is the majority vote across replicates.
    D-047: `rank` is the median rank across the *mentioned* replicates, per
    layer — ordinal-appropriate, robust to an outlying replicate, and
    consequence-light because Spearman re-ranks its inputs so only relative
    ordering between cells survives the collapse.
    """

    prompt_id: str
    platform: str
    layer: str
    mentioned: bool
    rank: float | None
    replicate_count: int
    tie_broken: bool = False

    @property
    def cell(self) -> tuple[str, str]:
        return (self.prompt_id, self.platform)


@dataclass(frozen=True)
class PairedCell:
    prompt_id: str
    platform: str
    api: CellJudgment
    consumer: CellJudgment

    @property
    def agrees(self) -> bool:
        return self.api.mentioned == self.consumer.mentioned

    @property
    def co_mentioned(self) -> bool:
        """Both layers recommended the client AND both carry a rank — the
        only cells that can contribute to a rank correlation."""
        return (
            self.api.mentioned
            and self.consumer.mentioned
            and self.api.rank is not None
            and self.consumer.rank is not None
        )


@dataclass(frozen=True)
class Contingency:
    """2x2 mention agreement table. Names follow the usual convention:
    `both_yes` and `both_no` are the agreeing diagonal."""

    both_yes: int
    api_only: int
    consumer_only: int
    both_no: int

    @property
    def n(self) -> int:
        return self.both_yes + self.api_only + self.consumer_only + self.both_no


@dataclass(frozen=True)
class PlatformAgreement:
    """Everything §8.4 asks to be computed for one platform, before the
    thresholds are applied."""

    platform: str
    n_paired_units: int
    raw_agreement: float | None
    cohen_kappa: float | None
    kappa_stability: KappaStability
    kappa_note: str
    co_mention_count: int
    spearman_rho: float | None
    contingency: Contingency
    tie_broken_cells: tuple[tuple[str, str], ...] = ()
    unpaired_cells: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class PlatformGateResult:
    """The gate's decision for one platform, ready to be stored."""

    agreement: PlatformAgreement
    verdict: Verdict
    pass_route: PassRoute | None
    reviewer: str | None
    notes: str
    review_required: bool = False

    @property
    def platform(self) -> str:
        return self.agreement.platform


@dataclass(frozen=True)
class CalibrationRun:
    """One calibration cycle's full result across every platform examined."""

    calibration_run_id: str
    property_id: str
    market_id: str
    prompt_set_version: str
    results: tuple[PlatformGateResult, ...] = field(default_factory=tuple)

    @property
    def eligible_platforms(self) -> tuple[str, ...]:
        return tuple(
            sorted(r.platform for r in self.results if r.verdict is Verdict.ELIGIBLE)
        )
