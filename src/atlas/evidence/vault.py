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


def upload_to_drive(local_path: str, folder_id: str) -> str:
    """TODO(Week 1): Google Drive upload via service account.

    Returns the Drive file ID / path to store as evidence.storage_path.
    """
    raise NotImplementedError("Google Drive evidence upload not yet implemented")
