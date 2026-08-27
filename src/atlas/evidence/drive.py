"""Google Drive transport for the Evidence Vault.

Operating System §3 puts MVP evidence storage on "Google Drive for early
non-PII evidence + manifests", with object storage and lifecycle rules as a
later trigger. This module is that transport and nothing more: it uploads a
file, proves the upload arrived intact, and returns the identifiers the
evidence row needs.

Built for the Layer B capture load rather than for a demo. Every capture in a
§8.3 consumer-surface cycle goes through this path, so it is resumable,
retried on transient failures, integrity-checked against the local file, and
idempotent on the payload hash — re-running a partly-finished capture batch
finds the files it already uploaded instead of duplicating them.

Operating System §7 requires every evidence record to carry evidence ID, run
ID, prompt version, provider/model/tool version, market, language, UTC
timestamp, source reference, payload hash and operator. The `evidence` table
holds all of those since migration 0007 (D-049) and is what an audit is
answered from; the full set is written onto the Drive file as appProperties as
well, so the stored artifact stays self-describing for anyone holding the file
without the database. The appProperties copy is truncated at Drive's 124-byte
limit and is not the authoritative one.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from atlas.config import get_settings

# `drive.file` scopes access to files this service account created, which is
# the narrowest scope that supports create/list/get here (Operating System §6:
# "Service roles are narrowly scoped").
SCOPES = ("https://www.googleapis.com/auth/drive.file",)

# Files above this go up in chunks rather than one request, so a dropped
# connection resumes instead of restarting. Screenshots sit below it; PDF and
# video captures do not.
RESUMABLE_THRESHOLD_BYTES = 5 * 1024 * 1024
CHUNK_SIZE_BYTES = 1024 * 1024

# Transient by Drive's own definition — rate limiting and backend faults.
# 4xx other than 429 are not retried: a permission or quota-exhausted error
# does not improve by being repeated.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# The Operating System §7 provenance fields that used to have no column in the
# `evidence` table and rode on Drive appProperties alone. Migration 0007 gave
# each of them a column (D-049), so this is now the list of fields written to
# BOTH places: the database row is the queryable, authoritative copy, and the
# appProperties copy keeps the downloaded artifact self-describing.
EVIDENCE_PROVENANCE_COLUMNS = (
    "prompt_version",
    "provider",
    "model",
    "tool_version",
    "market",
    "language",
    "source_reference",
)


@dataclass(frozen=True)
class DriveUpload:
    file_id: str
    web_view_link: str
    md5_checksum: str | None
    size_bytes: int
    reused_existing: bool = False

    @property
    def storage_path(self) -> str:
        """What goes in `evidence.storage_path`. The web link rather than the
        bare id, so the column is directly openable by a human chasing an audit
        without needing to know it is a Drive id."""
        return self.web_view_link or f"https://drive.google.com/file/d/{self.file_id}/view"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        return getattr(exc.resp, "status", None) in _RETRYABLE_STATUS
    # Socket resets and timeouts surface as bare OSError through httplib2.
    return isinstance(exc, (OSError, TimeoutError))


_retry = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)


def _credentials_path() -> str:
    settings = get_settings()
    path = (
        settings.google_drive_service_account_json_path
        or settings.google_application_credentials
    )
    if not path:
        raise RuntimeError(
            "No service-account credentials configured for Drive. Set "
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_PATH (preferred) or "
            "GOOGLE_APPLICATION_CREDENTIALS."
        )
    return path


@lru_cache
def get_drive_service():
    credentials = service_account.Credentials.from_service_account_file(
        _credentials_path(), scopes=list(SCOPES)
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def file_md5(local_path: str) -> str:
    """Local MD5, to check against the checksum Drive reports back.

    MD5 is Drive's integrity field, not a security choice — the evidence
    payload's tamper-evident hash is the SHA-256 in
    `atlas.evidence.vault.hash_payload`, which is separate and unaffected.
    """
    # MD5 here is Drive's integrity field, not a security primitive.
    digest = hashlib.md5()
    with open(local_path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _app_properties(metadata: dict[str, str | None]) -> dict[str, str]:
    """Drive caps each appProperties key+value at 124 bytes, so values are
    truncated rather than allowed to fail the whole upload. Nothing here is
    load-bearing for scoring — it is provenance for a human auditor."""
    properties = {}
    for key, value in metadata.items():
        if value is None or value == "":
            continue
        budget = 124 - len(key.encode("utf-8")) - 1
        properties[key] = str(value).encode("utf-8")[:budget].decode("utf-8", "ignore")
    return properties


@_retry
def find_by_payload_hash(payload_hash: str, folder_id: str | None = None) -> DriveUpload | None:
    """Idempotency: has this exact payload already been uploaded?

    Keyed on the SHA-256 payload hash written into appProperties at upload
    time, so a re-run of an interrupted capture batch reuses what it already
    stored instead of creating a second copy of the same evidence.
    """
    query = [f"appProperties has {{ key='payload_hash' and value='{payload_hash}' }}", "trashed=false"]
    if folder_id:
        query.append(f"'{folder_id}' in parents")
    response = (
        get_drive_service()
        .files()
        .list(
            q=" and ".join(query),
            fields="files(id,webViewLink,md5Checksum,size)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = response.get("files", [])
    if not files:
        return None
    found = files[0]
    return DriveUpload(
        file_id=found["id"],
        web_view_link=found.get("webViewLink", ""),
        md5_checksum=found.get("md5Checksum"),
        size_bytes=int(found.get("size", 0)),
        reused_existing=True,
    )


@_retry
def _create(body: dict, media: MediaFileUpload) -> dict:
    request = (
        get_drive_service()
        .files()
        .create(
            body=body,
            media_body=media,
            fields="id,webViewLink,md5Checksum,size",
            supportsAllDrives=True,
        )
    )
    if not media.resumable():
        return request.execute()

    response = None
    while response is None:
        _, response = request.next_chunk()
    return response


def upload_file(
    local_path: str,
    folder_id: str | None = None,
    *,
    payload_hash: str,
    metadata: dict[str, str | None] | None = None,
    name: str | None = None,
    mime_type: str | None = None,
    reuse_existing: bool = True,
) -> DriveUpload:
    """Upload one evidence file and verify it arrived intact.

    Raises if Drive's reported MD5 disagrees with the local file — a silently
    truncated upload would leave an evidence row pointing at an artifact that
    no longer matches its hash, which is the one failure this vault exists to
    make impossible.
    """
    if not os.path.isfile(local_path):
        raise FileNotFoundError(f"no evidence file at {local_path!r}")

    size = os.path.getsize(local_path)
    if size == 0:
        raise ValueError(
            f"{local_path!r} is empty — refusing to store a zero-byte evidence "
            "artifact, which would hash and upload cleanly while proving nothing"
        )

    if reuse_existing:
        existing = find_by_payload_hash(payload_hash, folder_id)
        if existing is not None:
            return existing

    body: dict = {
        "name": name or os.path.basename(local_path),
        "appProperties": _app_properties({**(metadata or {}), "payload_hash": payload_hash}),
    }
    if folder_id:
        body["parents"] = [folder_id]

    resumable = size > RESUMABLE_THRESHOLD_BYTES
    media = MediaFileUpload(
        local_path,
        mimetype=mime_type,
        chunksize=CHUNK_SIZE_BYTES,
        resumable=resumable,
    )
    created = _create(body, media)

    remote_md5 = created.get("md5Checksum")
    if remote_md5:
        local_md5 = file_md5(local_path)
        if remote_md5 != local_md5:
            raise RuntimeError(
                f"Drive upload integrity check failed for {local_path!r}: Drive "
                f"reports md5 {remote_md5}, local file is {local_md5}. The "
                f"uploaded file (id {created.get('id')}) does not match and must "
                "not be recorded as evidence."
            )

    return DriveUpload(
        file_id=created["id"],
        web_view_link=created.get("webViewLink", ""),
        md5_checksum=remote_md5,
        size_bytes=int(created.get("size", size)),
    )
