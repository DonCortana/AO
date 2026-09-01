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

`--offline` runs the same pipeline with an in-memory database double and a
stubbed adapter: no provider keys, no Drive, no live Supabase project. That
is the mode CI runs, and it exists to prove the wiring — plan -> claim ->
finalize -> evidence -> freeze gate — not to prove anything about Postgres
semantics. The locking and retry-ceiling guarantees are proved separately, in
tests/test_claim_task_postgres.py against a real server.

Either mode ends with the Evidence Vault freeze gate (Phase A §4): no
observation may be 'complete' without stored evidence and full provenance
behind it. The gate is scoped to the run plan this script just created, so it
reports on its own work and never on the 67 pre-Phase-A rows that predate the
vault path.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib

from _fixtures import ensure_system_zero_fixtures

from atlas.adapters.anthropic_adapter import AnthropicAdapter
from atlas.adapters.gemini_adapter import GeminiAdapter
from atlas.adapters.openai_adapter import OpenAIAdapter
from atlas.adapters.perplexity_adapter import PerplexityAdapter
from atlas.db.client import get_db
from atlas.evidence.vault import freeze_gate_violations
from atlas.planner.run_planner import plan_run
from atlas.runners import resume as resume_module
from atlas.runners.resume import resume_run

ADAPTERS = {
    "openai": OpenAIAdapter,
    "gemini": GeminiAdapter,
    "perplexity": PerplexityAdapter,
    "anthropic": AnthropicAdapter,
}


async def run_smoke_test(provider: str, domain: str, *, offline: bool = False) -> None:
    db, adapter_factory, folder_id = _wire(provider, offline)
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
        db=db,
    )[0]
    print(f"[1/5] Planned task ({provider}): {task.task_id}")

    # The task is claimed through resume_run, which goes through claim_task().
    # This script no longer writes status='running' itself: since migration
    # 0009 that column is the database's to move, and a script that set it by
    # hand would be exactly the application-convention drift Phase A removed.
    resume_module.ADAPTERS[provider] = adapter_factory
    summary = await resume_run(
        run_plan_id,
        db=db,
        owner=f"smoke-test:{provider}",
        evidence_folder_id=folder_id,
    )
    print(f"[2/5] Claimed and executed: {summary}")

    observation = (
        db.table("observations").select("*").eq("task_id", task.task_id).execute().data[0]
    )
    print(f"[3/5] Observation {observation['status']} — grounded={observation.get('search_invoked')}")

    evidence_rows = [
        row
        for row in db.table("evidence").select("*").execute().data
        if row.get("observation_id") == observation["id"]
    ]
    if not evidence_rows:
        raise SystemExit("FAIL: no evidence row was written for the observation")
    evidence = evidence_rows[0]
    print(
        f"[4/5] Evidence stored: hash={evidence['payload_hash'][:16]}... "
        f"path={evidence['storage_path']}"
    )

    cost_rows = [
        row for row in db.table("costs").select("*").execute().data
        if row.get("observation_id") == observation["id"]
    ]
    if not cost_rows:
        raise SystemExit("FAIL: no cost row was written for the observation")
    print(f"[5/5] Cost row recorded: ${cost_rows[0]['total_cost_usd']:.4f}")

    # ---- Freeze gate (Phase A §4) ----------------------------------------
    violations = freeze_gate_violations(db, run_plan_id)
    if violations:
        for v in violations:
            print(f"FREEZE GATE VIOLATION: task={v['task_id']} missing={v['missing']}")
        raise SystemExit(
            "FAIL: an observation reached 'complete' without complete vault "
            "evidence. This is the v1.0-MVP evidence-integrity gate."
        )
    print("\nFreeze gate passed: every complete observation in this run plan has "
          "stored evidence with full provenance.")

    print(f"Week 1 acceptance criterion met for {provider}: planned task -> claimed "
          "task -> completed observation -> hash -> cost row.")
    if cost_rows[0]["is_unknown_cost"]:
        print("NOTE: cost is flagged unknown — confirm pricing in atlas/config.py before this counts as a real ledger entry.")
    if observation["status"] == "excluded":
        print("NOTE: observation was EXCLUDED (never grounded after retry) — correct behaviour per Methodology §8.1, not a bug.")


def _wire(provider: str, offline: bool):
    """Return (db, adapter_factory, evidence_folder_id) for the chosen mode."""
    if not offline:
        return get_db(), ADAPTERS[provider], None

    # Offline: the in-memory double and stubbed adapter the test suite uses.
    # Imported from tests/ deliberately rather than duplicated — a second
    # copy of the double would drift from the one the suite actually asserts
    # against, and a smoke test running against a stale double proves less
    # than nothing.
    #
    # Python puts scripts/ on sys.path when running a script, not the repo
    # root, so `tests` is not importable without this.
    import sys

    repo_root = str(pathlib.Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from tests.conftest import FakeDB
    from tests.helpers import FakeAdapter, make_success_record

    db = FakeDB()
    adapter = FakeAdapter([make_success_record(provider=provider, marker="smoke")])

    # Stub the Drive leg only; the real store_evidence still writes the row,
    # so the freeze gate below is checking genuine provenance columns.
    import atlas.evidence.vault as vault_module

    def _fake_upload(local_path, folder_id=None, *, record=None):
        return f"https://drive.example/smoke/{record.payload_hash}"

    vault_module.upload_to_drive = _fake_upload

    return db, adapter.as_factory(), None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="run against an in-memory double with a stubbed adapter (no network, no keys)",
    )
    parser.add_argument("--provider", default="openai", choices=sorted(ADAPTERS.keys()))
    parser.add_argument(
        "--domain",
        default=None,
        help="Atlas's own domain — System Zero must never point at a hospitality client's site",
    )
    args = parser.parse_args()

    if not args.offline and not args.domain:
        parser.error("--domain is required unless --offline is given")

    asyncio.run(
        run_smoke_test(
            args.provider,
            args.domain or "https://offline.smoke.example",
            offline=args.offline,
        )
    )


if __name__ == "__main__":
    main()
