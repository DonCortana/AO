"""Layer B consumer-surface ingest.

Two failures this suite exists to make impossible, both silent:

  - a human capture landing with `surface_layer='api'`, which puts it in the
    AVS scoring frame (the D-043 inversion, reintroduced from the write side);
  - evidence written with a NULL `observation_id`, which never conflicts under
    the migration 0003 unique index and so appends a fresh row on every re-run.

Neither raises on its own. Both are caught here.

The Drive upload is stubbed at `vault.upload_to_drive`, so `store_evidence`
itself runs for real — the evidence rows asserted on are the ones the vault
actually writes, not a test double's idea of them.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from atlas.evidence import vault
from atlas.tools import consumer_ingest
from atlas.tools.consumer_ingest import (
    CONSUMER_SURFACE_MODELS,
    SHEET_COLUMNS,
    SURFACE_LAYER,
    assert_no_api_layer_leak,
    build_template_rows,
    import_from_sheet,
    parse_sheet,
    task_id_for,
    validate,
)

FROZEN_CORE = "frozen-core-samujana-v1"
TIERS = ["A", "A", "A", "B", "B", "B", "C", "C", "C", "D"]

START = "2026-08-29T14:03:00+07:00"
END = "2026-08-29T14:05:00+07:00"


@pytest.fixture
def campaign(fake_db):
    """One property's Frozen Core: ten prompts, one market, one run plan at the
    §8.3 target of three replicates."""
    property_id = str(uuid.uuid4())
    market_id = str(uuid.uuid4())
    run_plan_id = str(uuid.uuid4())

    fake_db.seed(
        "markets",
        [{"id": market_id, "property_id": property_id, "market_code": "TH", "language_code": "en"}],
    )
    prompts = [
        {
            "id": str(uuid.uuid4()),
            "set_type": "frozen_core",
            "version": FROZEN_CORE,
            "prompt_text": f"prompt {n} for tier {tier}",
            "intent_tier": tier,
            "market_id": market_id,
        }
        for n, tier in enumerate(TIERS)
    ]
    fake_db.seed("prompt_versions", prompts)
    fake_db.seed(
        "run_plans",
        [
            {
                "id": run_plan_id,
                "property_id": property_id,
                "run_type": "frozen_core",
                "replicate_count": 3,
                "status": "planned",
                "surface_layer": "consumer",
                # D-084/D-085: a Layer B plan records the prompt set it
                # measures, and validate() now refuses to file a capture under
                # a plan whose version disagrees with the sheet's.
                "prompt_set_version": FROZEN_CORE,
            }
        ],
    )
    return {"run_plan_id": run_plan_id, "prompts": prompts, "market_id": market_id}


@pytest.fixture
def screenshot(tmp_path):
    path = tmp_path / "capture.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"screenshot" * 500)
    return str(path)


@pytest.fixture(autouse=True)
def _no_drive(monkeypatch):
    """Stub only the network leg. `store_evidence` — its upsert, its column
    mapping, its upload-before-write ordering — runs for real."""
    uploaded = []

    def fake_upload(local_path, folder_id=None, *, record=None):
        uploaded.append({"path": local_path, "record": record})
        return f"https://drive.google.com/file/d/{len(uploaded)}/view"

    monkeypatch.setattr(vault, "upload_to_drive", fake_upload)
    return uploaded


def header():
    return list(SHEET_COLUMNS)


def row(
    prompt_version_id,
    *,
    provider="perplexity",
    replicate_index=0,
    capture_text="Samujana is a villa estate in Koh Samui.",
    surface_url="https://www.perplexity.ai/search/abc",
    evidence_path="",
    start=START,
    end=END,
    operator="Doud",
    tier="A",
    prompt_text="prompt",
    notes="",
):
    """Built by column name so adding a column cannot silently shift every
    value one cell left."""
    cells = {
        "prompt_version_id": prompt_version_id,
        "intent_tier": tier,
        "prompt_text": prompt_text,
        "provider": provider,
        "replicate_index": replicate_index,
        "capture_text": capture_text,
        "surface_url": surface_url,
        "evidence_path": evidence_path,
        "captured_start": start,
        "captured_end": end,
        "operator": operator,
        "notes": notes,
    }
    return [cells[name] for name in SHEET_COLUMNS]


def sheet(campaign, screenshot, *, count=3, provider="perplexity"):
    """A full ten-prompt campaign at `count` replicates per prompt."""
    values = [header()]
    for prompt in campaign["prompts"]:
        for replicate_index in range(count):
            values.append(
                row(
                    prompt["id"],
                    provider=provider,
                    replicate_index=replicate_index,
                    evidence_path=screenshot,
                    tier=prompt["intent_tier"],
                )
            )
    return values


def load(monkeypatch, values):
    monkeypatch.setattr(consumer_ingest, "read_values", lambda *a, **k: values)


# ---------------------------------------------------------------------
# surface_layer — the silent inversion
# ---------------------------------------------------------------------


def test_every_observation_payload_carries_surface_layer_consumer(campaign, screenshot):
    """The DDL default is 'api' (migration 0005), so an omitted field does not
    fail — it writes a human capture into the AVS frame."""
    rows, errors, _ = parse_sheet(sheet(campaign, screenshot, count=1))
    assert not errors
    assert rows
    for capture in rows:
        assert capture.to_observation(campaign["run_plan_id"])["surface_layer"] == "consumer"


def test_committed_rows_are_all_consumer_layer(fake_db, campaign, screenshot, monkeypatch):
    load(monkeypatch, sheet(campaign, screenshot))
    report = import_from_sheet(
        fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False
    )
    assert report.ok
    assert report.observations_written == 30
    assert {o["surface_layer"] for o in fake_db.tables["observations"]} == {"consumer"}


def test_post_commit_assertion_catches_an_api_layer_row_under_the_plan(fake_db, campaign):
    """§6's assertion. The leak need not come from this tool — any row under
    the plan at the wrong layer pollutes the frame, so the check is on the
    plan, not on what this import just wrote."""
    fake_db.seed(
        "observations",
        [
            {
                "id": str(uuid.uuid4()),
                "task_id": "leaked-1",
                "run_plan_id": campaign["run_plan_id"],
                "surface_layer": "api",
            }
        ],
    )
    with pytest.raises(RuntimeError, match="not surface_layer='consumer'"):
        assert_no_api_layer_leak(fake_db, campaign["run_plan_id"])


def test_post_commit_assertion_names_the_offending_task(fake_db, campaign):
    fake_db.seed(
        "observations",
        [
            {
                "id": str(uuid.uuid4()),
                "task_id": "leaked-1",
                "run_plan_id": campaign["run_plan_id"],
                "surface_layer": "api",
            }
        ],
    )
    with pytest.raises(RuntimeError, match="leaked-1"):
        assert_no_api_layer_leak(fake_db, campaign["run_plan_id"])


def test_post_commit_assertion_passes_on_a_clean_plan(fake_db, campaign, screenshot, monkeypatch):
    load(monkeypatch, sheet(campaign, screenshot, count=1))
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)
    assert_no_api_layer_leak(fake_db, campaign["run_plan_id"])  # does not raise


def test_a_leak_under_the_plan_fails_the_import_itself(fake_db, campaign, screenshot, monkeypatch):
    """The assertion runs inside import_from_sheet, so a commit into a polluted
    plan raises rather than returning success."""
    fake_db.seed(
        "observations",
        [
            {
                "id": str(uuid.uuid4()),
                "task_id": "leaked-1",
                "run_plan_id": campaign["run_plan_id"],
                "surface_layer": "api",
            }
        ],
    )
    load(monkeypatch, sheet(campaign, screenshot, count=1))
    with pytest.raises(RuntimeError, match="D-043"):
        import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)


# ---------------------------------------------------------------------
# §4 task_id and idempotency
# ---------------------------------------------------------------------


def test_task_id_follows_the_section_4_scheme(campaign):
    prompt_id = campaign["prompts"][0]["id"]
    assert task_id_for(campaign["run_plan_id"], "perplexity", prompt_id, 2) == (
        f"consumer:{campaign['run_plan_id']}:perplexity:{prompt_id}:2"
    )


def test_task_id_separates_the_same_prompt_on_two_surfaces(campaign):
    """The measurement unit is the cell (prompt_version_id, provider), which is
    what agreement.py pairs on. Two surfaces answering one prompt are two
    measurements — without provider in the key the upsert would eat one."""
    prompt_id = campaign["prompts"][0]["id"]
    plan = campaign["run_plan_id"]
    assert task_id_for(plan, "perplexity", prompt_id, 0) != task_id_for(
        plan, "openai", prompt_id, 0
    )


def test_re_ingesting_updates_the_cell_rather_than_duplicating_it(
    fake_db, campaign, screenshot, monkeypatch
):
    """§4: a human pipeline will be re-run after mistakes. Idempotency is not
    optional — and it has to reach the evidence ledger, not just observations."""
    load(monkeypatch, sheet(campaign, screenshot, count=1))
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)

    assert len(fake_db.tables["observations"]) == 10
    assert len(fake_db.tables["evidence"]) == 10


def test_a_corrected_capture_overwrites_only_its_own_cell(
    fake_db, campaign, screenshot, monkeypatch
):
    values = sheet(campaign, screenshot, count=1)
    load(monkeypatch, values)
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)

    corrected = [r[:] for r in values]
    corrected[1][SHEET_COLUMNS.index("capture_text")] = "corrected answer text"
    load(monkeypatch, corrected)
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)

    assert len(fake_db.tables["observations"]) == 10
    texts = [o["raw_response"]["capture_text"] for o in fake_db.tables["observations"]]
    assert texts.count("corrected answer text") == 1


# ---------------------------------------------------------------------
# Write ordering: observation first, evidence second
# ---------------------------------------------------------------------


def test_evidence_rows_carry_a_non_null_observation_id(
    fake_db, campaign, screenshot, monkeypatch
):
    """A NULL observation_id never conflicts under the migration 0003 unique
    index, so store_evidence's upsert would append instead of update."""
    load(monkeypatch, sheet(campaign, screenshot, count=1))
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)

    observation_ids = {o["id"] for o in fake_db.tables["observations"]}
    evidence_ids = {e["observation_id"] for e in fake_db.tables["evidence"]}
    assert None not in evidence_ids
    assert evidence_ids == observation_ids


