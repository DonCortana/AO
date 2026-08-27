"""Evidence Vault — hashing and manifest for raw provider responses.

Operating System §7: score-bearing non-personal observations are
append-only and versioned; retained for reproducibility. Personal data has
separate, shorter retention so GDPR deletion obligations never conflict
with an absolute no-delete rule. This module only ever writes; it has no
delete function by design — deletion of personal data is handled in the
Hospitality Automation environment, not here.

MVP evidence storage is Google Drive for non-PII evidence + manifests
(Operating System §3); object storage with lifecycle rules is a later
trigger, not a Week 1 concern.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from atlas.config import get_settings
from atlas.evidence.drive import file_md5, upload_file


def hash_payload(payload: dict) -> str:
    """SHA-256 of the canonical JSON payload. Deterministic key ordering so
    the same logical response always hashes identically."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class EvidenceRecord:
    evidence_id: str
    run_id: str
    prompt_version: str
    provider: str
    model: str
    tool_version: str | None
    market: str
    language: str
    captured_at: datetime
    payload_hash: str
    storage_path: str | None
    data_class: str  # matches evidence.data_class check constraint
    operator: str | None = None  # set only for human-captured (Layer B) evidence
    # Operating System §7 names "source reference" among the required evidence
    # fields. For Layer A this is the provider request/response identifier; for
    # Layer B it is the consumer surface URL or capture reference. The
    # `evidence` table has no column for it (see drive.EVIDENCE_TABLE_GAP), so
    # today it travels on the Drive file's appProperties only.
    source_reference: str | None = None
    observation_id: str | None = None
    manifest_id: str | None = None


def build_manifest(records: list[EvidenceRecord]) -> dict:
    """A manifest is generated only after reconciliation, never simply after
    the scheduler job exits (Operating System §4)."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "records": [
            {
                "evidence_id": r.evidence_id,
                "payload_hash": r.payload_hash,
                "provider": r.provider,
                "captured_at": r.captured_at.isoformat(),
            }
            for r in records
        ],
    }


def upload_to_drive(
    local_path: str,
    folder_id: str | None = None,
    *,
    record: EvidenceRecord | None = None,
) -> str:
    """Upload one evidence file to Drive and return its storage path.

    The return value is what belongs in `evidence.storage_path` — a directly
    openable Drive link rather than a bare file id, so an auditor chasing a
    hash does not need to know how to turn an id back into a URL.

    When `record` is supplied, the Operating System §7 provenance fields are
    written onto the Drive file as appProperties. The `evidence` table has no
    columns for several of them (see `atlas.evidence.drive.EVIDENCE_TABLE_GAP`),
    so this is what keeps the stored artifact self-describing.

    Uploads are idempotent on the payload hash: re-running an interrupted
    capture batch reuses files it already stored rather than duplicating them.
    """
    folder_id = folder_id or get_settings().google_drive_evidence_folder_id
    if not folder_id:
        raise RuntimeError(
            "No Drive folder configured for evidence. Set "
            "GOOGLE_DRIVE_EVIDENCE_FOLDER_ID or pass folder_id explicitly."
        )

    payload_hash = record.payload_hash if record else file_md5(local_path)
    upload = upload_file(
        local_path,
        folder_id,
        payload_hash=payload_hash,
        metadata=_provenance(record) if record else None,
    )
    return upload.storage_path


def _provenance(record: EvidenceRecord) -> dict[str, str | None]:
    """Operating System §7's required evidence fields, as Drive appProperties.

    "Every evidence record carries evidence ID, run ID, prompt version,
    provider/model/tool version, market, language, UTC timestamp, source
    reference, payload hash and operator where human capture is used."
    """
    return {
        "evidence_id": record.evidence_id,
        "run_id": record.run_id,
        "prompt_version": record.prompt_version,
        "provider": record.provider,
        "model": record.model,
        "tool_version": record.tool_version,
        "market": record.market,
        "language": record.language,
        "captured_at": record.captured_at.astimezone(timezone.utc).isoformat(),
        "source_reference": record.source_reference,
        "data_class": record.data_class,
        # §7: "operator where human capture is used" — set only for Layer B.
        "operator": record.operator,
    }


def store_evidence(db, record: EvidenceRecord, local_path: str, folder_id: str | None = None) -> str:
    """Upload the artifact, then write the `evidence` row pointing at it.

    Upload first, deliberately. A row whose `storage_path` points at nothing is
    worse than a missing row: it asserts evidence exists and can be produced on
    audit. If the upload fails this raises and no row is written, leaving the
    observation visibly unevidenced (Operating System §4's reconciliation is
    what catches that) rather than falsely evidenced.

    Upserts on `observation_id`, matching the unique constraint migration 0003
    added for exactly this reason (D-032): a retried or resumed capture must
    never double-write the evidence ledger.
    """
    storage_path = upload_to_drive(local_path, folder_id, record=record)
    db.table("evidence").upsert(
        {
            "observation_id": record.observation_id,
            "run_id": record.run_id,
            "payload_hash": record.payload_hash,
            "manifest_id": record.manifest_id,
            "storage_path": storage_path,
            "data_class": record.data_class,
            "captured_by": record.operator,
        },
        on_conflict="observation_id",
    ).execute()
    return storage_path
