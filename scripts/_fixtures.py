"""Shared System Zero fixture helpers for smoke-test scripts.

Every provider smoke test needs the same client/property/market/
prompt_version rows. Extracted here rather than duplicated per provider
script — duplicating it once for Gemini would have meant duplicating it
again for Perplexity and Anthropic too.
"""

from __future__ import annotations

SYSTEM_ZERO_CLIENT_NAME = "Atlas Optimisation (System Zero)"


def ensure_system_zero_fixtures(db, domain: str) -> dict:
    """Idempotent lookup-or-create for the System Zero client/property/
    market/prompt_version rows every provider smoke test needs."""

    client_row = db.table("clients").select("id").eq("name", SYSTEM_ZERO_CLIENT_NAME).execute()
    client_id = (
        client_row.data[0]["id"]
        if client_row.data
        else db.table("clients").insert({"name": SYSTEM_ZERO_CLIENT_NAME, "status": "active"}).execute().data[0]["id"]
    )

    property_row = (
        db.table("properties").select("id").eq("client_id", client_id).eq("is_system_zero", True).execute()
    )
    property_id = (
        property_row.data[0]["id"]
        if property_row.data
        else db.table("properties")
        .insert(
            {
                "client_id": client_id,
                "name": "Atlas Optimisation — System Zero",
                "website_url": domain,
                "is_system_zero": True,
                "is_calibration_property": False,
            }
        )
        .execute()
        .data[0]["id"]
    )

    market_row = db.table("markets").select("id").eq("property_id", property_id).eq("market_code", "US").execute()
    market_id = (
        market_row.data[0]["id"]
        if market_row.data
        else db.table("markets")
        .insert({"property_id": property_id, "market_code": "US", "language_code": "en", "is_primary": True})
        .execute()
        .data[0]["id"]
    )

    prompt_row = (
        db.table("prompt_versions").select("id").eq("market_id", market_id).eq("set_type", "discovery").execute()
    )
    prompt_version_id = (
        prompt_row.data[0]["id"]
        if prompt_row.data
        else db.table("prompt_versions")
        .insert(
            {
                "set_type": "discovery",
                "version": "system-zero-smoke-test-v1",
                "prompt_text": "What is Atlas Optimisation and what does it do?",
                "intent_tier": "D",  # branded/navigational — smoke test asks about the entity itself
                "market_id": market_id,
                "is_holdout": False,
            }
        )
        .execute()
        .data[0]["id"]
    )

    return {
        "client_id": client_id,
        "property_id": property_id,
        "market_id": market_id,
        "prompt_version_id": prompt_version_id,
    }