def test_no_evidence_is_written_when_the_observation_write_returns_nothing(
    fake_db, campaign, screenshot, monkeypatch
):
    """Rather than falling back to a NULL observation_id, which would look like
    a successful capture and quietly duplicate on the next run."""
    load(monkeypatch, sheet(campaign, screenshot, count=1))

    real_table = fake_db.table

    def blind_upsert(name):
        query = real_table(name)
        if name == "observations":
            query.execute = lambda: type("R", (), {"data": []})()
        return query

    monkeypatch.setattr(fake_db, "table", blind_upsert)
    with pytest.raises(RuntimeError, match="without an observation_id"):
        import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)


# ---------------------------------------------------------------------
# The evidence record — §5 / §7 fields
# ---------------------------------------------------------------------


def test_the_screenshot_is_hashed_with_sha256_file(
    fake_db, campaign, screenshot, monkeypatch
):
    """hash_payload canonical-JSON-encodes a dict and raises on a PNG."""
    load(monkeypatch, sheet(campaign, screenshot, count=1))
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)

    expected = hashlib.sha256(Path(screenshot).read_bytes()).hexdigest()
    assert {e["payload_hash"] for e in fake_db.tables["evidence"]} == {expected}


def test_evidence_carries_the_section_7_human_capture_fields(
    fake_db, campaign, screenshot, monkeypatch
):
    load(monkeypatch, sheet(campaign, screenshot, count=1))
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)

    evidence = fake_db.tables["evidence"][0]
    assert evidence["data_class"] == "raw_ai_response"
    assert evidence["captured_by"] == "Doud"  # §7's operator, for human capture
    assert evidence["source_reference"] == "https://www.perplexity.ai/search/abc"
    assert evidence["provider"] == "perplexity"
    assert evidence["model"] == "perplexity-web"
    assert evidence["market"] == "TH"
    assert evidence["language"] == "en"
    assert evidence["prompt_version"] == FROZEN_CORE
    assert evidence["tool_version"] is None  # a browser capture has no tool leg
    assert evidence["run_id"] == campaign["run_plan_id"]


