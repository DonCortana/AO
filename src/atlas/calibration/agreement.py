"""§8.4 agreement statistics — majority collapse, raw agreement, Cohen kappa,
Spearman rho.

Hand-implemented on the standard library, following D-037's precedent for the
bootstrap and Wilson intervals: no numpy, no scipy. A client-facing gate
result must be exactly reproducible from stored inputs, and each function here
is verified against a reference implementation to a stated tolerance in
tests/test_calibration_agreement.py.

D-045 fixes the unit of analysis as the prompt-platform cell. Everything in
this module operates on collapsed cells; nothing pairs replicates. D-047 fixes
the rank collapse within a cell as the median across its mentioned replicates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from statistics import median

from atlas.calibration.types import (
    MIN_CO_MENTIONS_FOR_SPEARMAN,
    CellJudgment,
    Contingency,
    KappaStability,
    PairedCell,
    PlatformAgreement,
    Replicate,
)

# Advisory-only prevalence heuristics. These never gate a platform: §8.4's
# fallback route requires a named human reviewer, so these only *surface* the
# kappa paradox for that reviewer to judge. See KappaStability.
_PREVALENCE_INDEX_UNSTABLE = 0.70
_SMALL_SAMPLE_CELLS = 10


def collapse_replicates(
    replicates: Sequence[Replicate],
    *,
    prompt_id: str,
    platform: str,
    layer: str,
) -> CellJudgment | None:
    """Collapse one cell's replicates to a single mention judgment (D-045) and
    a single rank (D-047).

    Mention is a majority vote. Both planned replicate counts are odd (Layer A
    n=5 per §6.1, Layer B n=3 per §8.3), so the intended case cannot tie. If an
    operational shortfall — a failed call, an abandoned capture — leaves an
    even count and the vote splits exactly, the tie resolves to *absent* and
    the cell is marked `tie_broken` for manual review. That direction is
    deliberate: breaking toward mention would flatter agreement, and dropping
    the cell silently would shrink the denominator invisibly (D-045, and the
    D-038(d) precedent that an exclusion must be visible).

    Returns None for a cell with no usable replicate at all — the caller
    excludes it from the frame and reports it, rather than imputing.
    """
    if not replicates:
        return None

    mentions = sum(1 for r in replicates if r.mentioned)
    total = len(replicates)
    tie = total % 2 == 0 and mentions * 2 == total
    mentioned = (mentions * 2 > total) if not tie else False

    # D-047: the cell's rank is the MEDIAN rank across its mentioned
    # replicates, per layer. D-045 fixed the cell as the unit and specified the
    # mention collapse, but Spearman needs one rank per cell per layer and a
    # majority vote does not produce one.
    #
    # Median rather than mean because rank is ordinal — a position is a place
    # in a list, not a quantity, and §4.1 itself prices the gaps unevenly
    # (1.00 -> 0.80 between ranks 1 and 2; 0.25 -> 0.25 between ranks 9 and
    # 10). It is also robust to one outlying replicate, which matters at n=5
    # and n=3. Rejected: the minimum (reports the client's luckiest replicate)
    # and the mode (undefined whenever replicates disagree, the normal case).
    #
    # Low-stakes by construction: these ranks feed only Spearman, which
    # re-ranks its inputs, so only relative ordering between cells survives.
    # An even-count cell may therefore hold a non-integer rank — acceptable
    # because no client-facing number reports a cell rank directly.
    #
    # Non-mention replicates contribute no rank and are excluded from the
    # median rather than entering it as a sentinel; a cell whose majority
    # judgment is absent carries no rank at all.
    ranks = [r.rank for r in replicates if r.mentioned and r.rank is not None]
    rank = float(median(ranks)) if (mentioned and ranks) else None

    return CellJudgment(
        prompt_id=prompt_id,
        platform=platform,
        layer=layer,
        mentioned=mentioned,
        rank=rank,
        replicate_count=total,
        tie_broken=tie,
    )


def pair_cells(
    api_cells: Iterable[CellJudgment],
    consumer_cells: Iterable[CellJudgment],
) -> tuple[list[PairedCell], list[tuple[str, str]]]:
    """Pair the two layers on (prompt_id, platform).

    The frame is the *intersection* — a cell observed on only one layer cannot
    contribute to an agreement judgment. Unpaired cells are returned rather
    than dropped, so the exclusion is visible in the stored result (D-038(d)
    precedent, applied to §8.4).
    """
    api_by_cell = {c.cell: c for c in api_cells}
    consumer_by_cell = {c.cell: c for c in consumer_cells}

    paired = [
        PairedCell(
            prompt_id=cell[0],
            platform=cell[1],
            api=api_by_cell[cell],
            consumer=consumer_by_cell[cell],
        )
        for cell in sorted(api_by_cell.keys() & consumer_by_cell.keys())
    ]
    unpaired = sorted(api_by_cell.keys() ^ consumer_by_cell.keys())
    return paired, unpaired


def contingency(paired: Sequence[PairedCell]) -> Contingency:
    counts: dict[tuple[bool, bool], int] = defaultdict(int)
    for p in paired:
        counts[(p.api.mentioned, p.consumer.mentioned)] += 1
    return Contingency(
        both_yes=counts[(True, True)],
        api_only=counts[(True, False)],
        consumer_only=counts[(False, True)],
        both_no=counts[(False, False)],
    )


def raw_agreement(table: Contingency) -> float | None:
    """§8.4 "raw mention agreement": the proportion of cells where both layers
    reached the same mention judgment."""
    if table.n == 0:
        return None
    return (table.both_yes + table.both_no) / table.n


def cohen_kappa(table: Contingency) -> tuple[float | None, KappaStability, str]:
    """Cohen kappa for two raters on a binary outcome, with a stability note.

    kappa = (po - pe) / (1 - pe), where pe is chance agreement computed from
    the two raters' marginals.

    Returns (kappa, stability, note). kappa is None only when pe == 1, which
    happens when both layers assigned every cell to the same single category:
    (1 - pe) is zero and the statistic is arithmetically undefined. That is a
    real, fully-agreeing result — not a missing one — and §8.4's ">=85% raw
    agreement plus documented manual review" route exists precisely for it.
    """
    n = table.n
    if n == 0:
        return None, KappaStability.UNSTABLE_SMALL_SAMPLE, "no paired cells"

    po = (table.both_yes + table.both_no) / n
    api_yes = (table.both_yes + table.api_only) / n
    consumer_yes = (table.both_yes + table.consumer_only) / n
    pe = api_yes * consumer_yes + (1.0 - api_yes) * (1.0 - consumer_yes)

    if abs(1.0 - pe) < 1e-12:
        return (
            None,
            KappaStability.UNDEFINED_DEGENERATE,
            (
                f"kappa undefined: both layers assigned all {n} cells to one "
                f"category (pe=1), raw agreement {po:.4f}"
            ),
        )

    kappa = (po - pe) / (1.0 - pe)

    # Advisory diagnostics only — these do not gate. See module note.
    prevalence_index = abs(table.both_yes - table.both_no) / n
    if n < _SMALL_SAMPLE_CELLS:
        return (
            kappa,
            KappaStability.UNSTABLE_SMALL_SAMPLE,
            (
                f"only {n} paired cells; kappa is imprecise at this n "
                "(advisory, not a gate)"
            ),
        )
    if prevalence_index > _PREVALENCE_INDEX_UNSTABLE:
        return (
            kappa,
            KappaStability.UNSTABLE_PREVALENCE,
            (
                f"prevalence index {prevalence_index:.2f} exceeds "
                f"{_PREVALENCE_INDEX_UNSTABLE:.2f}: one outcome dominates both "
                "marginals, which depresses kappa relative to raw agreement "
                f"{po:.4f} (kappa paradox; advisory, not a gate)"
            ),
        )
    return kappa, KappaStability.STABLE, f"pe={pe:.4f}, n={n}"


def _midranks(values: Sequence[float]) -> list[float]:
    """Ranks with ties averaged — the midrank correction. Required because
    co-mentioned rank data repeats positions constantly (several prompts all
    returning the client at rank 1), and the classic
    1 - 6*sum(d^2)/(n*(n^2-1)) shortcut is simply wrong in the presence of
    ties. This is what scipy.stats.spearmanr computes."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        midrank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = midrank
        i = j + 1
    return ranks


