"""RPV labeling tool — reject, never coerce.

The rule this suite protects: a label that is wrong must be *rejected with its
sheet row number*, never quietly repaired. D-034 makes "not parsed" and
"parsed and absent" semantically different, so a coerced label is worse than a
missing one — it is indistinguishable from a real one once it lands.
"""

from __future__ import annotations

import uuid

import pytest

from atlas.tools import rpv_labeling
from atlas.tools.rpv_labeling import (
    OUTCOME_TYPES,
    SHEET_COLUMNS,
    LiveSchema,
    build_export_rows,
    parse_sheet,
    unlabelled_observations,
    validate,
)

LIVE_SCHEMA = LiveSchema(
    columns=frozenset(
        {
            "id",
            "observation_id",
            "entity_name",
            "is_client_entity",
            "rank",
            "rpv",
            "outcome_type",
            "entity_conflict",
            "created_at",
        }
    ),
    required=frozenset(
        {"id", "entity_name", "is_client_entity", "rpv", "outcome_type", "entity_conflict", "created_at"}
    ),
    types={},
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(rpv_labeling, "fetch_live_schema", lambda: LIVE_SCHEMA)


def header():
    return list(SHEET_COLUMNS)


def row(observation_id, entity="Samujana", client="TRUE", outcome="ranked", rank=1,
        provider="openai", layer="api", version="accommodation-th-en-v1.0", notes=""):
    return [observation_id, provider, layer, version, entity, client, outcome, rank, notes]


@pytest.fixture
def labeled_db(fake_db):
    prompt_version_id = str(uuid.uuid4())
    run_plan_id = str(uuid.uuid4())
    fake_db.seed(
        "prompt_versions",
        [{"id": prompt_version_id, "version": "accommodation-th-en-v1.0", "intent_tier": "A"}],
    )
    observation_ids = [str(uuid.uuid4()) for _ in range(3)]
    fake_db.seed(
        "observations",
        [
            {
                "id": oid,
                "run_plan_id": run_plan_id,
                "prompt_version_id": prompt_version_id,
                "provider": "openai",
                "surface_layer": "api",
                "replicate_index": i,
                "status": "complete",
            }
            for i, oid in enumerate(observation_ids)
        ],
    )
    return {"db": fake_db, "ids": observation_ids, "run_plan_id": run_plan_id}


# ---------------------------------------------------------------------
# Vocabulary — verified against the live constraint, not assumed
# ---------------------------------------------------------------------


def test_vocabulary_is_the_six_methodology_values():
    """Confirmed empirically against the live check constraint 2026-08-27.
    Note 'recommended' is NOT a value — §4.1 splits positives into 'ranked'
    (with an ordinal) and 'unordered_positive' (without)."""
    assert set(OUTCOME_TYPES) == {
        "ranked",
        "unordered_positive",
        "source_only_mention",
        "absent",
        "negative_mention",
        "entity_conflict",
    }
    assert "recommended" not in OUTCOME_TYPES
    assert "source_only" not in OUTCOME_TYPES  # legacy, retired by D-035
    assert "negative" not in OUTCOME_TYPES


# ---------------------------------------------------------------------
# Parsing — strict
# ---------------------------------------------------------------------


def test_header_mismatch_is_rejected_wholesale():
    rows, errors, _ = parse_sheet([["observation_id", "provider"], []])
    assert rows == []
    assert "header mismatch" in errors[0].message


def test_blank_rows_are_skipped_not_errored():
    _, errors, blank = parse_sheet([header(), ["", "", "", "", "", "", "", "", ""]])
    assert errors == []
    assert blank == 1


@pytest.mark.parametrize("value", ["maybe", "Y E S", "2", ""])
def test_non_boolean_is_client_entity_is_rejected(value):
    _, errors, _ = parse_sheet([header(), row(str(uuid.uuid4()), client=value)])
    assert any(e.column == "is_client_entity" for e in errors)


@pytest.mark.parametrize("value,expected", [("TRUE", True), ("false", False), ("1", True), ("n", False)])
def test_accepted_boolean_spellings(value, expected):
    rows, errors, _ = parse_sheet([header(), row(str(uuid.uuid4()), client=value)])
    assert errors == []
    assert rows[0].is_client_entity is expected


def test_unknown_outcome_type_is_rejected():
    _, errors, _ = parse_sheet([header(), row(str(uuid.uuid4()), outcome="recommended")])
    assert any(e.column == "outcome_type" and "recommended" in e.message for e in errors)


def test_ranked_without_a_rank_is_rejected():
    _, errors, _ = parse_sheet([header(), row(str(uuid.uuid4()), outcome="ranked", rank="")])
    assert any(e.column == "rank" and "requires a rank" in e.message for e in errors)


@pytest.mark.parametrize("outcome", ["absent", "unordered_positive", "source_only_mention"])
def test_rank_on_a_non_ranked_outcome_is_rejected(outcome):
    """An unordered positive has no ordinal position (§4.1). A rank here means
    the labeler picked the wrong outcome_type."""
    _, errors, _ = parse_sheet([header(), row(str(uuid.uuid4()), outcome=outcome, rank=3)])
    assert any(e.column == "rank" and "must not carry a rank" in e.message for e in errors)


def test_zero_and_negative_ranks_are_rejected():
    for bad in (0, -1):
        _, errors, _ = parse_sheet([header(), row(str(uuid.uuid4()), rank=bad)])
        assert any(e.column == "rank" for e in errors)


def test_non_uuid_observation_id_is_rejected():
    _, errors, _ = parse_sheet([header(), row("not-a-uuid")])
    assert any(e.column == "observation_id" for e in errors)


def test_every_error_names_its_sheet_row():
    values = [header(), row(str(uuid.uuid4()), client="maybe"), row(str(uuid.uuid4()), rank=0)]
    _, errors, _ = parse_sheet(values)
    assert {e.row_number for e in errors} == {2, 3}


def test_all_errors_collected_not_just_the_first():
    _, errors, _ = parse_sheet([header(), row("bad", entity="", client="maybe", outcome="nope")])
    assert len(errors) >= 3


# ---------------------------------------------------------------------
# RPV derivation — §4.1, never hand-typed
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome,rank,expected",
    [
        ("ranked", 1, 1.00),
        ("ranked", 2, 0.80),
        ("ranked", 3, 0.65),
        ("ranked", 5, 0.45),
        ("ranked", 9, 0.25),
        ("ranked", 11, 0.00),  # D-041
        ("unordered_positive", None, 0.30),
        ("absent", None, 0.00),
        ("source_only_mention", None, 0.00),
        ("negative_mention", None, 0.00),
    ],
)
def test_rpv_is_derived_from_the_label(outcome, rank, expected):
    rows, errors, _ = parse_sheet(
        [header(), row(str(uuid.uuid4()), outcome=outcome, rank=rank if rank else "")]
    )
    assert errors == []
    assert rows[0].to_recommendation()["rpv"] == pytest.approx(expected)