def test_the_surface_url_lives_on_the_evidence_row_not_in_the_envelope(
    fake_db, campaign, screenshot, monkeypatch
):
    """§5: the envelope holds capture_text only. The URL is §7's source
    reference and is stored untruncated in the column."""
    load(monkeypatch, sheet(campaign, screenshot, count=1))
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)

    observation = fake_db.tables["observations"][0]
    assert set(observation["raw_response"]) == {"capture_text"}
    assert fake_db.tables["evidence"][0]["source_reference"].startswith("https://")


def test_capture_timing_lands_on_the_observation(fake_db, campaign, screenshot, monkeypatch):
    """store_evidence never writes evidence.captured_at — the column defaults
    to now(), i.e. ingest time — so request_time/completion_time are the only
    record of when the capture actually happened."""
    load(monkeypatch, sheet(campaign, screenshot, count=1))
    import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False)

    observation = fake_db.tables["observations"][0]
    assert observation["request_time"] == "2026-08-29T14:03:00+07:00"
    assert observation["completion_time"] == "2026-08-29T14:05:00+07:00"


def test_consumer_surface_model_literal_is_derived_not_typed(campaign, screenshot):
    rows, _, _ = parse_sheet(sheet(campaign, screenshot, count=1, provider="google_ai"))
    payload = rows[0].to_observation(campaign["run_plan_id"])
    assert payload["model"] == CONSUMER_SURFACE_MODELS["google_ai"] == "google-ai-overviews"
    # D-042: google_ai has no Layer A leg and the check constraint enforces it.
    assert payload["surface_layer"] == SURFACE_LAYER


