"""Thin Google Sheets client — service-account auth, values + validation.

Shares the service-account credential the Evidence Vault uses (Operating
System §3 puts MVP evidence in Google Drive). Kept deliberately small: this
module knows about A1 ranges and cell values, and nothing about RPV.
"""

from __future__ import annotations

from functools import lru_cache

from google.oauth2 import service_account
from googleapiclient.discovery import build

from atlas.config import get_settings

# Sheets needs write access to create the labeling tab and its dropdown
# validation; Drive scope is required to create a new spreadsheet at all.
SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
)


def _credentials_path() -> str:
    settings = get_settings()
    path = settings.google_drive_service_account_json_path or settings.google_application_credentials
    if not path:
        raise RuntimeError(
            "No service-account credentials configured. Set "
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_PATH (preferred for Drive/Sheets) "
            "or GOOGLE_APPLICATION_CREDENTIALS."
        )
    return path


@lru_cache
def get_sheets_service():
    credentials = service_account.Credentials.from_service_account_file(
        _credentials_path(), scopes=list(SCOPES)
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def read_values(spreadsheet_id: str, a1_range: str) -> list[list[str]]:
    """Return raw cell values. Uses UNFORMATTED_VALUE so a rank typed as a
    number does not arrive locale-formatted, and so an empty cell stays empty
    rather than becoming a coerced zero."""
    response = (
        get_sheets_service()
        .spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    return response.get("values", [])


def write_values(spreadsheet_id: str, a1_range: str, values: list[list]) -> dict:
    return (
        get_sheets_service()
        .spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            valueInputOption="RAW",
            body={"values": values},
        )
        .execute()
    )


def get_sheet_id(spreadsheet_id: str, title: str) -> int:
    """Numeric sheetId for a tab title — needed by batchUpdate requests, which
    do not accept A1 tab names."""
    meta = (
        get_sheets_service()
        .spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets.properties")
        .execute()
    )
    for sheet in meta.get("sheets", []):
        properties = sheet["properties"]
        if properties["title"] == title:
            return properties["sheetId"]
    raise ValueError(
        f"spreadsheet {spreadsheet_id!r} has no tab titled {title!r}; "
        f"found {[s['properties']['title'] for s in meta.get('sheets', [])]}"
    )


def batch_update(spreadsheet_id: str, requests: list[dict]) -> dict:
    return (
        get_sheets_service()
        .spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests})
        .execute()
    )
