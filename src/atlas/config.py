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
    anthropic_api_key: str
    perplexity_api_key: str

    # Gemini auth (D-029): Vertex AI service-account auth, not an AI Studio
    # API/authorization key. Routes around the AI Studio prepay-credits
    # billing bug via standard Cloud Billing instead. Auth itself is via
    # Application Default Credentials, which reads GOOGLE_APPLICATION_
    # CREDENTIALS from the environment automatically — it is not passed to
    # the genai.Client call explicitly. Still required here so a missing
    # credential fails fast with a clear message instead of a confusing
    # auth error deep inside the Vertex client.
    google_cloud_project: str
    google_cloud_location: str
    google_application_credentials: str

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
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        perplexity_api_key=_require("PERPLEXITY_API_KEY"),
        google_cloud_project=_require("GOOGLE_CLOUD_PROJECT"),
        google_cloud_location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        google_application_credentials=_require("GOOGLE_APPLICATION_CREDENTIALS"),
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
        effective_from=date(2026, 8, 26),
        # Confirmed against platform.openai.com/docs/pricing for gpt-5.6
        # (short-context tier — Atlas prompts are single-sentence intent
        # queries, well under any long-context threshold). If prompt length
        # grows enough to risk crossing into the long-context tier
        # ($4.00/$15.00 per MTok), this needs a length-aware lookup instead
        # of a flat rate — not built yet, flag if that becomes a risk.
        input_per_mtok=2.00,
        output_per_mtok=10.00,
        search_unit_cost=0.010,  # $10 / 1,000 web_search calls
        notes="gpt-5.6, short-context tier",
    ),
    "gemini": ProviderPrice(
        provider="gemini",
        effective_from=date(2026, 8, 26),
        # D-030: model pin dropped to gemini-2.5-flash (gemini-3.7-flash is
        # not yet GA on Vertex AI — see gemini_adapter.py). Confirmed
        # against cloud.google.com/gemini-enterprise-agent-platform/
        # generative-ai/pricing (Vertex AI's current name), 2026-08-26.
        input_per_mtok=0.30,
        output_per_mtok=2.50,
        # $35/1,000 Grounding with Google Search requests on Vertex AI,
        # beyond a free quota of 1,500 grounded prompts/day. This is a
        # materially different rate AND a different free-tier shape than
        # the AI Studio path's $14/1,000 with 5,000/month free — the two
        # billing surfaces are not interchangeable, confirming the D-029
        # note that this needed independent reconfirmation, not a carried-
        # forward assumption. This flat rate does not model the free daily
        # quota, so it overcharges every observation until usage crosses
        # it — fine for a smoke test, revisit before this feeds a real
        # client cost ledger.
        search_unit_cost=0.035,
        notes="gemini-2.5-flash via Vertex AI (D-030, supersedes the gemini-3.7-flash AI Studio pricing this row previously held). Re-check pricing and reconsider the model pin once Gemini 3.x reaches Vertex AI GA.",
    ),
    "perplexity": ProviderPrice(
        provider="perplexity",
        effective_from=date(2026, 8, 26),
        # sonar model, Sonar Chat Completions (D-031) — confirmed against
        # docs.perplexity.ai pricing, 2026-08-26.
        input_per_mtok=1.00,
        output_per_mtok=1.00,
        # Sonar charges a flat per-request fee on top of token cost, tiered
        # by search_context_size: $5/1,000 (low), $8/1,000 (medium),
        # $12/1,000 (high) requests. The adapter does not force a tier
        # explicitly (no confirmed request parameter for it — see
        # perplexity_adapter.py), so the actual tier used is whatever
        # Sonar defaults to. Priced here at the "low" rate as a documented
        # floor estimate, not a confirmed match — reconcile against the
        # tier actually reported in each response's usage object once
        # live-tested, and tighten this before it feeds a real client
        # cost ledger.
        search_unit_cost=0.005,
        notes="sonar via Sonar Chat Completions (D-031) — retires 2026-09-27, tracked rebuild deadline in decision-register.md.",
    ),
}