def test_entity_conflict_sets_the_flag_and_stores_a_placeholder_rpv():
    """§4.1 excludes entity conflicts rather than scoring them, so the stored
    rpv is never read — but the column is NOT NULL."""
    rows, _, _ = parse_sheet([header(), row(str(uuid.uuid4()), outcome="entity_conflict", rank="")])
    payload = rows[0].to_recommendation()
    assert payload["entity_conflict"] is True
    assert payload["rpv"] == 0.00


def test_rpv_is_not_a_sheet_column():
    assert "rpv" not in SHEET_COLUMNS
    assert "entity_conflict" not in SHEET_COLUMNS


# ---------------------------------------------------------------------
# Validation against the database
# ---------------------------------------------------------------------


def test_valid_rows_pass(labeled_db):
    db, ids = labeled_db["db"], labeled_db["ids"]
    report = validate(db, [header(), row(ids[0]), row(ids[1], outcome="absent", rank="")])
    assert report.ok, report.render()
    assert len(report.valid) == 2


def test_unknown_observation_id_is_rejected(labeled_db):
    report = validate(labeled_db["db"], [header(), row(str(uuid.uuid4()))])
    assert not report.ok
    assert "does not exist in observations" in report.errors[0].message


def test_mispasted_observation_id_is_caught_by_the_context_tripwire(labeled_db):
    """The id is a real observation, but the provider exported next to it was
    'openai' and the sheet row says 'gemini' — the labeler pasted across lines."""
    db, ids = labeled_db["db"], labeled_db["ids"]
    report = validate(db, [header(), row(ids[0], provider="gemini")])
    assert not report.ok
    assert "mis-pasted" in report.errors[0].message
    assert "gemini" in report.errors[0].message


def test_blank_context_cell_is_not_a_mismatch(labeled_db):
    db, ids = labeled_db["db"], labeled_db["ids"]
    report = validate(db, [header(), row(ids[0], provider="", layer="")])
    assert report.ok, report.render()


def test_two_client_rows_for_one_observation_are_rejected(labeled_db):
    """D-034: the parse must emit exactly one client-entity row."""
    db, ids = labeled_db["db"], labeled_db["ids"]
    report = validate(db, [header(), row(ids[0]), row(ids[0], entity="Samujana Villas")])
    assert not report.ok
    assert "exactly one (D-034)" in report.errors[0].message


def test_competitor_rows_for_one_observation_are_allowed(labeled_db):
    """Share of Voice (§9) needs the competitor rows; only the *client* row is
    limited to one."""
    db, ids = labeled_db["db"], labeled_db["ids"]
    report = validate(
        db,
        [
            header(),
            row(ids[0], entity="Samujana", client="TRUE", rank=1),
            row(ids[0], entity="Six Senses Samui", client="FALSE", rank=2),
            row(ids[0], entity="Cape Fahn", client="FALSE", rank=3),
        ],
    )
    assert report.ok, report.render()
    assert len(report.valid) == 3


