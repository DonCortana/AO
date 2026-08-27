"""The scoring engine's input boundary — decision-register D-034.

The distinction these tests exist to protect: an observation with *no*
recommendation row is "not yet parsed" and counts against §6.2 completeness;
an observation with a row saying `absent` is a measured zero. Collapsing them
turns a parsing backlog into a reported regression.

Runs against the FakeDB double in tests/conftest.py, which mirrors the
chained Supabase/postgrest query interface.
"""

from __future__ import annotations

import uuid

import pytest

from atlas.adapters.base import OutcomeType
from atlas.scoring import compute_avs, load_period

EXACT = 1e-12


@pytest.fixture
def scoring_db(fake_db):
    """One run plan: 1 prompt (tier A), 1 provider, 4 replicates, all
    complete and grounded. No recommendation rows yet."""
    run_plan_id = str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())

    fake_db.seed("prompt_versions", [{"id": prompt_version_id, "intent_tier": "A"}])
    observation_ids = []
    rows = []
    for index in range(4):
        observation_id = str(uuid.uuid4())
        observation_ids.append(observation_id)
        rows.append(
            {
                "id": observation_id,
                "run_plan_id": run_plan_id,
                "prompt_version_id": prompt_version_id,
                "provider": "openai",
                "replicate_index": index,
                "status": "complete",
                "grounding_status": "grounded",
            }
        )
    fake_db.seed("observations", rows)
    return {
        "db": fake_db,
        "run_plan_id": run_plan_id,
        "prompt_version_id": prompt_version_id,
        "observation_ids": observation_ids,
    }


def recommendation(observation_id, rpv, outcome_type, rank=None, is_client=True, conflict=False):
    return {
        "observation_id": observation_id,
        "rpv": rpv,
        "rank": rank,
        "outcome_type": outcome_type,
        "is_client_entity": is_client,
        "entity_conflict": conflict,
    }


# ---------------------------------------------------------------------
# The load-bearing distinction
# ---------------------------------------------------------------------


def test_unparsed_observation_is_incomplete_not_absent(scoring_db):
    """Four complete observations, only two parsed.

    completeness = 2 scoreable / 4 planned = 50.0%, and the two unparsed
    observations contribute *nothing* to AVS — they are not zeros.

        PVS = (1.00 + 0.80) / 2 = 0.90  ->  AVS 90.0

    Had the unparsed pair been read as RPV 0.00 the answer would be
    (1.00 + 0.80) / 4 = 0.45 -> AVS 45.0: a parsing backlog reported as a
    45-point visibility collapse.
    """
    db = scoring_db["db"]
    ids = scoring_db["observation_ids"]
    db.seed(
        "recommendations",
        [
            recommendation(ids[0], 1.00, "ranked", rank=1),
            recommendation(ids[1], 0.80, "ranked", rank=2),
        ],
    )

    period = load_period(db, scoring_db["run_plan_id"], "baseline")
    assert period.planned_observation_count == 4
    assert period.scoreable_observation_count == 2
    assert period.completeness_pct == pytest.approx(50.0, abs=EXACT)

    result = compute_avs(period.observations, ["openai"])
    assert result.avs == pytest.approx(90.0, abs=EXACT)


def test_explicit_absent_row_is_a_measured_zero(scoring_db):
    """The same four observations, all four parsed, two of them absent.

        PVS = (1.00 + 0.80 + 0.00 + 0.00) / 4 = 0.45  ->  AVS 45.0

    completeness is now 100% — the run measured what it planned to measure.
    """
    db = scoring_db["db"]
    ids = scoring_db["observation_ids"]
    db.seed(
        "recommendations",
        [
            recommendation(ids[0], 1.00, "ranked", rank=1),
            recommendation(ids[1], 0.80, "ranked", rank=2),
            recommendation(ids[2], 0.00, "absent"),
            recommendation(ids[3], 0.00, "absent"),
        ],
    )

    period = load_period(db, scoring_db["run_plan_id"], "baseline")
    assert period.completeness_pct == pytest.approx(100.0, abs=EXACT)

    result = compute_avs(period.observations, ["openai"])
    assert result.avs == pytest.approx(45.0, abs=EXACT)


def test_source_only_and_negative_survive_the_load_distinctly(scoring_db):
    """All three zero-scoring outcomes score 0.00 but stay distinguishable
    in the loaded record (D-034)."""
    db = scoring_db["db"]
    ids = scoring_db["observation_ids"]
    db.seed(
        "recommendations",
        [
            recommendation(ids[0], 1.00, "ranked", rank=1),
            recommendation(ids[1], 0.00, "source_only_mention"),
            recommendation(ids[2], 0.00, "negative_mention"),
            recommendation(ids[3], 0.00, "absent"),
        ],
    )
    period = load_period(db, scoring_db["run_plan_id"], "baseline")
    outcomes = {o.outcome_type for o in period.observations}
    assert outcomes == {
        OutcomeType.RANKED,
        OutcomeType.SOURCE_ONLY_MENTION,
        OutcomeType.NEGATIVE_MENTION,
        OutcomeType.ABSENT,
    }
    assert compute_avs(period.observations, ["openai"]).avs == pytest.approx(25.0, abs=EXACT)


# ---------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------


def test_ungrounded_ineligible_observation_is_not_scoreable(scoring_db):
    """§8.1: an observation that stayed ungrounded after the retry is
    "marked ineligible rather than silently scored as grounded\"."""
    db = scoring_db["db"]
    ids = scoring_db["observation_ids"]
    db.tables["observations"][3]["grounding_status"] = "ungrounded_ineligible"
    db.seed(
        "recommendations",
        [recommendation(observation_id, 1.00, "ranked", rank=1) for observation_id in ids],
    )

    period = load_period(db, scoring_db["run_plan_id"], "baseline")
    assert period.scoreable_observation_count == 3
    assert period.planned_observation_count == 4
    assert period.completeness_pct == pytest.approx(75.0, abs=EXACT)


