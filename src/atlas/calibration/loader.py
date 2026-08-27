"""Build §8.4 calibration input from the database.

Reads the same boundary the scoring engine reads — the `recommendations`
table (D-034) — so a calibration judgment and an AVS are derived from exactly
one parse of each observation. What differs is the *projection*: scoring needs
RPV, the gate needs only presence and rank. One manual parsing pass therefore
feeds both, which is what makes the calibration's parsing load tractable.

Layer separation comes from `observations.surface_layer` (D-043, migration
0005). This module is the only place that reads consumer-layer rows; the
scoring loader admits `surface_layer = 'api'` exclusively.
"""

from __future__ import annotations

from collections import defaultdict

from atlas.adapters.base import OutcomeType
from atlas.calibration.agreement import collapse_replicates
from atlas.calibration.types import LAYER_API, LAYER_CONSUMER, CellJudgment, Replicate
from atlas.scoring.loader import INELIGIBLE_GROUNDING

# §4.1: only positive recommendations are mentions. A source-only citation is
# explicitly "not a recommendation" and must never count as presence here —
# doing so would let a citation on one layer agree with a recommendation on
# the other and silently inflate the gate.
MENTION_OUTCOMES = frozenset({OutcomeType.RANKED, OutcomeType.UNORDERED_POSITIVE})

_MIGRATION_0005 = "migrations/0005_surface_layer_and_calibration_results.sql"


def load_cells(
    db,
    run_plan_ids: list[str],
    *,
    layer: str,
) -> dict[str, list[CellJudgment]]:
    """Collapse one layer's observations into per-platform cell judgments.

    Returns {platform: [CellJudgment, ...]}. Cells are collapsed by majority
    vote per D-045; a cell with no usable replicate is omitted entirely rather
    than imputed, and shows up downstream as an unpaired cell.
    """
    if layer not in (LAYER_API, LAYER_CONSUMER):
        raise ValueError(f"unknown surface layer {layer!r}")

    observations = (
        db.table("observations")
        .select(
            "id,provider,prompt_version_id,replicate_index,status,"
            "grounding_status,surface_layer"
        )
        .in_("run_plan_id", run_plan_ids)
        .eq("surface_layer", layer)
        .execute()
        .data
    )
    if not observations:
        raise ValueError(
            f"no {layer!r}-layer observations for run plans {run_plan_ids!r}. "
            f"If migration 0005 is not yet applied, surface_layer does not "
            f"exist yet — apply {_MIGRATION_0005} first (D-043)."
        )

    observation_ids = [o["id"] for o in observations]
    recommendations = (
        db.table("recommendations")
        .select("observation_id,rank,outcome_type,is_client_entity,entity_conflict")
        .in_("observation_id", observation_ids)
        .execute()
        .data
    )

    client_row_by_observation: dict[str, dict] = {}
    for row in recommendations:
        if not row.get("is_client_entity"):
            continue
        observation_id = row["observation_id"]
        if observation_id in client_row_by_observation:
            raise ValueError(
                f"observation {observation_id!r} has more than one client-entity "
                "recommendation row; the parse must emit exactly one (D-034)"
            )
        client_row_by_observation[observation_id] = row

    by_cell: dict[tuple[str, str], list[Replicate]] = defaultdict(list)
    for observation in observations:
        if observation["status"] != "complete":
            continue
        # §8.1: an observation that stayed ungrounded after its retry is
        # ineligible, not a zero. It is absent from the frame, not an absence.
        if observation.get("grounding_status") == INELIGIBLE_GROUNDING:
            continue
        row = client_row_by_observation.get(observation["id"])
        if row is None:
            # D-034: not yet parsed. Never read as "not mentioned".
            continue
        # §4.1: entity conflicts are excluded from scoring and feed P3. They
        # are equally not a presence judgment.
        if row.get("entity_conflict"):
            continue

        outcome_type = OutcomeType(row["outcome_type"])
        if outcome_type is OutcomeType.ENTITY_CONFLICT:
            continue

        mentioned = outcome_type in MENTION_OUTCOMES
        rank = row.get("rank") if mentioned else None
        cell = (observation["prompt_version_id"], observation["provider"])
        by_cell[cell].append(Replicate(mentioned=mentioned, rank=rank))

    cells_by_platform: dict[str, list[CellJudgment]] = defaultdict(list)
    for (prompt_id, platform), replicates in by_cell.items():
        judgment = collapse_replicates(
            replicates, prompt_id=prompt_id, platform=platform, layer=layer
        )
        if judgment is not None:
            cells_by_platform[platform].append(judgment)

    return dict(cells_by_platform)
