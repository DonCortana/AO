"""Run planner — builds the expected task list before any provider call.

Execution Plan Technical Lane, step 3: "Implement run planner first. It
creates the expected task list in the database before any provider call."
This is deliberately the first thing built, ahead of every adapter, because
reconciliation (atlas.reconciliation.reconcile) has nothing to compare
against without it, and GitHub Actions runs may be delayed or dropped
(Operating System §4) — the planned row is what survives that, not the
scheduler's memory of what it meant to do.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from atlas.db.client import get_db


def deterministic_task_id(
    run_plan_id: str,
    prompt_version_id: str,
    provider: str,
    replicate_index: int,
) -> str:
    """Stable task ID so re-running a batch can never duplicate a scored
    observation (Operating System §4: "Task IDs are idempotent: rerunning a
    completed task cannot duplicate the scored observation.").
    """
    raw = f"{run_plan_id}:{prompt_version_id}:{provider}:{replicate_index}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass
class PlannedTask:
    task_id: str
    run_plan_id: str
    prompt_version_id: str
    provider: str
    replicate_index: int


def plan_run(
    run_plan_id: str,
    prompt_version_ids: list[str],
    providers: list[str],
    replicate_count: int,
    *,
    db=None,
) -> list[PlannedTask]:
    """Build the full expected task list for a run_plan row.

    Writes one `observations` row per task with status='planned' before any
    provider is called. This is what reconciliation compares completed work
    against — see atlas.reconciliation.reconcile.

    **Insert-missing-only.** Re-planning an existing run plan adds the rows
    that are absent and leaves every existing row exactly as it is. It is
    never an update path.

    That is a behaviour change, and it fixes a verified data-loss bug. This
    function previously issued a plain `upsert(on_conflict="task_id")`, which
    PostgREST sends as ON CONFLICT DO UPDATE — so re-planning a run wrote the
    literal payload below over every already-executed task, resetting
    `status` to 'planned' and blanking `model` back to ''. A completed,
    scored observation silently became an unplanned one, and the identity of
    the model that produced it was destroyed. `atlas.calibration.driver`
    deliberately re-plans to absorb a repeat invocation (its "plan_run's
    upsert on task_id absorbs the repeat" path), so this was reachable by
    normal operation, not just by hand.

    `ignore_duplicates=True` is what makes it insert-missing-only: verified
    against the installed postgrest 2.31.0 (not documentation) that it emits
    `Prefer: resolution=ignore-duplicates`, which PostgREST translates to ON
    CONFLICT DO NOTHING rather than DO UPDATE.

    One consequence worth knowing at the call site: with DO NOTHING the
    write returns only the rows it actually inserted, so the response is not
    a reliable count of the plan. The returned `tasks` list is computed here
    and is the full expected set regardless of how many already existed.

    `db` is an optional, keyword-only injection seam, added so the
    insert-missing-only behaviour above can be tested directly rather than
    only through a stand-in planner. Every existing call site omits it and
    gets `get_db()` exactly as before.
    """
    tasks = [
        PlannedTask(
            task_id=deterministic_task_id(run_plan_id, prompt_version_id, provider, i),
            run_plan_id=run_plan_id,
            prompt_version_id=prompt_version_id,
            provider=provider,
            replicate_index=i,
        )
        for prompt_version_id in prompt_version_ids
        for provider in providers
        for i in range(replicate_count)
    ]

    db = db or get_db()
    db.table("observations").upsert(
        [
            {
                "task_id": t.task_id,
                "run_plan_id": t.run_plan_id,
                "prompt_version_id": t.prompt_version_id,
                "provider": t.provider,
                "replicate_index": t.replicate_index,
                "status": "planned",
                # model/model_snapshot/tool_version populated by the adapter
                # at call time, not by the planner.
                "model": "",
            }
            for t in tasks
        ],
        on_conflict="task_id",
        ignore_duplicates=True,
    ).execute()

    return tasks
