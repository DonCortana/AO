"""Resume runner — executes queued/retryable observations against real
provider adapters, resumably.

Execution Plan Technical Lane step 8 (retry, idempotency, reconciliation).
Operating System §4: a scheduled GitHub Actions run "may be delayed or
dropped under high load" — this module, together with
atlas.reconciliation.reconcile, is the fix: the database is the record of
what happened, not the scheduler's memory of what it meant to do. Every
call here is safe to invoke again against the same run_plan_id, including
mid-run after a crash.

Idempotency design (see docs/decision-register.md D-032, D-033):
  1. Each task is claimed with an atomic, status-guarded UPDATE
     (queued/retryable -> running). Two overlapping resume_run calls for
     the same run_plan_id can never both claim the same task_id — Postgres
     serializes the two UPDATEs and only the first one's WHERE clause still
     matches.
  2. The provider is only ever called after a task is claimed, and the
     result is written back with a second status-guarded UPDATE
     (running -> terminal). If a process dies between the provider call
     returning and that UPDATE committing, the task is left in `running`;
     the next reconciliation pass requeues it and a later resume_run will
     call the provider again for that task. This residual double-call
     window is accepted and documented (D-033), not silently papered over
     — no provider wired here exposes a client-supplied idempotency key for
     its generation endpoint.
  3. Evidence and cost rows are upserted on_conflict=observation_id
     (migration 0003), so even when step 2's redial happens, the ledger
     itself never double-writes.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic
import openai
from google.genai import errors as genai_errors
from supabase import Client

from atlas.adapters.anthropic_adapter import AnthropicAdapter
from atlas.adapters.base import PromptContext, RunState
from atlas.adapters.gemini_adapter import GeminiAdapter
from atlas.adapters.openai_adapter import OpenAIAdapter
from atlas.adapters.perplexity_adapter import PerplexityAdapter
from atlas.db.client import get_db
from atlas.reconciliation.reconcile import reconcile_run

ADAPTERS = {
    "openai": OpenAIAdapter,
    "gemini": GeminiAdapter,
    "perplexity": PerplexityAdapter,
    "anthropic": AnthropicAdapter,
}

# Confirmed against the installed packages, not documentation (openai 3.3.1,
# anthropic 1.0.0, google-genai 2.20.0, 2026-08-26): APITimeoutError
# subclasses APIConnectionError on both openai and anthropic, so catching
# the parent covers both a hard connect failure and a client-side timeout.
# genai.errors.ServerError is the 5xx bucket for Vertex AI. Perplexity's
# adapter uses the openai client pointed at a different base_url, so
# openai's exception types apply to it too.
#
# These are transient/infrastructure failures — worth an automatic retry on
# the next reconcile+resume cycle, per RunState.RETRYABLE.
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.APIConnectionError,
    anthropic.APIConnectionError,
    genai_errors.ServerError,
)

# Malformed/unparseable responses and 4xx client errors are not fixed by a
# blind retry — RunState.FAILED, which reconciliation does not auto-requeue
# (Operating System §4: only retryable -> queued and reconciled -> queued
# are automatic). Listed explicitly so the classification is legible; any
# exception not caught by TRANSIENT_EXCEPTIONS above falls through to the
# generic except Exception below and is also treated as FAILED — fail
# closed rather than guessing a new exception type is safe to retry.
NON_TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    openai.APIResponseValidationError,
    anthropic.APIResponseValidationError,
    genai_errors.ClientError,
    genai_errors.UnknownApiResponseError,
)


@dataclass
class ResumeSummary:
    run_plan_id: str
    complete: int = 0
    excluded: int = 0
    retryable: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def attempted(self) -> int:
        return self.complete + self.excluded + self.retryable + self.failed + self.skipped

    def record(self, outcome: str) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)


async def resume_run(run_plan_id: str, *, db: Client | None = None) -> ResumeSummary:
    """Reconcile, then execute every queued/retryable task for run_plan_id.

    Safe to call repeatedly for the same run_plan_id — already-complete or
    already-excluded tasks are not in scope (reconcile_run only requeues
    planned/queued/running/retryable), and in-flight tasks are claimed
    exclusively (see module docstring).
    """
    db = db or get_db()
    reconcile_run(run_plan_id, db=db)

    run_plan_rows = db.table("run_plans").select("property_id").eq("id", run_plan_id).execute().data
    if not run_plan_rows:
        raise ValueError(f"run_plans.id={run_plan_id!r} not found")
    property_id = run_plan_rows[0]["property_id"]

    candidates = (
        db.table("observations")
        .select("task_id, run_plan_id, provider, prompt_version_id, replicate_index, retry_number")
        .eq("run_plan_id", run_plan_id)
        .in_("status", [RunState.QUEUED.value, RunState.RETRYABLE.value])
        .execute()
        .data
    )

    summary = ResumeSummary(run_plan_id=run_plan_id)
    if not candidates:
        return summary

    prompt_version_ids = sorted({c["prompt_version_id"] for c in candidates})
    prompt_versions = {
        row["id"]: row
        for row in db.table("prompt_versions").select("*").in_("id", prompt_version_ids).execute().data
    }
    market_ids = sorted({pv["market_id"] for pv in prompt_versions.values()})
    markets = {row["id"]: row for row in db.table("markets").select("*").in_("id", market_ids).execute().data}

    for candidate in candidates:
        outcome = await _resume_one_task(db, candidate, property_id, prompt_versions, markets)
        summary.record(outcome)

    return summary


async def _resume_one_task(
    db: Client,
    candidate: dict,
    property_id: str,
    prompt_versions: dict[str, dict],
    markets: dict[str, dict],
) -> str:
    task_id = candidate["task_id"]
    provider = candidate["provider"]

    claim = (
        db.table("observations")
        .update({"status": RunState.RUNNING.value})
        .eq("task_id", task_id)
        .in_("status", [RunState.QUEUED.value, RunState.RETRYABLE.value])
        .execute()
    )
    if not claim.data:
        # Another process already claimed or finished this task between our
        # candidate select and this update — not an error, just no longer
        # ours to run.
        return "skipped"

    claimed_row = claim.data[0]
    observation_id = claimed_row["id"]
    prior_retry_number = claimed_row.get("retry_number") or 0

    prompt_version = prompt_versions[candidate["prompt_version_id"]]
    market = markets[prompt_version["market_id"]]
    prompt_context = PromptContext(
        prompt_id=prompt_version["id"],
        prompt_version=prompt_version["version"],
        prompt_text=prompt_version["prompt_text"],
        market=market["market_code"],
        language=market["language_code"],
        intent_tier=prompt_version["intent_tier"],
        set_type=prompt_version["set_type"],
    )

    adapter = ADAPTERS[provider]()

    try:
        record = await adapter.observe(prompt_context, candidate["replicate_index"])
    except TRANSIENT_EXCEPTIONS as exc:
        _finalize_failure(db, task_id, RunState.RETRYABLE, prior_retry_number, exc)
        return "retryable"
    except NON_TRANSIENT_EXCEPTIONS as exc:
        _finalize_failure(db, task_id, RunState.FAILED, prior_retry_number, exc)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - fail closed: an unclassified error isn't provably safe to auto-retry
        _finalize_failure(db, task_id, RunState.FAILED, prior_retry_number, exc)
        return "failed"

    finalize = (
        db.table("observations")
        .update(
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
                "retry_number": prior_retry_number + record.execution.retry_number,
                "input_tokens": record.cost.input_tokens,
                "output_tokens": record.cost.output_tokens,
                "search_tool_units": record.cost.search_tool_units,
                "cost_usd": record.cost.cost_usd,
                "is_unknown_cost": record.cost.is_unknown_cost,
            }
        )
        .eq("task_id", task_id)
        .eq("status", RunState.RUNNING.value)
        .execute()
    )
    if not finalize.data:
        # Lost a race to finalize this task (shouldn't happen given the
        # exclusive claim above, but the guard costs nothing and means we
        # never write evidence/cost for a task another process's write won).
        return "skipped"

    db.table("evidence").upsert(
        {
            "observation_id": observation_id,
            "run_id": candidate["run_plan_id"],
            "payload_hash": record.evidence.payload_hash,
            "manifest_id": None,
            "storage_path": None,
            "data_class": "raw_ai_response",
        },
        on_conflict="observation_id",
    ).execute()

    db.table("costs").upsert(
        {
            "observation_id": observation_id,
            "property_id": property_id,
            "provider": provider,
            "input_tokens": record.cost.input_tokens,
            "output_tokens": record.cost.output_tokens,
            "search_units": record.cost.search_tool_units,
            "total_cost_usd": record.cost.cost_usd,
            "is_unknown_cost": record.cost.is_unknown_cost,
        },
        on_conflict="observation_id",
    ).execute()

    return record.execution.status.value


def _finalize_failure(db: Client, task_id: str, state: RunState, prior_retry_number: int, exc: Exception) -> None:
    db.table("observations").update(
        {
            "status": state.value,
            "completion_time": datetime.now(timezone.utc).isoformat(),
            "retry_number": prior_retry_number + 1,
            "error_code": f"{type(exc).__module__}.{type(exc).__qualname__}: {exc}",
        }
    ).eq("task_id", task_id).eq("status", RunState.RUNNING.value).execute()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-plan-id", required=True, help="run_plans.id to resume")
    args = parser.parse_args()
    summary = asyncio.run(resume_run(args.run_plan_id))
    print(summary)


if __name__ == "__main__":
    main()
