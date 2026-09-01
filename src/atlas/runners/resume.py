"""Resume runner — executes claimable observations against real provider
adapters, resumably.

Execution Plan Technical Lane step 8 (retry, idempotency, reconciliation).
Operating System §4: a scheduled GitHub Actions run "may be delayed or
dropped under high load" — this module, together with
atlas.reconciliation.reconcile, is the fix: the database is the record of
what happened, not the scheduler's memory of what it meant to do. Every
call here is safe to invoke again against the same run_plan_id, including
mid-run after a crash.

Idempotency design (see docs/decision-register.md D-032, D-033, and
Action Plan v2.0 Phase A):

  1. Each task is claimed by calling the `claim_task()` Postgres function
     (migration 0009). That function is the ONLY path to status='running'
     — this module no longer writes 'running' itself. It picks one row with
     FOR UPDATE SKIP LOCKED, stamps a lease, and increments the attempt
     counter, all in one statement. Two workers running concurrently against
     the same run plan therefore cannot take the same row: one gets it, the
     other skips past the locked row and takes the next or nothing.

     Before 0009 the claim was a status-guarded UPDATE issued from here.
     That was correct only while every writer remembered to issue exactly
     that UPDATE, and it had no way to express a retry ceiling — so a task
     that failed transiently forever was redispatched forever.

  2. The provider is only ever called after a task is claimed, and the
     result is written back with a status-guarded UPDATE that ALSO matches
     on `lease_owner`. The lease guard is what makes the write safe against
     the reclaim path: a worker that stalled long enough for its lease to
     expire, and whose task another worker has since taken, will match zero
     rows and abandon its result rather than overwrite the reclaimer's.

  3. A crashed worker is recovered by lease expiry, not by a blanket
     requeue. The task stays 'running' until `lease_expires_at` passes, at
     which point `claim_task()` reclaims it. This narrows D-033's redial
     window (provider call succeeds, finalize dies) but does not close it:
     the reclaiming worker does call the provider again. No provider wired
     here exposes a client-supplied idempotency key for its generation
     endpoint, so that residual double-call remains accepted and documented,
     not silently papered over.

  4. The retry ceiling lives in `claim_task()`'s predicate
     (`retry_number < max_attempts`), so no code path here can loop past it.
     Tasks that reach the ceiling are finalized to 'failed' by reconcile.

  5. Evidence and cost rows are upserted on_conflict=observation_id
     (migration 0003), so even when step 3's redial happens, the ledger
     itself never double-writes.

**Evidence goes through the vault** (Phase A §4). This module used to write
the `evidence` row directly, with `manifest_id=None`, `storage_path=None`
and none of the D-049 provenance columns — an evidence ledger that recorded
a hash of a payload stored nowhere, which is an assertion that evidence
exists rather than evidence. It now calls `vault.store_evidence()`, which
uploads the artifact to Drive first and only then writes the row. An
observation cannot reach 'complete' unless that succeeded — see
`_store_evidence_for`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import anthropic
import openai
from google.genai import errors as genai_errors
from supabase import Client

from atlas.adapters.anthropic_adapter import AnthropicAdapter
from atlas.adapters.base import PromptContext, RunState
from atlas.adapters.gemini_adapter import GeminiAdapter
from atlas.adapters.openai_adapter import OpenAIAdapter
from atlas.adapters.perplexity_adapter import PerplexityAdapter
from atlas.costs.ledger import check_budget_rail
from atlas.db.client import get_db
from atlas.evidence.vault import EvidenceRecord, store_evidence
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


# How long a claimed task is presumed alive. Long enough that a slow but
# healthy provider call is never reclaimed out from under a live worker
# (the slowest adapter path here is a grounded retry, well under a minute),
# short enough that a crashed worker's task is recoverable within one
# scheduled cycle rather than at the next human intervention.
DEFAULT_LEASE_SECONDS = 600

# Re-check the budget rail every this many claims (Phase A §5: "before each
# batch claim loop"). Small enough that a run cannot burn far past its
# monthly budget between checks.
DEFAULT_BATCH_SIZE = 10

# Retry backoff (Phase A §1): next_attempt_at = now + base * 2^attempts,
# capped. Applied on retryable outcomes only; a FAILED task is not retried
# at all.
BACKOFF_BASE_SECONDS = 30
BACKOFF_CAP_SECONDS = 900

# Operating System §7 data class for a raw Layer A provider response.
EVIDENCE_DATA_CLASS = "raw_ai_response"

# Env var supplying the per-property monthly budget when a caller does not
# pass one. There is no budget column on `properties` — adding one is schema
# work outside Phase A — so the rail is configured, not stored. When neither
# is set the rail is explicitly disabled and says so in the summary, rather
# than silently defaulting to a number nobody chose.
BUDGET_ENV_VAR = "ATLAS_MONTHLY_BUDGET_USD"


@dataclass
class ResumeSummary:
    run_plan_id: str
    complete: int = 0
    excluded: int = 0
    retryable: int = 0
    failed: int = 0
    skipped: int = 0
    # Which worker identity claimed under this call — recorded so a run that
    # went wrong can be traced to the process that ran it.
    owner: str = ""
    # Last budget rail reading ('ok' | 'alert' | 'stop' | 'not_configured').
    budget_state: str = "not_configured"
    # Set when the loop stopped for a reason other than "no more work".
    stopped_reason: str | None = None
    # Tasks claimed but abandoned before finalize because evidence could not
    # be stored — see _store_evidence_for.
    unevidenced_task_ids: list[str] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return self.complete + self.excluded + self.retryable + self.failed + self.skipped

    def record(self, outcome: str) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)


def worker_identity() -> str:
    """A per-process lease owner.

    Host and pid make it legible to a human reading the table; the random
    suffix keeps two runs in the same container (or a recycled pid) from
    sharing an identity, which would let one worker's finalize satisfy the
    lease guard on another's claim.
    """
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _backoff_until(attempts: int, now: datetime | None = None) -> str:
    """When a retryable task becomes claimable again."""
    now = now or datetime.now(timezone.utc)
    delay = min(BACKOFF_BASE_SECONDS * (2**attempts), BACKOFF_CAP_SECONDS)
    return (now + timedelta(seconds=delay)).isoformat()


def provider_request_id(raw_response: object) -> str | None:
    """Best-effort provider-side request identifier out of a raw response.

    Populates `observations.provider_request_id` and the Operating System §7
    "source reference" on the evidence row. Best-effort deliberately: three of
    the four adapters store a payload with a top-level `id` (OpenAI Responses,
    Perplexity/Sonar Chat Completions, Anthropic Messages), Vertex does not
    reliably, and no adapter is being changed in Phase A to surface one
    explicitly. Returning None is an honest "this provider did not give us
    one", not a failure.
    """
    if isinstance(raw_response, dict):
        for key in ("id", "response_id", "request_id"):
            value = raw_response.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _resolve_budget(monthly_budget_usd: float | None) -> float | None:
    if monthly_budget_usd is not None:
        return monthly_budget_usd
    raw = os.environ.get(BUDGET_ENV_VAR)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{BUDGET_ENV_VAR}={raw!r} is not a number") from exc


def claim_task(
    db: Client, run_plan_id: str, owner: str, lease_seconds: int = DEFAULT_LEASE_SECONDS
) -> dict | None:
    """Claim one task via the migration 0009 Postgres function.

    The only way this module obtains a running task. Returns the claimed
    observation row, or None when nothing is claimable — which covers "all
    done", "everything left is at the retry ceiling", "everything left is
    backing off", and "another worker holds a live lease on it".
    """
    claimed = db.rpc(
        "claim_task",
        {
            "p_run_plan_id": run_plan_id,
            "p_owner": owner,
            "p_lease_seconds": lease_seconds,
        },
    ).execute()
    return claimed.data[0] if claimed.data else None


async def resume_run(
    run_plan_id: str,
    *,
    db: Client | None = None,
    owner: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    monthly_budget_usd: float | None = None,
    evidence_folder_id: str | None = None,
) -> ResumeSummary:
    """Reconcile, then claim and execute tasks for run_plan_id until none remain.

    Safe to call repeatedly, and safe to run concurrently with itself: every
    task is taken through `claim_task()`, so two workers pointed at the same
    run plan divide the work rather than duplicating it.

    The budget rail is checked before the first claim and again every
    `batch_size` claims (Operating System §10, Phase A §5). A 'stop' reading
    ends the loop and is reported in `stopped_reason`; work already claimed is
    always finished and written, because abandoning a task after paying for
    the provider call would lose evidence Atlas has already been charged for.
    """
    db = db or get_db()
    owner = owner or worker_identity()
    reconcile_run(run_plan_id, db=db)

    run_plan_rows = db.table("run_plans").select("property_id").eq("id", run_plan_id).execute().data
    if not run_plan_rows:
        raise ValueError(f"run_plans.id={run_plan_id!r} not found")
    property_id = run_plan_rows[0]["property_id"]

    summary = ResumeSummary(run_plan_id=run_plan_id, owner=owner)
    budget = _resolve_budget(monthly_budget_usd)
    cache: dict[str, dict] = {}
    claimed_count = 0

    while True:
        if budget is not None and claimed_count % batch_size == 0:
            summary.budget_state = check_budget_rail(property_id, budget, db=db)
            if summary.budget_state == "stop":
                # Operating System §10: at 100% only critical in-flight work
                # proceeds. Claiming a new task is not critical work.
                summary.stopped_reason = (
                    f"budget rail returned 'stop' for property {property_id} "
                    f"against a monthly budget of {budget}"
                )
                break

        claimed = claim_task(db, run_plan_id, owner, lease_seconds)
        if claimed is None:
            break
        claimed_count += 1

        outcome = await _run_claimed_task(
            db, claimed, property_id, cache, owner, evidence_folder_id, summary
        )
        summary.record(outcome)

    return summary


def _prompt_context_for(db: Client, claimed: dict, cache: dict) -> tuple[PromptContext, dict, dict]:
    """Load (and memoize) the prompt version + market for a claimed task.

    Loaded per claim rather than pre-loaded for a candidate list, because
    there is no candidate list any more — tasks arrive one at a time from
    claim_task. The cache keeps a run of N replicates over the same prompt to
    two reads rather than 2N.
    """
    prompt_version_id = claimed["prompt_version_id"]
    prompt_version = cache.get(f"pv:{prompt_version_id}")
    if prompt_version is None:
        rows = db.table("prompt_versions").select("*").eq("id", prompt_version_id).execute().data
        if not rows:
            raise ValueError(f"prompt_versions.id={prompt_version_id!r} not found")
        prompt_version = rows[0]
        cache[f"pv:{prompt_version_id}"] = prompt_version

    market_id = prompt_version["market_id"]
    market = cache.get(f"mk:{market_id}")
    if market is None:
        rows = db.table("markets").select("*").eq("id", market_id).execute().data
        if not rows:
            raise ValueError(f"markets.id={market_id!r} not found")
        market = rows[0]
        cache[f"mk:{market_id}"] = market

    context = PromptContext(
        prompt_id=prompt_version["id"],
        prompt_version=prompt_version["version"],
        prompt_text=prompt_version["prompt_text"],
        market=market["market_code"],
        language=market["language_code"],
        intent_tier=prompt_version["intent_tier"],
        set_type=prompt_version["set_type"],
    )
    return context, prompt_version, market


def _store_evidence_for(
    db: Client,
    claimed: dict,
    record,
    prompt_version: dict,
    market: dict,
    folder_id: str | None,
) -> str:
    """Upload the raw response and write its `evidence` row through the vault.

    Returns the Drive storage path. Raises if the upload or the row write
    fails — which is the point: the caller treats a raise as "this
    observation may not be finalized as complete".

    The payload is written to a temp file because `store_evidence` uploads a
    file. `hash_payload` (canonical JSON of the dict), not `sha256_file`, is
    the right hash here: the artifact is a provider response, and the
    canonical encoding is what makes the same logical response hash
    identically across runs regardless of key order on the wire.
    """
    raw_response = record.outcome.raw_response
    source_reference = provider_request_id(raw_response)

    evidence_record = EvidenceRecord(
        evidence_id=str(uuid.uuid4()),
        run_id=claimed["run_plan_id"],
        prompt_version=prompt_version.get("version", ""),
        provider=claimed["provider"],
        model=record.identity.model,
        tool_version=record.identity.tool_version,
        market=market.get("market_code", ""),
        language=market.get("language_code", ""),
        captured_at=record.execution.request_time,
        payload_hash=record.evidence.payload_hash,
        storage_path=None,  # set by store_evidence from the Drive upload
        data_class=EVIDENCE_DATA_CLASS,
        operator=None,  # §7's operator field is for human (Layer B) capture
        source_reference=source_reference,
        observation_id=claimed["id"],
        manifest_id=record.evidence.manifest_id,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = os.path.join(tmpdir, f"{claimed['task_id']}.json")
        with open(local_path, "w", encoding="utf-8") as handle:
            json.dump(raw_response, handle, sort_keys=True, separators=(",", ":"))
        return store_evidence(db, evidence_record, local_path, folder_id)


async def _run_claimed_task(
    db: Client,
    claimed: dict,
    property_id: str,
    cache: dict,
    owner: str,
    folder_id: str | None,
    summary: ResumeSummary,
) -> str:
    task_id = claimed["task_id"]
    provider = claimed["provider"]
    # claim_task already incremented this, so it is the number of the attempt
    # now being made — which is exactly the exponent the backoff wants.
    attempts = claimed.get("retry_number") or 0

    context, prompt_version, market = _prompt_context_for(db, claimed, cache)
    adapter = ADAPTERS[provider]()

    try:
        record = await adapter.observe(context, claimed["replicate_index"])
    except TRANSIENT_EXCEPTIONS as exc:
        _finalize_failure(db, task_id, owner, RunState.RETRYABLE, attempts, exc)
        return "retryable"
    except NON_TRANSIENT_EXCEPTIONS as exc:
        _finalize_failure(db, task_id, owner, RunState.FAILED, attempts, exc)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - fail closed: an unclassified error isn't provably safe to auto-retry
        _finalize_failure(db, task_id, owner, RunState.FAILED, attempts, exc)
        return "failed"

    # ---- Evidence BEFORE the terminal status (Phase A §4) -----------------
    #
    # Ordering is the enforcement. If the vault write fails, the observation
    # never reaches 'complete' — it goes back to 'retryable' and is picked up
    # again. The reverse order would leave a scored, complete observation
    # whose evidence is a hash of something stored nowhere, which is the
    # exact state this section exists to make unreachable.
    try:
        _store_evidence_for(db, claimed, record, prompt_version, market, folder_id)
    except Exception as exc:  # noqa: BLE001 - any vault failure must block completion
        summary.unevidenced_task_ids.append(task_id)
        _finalize_failure(db, task_id, owner, RunState.RETRYABLE, attempts, exc)
        return "retryable"

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
                "provider_request_id": provider_request_id(record.outcome.raw_response),
                # retry_number is NOT written here. claim_task owns it
                # (migration 0009); adding the adapter's in-call grounding
                # retry to it would corrupt the retry ceiling's counter. That
                # in-call retry is recorded in grounding_status.
                "input_tokens": record.cost.input_tokens,
                "output_tokens": record.cost.output_tokens,
                "search_tool_units": record.cost.search_tool_units,
                "cost_usd": record.cost.cost_usd,
                "is_unknown_cost": record.cost.is_unknown_cost,
                "lease_expires_at": None,
            }
        )
        .eq("task_id", task_id)
        .eq("status", RunState.RUNNING.value)
        # The lease guard. A worker whose lease expired and whose task was
        # reclaimed matches zero rows here and drops its result rather than
        # overwriting the reclaimer's work.
        .eq("lease_owner", owner)
        .execute()
    )
    if not finalize.data:
        return "skipped"

    db.table("costs").upsert(
        {
            "observation_id": claimed["id"],
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


def _finalize_failure(
    db: Client,
    task_id: str,
    owner: str,
    state: RunState,
    attempts: int,
    exc: Exception,
) -> None:
    """Write a non-success terminal/retryable outcome, under the lease guard.

    A RETRYABLE outcome also sets `next_attempt_at`, which is what keeps a
    failing task from being re-claimed in a tight loop: claim_task will not
    take a retryable row until that time passes.
    """
    payload = {
        "status": state.value,
        "completion_time": datetime.now(timezone.utc).isoformat(),
        "error_code": f"{type(exc).__module__}.{type(exc).__qualname__}: {exc}",
        "lease_expires_at": None,
    }
    if state is RunState.RETRYABLE:
        payload["next_attempt_at"] = _backoff_until(attempts)

    db.table("observations").update(payload).eq("task_id", task_id).eq(
        "status", RunState.RUNNING.value
    ).eq("lease_owner", owner).execute()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-plan-id", required=True, help="run_plans.id to resume")
    parser.add_argument(
        "--monthly-budget-usd",
        type=float,
        default=None,
        help=f"per-property monthly budget for the rail; defaults to ${BUDGET_ENV_VAR}",
    )
    parser.add_argument(
        "--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS, help="claim lease duration"
    )
    args = parser.parse_args()
    summary = asyncio.run(
        resume_run(
            args.run_plan_id,
            monthly_budget_usd=args.monthly_budget_usd,
            lease_seconds=args.lease_seconds,
        )
    )
    print(summary)


if __name__ == "__main__":
    main()
