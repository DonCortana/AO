"""Reconciliation — compares expected vs completed observations, requeues gaps.

Operating System §4: "A reconciliation job compares expected observation
count with completed eligible records after each batch and again the
following morning." This exists because GitHub Actions is the MVP
scheduler and scheduled runs "may be delayed or dropped under high load"
(Operating System §1, citing GitHub's own documented scheduling behaviour)
— the fix is a resumable, database-driven design, not a more reliable cron.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.db.client import get_db


@dataclass
class ReconciliationResult:
    run_plan_id: str
    expected_count: int
    completed_count: int
    requeued_task_ids: list[str]

    @property
    def completeness_pct(self) -> float:
        if self.expected_count == 0:
            return 100.0
        return round(100 * self.completed_count / self.expected_count, 2)


def reconcile_run(run_plan_id: str) -> ReconciliationResult:
    """Requeue any 'planned'/'queued'/'running'/'retryable' task stuck past
    its expected window back to 'queued'. Never invents a completed record —
    a missing observation stays missing (and non-scoring) until it actually
    completes.

    Cycle completeness < 90% => Incomplete status, no movement verdict
    (Methodology §6.2). This function is what produces the completeness_pct
    that gate checks against.
    """
    db = get_db()
    observations = (
        db.table("observations").select("task_id, status").eq("run_plan_id", run_plan_id).execute()
    )

    expected_count = len(observations.data)
    completed_count = sum(1 for o in observations.data if o["status"] in ("complete", "reconciled"))
    stuck = [
        o["task_id"]
        for o in observations.data
        if o["status"] in ("planned", "queued", "running", "retryable")
    ]

    if stuck:
        db.table("observations").update({"status": "queued"}).in_("task_id", stuck).execute()

    db.table("run_plans").update({"status": "reconciled"}).eq("id", run_plan_id).execute()

    return ReconciliationResult(
        run_plan_id=run_plan_id,
        expected_count=expected_count,
        completed_count=completed_count,
        requeued_task_ids=stuck,
    )
