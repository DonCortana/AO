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
    the same logical response always hashes identically.

    Takes a dict. Binary evidence goes through `sha256_file` instead — see
    the contract there."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    """SHA-256 of a file's raw bytes, read in 1 MiB blocks.

    The companion to `hash_payload`, and the two are not interchangeable.
    `hash_payload` hashes a **dict** through its canonical JSON encoding,
    which is what an API provider's response goes through. `sha256_file`
    hashes **the bytes on disk**, exactly as stored.

    Binary evidence requires this one. A screenshot of a consumer surface has
    no dict form at all, so `hash_payload` raises on it rather than hashing
    it — Layer B captures (Operating System §7's human-capture evidence) must
    use `sha256_file`.

    Both produce what `evidence.payload_hash` holds, which is NOT NULL. The
    choice between them is per-artifact and belongs to the caller, which is
    the only party that knows whether it captured a response or a file.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
    # Layer B it is the consumer surface URL or capture reference. Stored in
    # full in `evidence.source_reference` since migration 0007 (D-049); the
    # Drive appProperties copy is truncated and not authoritative.
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
    written onto the Drive file as appProperties, keeping the stored artifact
    self-describing for anyone who has the file but not the database. Since
    migration 0007 (D-049) the same fields are also columns on `evidence`, and
    that row — not this copy — is what an audit is answered from.

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

    Writes the full Operating System §7 provenance set to the row, not just to
    Drive: migration 0007 (D-049) added `prompt_version`, `provider`, `model`,
    `tool_version`, `market`, `language` and `source_reference` so the copy an
    audit queries lives beside the hash it belongs to.
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
            # Operating System §7, columns added by migration 0007 (D-049).
            "prompt_version": record.prompt_version,
            "provider": record.provider,
            "model": record.model,
            "tool_version": record.tool_version,
            "market": record.market,
            "language": record.language,
            "source_reference": record.source_reference,
        },
        on_conflict="observation_id",
    ).execute()
    return storage_path
