"""Environment configuration and versioned provider pricing.

Operating System §10: "Provider pricing is stored as versioned configuration
and revalidated at build time and quarterly." Pricing is never hardcoded
inline in adapter or cost-ledger logic — it is looked up from here so a
provider price change is a one-line diff with a visible history.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    atlas_env: str
    score_model_version: str

    openai_api_key: str
    gemini_api_key: str
    anthropic_api_key: str
    perplexity_api_key: str

    supabase_url: str
    supabase_service_role_key: str

    google_drive_evidence_folder_id: str
    google_drive_service_account_json_path: str


@lru_cache
def get_settings() -> Settings:
    return Settings(
        atlas_env=os.environ.get("ATLAS_ENV", "development"),
        score_model_version=os.environ.get("SCORE_MODEL_VERSION", "v1.0-RC"),
        openai_api_key=_require("OPENAI_API_KEY"),
        gemini_api_key=_require("GEMINI_API_KEY"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        perplexity_api_key=_require("PERPLEXITY_API_KEY"),
        supabase_url=_require("SUPABASE_URL"),
        supabase_service_role_key=_require("SUPABASE_SERVICE_ROLE_KEY"),
        google_drive_evidence_folder_id=os.environ.get(
            "GOOGLE_DRIVE_EVIDENCE_FOLDER_ID", ""
        ),
        google_drive_service_account_json_path=os.environ.get(
            "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_PATH", ""
        ),
    )


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required env var {name}. Copy .env.example to .env for "
            "local dev, or confirm the GitHub 'production' environment secret "
            "is set for CI."
        )
    return value


@dataclass(frozen=True)
class ProviderPrice:
    """Versioned per-provider pricing, effective from a given date.

    Amounts are USD per unit. Token prices are per 1M tokens to match
    published provider pricing pages directly (avoids silent decimal errors
    from manual per-token conversion).
    """

    provider: str
    effective_from: date
    input_per_mtok: float
    output_per_mtok: float
    search_unit_cost: float | None  # e.g. cost per web search call/unit
    notes: str = ""


# Revalidate quarterly per Operating System §10. Update this table, never a
# call site, when a provider changes pricing.
PROVIDER_PRICING: dict[str, ProviderPrice] = {
    "anthropic": ProviderPrice(
        provider="anthropic",
        effective_from=date(2026, 8, 25),
        input_per_mtok=2.00,
        output_per_mtok=10.00,
        search_unit_cost=0.010,  # $10 / 1,000 searches
        notes="claude-sonnet-5, introductory pricing confirmed held past Sept 2026",
    ),
    "openai": ProviderPrice(
        provider="openai",
        effective_from=date(2026, 8, 25),
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        search_unit_cost=None,
        notes="TODO: confirm current Responses API + web_search pricing before Week 2 build",
    ),
    "gemini": ProviderPrice(
        provider="gemini",
        effective_from=date(2026, 8, 25),
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        search_unit_cost=None,
        notes="TODO: confirm current Interactions API + google_search pricing before Week 2 build",
    ),
    "perplexity": ProviderPrice(
        provider="perplexity",
        effective_from=date(2026, 8, 25),
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        search_unit_cost=None,
        notes="TODO: confirm current Sonar API pricing before Week 2 build",
    ),
}