def test_cost_columns_are_unknown_not_zero(campaign, screenshot):
    rows, _, _ = parse_sheet(sheet(campaign, screenshot, count=1))
    payload = rows[0].to_observation(campaign["run_plan_id"])
    assert payload["cost_usd"] is None
    assert payload["is_unknown_cost"] is True


# ---------------------------------------------------------------------
# Validation — refusals
# ---------------------------------------------------------------------


def test_refuses_a_prompt_version_outside_the_verified_set(
    fake_db, campaign, screenshot
):
    values = [header(), row(str(uuid.uuid4()), evidence_path=screenshot)]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert "not one of the 10 prompt versions" in str(report.errors[0])


def test_refuses_a_missing_evidence_path(fake_db, campaign):
    values = [header(), row(campaign["prompts"][0]["id"], evidence_path="")]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert any(e.column == "evidence_path" for e in report.errors)


def test_refuses_an_evidence_path_that_does_not_exist(fake_db, campaign):
    values = [header(), row(campaign["prompts"][0]["id"], evidence_path="/no/such/shot.png")]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert "unverifiable" in str(report.errors[0])


def test_refuses_a_zero_byte_screenshot(fake_db, campaign, tmp_path):
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    values = [header(), row(campaign["prompts"][0]["id"], evidence_path=str(empty))]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert "proving nothing" in str(report.errors[0])


def test_refuses_missing_capture_text(fake_db, campaign, screenshot):
    values = [header(), row(campaign["prompts"][0]["id"], evidence_path=screenshot, capture_text="")]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert any(e.column == "capture_text" for e in report.errors)


def test_refuses_a_provider_outside_the_check_constraint(fake_db, campaign, screenshot):
    values = [
        header(),
        row(campaign["prompts"][0]["id"], provider="perplexity-web", evidence_path=screenshot),
    ]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert any(e.column == "provider" for e in report.errors)


def test_refuses_more_replicates_than_the_plan_declares(fake_db, campaign, screenshot):
    prompt_id = campaign["prompts"][0]["id"]
    values = [header()] + [
        row(prompt_id, replicate_index=n, evidence_path=screenshot) for n in range(4)
    ]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert "replicate_count=3" in str(report.errors[0])