def test_same_entity_twice_for_one_observation_is_rejected(labeled_db):
    """D-048: one row per entity per observation. The same competitor entered
    at two ranks used to pass validation and fail later on migration 0006's
    unique constraint — a Postgres constraint name instead of a sheet row."""
    db, ids = labeled_db["db"], labeled_db["ids"]
    report = validate(
        db,
        [
            header(),
            row(ids[0], entity="Samujana", client="TRUE", rank=1),
            row(ids[0], entity="Six Senses Samui", client="FALSE", rank=2),
            row(ids[0], entity="Six Senses Samui", client="FALSE", rank=7),
        ],
    )
    assert not report.ok
    (error,) = report.errors
    # Sheet row 4: header is row 1, so the second Six Senses row is the fourth.
    assert error.row_number == 4
    assert error.column == "entity_name"
    assert "at sheet row 3" in error.message
    assert "(D-048)" in error.message
    # The first occurrence still stands — only the repeat is rejected.
    assert [r.row_number for r in report.valid] == [2, 3]


def test_same_entity_name_across_different_observations_is_allowed(labeled_db):
    """The key is the pair. One competitor appearing in every observation of a
    run is the normal case, not a duplicate."""
    db, ids = labeled_db["db"], labeled_db["ids"]
    report = validate(
        db,
        [
            header(),
            row(ids[0], entity="Six Senses Samui", client="FALSE", rank=2),
            row(ids[1], entity="Six Senses Samui", client="FALSE", rank=2),
        ],
    )
    assert report.ok, report.render()
    assert len(report.valid) == 2


def test_already_labelled_observation_is_refused(labeled_db):
    db, ids = labeled_db["db"], labeled_db["ids"]
    db.seed("recommendations", [{"observation_id": ids[0], "entity_name": "Samujana"}])
    report = validate(db, [header(), row(ids[0])])
    assert not report.ok
    assert "already has recommendation" in report.errors[0].message


def test_schema_mismatch_stops_before_row_validation(labeled_db, monkeypatch):
    """If the deployed table lacks a column the tool writes, per-row results are
    meaningless — report the structural fault and stop."""
    monkeypatch.setattr(
        rpv_labeling,
        "fetch_live_schema",
        lambda: LiveSchema(columns=frozenset({"id", "entity_name"}), required=frozenset(), types={}),
    )
    report = validate(labeled_db["db"], [header(), row(labeled_db["ids"][0])])
    assert not report.ok
    assert "has no column" in report.errors[0].message


# ---------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------


def test_export_covers_only_unlabelled_complete_observations(labeled_db):
    db, ids = labeled_db["db"], labeled_db["ids"]
    db.seed("recommendations", [{"observation_id": ids[0], "entity_name": "Samujana"}])
    pending = unlabelled_observations(db, [labeled_db["run_plan_id"]])
    assert {o["id"] for o in pending} == {ids[1], ids[2]}


def test_export_skips_incomplete_observations(fake_db):
    run_plan_id = str(uuid.uuid4())
    fake_db.seed(
        "observations",
        [
            {"id": str(uuid.uuid4()), "run_plan_id": run_plan_id, "status": "failed"},
            {"id": str(uuid.uuid4()), "run_plan_id": run_plan_id, "status": "running"},
        ],
    )
    assert unlabelled_observations(fake_db, [run_plan_id]) == []


def test_export_rows_start_with_the_header_and_leave_labels_blank(labeled_db):
    db = labeled_db["db"]
    pending = unlabelled_observations(db, [labeled_db["run_plan_id"]])
    rows = build_export_rows(db, pending)
    assert rows[0] == list(SHEET_COLUMNS)
    assert len(rows) == 4
    for exported in rows[1:]:
        assert exported[SHEET_COLUMNS.index("prompt_version")] == "accommodation-th-en-v1.0"
        assert exported[SHEET_COLUMNS.index("surface_layer")] == "api"
        for column in ("entity_name", "is_client_entity", "outcome_type", "rank", "notes"):
            assert exported[SHEET_COLUMNS.index(column)] == ""


def test_exported_rows_round_trip_through_the_parser(labeled_db):
    """Export writes what validate expects — the two halves cannot drift."""
    db = labeled_db["db"]
    rows = build_export_rows(db, unlabelled_observations(db, [labeled_db["run_plan_id"]]))
    for exported in rows[1:]:
        exported[SHEET_COLUMNS.index("entity_name")] = "Samujana"
        exported[SHEET_COLUMNS.index("is_client_entity")] = "TRUE"
        exported[SHEET_COLUMNS.index("outcome_type")] = "ranked"
        exported[SHEET_COLUMNS.index("rank")] = 1
    report = validate(db, rows)
    assert report.ok, report.render()
    assert len(report.valid) == 3