def test_incomplete_status_observation_is_not_scoreable(scoring_db):
    """A failed or still-running task is never scored (Operating System §1)."""
    db = scoring_db["db"]
    ids = scoring_db["observation_ids"]
    db.tables["observations"][2]["status"] = "failed"
    db.tables["observations"][3]["status"] = "running"
    db.seed(
        "recommendations",
        [recommendation(observation_id, 1.00, "ranked", rank=1) for observation_id in ids],
    )

    period = load_period(db, scoring_db["run_plan_id"], "baseline")
    assert period.scoreable_observation_count == 2


def test_competitor_rows_are_ignored_for_the_client_avs(scoring_db):
    """Competitor rows stay in the table for Share of Voice (§9) but are not
    the client's AVS input."""
    db = scoring_db["db"]
    ids = scoring_db["observation_ids"]
    db.seed(
        "recommendations",
        [
            recommendation(ids[0], 1.00, "ranked", rank=1),
            recommendation(ids[0], 0.80, "ranked", rank=2, is_client=False),
            recommendation(ids[1], 0.80, "ranked", rank=2),
            recommendation(ids[1], 1.00, "ranked", rank=1, is_client=False),
        ],
    )
    period = load_period(db, scoring_db["run_plan_id"], "baseline")
    assert period.scoreable_observation_count == 2
    assert compute_avs(period.observations, ["openai"]).avs == pytest.approx(90.0, abs=EXACT)


def test_entity_conflict_row_loads_but_does_not_score(scoring_db):
    db = scoring_db["db"]
    ids = scoring_db["observation_ids"]
    db.seed(
        "recommendations",
        [
            recommendation(ids[0], 1.00, "ranked", rank=1),
            recommendation(ids[1], 0.00, "entity_conflict", conflict=True),
        ],
    )
    period = load_period(db, scoring_db["run_plan_id"], "baseline")
    # It loaded — it is a parsed observation, so it counts toward completeness.
    assert period.scoreable_observation_count == 2
    # But §4.1 excludes it from the score.
    result = compute_avs(period.observations, ["openai"])
    assert result.avs == pytest.approx(100.0, abs=EXACT)
    assert result.excluded.entity_conflicts == 1


# ---------------------------------------------------------------------
# Integrity guards
# ---------------------------------------------------------------------


def test_legacy_outcome_type_names_the_migration(scoring_db):
    """D-035: migration 0001 wrote 'source_only'; §4.1 says
    'source_only_mention'. Until migration 0004 is applied, reject loudly."""
    db = scoring_db["db"]
    db.seed(
        "recommendations",
        [recommendation(scoring_db["observation_ids"][0], 0.00, "source_only")],
    )
    with pytest.raises(ValueError, match="0004_recommendations_outcome_type_vocabulary"):
        load_period(db, scoring_db["run_plan_id"], "baseline")


def test_stored_rpv_disagreeing_with_the_rank_is_rejected(scoring_db):
    """A rank-2 outcome must carry RPV 0.80 (§4.1). Catching a bad manual
    parse at the boundary rather than in a client report."""
    db = scoring_db["db"]
    db.seed(
        "recommendations",
        [recommendation(scoring_db["observation_ids"][0], 1.00, "ranked", rank=2)],
    )
    with pytest.raises(ValueError, match="disagrees with Methodology"):
        load_period(db, scoring_db["run_plan_id"], "baseline")


def test_rpv_verification_can_be_disabled(scoring_db):
    db = scoring_db["db"]
    db.seed(
        "recommendations",
        [recommendation(scoring_db["observation_ids"][0], 1.00, "ranked", rank=2)],
    )
    period = load_period(db, scoring_db["run_plan_id"], "baseline", verify_rpv=False)
    assert period.observations[0].rpv == 1.00


def test_two_client_rows_for_one_observation_is_rejected(scoring_db):
    """The parse must emit exactly one client-entity row per observation."""
    db = scoring_db["db"]
    observation_id = scoring_db["observation_ids"][0]
    db.seed(
        "recommendations",
        [
            recommendation(observation_id, 1.00, "ranked", rank=1),
            recommendation(observation_id, 0.80, "ranked", rank=2),
        ],
    )
    with pytest.raises(ValueError, match="more than one client-entity"):
        load_period(db, scoring_db["run_plan_id"], "baseline")


def test_unknown_outcome_type_is_rejected(scoring_db):
    db = scoring_db["db"]
    db.seed(
        "recommendations",
        [recommendation(scoring_db["observation_ids"][0], 0.50, "maybe_probably")],
    )
    with pytest.raises(ValueError, match="unknown recommendations.outcome_type"):
        load_period(db, scoring_db["run_plan_id"], "baseline")


def test_run_plan_with_no_observations_is_rejected(fake_db):
    with pytest.raises(ValueError, match="no planned observations"):
        load_period(fake_db, str(uuid.uuid4()), "baseline")


def test_missing_prompt_version_row_is_rejected(scoring_db):
    db = scoring_db["db"]
    db.tables["prompt_versions"].clear()
    db.seed(
        "recommendations",
        [recommendation(scoring_db["observation_ids"][0], 1.00, "ranked", rank=1)],
    )
    with pytest.raises(ValueError, match="prompt_versions rows missing"):
        load_period(db, scoring_db["run_plan_id"], "baseline")