def test_refuses_the_same_cell_entered_twice(fake_db, campaign, screenshot):
    """A copied row whose replicate_index was never advanced. The upsert would
    absorb it silently, so validate catches it and names both sheet rows."""
    prompt_id = campaign["prompts"][0]["id"]
    values = [
        header(),
        row(prompt_id, provider="perplexity", replicate_index=0, evidence_path=screenshot),
        row(prompt_id, provider="perplexity", replicate_index=0, evidence_path=screenshot),
    ]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert "same task_id" in str(report.errors[0])
    assert "sheet row 2" in str(report.errors[0])


def test_accepts_one_prompt_replicate_captured_on_two_surfaces(
    fake_db, campaign, screenshot, monkeypatch
):
    """Two providers, same prompt and replicate: two cells, two observations.
    This is the pair the old provider-less scheme collapsed into one row."""
    prompt_id = campaign["prompts"][0]["id"]
    values = [
        header(),
        row(prompt_id, provider="perplexity", replicate_index=0, evidence_path=screenshot),
        row(prompt_id, provider="openai", replicate_index=0, evidence_path=screenshot),
    ]
    load(monkeypatch, values)
    report = import_from_sheet(
        fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False
    )
    assert report.ok
    assert report.observations_written == 2
    assert len(fake_db.tables["observations"]) == 2
    assert {o["provider"] for o in fake_db.tables["observations"]} == {"perplexity", "openai"}


def test_refuses_a_naive_timestamp(fake_db, campaign, screenshot):
    values = [
        header(),
        row(campaign["prompts"][0]["id"], evidence_path=screenshot, start="2026-08-29T14:03:00"),
    ]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert "no UTC offset" in str(report.errors[0])


def test_refuses_a_capture_that_ends_before_it_starts(fake_db, campaign, screenshot):
    values = [
        header(),
        row(campaign["prompts"][0]["id"], evidence_path=screenshot, start=END, end=START),
    ]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert "before it starts" in str(report.errors[0])


def test_refuses_a_header_the_operator_edited(fake_db, campaign):
    report = validate(fake_db, [["prompt_version_id", "capture_text"]], campaign["run_plan_id"])
    assert not report.ok
    assert "header mismatch" in str(report.errors[0])


def test_nothing_is_written_when_any_row_fails(fake_db, campaign, screenshot, monkeypatch):
    """All-or-nothing: a half-applied import leaves the sheet and the database
    disagreeing about which cells were captured."""
    values = sheet(campaign, screenshot, count=1)
    values[2][SHEET_COLUMNS.index("capture_text")] = ""
    load(monkeypatch, values)

    report = import_from_sheet(
        fake_db, "sheet-1", "Capture", campaign["run_plan_id"], dry_run=False
    )
    assert not report.ok
    assert fake_db.tables.get("observations", []) == []
    assert fake_db.tables.get("evidence", []) == []


def test_a_dry_run_writes_nothing(fake_db, campaign, screenshot, monkeypatch):
    load(monkeypatch, sheet(campaign, screenshot, count=1))
    report = import_from_sheet(fake_db, "sheet-1", "Capture", campaign["run_plan_id"])
    assert report.ok
    assert not report.committed
    assert fake_db.tables.get("observations", []) == []


# ---------------------------------------------------------------------
# Validation — warnings, which never block
# ---------------------------------------------------------------------


def test_warns_but_accepts_a_cell_below_the_replicate_target(fake_db, campaign, screenshot):
    """§8.3: n=3 is a target, not a floor. The shortfall is recorded and
    visible downstream on CellJudgment.replicate_count."""
    report = validate(fake_db, sheet(campaign, screenshot, count=1), campaign["run_plan_id"])
    assert report.ok
    assert len(report.valid) == 10
    assert any("below the §8.3 target of 3" in w for w in report.warnings)


def test_warns_on_a_cell_captured_not_at_all(fake_db, campaign, screenshot):
    """The condition that drops the frame to n=9 and routes the §8.4 gate to
    the fallback, which needs a named human reviewer."""
    values = sheet(campaign, screenshot, count=1)
    missing = campaign["prompts"][0]["id"]
    values = [values[0]] + [r for r in values[1:] if r[0] != missing]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert report.ok
    assert any(missing in w and "no captures at all" in w for w in report.warnings)


