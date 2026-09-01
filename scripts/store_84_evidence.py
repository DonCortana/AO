"""Store the three §8.4 eligibility artifacts and set the four criteria columns.

One-off operational action for D-053 / D-057. Dry run by default.

Design notes — every one of these is a deviation forced by the schema, not a
preference. See the report for the full list.
  * observation_id is None: these are PROPERTY-level artifacts, not
    observation-level. store_evidence upserts on_conflict='observation_id',
    which never matches on NULL, so it is NOT idempotent here — we guard on
    payload_hash ourselves before calling it.
  * run_id is None: there is no run. The column is nullable text (no FK).
  * payload_hash is SHA-256 of the FILE BYTES, via vault.sha256_file():
    vault.hash_payload() takes a dict and cannot hash a PNG.
"""

from __future__ import annotations

import argparse
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from atlas.db.client import get_db
from atlas.evidence.vault import EvidenceRecord, sha256_file, store_evidence

# Artifact paths are resolved against the repo root, not the working
# directory, so this runs the same from anywhere.
REPO_ROOT = Path(__file__).resolve().parents[1]

PROPERTY_ID = "df2e65c5-190c-4879-88b4-78557176ef4e"
MARKET, LANGUAGE = "TH", "en"


def captured_at(path: str) -> datetime:
    return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)


ARTIFACTS = [
    {
        "key": "google",
        "path": "evidence/samujana-google-reviews.png",
        "criterion": "review_presence",
        "primary": True,
        "data_class": "public_review_text",
        "provider": "google",
        "operator": "Doud",
        "source_reference": "https://www.google.com/maps?cid=11853452962372942458",
    },
    {
        "key": "tripadvisor",
        "path": "evidence/samujana-tripadvisor-reviews.png",
        "criterion": "review_presence",
        "primary": False,
        "data_class": "public_review_text",
        "provider": "tripadvisor",
        "operator": "Doud",
        "source_reference": (
            "https://www.tripadvisor.com/Hotel_Review-g1893002-d9466413-Reviews-"
            "Samujana_Villas-Choeng_Mon_Bophut_Ko_Samui_Surat_Thani_Province.html"
        ),
    },
    {
        "key": "tat",
        "path": "evidence/samujana-tat-newsroom-michelin-keys-2025.html",
        "criterion": "third_party_reference",
        "primary": True,
        # No data_class fits property-eligibility evidence; this is the least
        # wrong of the four the 0001 check constraint allows.
        "data_class": "score_bearing_non_personal",
        "provider": "tatnews.org",
        # Machine-fetched via curl in-session, not human capture. OS §7 asks
        # for an operator "where human capture is used" — so None.
        "operator": None,
        "source_reference": (
            "https://www.tatnews.org/2025/10/thailand-celebrates-62-outstanding-"
            "hotels-in-2025-michelin-key-selection/"
        ),
    },
]


def main(commit: bool) -> None:
    db = get_db()
    print(f"{'COMMIT' if commit else 'DRY RUN'} — §8.4 evidence for property {PROPERTY_ID}\n")

    results = {}
    for art in ARTIFACTS:
        path = str(REPO_ROOT / art["path"])
        digest = sha256_file(path)
        size = os.path.getsize(path)
        when = captured_at(path)

        existing = db.table("evidence").select("id, storage_path").eq("payload_hash", digest).execute().data
        print(f"[{art['key']}] {path}")
        print(f"    sha256        : {digest}")
        print(f"    size          : {size:,} bytes")
        print(f"    captured_at   : {when.isoformat()}")
        print(f"    data_class    : {art['data_class']}")
        print(f"    provider      : {art['provider']}")
        print(f"    operator      : {art['operator']}")
        print(f"    source_ref    : {art['source_reference']}")
        print(f"    criterion     : {art['criterion']} (primary={art['primary']})")

        if existing:
            print(f"    -> already stored as evidence.id={existing[0]['id']} — skipping\n")
            results[art["key"]] = existing[0]["id"]
            continue

        if not commit:
            print("    -> WOULD upload to Drive + insert evidence row\n")
            continue

        record = EvidenceRecord(
            evidence_id=str(uuid.uuid4()),
            run_id=None,            # no run — property-level artifact
            prompt_version=None,    # no prompt
            provider=art["provider"],
            model=None,             # no model
            tool_version=None,
            market=MARKET,
            language=LANGUAGE,
            captured_at=when,
            payload_hash=digest,
            storage_path=None,      # set by store_evidence from the Drive upload
            data_class=art["data_class"],
            operator=art["operator"],
            source_reference=art["source_reference"],
            observation_id=None,    # property-level, not observation-level
            manifest_id=None,
        )
        storage_path = store_evidence(db, record, path)
        row = db.table("evidence").select("id").eq("payload_hash", digest).execute().data[0]
        print(f"    -> drive       : {storage_path}")
        print(f"    -> evidence.id : {row['id']}\n")
        results[art["key"]] = row["id"]

    if not commit:
        print("Dry run — nothing uploaded, nothing written.")
        return

    update = {
        "review_presence_verified": True,
        "review_presence_evidence_ref": results["google"],
        "third_party_reference_verified": True,
        "third_party_reference_evidence_ref": results["tat"],
    }
    print(f"properties UPDATE: {update}")
    db.table("properties").update(update).eq("id", PROPERTY_ID).execute()
    print("properties row updated.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--commit", action="store_true")
    main(p.parse_args().commit)
