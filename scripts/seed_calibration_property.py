"""Seed the hospitality calibration property — Execution Plan §4, Methodology §8.4.

Creates the client, property and market rows for the first real calibration
property. Idempotent: re-running looks up existing rows rather than
duplicating them.

Deliberately does NOT seed `prompt_versions`. The Accommodation Frozen Core
text is not final, and §7 makes a Frozen Core set immutable between baseline
and validation — "Prompt changes that alter intent, entity, tier or set
membership require a major prompt-set version and a new baseline". Writing a
draft set would either freeze the draft or force a rebaseline to correct it.
Seed prompts in a separate step, once the text is agreed.

    python scripts/seed_calibration_property.py --dry-run   # default
    python scripts/seed_calibration_property.py \
        --gbp-url 'https://maps.google.com/?cid=...' --commit

--------------------------------------------------------------------------
PROPERTY CATEGORY — verification result, read before changing
--------------------------------------------------------------------------
`properties.category` was checked against the repo and the governing docs
before this value was chosen. There is **no profile-pack enum anywhere** —
not in the schema (the column is free text with no check constraint), not in
the methodology, not in the operating system, not in the execution plan, and
not in any code or fixture. The only category-shaped vocabulary in the docs
is Methodology §5.1's "Hotel/Resort/Restaurant/LocalBusiness", and that is
the *examples* column for P2 structured-data entity markup — schema.org types
to look for on the site — not a registry of Atlas property profiles. The
comment on `properties.category` in migration 0001 copies that string, which
conflates the two.

So CALIBRATION_CATEGORY below is an unvalidated free-text label. It is not
checked against anything, because there is nothing to check it against.
Establishing a profile registry (which drives "8-12 by profile" in §7 and
"the same profile" in §5.3) is an open item and should be decided before a
second profile exists to be confused with this one.
"""

from __future__ import annotations

import argparse

from atlas.db.client import get_db

CALIBRATION_CLIENT_NAME = "Samujana"
CALIBRATION_PROPERTY_NAME = "Samujana"

# See the module docstring: unvalidated free text, no enum exists.
CALIBRATION_CATEGORY = "Villa/Estate"

CALIBRATION_WEBSITE_URL = "https://www.samujana.com"

# §8.4 makes a Google Business Profile one of the four selection criteria, so
# the real profile URL must be supplied before the calibration is run. Passed
# with --gbp-url rather than held as a constant here: it is a per-property
# fact, and a commit without it is refused rather than defaulted (see seed()).
# For Samujana the confirmed value is recorded as decision-register D-052.

# Execution Plan §4: "Use one primary market/language for the first
# calibration." Thailand / English.
CALIBRATION_MARKET_CODE = "TH"
CALIBRATION_LANGUAGE_CODE = "en"


def _lookup_or_create(db, table: str, filters: dict, payload: dict, *, commit: bool) -> str | None:
    # In a dry run a parent row is never created, so its id is None and any
    # child lookup keyed on it cannot be executed. Report and move on rather
    # than sending "None" to Postgres as a uuid.
    if any(value is None for value in filters.values()):
        print(f"  {table}: WOULD CREATE (parent row not yet created) {payload}")
        return None

    query = db.table(table).select("id")
    for column, value in filters.items():
        query = query.eq(column, value)
    existing = query.execute().data
    if existing:
        print(f"  {table}: exists -> {existing[0]['id']}")
        return existing[0]["id"]
    if not commit:
        print(f"  {table}: WOULD CREATE {payload}")
        return None
    created = db.table(table).insert(payload).execute().data[0]["id"]
    print(f"  {table}: created -> {created}")
    return created


def seed(*, commit: bool, gbp_url: str | None = None) -> dict:
    if commit and not gbp_url:
        raise SystemExit(
            "--gbp-url is required with --commit. Methodology §8.4 requires "
            "the calibration property to have a Google Business Profile; pass "
            "the real URL rather than committing the row without it."
        )

    db = get_db()
    print(f"Seeding calibration property {CALIBRATION_PROPERTY_NAME!r} "
          f"({'COMMIT' if commit else 'DRY RUN'})")

    client_id = _lookup_or_create(
        db,
        "clients",
        {"name": CALIBRATION_CLIENT_NAME},
        {"name": CALIBRATION_CLIENT_NAME, "status": "active"},
        commit=commit,
    )

    property_id = _lookup_or_create(
        db,
        "properties",
        {"name": CALIBRATION_PROPERTY_NAME, "is_calibration_property": True},
        {
            "client_id": client_id,
            "name": CALIBRATION_PROPERTY_NAME,
            "category": CALIBRATION_CATEGORY,
            "website_url": CALIBRATION_WEBSITE_URL,
            "google_business_profile_url": gbp_url,
            # Execution Plan §3: System Zero tests engineering only and never
            # performs hospitality calibration. These two flags are mutually
            # exclusive by design.
            "is_system_zero": False,
            "is_calibration_property": True,
        },
        commit=commit,
    )

    market_id = _lookup_or_create(
        db,
        "markets",
        {
            "property_id": property_id,
            "market_code": CALIBRATION_MARKET_CODE,
            "language_code": CALIBRATION_LANGUAGE_CODE,
        },
        {
            "property_id": property_id,
            "market_code": CALIBRATION_MARKET_CODE,
            "language_code": CALIBRATION_LANGUAGE_CODE,
            "is_primary": True,
        },
        commit=commit,
    )

    print(
        "\nNOT seeded: prompt_versions. The Accommodation Frozen Core text is "
        "not final and §7 makes the set immutable once a baseline runs — seed "
        "it in a separate step once the text is agreed."
    )
    return {"client_id": client_id, "property_id": property_id, "market_id": market_id}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit",
        action="store_true",
        help="actually write rows (default is a dry run that writes nothing)",
    )
    parser.add_argument(
        "--gbp-url",
        default=None,
        help="Google Business Profile URL for the property (§8.4 selection "
             "criterion). Required with --commit.",
    )
    args = parser.parse_args()
    seed(commit=args.commit, gbp_url=args.gbp_url)