def test_an_uncaptured_template_row_is_not_an_error(fake_db, campaign, screenshot):
    """The operator leaves a cell blank rather than deleting the row. That is
    an absent cell, not a malformed one."""
    values = sheet(campaign, screenshot, count=1)
    blank = [values[1][i] if name in ("prompt_version_id", "intent_tier", "prompt_text", "provider", "replicate_index") else ""
             for i, name in enumerate(SHEET_COLUMNS)]
    values[1] = blank
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert report.ok
    assert report.uncaptured == 1
    assert len(report.valid) == 9


def test_a_half_filled_capture_is_an_error_not_an_absent_cell(fake_db, campaign):
    """Distinguishes "not captured" from "captured and mis-entered" — the
    second must not disappear into a warning."""
    values = [header(), row(campaign["prompts"][0]["id"], evidence_path="", operator="")]
    report = validate(fake_db, values, campaign["run_plan_id"])
    assert not report.ok
    assert report.uncaptured == 0


# ---------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------


def test_template_emits_one_row_per_prompt_and_replicate(fake_db, campaign):
    rows = build_template_rows(fake_db, "perplexity", 3)
    assert rows[0] == list(SHEET_COLUMNS)
    assert len(rows) == 1 + 30


def test_template_is_sorted_by_tier_and_is_deterministic(fake_db, campaign):
    rows = build_template_rows(fake_db, "perplexity", 2)[1:]
    tiers = [r[SHEET_COLUMNS.index("intent_tier")] for r in rows]
    assert tiers == sorted(tiers)
    assert rows == build_template_rows(fake_db, "perplexity", 2)[1:]


def test_template_prefills_the_operator_columns_empty(fake_db, campaign):
    rows = build_template_rows(fake_db, "perplexity", 1)[1:]
    for name in ("capture_text", "surface_url", "evidence_path", "operator"):
        assert {r[SHEET_COLUMNS.index(name)] for r in rows} == {""}


def test_template_refuses_a_provider_outside_the_constraint(fake_db, campaign):
    with pytest.raises(ValueError, match="not one of"):
        build_template_rows(fake_db, "perplexity-web", 3)


def test_validate_refuses_when_the_prompt_set_is_not_seeded(fake_db, campaign, screenshot):
    report = validate(
        fake_db,
        sheet(campaign, screenshot, count=1),
        campaign["run_plan_id"],
        prompt_set_version="frozen-core-nobody-v9",
    )
    assert not report.ok
    assert "refusing to ingest" in str(report.errors[0])


# ---------------------------------------------------------------------
# D-085 — the sheet's prompt set and the plan's must agree. validate()
# previously read the plan for replicate_count and the prompt set for the
# verified ids, and never connected the two.
# ---------------------------------------------------------------------


def test_refuses_a_sheet_whose_prompt_set_differs_from_the_plan(
    fake_db, campaign, screenshot
):
    fake_db.tables["run_plans"][0]["prompt_set_version"] = "frozen-core-other-v1"

    report = validate(fake_db, sheet(campaign, screenshot, count=1), campaign["run_plan_id"])

    assert not report.ok
    joined = " ".join(str(e) for e in report.errors)
    assert "measures prompt set 'frozen-core-other-v1'" in joined
    assert "D-085" in joined


def test_refuses_a_plan_that_records_no_prompt_set(fake_db, campaign, screenshot):
    """The nullable column reaching ingest. Refused rather than skipped, so a
    capture cannot be filed under a plan whose prompt set is unknown."""
    fake_db.tables["run_plans"][0]["prompt_set_version"] = None

    report = validate(fake_db, sheet(campaign, screenshot, count=1), campaign["run_plan_id"])

    assert not report.ok
    assert "records no prompt_set_version" in " ".join(str(e) for e in report.errors)


def test_refuses_a_plan_recording_a_blank_prompt_set(fake_db, campaign, screenshot):
    """Matches run._check_prompt_set_version: '' and whitespace are refused the
    same way NULL is, so a plan the gate would refuse cannot be ingested into."""
    fake_db.tables["run_plans"][0]["prompt_set_version"] = "   "

    report = validate(fake_db, sheet(campaign, screenshot, count=1), campaign["run_plan_id"])

    assert not report.ok
    assert "records no prompt_set_version" in " ".join(str(e) for e in report.errors)
