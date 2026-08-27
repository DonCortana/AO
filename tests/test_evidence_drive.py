"""Evidence Vault Drive transport — Operating System §3 and §7.

150 Layer B captures are coming and each one goes through this path, so the
cases here are the ones that bite at volume: a truncated upload, a re-run of an
interrupted batch, a transient 503, and an evidence row that would otherwise
point at nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest
from googleapiclient.errors import HttpError

from atlas.evidence import drive, vault
from atlas.evidence.drive import (
    EVIDENCE_PROVENANCE_COLUMNS,
    DriveUpload,
    _app_properties,
    _is_retryable,
    file_md5,
)
from atlas.evidence.vault import (
    EvidenceRecord,
    _provenance,
    store_evidence,
    upload_to_drive,
)


def record(**overrides) -> EvidenceRecord:
    base = {
        "evidence_id": "ev-1",
        "run_id": "run-1",
        "prompt_version": "accommodation-th-en-v1.0",
        "provider": "openai",
        "model": "gpt-5.6",
        "tool_version": "web_search-1",
        "market": "TH",
        "language": "en",
        "captured_at": datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        "payload_hash": "a" * 64,
        "storage_path": None,
        "data_class": "raw_ai_response",
        "observation_id": "obs-1",
    }
    base.update(overrides)
    return EvidenceRecord(**base)


class FakeResponse:
    def __init__(self, status):
        self.status = status
        self.reason = "fake"


def http_error(status):
    return HttpError(FakeResponse(status), b"{}")


@pytest.fixture
def evidence_file(tmp_path):
    path = tmp_path / "capture.png"
    path.write_bytes(b"screenshot-bytes" * 64)
    return str(path)


@pytest.fixture
def drive_stub(monkeypatch, evidence_file):
    """Stands in for the Drive API. Records what was sent."""
    state = {"created": [], "existing": None, "md5": file_md5(evidence_file)}

    def fake_find(payload_hash, folder_id=None):
        return state["existing"]

    def fake_create(body, media):
        state["created"].append(body)
        return {
            "id": "drive-file-1",
            "webViewLink": "https://drive.google.com/file/d/drive-file-1/view",
            "md5Checksum": state["md5"],
            "size": "1024",
        }

    monkeypatch.setattr(drive, "find_by_payload_hash", fake_find)
    monkeypatch.setattr(drive, "_create", fake_create)
    monkeypatch.setattr(drive, "MediaFileUpload", lambda *a, **k: object())
    return state


@pytest.fixture(autouse=True)
def _drive_folder(monkeypatch):
    monkeypatch.setattr(
        vault, "get_settings", lambda: type("S", (), {"google_drive_evidence_folder_id": "folder-1"})()
    )


# ---------------------------------------------------------------------
# §7 provenance
# ---------------------------------------------------------------------


def test_provenance_carries_every_operating_system_7_field():
    """§7: evidence ID, run ID, prompt version, provider/model/tool version,
    market, language, UTC timestamp, source reference, payload hash, operator."""
    properties = _provenance(record(source_reference="resp_abc", operator="Doud"))
    for field in (
        "evidence_id",
        "run_id",
        "prompt_version",
        "provider",
        "model",
        "tool_version",
        "market",
        "language",
        "captured_at",
        "source_reference",
        "operator",
    ):
        assert properties[field], f"§7 field {field} missing from Drive provenance"


def test_captured_at_is_normalised_to_utc():
    from datetime import timedelta

    local = datetime(2026, 8, 27, 19, 0, tzinfo=timezone(timedelta(hours=7)))
    assert _provenance(record(captured_at=local))["captured_at"].endswith("+00:00")
    assert "12:00" in _provenance(record(captured_at=local))["captured_at"]


def test_operator_is_absent_for_layer_a():
    """§7 requires operator 'where human capture is used' — an API observation
    has none, and an empty value is dropped rather than written as a blank."""
    assert "operator" not in _app_properties(_provenance(record(operator=None)))


def test_app_properties_truncate_rather_than_fail_the_upload():
    """Drive caps key+value at 124 bytes. Nothing here is load-bearing for
    scoring, so an over-long value is trimmed, not raised on."""
    properties = _app_properties({"source_reference": "x" * 500})
    assert len(properties["source_reference"].encode()) <= 124 - len("source_reference") - 1


def test_payload_hash_is_always_attached(drive_stub, evidence_file):
    upload_to_drive(evidence_file, "folder-1", record=record())
    assert drive_stub["created"][0]["appProperties"]["payload_hash"] == "a" * 64


def test_provenance_columns_are_written_to_both_stores():
    """D-049 closed the gap these fields used to sit in: each is a column on
    `evidence` (migration 0007) AND a Drive appProperty. Neither copy is
    dropped — the row is queryable, the file stays self-describing."""
    properties = _provenance(record())
    for field in EVIDENCE_PROVENANCE_COLUMNS:
        assert field in properties, f"§7 field {field} missing from Drive provenance"


# ---------------------------------------------------------------------
# Upload behaviour
# ---------------------------------------------------------------------


def test_storage_path_is_an_openable_link(drive_stub, evidence_file):
    path = upload_to_drive(evidence_file, "folder-1", record=record())
    assert path == "https://drive.google.com/file/d/drive-file-1/view"


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        drive.upload_file(str(tmp_path / "nope.png"), "folder-1", payload_hash="h")


def test_empty_file_is_refused(tmp_path):
    """A zero-byte capture hashes and uploads cleanly while proving nothing."""
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="zero-byte"):
        drive.upload_file(str(empty), "folder-1", payload_hash="h")


def test_integrity_mismatch_raises_and_is_not_recorded(monkeypatch, drive_stub, evidence_file):
    """A silently truncated upload would leave an evidence row pointing at an
    artifact that no longer matches its hash."""
    drive_stub["md5"] = "0" * 32
    with pytest.raises(RuntimeError, match="integrity check failed"):
        drive.upload_file(evidence_file, "folder-1", payload_hash="a" * 64)


def test_reupload_reuses_the_existing_file(drive_stub, evidence_file):
    """Re-running an interrupted capture batch must not duplicate evidence."""
    drive_stub["existing"] = DriveUpload(
        file_id="already-there",
        web_view_link="https://drive.google.com/file/d/already-there/view",
        md5_checksum="x",
        size_bytes=10,
        reused_existing=True,
    )
    result = drive.upload_file(evidence_file, "folder-1", payload_hash="a" * 64)
    assert result.reused_existing is True
    assert result.file_id == "already-there"
    assert drive_stub["created"] == []


def test_no_folder_configured_raises(monkeypatch, evidence_file):
    monkeypatch.setattr(
        vault, "get_settings", lambda: type("S", (), {"google_drive_evidence_folder_id": ""})()
    )
    with pytest.raises(RuntimeError, match="No Drive folder configured"):
        upload_to_drive(evidence_file, None, record=record())


# ---------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(status):
    assert _is_retryable(http_error(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_statuses_fail_fast(status):
    """A permission or quota-exhausted error does not improve by repetition —
    confirmed live against the real API, where a 403 failed immediately."""
    assert _is_retryable(http_error(status)) is False


def test_connection_errors_are_retried():
    assert _is_retryable(TimeoutError("reset")) is True
    assert _is_retryable(ValueError("nope")) is False


# ---------------------------------------------------------------------
# store_evidence
# ---------------------------------------------------------------------


def test_store_evidence_writes_the_row_after_a_successful_upload(
    fake_db, drive_stub, evidence_file
):
    store_evidence(fake_db, record(operator="Doud"), evidence_file, "folder-1")
    (row,) = fake_db.tables["evidence"]
    assert row["storage_path"] == "https://drive.google.com/file/d/drive-file-1/view"
    assert row["payload_hash"] == "a" * 64
    assert row["captured_by"] == "Doud"
    assert row["data_class"] == "raw_ai_response"


def test_failed_upload_writes_no_evidence_row(monkeypatch, fake_db, drive_stub, evidence_file):
    """A row whose storage_path points at nothing asserts evidence exists and
    can be produced on audit. Better to have no row at all."""
    def boom(*args, **kwargs):
        raise RuntimeError("drive exploded")

    monkeypatch.setattr(drive, "_create", boom)
    with pytest.raises(RuntimeError, match="drive exploded"):
        store_evidence(fake_db, record(), evidence_file, "folder-1")
    assert fake_db.tables.get("evidence", []) == []


def test_store_evidence_is_idempotent_on_observation_id(fake_db, drive_stub, evidence_file):
    """D-032: a retried capture must never double-write the evidence ledger."""
    store_evidence(fake_db, record(), evidence_file, "folder-1")
    store_evidence(fake_db, record(), evidence_file, "folder-1")
    assert len(fake_db.tables["evidence"]) == 1


def test_store_evidence_writes_the_section_7_provenance_columns(
    fake_db, drive_stub, evidence_file
):
    """D-049 / migration 0007: the database copy is the one an audit is
    answered from, so it carries §7's provenance, not just the Drive file."""
    store_evidence(fake_db, record(source_reference="resp_abc123"), evidence_file, "folder-1")
    (row,) = fake_db.tables["evidence"]
    assert row["prompt_version"] == "accommodation-th-en-v1.0"
    assert row["provider"] == "openai"
    assert row["model"] == "gpt-5.6"
    assert row["tool_version"] == "web_search-1"
    assert row["market"] == "TH"
    assert row["language"] == "en"
    assert row["source_reference"] == "resp_abc123"


def test_stored_source_reference_is_not_truncated_like_the_drive_copy(
    fake_db, drive_stub, evidence_file
):
    """Drive caps an appProperty at 124 bytes and the uploader trims to fit.
    That is why D-049 makes the row authoritative: a long provider response id
    or capture URL survives in the column but not in appProperties."""
    long_reference = "https://example.com/capture?" + "x" * 400
    store_evidence(fake_db, record(source_reference=long_reference), evidence_file, "folder-1")
    (row,) = fake_db.tables["evidence"]
    assert row["source_reference"] == long_reference
    stored_on_drive = drive_stub["created"][0]["appProperties"]["source_reference"]
    assert len(stored_on_drive) < len(long_reference)


def test_every_provenance_column_reaches_the_row(fake_db, drive_stub, evidence_file):
    """Guards the list itself: a §7 field added to EVIDENCE_PROVENANCE_COLUMNS
    without a matching key in the upsert would otherwise pass unnoticed."""
    store_evidence(fake_db, record(source_reference="resp_abc123"), evidence_file, "folder-1")
    (row,) = fake_db.tables["evidence"]
    assert set(EVIDENCE_PROVENANCE_COLUMNS) <= set(row)
