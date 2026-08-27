"""The scoring engine's only input path — the `recommendations` table.

Decision-register D-034: the engine reads rows whose `rpv`, `rank`,
`outcome_type`, `is_client_entity` and `entity_conflict` are already
populated. It never reads `observations.raw_response` or
`Outcome.parsed_recommendations`. How an RPV got assigned — a human today, a
parser later once it clears Execution Plan §10's >=95% accuracy gate — is
deliberately invisible past this module, so the parser can be swapped in
without the scoring math changing.

Only Layer A observations are scored (D-043): `surface_layer = 'api'`. Layer B
consumer-surface captures live in the same table and are read by
`atlas.calibration`, never by this module.

The load-bearing rule enforced here (D-034): a complete observation carrying
*no* client recommendation row is "not yet parsed". It is excluded from the
scoreable count and so counts against §6.2 completeness. It is never read as
RPV 0.00. Scoring a zero requires an explicit row saying which kind of zero
it was — `absent`, `source_only_mention` or `negative_mention`.
"""

from __future__ import annotations

from atlas.adapters.base import OutcomeType
from atlas.scoring.types import PeriodObservations, ReplicateObservation, rpv_for

# Methodology §8.1 Layer A. Mirrors observations.surface_layer (migration
# 0005). Defined here rather than imported from atlas.calibration so the
# scoring package keeps no dependency on the calibration package.
LAYER_API = "api"


_MIGRATION_0005 = "migrations/0005_surface_layer_and_calibration_results.sql"


def _select_api_layer(db, run_plan_id: str) -> list[dict]:
    """Layer A observations for one run plan.

    D-043: only Layer A (§8.1 controlled API benchmark) rows are ever scored.
    A Layer B consumer capture carries the same `provider` value as its API
    counterpart, so without this filter human captures would read as extra
    replicates of the same platform and fold straight into a client-facing
    AVS — inverting §8.3 and destroying the very comparison the §8.4 gate
    exists to make, silently.

    Written as a positive allowlist rather than a `!= 'consumer'` exclusion so
    that any future third layer is excluded by default and must be admitted
    deliberately.
    """
    return (
        db.table("observations")
        .select(
            "id,provider,prompt_version_id,replicate_index,status,"
            "grounding_status,surface_layer"
        )
        .eq("run_plan_id", run_plan_id)
        .eq("surface_layer", LAYER_API)
        .execute()
        .data
    )


# Migration 0001 wrote these two names; Methodology §4.1 and OutcomeType use
# the *_mention forms. Migration 0004 (D-035) aligns the constraint. Until it
# is applied, a legacy value is rejected loudly rather than mapped silently.
_LEGACY_OUTCOME_TYPES = {
    "source_only": "source_only_mention",
    "negative": "negative_mention",
}

# §8.1: an ungrounded response that stayed ungrounded after the retry is
# "marked ineligible rather than silently scored as grounded".
INELIGIBLE_GROUNDING = "ungrounded_ineligible"


def _outcome_type(raw: str) -> OutcomeType:
    if raw in _LEGACY_OUTCOME_TYPES:
        raise ValueError(
            f"recommendations.outcome_type holds legacy value {raw!r}; expected "
            f"{_LEGACY_OUTCOME_TYPES[raw]!r} per Methodology §4.1. Apply "
            "migrations/0004_recommendations_outcome_type_vocabulary.sql (D-035)."
        )
    try:
        return OutcomeType(raw)
    except ValueError as exc:
        raise ValueError(f"unknown recommendations.outcome_type {raw!r}") from exc


def load_period(
    db,
    run_plan_id: str,
    label: str,
    *,
    verify_rpv: bool = True,
) -> PeriodObservations:
    """Build one period's scoring input from the database.

    `verify_rpv` cross-checks each stored `rpv` against the §4.1 table given
    the row's own `outcome_type` and `rank`. It does not assign RPV — it
    catches a bad manual parse at the boundary instead of in a client report.
    """
    try:
        observations = _select_api_layer(db, run_plan_id)
    except Exception as exc:  # noqa: BLE001 — re-raised with actionable context
        if "surface_layer" in str(exc):
            raise ValueError(
                "observations.surface_layer does not exist. Apply "
                f"{_MIGRATION_0005} before scoring (D-043) — without it, Layer B "
                "consumer captures cannot be told apart from Layer A replicates "
                "and would fold into AVS."
            ) from exc
        raise
    if not observations:
        raise ValueError(f"run plan {run_plan_id!r} has no planned observations")

    # D-039: the planner writes the full task list before any provider call
    # (Technical Lane step 3), so this denominator is fixed in advance. Since
    # D-043 it is the run plan's *Layer A* task list — Layer B captures belong
    # to their own run plan and are not part of an AVS completeness ratio.
    planned_count = len(observations)

    prompt_version_ids = sorted({o["prompt_version_id"] for o in observations})
    prompt_versions = (
        db.table("prompt_versions")
        .select("id,intent_tier")
        .in_("id", prompt_version_ids)
        .execute()
        .data
    )
    tier_by_prompt_version = {p["id"]: p["intent_tier"] for p in prompt_versions}
    missing = set(prompt_version_ids) - set(tier_by_prompt_version)
    if missing:
        raise ValueError(f"prompt_versions rows missing for {sorted(missing)}")

    observation_ids = [o["id"] for o in observations]
    recommendations = (
        db.table("recommendations")
        .select("observation_id,rpv,rank,outcome_type,is_client_entity,entity_conflict")
        .in_("observation_id", observation_ids)
        .execute()
        .data
    )

    client_row_by_observation: dict[str, dict] = {}
    for row in recommendations:
        if not row.get("is_client_entity"):
            # Competitor rows are retained in the table for Share of Voice
            # (§9) but are not the client's AVS input.
            continue
        observation_id = row["observation_id"]
        if observation_id in client_row_by_observation:
            raise ValueError(
                f"observation {observation_id!r} has more than one client-entity "
                "recommendation row; the parse must emit exactly one (D-034)"
            )
        client_row_by_observation[observation_id] = row

    scoreable: list[ReplicateObservation] = []
    for observation in observations:
        row = client_row_by_observation.get(observation["id"])
        if row is None:
            # D-034: not yet parsed. Counts against completeness, never a zero.
            continue
        if observation["status"] != "complete":
            continue
        if observation.get("grounding_status") == INELIGIBLE_GROUNDING:
            continue

        outcome_type = _outcome_type(row["outcome_type"])
        rpv = float(row["rpv"])
        rank = row.get("rank")

        if verify_rpv:
            expected = rpv_for(outcome_type, rank)
            if expected is not None and abs(expected - rpv) > 1e-9:
                raise ValueError(
                    f"observation {observation['id']!r}: stored rpv {rpv} disagrees "
                    f"with Methodology §4.1 for outcome_type={outcome_type.value!r} "
                    f"rank={rank!r} (expected {expected})"
                )

        scoreable.append(
            ReplicateObservation(
                prompt_id=observation["prompt_version_id"],
                intent_tier=tier_by_prompt_version[observation["prompt_version_id"]],
                platform=observation["provider"],
                replicate_index=observation["replicate_index"],
                rpv=rpv,
                outcome_type=outcome_type,
                entity_conflict=bool(row.get("entity_conflict")),
                observation_id=observation["id"],
            )
        )

    return PeriodObservations(
        label=label,
        observations=tuple(scoreable),
        planned_observation_count=planned_count,
        scoreable_observation_count=len(scoreable),
    )