def spearman_rho(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman rank correlation with midrank tie correction.

    Computed as the Pearson correlation of the midranks. Returns None when
    either series has zero variance (every rank identical), where the
    correlation is undefined rather than zero — reporting 0.0 there would read
    as "no rank agreement" when the truth is "perfect agreement, no spread to
    correlate".
    """
    if len(xs) != len(ys):
        raise ValueError("spearman_rho requires equal-length series")
    n = len(xs)
    if n < 2:
        return None

    rx, ry = _midranks(xs), _midranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    dx = [v - mx for v in rx]
    dy = [v - my for v in ry]

    numerator = sum(a * b for a, b in zip(dx, dy, strict=True))
    denominator = (sum(a * a for a in dx) * sum(b * b for b in dy)) ** 0.5
    if denominator == 0.0:
        return None
    return numerator / denominator


def platform_agreement(
    platform: str,
    api_cells: Iterable[CellJudgment],
    consumer_cells: Iterable[CellJudgment],
) -> PlatformAgreement:
    """Compute every §8.4 statistic for one platform. Applies no thresholds —
    the gate decision lives in gate.py so that computing and judging stay
    separable and separately testable."""
    paired, unpaired = pair_cells(api_cells, consumer_cells)
    table = contingency(paired)
    kappa, stability, note = cohen_kappa(table)

    co_mentioned = [p for p in paired if p.co_mentioned]
    # §8.4: Spearman is reported "where at least 10 co-mentioned observations
    # exist". Below the floor it is not computed at all — reporting a rho from
    # 4 pairs would invite exactly the rescue §8.4 forbids.
    rho: float | None = None
    if len(co_mentioned) >= MIN_CO_MENTIONS_FOR_SPEARMAN:
        rho = spearman_rho(
            [p.api.rank for p in co_mentioned],  # type: ignore[misc]
            [p.consumer.rank for p in co_mentioned],  # type: ignore[misc]
        )

    tie_broken = tuple(
        sorted(
            {
                judgment.cell
                for pair in paired
                for judgment in (pair.api, pair.consumer)
                if judgment.tie_broken
            }
        )
    )

    return PlatformAgreement(
        platform=platform,
        n_paired_units=table.n,
        raw_agreement=raw_agreement(table),
        cohen_kappa=kappa,
        kappa_stability=stability,
        kappa_note=note,
        co_mention_count=len(co_mentioned),
        spearman_rho=rho,
        contingency=table,
        tie_broken_cells=tie_broken,
        unpaired_cells=tuple(unpaired),
    )
