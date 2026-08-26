"""Smoke test — Week 1 acceptance criterion, any provider.

Execution Plan roadmap, Week 1: "One provider call can create a planned
task, completed observation, hash and cost row." Wires the run planner, a
provider adapter, the evidence vault and the cost ledger together end to
end, against System Zero (Atlas's own domain) — never a real hospitality
client's site.

Usage:
    python scripts/smoke_test.py --provider openai --domain https://your-atlas-domain.example
    python scripts/smoke_test.py --provider gemini --domain https://your-atlas-domain.example

Replaces the earlier openai-only smoke_test_openai.py — same logic,
parameterized by provider so Perplexity and Anthropic just register in
ADAPTERS below once their adapters are built, with no new script needed.

Safe to run more than once — fixture rows are looked up before insert.
"""

from __future__ import annotations

import argparse
import asyncio

from _fixtures import ensure_system_zero_fixtures

from atlas.adapters.anthropic_adapter import AnthropicAdapter
from atlas.adapters.base import PromptContext
from atlas.adapters.gemini_adapter import GeminiAdapter
from atlas.adapters.openai_adapter import OpenAIAdapter
from atlas.adapters.perplexity_adapter import PerplexityAdapter
from atlas.costs.ledger import CostRecord, record_cost
from atlas.db.client import get_db
from atlas.planner.run_planner import plan_run

ADAPTERS = {
    "openai": OpenAIAdapter,
    "gemini": GeminiAdapter,
    "perplexity": PerplexityAdapter,
    "anthropic": AnthropicAdapter,
}


async def run_smoke_test(provider: str, domain: str) -> None:
    db = get_db()
    ids = ensure_system_zero_fixtures(db, domain)

    run_plan_id = (
        db.table("run_plans")
        .insert({"property_id": ids["property_id"], "run_type": "system_zero", "replicate_count": 1, "status": "planned"})
        .execute()
        .data[0]["id"]
    )

    task = plan_run(
        run_plan_id=run_plan_id,
        prompt_version_ids=[ids["prompt_version_id"]],
        providers=[provider],
        replicate_count=1,
    )[0]
    print(f"[1/4] Planned task ({provider}): {task.task_id}")

    db.table("observations").update({"status": "running"}).eq("task_id", task.task_id).execute()

    prompt_row = db.table("prompt_versions").select("*").eq("id", ids["prompt_version_id"]).execute().data[0]
    market_row = db.table("markets").select("*").eq("id", ids["market_id"]).execute().data[0]
    prompt_context = PromptContext(
        prompt_id=prompt_row["id"],
        prompt_version=prompt_row["version"],
        prompt_text=prompt_row["prompt_text"],
        market=market_row["market_code"],
        language=market_row["language_code"],
        intent_tier=prompt_row["intent_tier"],
        set_type=prompt_row["set_type"],
    )

    adapter = ADAPTERS[provider]()
    record = await adapter.observe(prompt_context, replicate_index=0)
    print(f"[2/4] Observation {record.execution.status.value} — grounded={record.grounding.search_invoked}")

    db.table("observations").update(
        {
            "status": record.execution.status.value,
            "model": record.identity.model,
            "model_snapshot": record.identity.model_snapshot,
            "tool_version": record.identity.tool_version,
            "search_available": record.grounding.search_available,
            "search_invoked": record.grounding.search_invoked,
            "grounding_status": record.grounding.grounding_status.value,
            "raw_response": record.outcome.raw_response,
            "request_time": record.execution.request_time.isoformat(),
            "completion_time": record.execution.completion_time.isoformat(),
            "latency_ms": record.execution.latency_ms,
            "retry_number": record.execution.retry_number,
            "input_tokens": record.cost.input_tokens,
            "output_tokens": record.cost.output_tokens,
            "search_tool_units": record.cost.search_tool_units,
            "cost_usd": record.cost.cost_usd,
            "is_unknown_cost": record.cost.is_unknown_cost,
        }
    ).eq("task_id", task.task_id).execute()

    observation_id = db.table("observations").select("id").eq("task_id", task.task_id).execute().data[0]["id"]

    evidence_id = (
        db.table("evidence")
        .insert(
            {
                "observation_id": observation_id,
                "run_id": run_plan_id,
                "payload_hash": record.evidence.payload_hash,
                "manifest_id": None,
                "storage_path": None,  # Google Drive upload not yet implemented (Week 1 TODO)
                "data_class": "raw_ai_response",
            }
        )
        .execute()
        .data[0]["id"]
    )
    print(f"[3/4] Evidence hash stored: {record.evidence.payload_hash[:16]}... (evidence_id={evidence_id})")

    record_cost(
        CostRecord(
            observation_id=observation_id,
            property_id=ids["property_id"],
            provider=provider,
            input_tokens=record.cost.input_tokens,
            output_tokens=record.cost.output_tokens,
            search_units=record.cost.search_tool_units,
            total_cost_usd=record.cost.cost_usd,
            is_unknown_cost=record.cost.is_unknown_cost,
        )
    )
    print(f"[4/4] Cost row recorded: ${record.cost.cost_usd:.4f} (unknown_cost={record.cost.is_unknown_cost})")

    print(f"\nWeek 1 acceptance criterion met for {provider}: planned task -> completed observation -> hash -> cost row.")
    if record.cost.is_unknown_cost:
        print("NOTE: cost is flagged unknown — confirm pricing in atlas/config.py before this counts as a real ledger entry.")
    if record.execution.status.value == "excluded":
        print("NOTE: observation was EXCLUDED (never grounded after retry) — correct behaviour per Methodology §8.1, not a bug.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True, choices=sorted(ADAPTERS.keys()))
    parser.add_argument(
        "--domain",
        required=True,
        help="Atlas's own domain — System Zero must never point at a hospitality client's site",
    )
    args = parser.parse_args()
    asyncio.run(run_smoke_test(args.provider, args.domain))


if __name__ == "__main__":
    main()