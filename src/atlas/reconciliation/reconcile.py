"""Reconciliation — reports run completeness and finalizes unrecoverable work.

Operating System §4: "A reconciliation job compares expected observation
count with completed eligible records after each batch and again the
following morning." This exists because GitHub Actions is the MVP
scheduler and scheduled runs "may be delayed or dropped under high load"
(Operating System §1, citing GitHub's own documented scheduling behaviour)
— the fix is a resumable, database-driven design, not a more reliable cron.

**Recovery moved out of this module** (Action Plan v2.0 Phase A). Until
migration 0009 this function was the recovery path, and it recovered by
requeuing every non-terminal row it could see — 'planned', 'queued',
'running' and 'retryable' alike — back to 'queued'. Three things were wrong
with that:

  - It could not tell a crashed worker from a healthy in-flight one. A task
    that a live process was mid-way through calling a provider for was
    requeued out from under it, and the next resume pass dispatched it
    again. The blanket requeue was itself a source of double dispatch.
  - It had no retry ceiling, so a task that failed transiently forever was
    requeued forever, silently.
  - Its docstring claimed it requeued work "stuck past its expected window".
    No age check existed anywhere in the function. It requeued unconditionally.

Recovery is now `claim_task()`'s expired-lease branch (migration 0009): a
worker that dies stops renewing its lease, the lease expires, and the next
claim takes the row. That is the only mechanism that can distinguish a dead
worker from a live one, because only a live one holds an unexpired lease.

What is left here is reporting plus the two finalizations nothing else can
do:

  - Retry-ceiling finalization. A non-terminal row at `retry_number >=
    max_attempts` can never be claimed again (claim_task's predicate
    excludes it), so without this it would sit non-terminal forever and no
    run containing it could ever complete. It becomes 'failed' — the run
    ends honestly rather than hanging.
  - Expired-lease requeue, for reporting symmetry. claim_task would reclaim
    these anyway; moving them to 'queued' here makes "abandoned" visible in
    the status column instead of only in a lease timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from supabase import Client

from atlas.db.client import get_db

# A run is finished when every observation has stopped moving. 'reconciled'
# is in the migration 0001 status vocabulary and is treated as terminal here,
# though nothing currently writes it to an observation.
TERMINAL_STATUSES = ("complete", "failed", "excluded", "reconciled")

# What counts toward Methodology §6.2's completeness threshold. Deliberately
# narrower than TERMINAL_STATUSES: a failed or excluded observation is
# finished, but it is not a completed eligible record and must never inflate
# the percentage a movement verdict is gated on.
COMPLETED_STATUSES = ("complete", "reconciled")


@dataclass
class ReconciliationResult:
    run_plan_id: str
    expected_count: int
    completed_count: int
    # Expired-lease rows moved back to 'queued' — presumed-dead workers.
    requeued_task_ids: list[str]
    # Rows finalized to 'failed' because they exhausted max_attempts.
    failed_task_ids: list[str] = field(default_factory=list)
    # Non-terminal rows left deliberately untouched: they are claimable
    # already and reporting them is the whole job.
    pending_task_ids: list[str] = field(default_factory=list)
    run_marked_reconciled: bool = False

    @property
    def completeness_pct(self) -> float:
        if self.expected_count == 0:
            return 100.0
        return round(100 * self.completed_count / self.expected_count, 2)

    @property
    def all_terminal(self) -> bool:
        """Every observation has stopped moving — the precondition for
        marking the run itself reconciled."""
        return not self.requeued_task_ids and not self.pending_task_ids


def _as_utc(value) -> datetime | None:
    """Parse a timestamptz as PostgREST returns it (ISO 8601 string), or pass
    a datetime through. A naive value is read as UTC — every timestamp in
    this schema is timestamptz and Supabase serializes them with an offset,
    so a naive one means a test fixture wrote it, not the database."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        # fromisoformat parses a trailing 'Z' natively on the 3.12+ this
        # project requires — no pre-substitution needed.
        dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def reconcile_run(run_plan_id: str, *, db: Client | None = None) -> ReconciliationResult:
    """Report completeness for one run plan, and finalize what cannot recover.

    Does NOT requeue 'planned', 'queued' or 'retryable' rows. They are
    claimable exactly as they are — `claim_task()` takes all three — so
    touching them would only destroy the retry backoff in `next_attempt_at`.
    They are returned in `pending_task_ids` instead.

    Marks `run_plans.status = 'reconciled'` only when every observation is
    terminal. The previous version wrote that unconditionally, at the end of
    every pass, so a run with work still queued was recorded as reconciled —
    acknowledged in D-063 and corrected here. When work remains, the run's
    status is left exactly as it was and `run_marked_reconciled` is False.

    `db` is injectable (defaults to the real Supabase client) so tests can
    pass a fake double and exercise induced-failure/recovery scenarios
    without touching the live database.
    """
    db = db or get_db()
    observations = (
        db.table("observations")
        .select("task_id, status, retry_number, max_attempts, lease_expires_at")
        .eq("run_plan_id", run_plan_id)
        .execute()
    )
    rows = observations.data
    now = datetime.now(timezone.utc)

    expected_count = len(rows)
    completed_count = sum(1 for o in rows if o["status"] in COMPLETED_STATUSES)

    exhausted: list[str] = []
    requeued: list[str] = []
    pending: list[str] = []

    for o in rows:
        if o["status"] in TERMINAL_STATUSES:
            continue

        attempts = o.get("retry_number") or 0
        ceiling = o.get("max_attempts")
        # Defensive: a null ceiling would make the comparison below False and
        # silently grant infinite retries, which is the failure this whole
        # migration exists to remove. The column is NOT NULL DEFAULT 3, so
        # this only fires against a fixture that omitted it.
        ceiling = 3 if ceiling is None else ceiling

        if attempts >= ceiling:
            # Unclaimable by construction — claim_task requires attempts <
            # ceiling. Finalize rather than leave it to hang the run.
            exhausted.append(o["task_id"])
            continue

        lease_expires_at = _as_utc(o.get("lease_expires_at"))
        if o["status"] == "running" and lease_expires_at is not None and lease_expires_at < now:
            requeued.append(o["task_id"])
            continue

        # Claimable already ('planned'/'queued'/'retryable'), or 'running'
        # under a live lease that a healthy worker still holds. Report, do
        # not touch.
        pending.append(o["task_id"])

    if exhausted:
        db.table("observations").update(
            {
                "status": "failed",
                "error_code": "atlas.reconcile: retry ceiling reached (retry_number >= max_attempts)",
            }
        ).in_("task_id", exhausted).execute()

    if requeued:
        # Clear the lease along with the status. Leaving a dead worker's name
        # on a row that is queued again would make the next reader think it
        # is still held.
        db.table("observations").update(
            {"status": "queued", "lease_owner": None, "lease_expires_at": None}
        ).in_("task_id", requeued).execute()

    result = ReconciliationResult(
        run_plan_id=run_plan_id,
        expected_count=expected_count,
        completed_count=completed_count,
        requeued_task_ids=requeued,
        failed_task_ids=exhausted,
        pending_task_ids=pending,
    )

    if result.all_terminal:
        db.table("run_plans").update({"status": "reconciled"}).eq("id", run_plan_id).execute()
        result.run_marked_reconciled = True

    return result
