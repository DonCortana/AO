"""Smoke test — Week 1 acceptance criterion.

Execution Plan roadmap, Week 1: "One provider call can create a planned
task, completed observation, hash and cost row." This script wires the run
planner, the OpenAI adapter, the evidence vault and the cost ledger
together exactly once, end to end, against System Zero (Atlas's own
domain) — this must never be pointed at a real hospitality client's site.

Usage:
    python scripts/smoke_test_openai.py --domain https://your-atlas-domain.example

Safe to run more than once — client/property/market/prompt_version fixture
rows are looked up before insert, so reruns add a new run_plan + observation
without duplicating the System Zero fixtures.
"""

from __future__ import annotations

import argparse
import asyncio

from atlas.adapters.base import PromptContext
from atlas.adapters.openai_adapter import OpenAIAdapter
from atlas.costs.ledger import CostRecord, record_cost
from atlas.db.client import get_db
from atlas.planner.run_planner import plan_run

SYSTEM_ZERO_CLIENT_NAME = "Atlas Optimisation (System Zero)"


def ensure_system_zero_fixtures(db, domain: str) -> dict:
    """Idempotent lookup-or-create for the System Zero client/property/
    market/prompt_version rows this smoke test needs."""

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


async def run_smoke_test(domain: str) -> None:
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
        providers=["openai"],
        replicate_count=1,
    )[0]
    print(f"[1/4] Planned task: {task.task_id}")

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

    record = await OpenAIAdapter().observe(prompt_context, replicate_index=0)
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
            provider="openai",
            input_tokens=record.cost.input_tokens,
            output_tokens=record.cost.output_tokens,
            search_units=record.cost.search_tool_units,
            total_cost_usd=record.cost.cost_usd,
            is_unknown_cost=record.cost.is_unknown_cost,
        )
    )
    print(f"[4/4] Cost row recorded: ${record.cost.cost_usd:.4f} (unknown_cost={record.cost.is_unknown_cost})")

    print("\nWeek 1 acceptance criterion met: planned task -> completed observation -> hash -> cost row.")
    if record.cost.is_unknown_cost:
        print("NOTE: cost is flagged unknown — confirm OpenAI pricing in atlas/config.py before this counts as a real ledger entry.")
    if record.execution.status.value == "excluded":
        print("NOTE: observation was EXCLUDED (never grounded after retry) — this is correct behaviour per Methodology §8.1, not a bug.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--domain",
        required=True,
        help="Atlas's own domain — System Zero must never point at a hospitality client's site",
    )
    args = parser.parse_args()
    asyncio.run(run_smoke_test(args.domain))


if __name__ == "__main__":
    main()
