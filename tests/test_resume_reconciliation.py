"""Induced-failure tests for atlas.runners.resume + atlas.reconciliation.reconcile.

Technical Lane step 8: prove the pipeline actually recovers from a timeout,
a malformed provider response, and a mid-run crash — not just that the code
reads as if it would. Each test deliberately breaks one thing and asserts
the database ends up in the correct state, using the real installed SDK
exception types (see tests/helpers.py).

These run against the FakeDB double, which emulates the migration 0009
`claim_task()` predicate but cannot emulate FOR UPDATE SKIP LOCKED. The
locking guarantees live in tests/test_claim_task_postgres.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from atlas.reconciliation.reconcile import reconcile_run
from atlas.runners import resume as resume_module
from atlas.runners.resume import resume_run
from tests.helpers import (
    FakeAdapter,
    make_malformed_response_error,
    make_success_record,
    make_timeout_error,
)


def _obs(fake_db, task_id: str) -> dict:
    return next(r for r in fake_db.tables["observations"] if r["task_id"] == task_id)


def _expire_backoff(fake_db, task_id: str) -> None:
    """Simulate the retry backoff window elapsing.

    claim_task will not re-claim a 'retryable' row until next_attempt_at has
    passed. Tests move that timestamp into the past rather than sleeping.
    """
    _obs(fake_db, task_id)["next_attempt_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()


def _expire_lease(fake_db, task_id: str) -> None:
    """Simulate a worker's lease expiring — i.e. the worker died."""
    _obs(fake_db, task_id)["lease_expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()


@pytest.mark.asyncio
async def test_timeout_backs_off_then_recovers(fake_db, pipeline, fake_drive, monkeypatch):
    adapter = FakeAdapter([make_timeout_error(), make_success_record()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    summary_1 = await resume_run(pipeline["run_plan_id"], db=fake_db)
    assert summary_1.retryable == 1
    assert summary_1.complete == 0
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "retryable"
    assert obs["error_code"] and "APITimeoutError" in obs["error_code"]
    assert fake_db.tables.get("evidence", []) == []
    assert fake_db.tables.get("costs", []) == []

    # The claim incremented the attempt counter, and the failure scheduled a
    # backoff — 30s * 2^1 for the first attempt.
    assert obs["retry_number"] == 1
    backoff = datetime.fromisoformat(obs["next_attempt_at"])
    assert backoff > datetime.now(timezone.utc)

    # While the backoff window is open the task is NOT claimable. This is the
    # behaviour that stops a persistently failing task spinning in a tight
    # redispatch loop, so assert it rather than assuming it.
    summary_2 = await resume_run(pipeline["run_plan_id"], db=fake_db)
    assert summary_2.attempted == 0
    assert adapter.call_count == 1

    _expire_backoff(fake_db, pipeline["task_id"])
    summary_3 = await resume_run(pipeline["run_plan_id"], db=fake_db)
    assert summary_3.complete == 1
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "complete"
    assert obs["retry_number"] == 2
    assert adapter.call_count == 2
    assert len(fake_db.tables["evidence"]) == 1
    assert len(fake_db.tables["costs"]) == 1
    assert fake_db.tables["evidence"][0]["observation_id"] == pipeline["observation_id"]
    assert fake_db.tables["costs"][0]["observation_id"] == pipeline["observation_id"]


@pytest.mark.asyncio
async def test_malformed_response_marks_failed_never_complete(fake_db, pipeline, fake_drive, monkeypatch):
    adapter = FakeAdapter([make_malformed_response_error()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    summary = await resume_run(pipeline["run_plan_id"], db=fake_db)

    assert summary.failed == 1
    assert summary.complete == 0
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "failed"
    assert "APIResponseValidationError" in obs["error_code"]
    assert fake_db.tables.get("evidence", []) == []
    assert fake_db.tables.get("costs", []) == []
    # FAILED is terminal, so no backoff is scheduled — it is not coming back.
    assert obs.get("next_attempt_at") is None

    # FAILED is not auto-requeued (Operating System §4) — a further
    # reconcile pass must leave it alone, unlike RETRYABLE.
    reconcile_run(pipeline["run_plan_id"], db=fake_db)
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "failed"


@pytest.mark.asyncio
async def test_mid_run_crash_recovers_on_lease_expiry_without_double_charge(
    fake_db, pipeline, fake_drive, monkeypatch
):
    adapter = FakeAdapter([make_success_record(marker="attempt-1"), make_success_record(marker="attempt-2")])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    crash_state = {"armed": True}

    def before_execute(query):
        is_finalize_write = (
            query.table_name == "observations"
            and query.op == "update"
            and query.payload is not None
            and "raw_response" in query.payload
        )
        if is_finalize_write and crash_state["armed"]:
            crash_state["armed"] = False
            raise RuntimeError("simulated process crash — died after the provider call, before the commit")

    fake_db.before_execute = before_execute

    with pytest.raises(RuntimeError, match="simulated process crash"):
        await resume_run(pipeline["run_plan_id"], db=fake_db)

    # The provider was called and the crash means the status write never
    # landed — the task is stuck in 'running', holding a lease.
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "running"
    assert adapter.call_count == 1

    # Evidence WAS written, because evidence is stored before the terminal
    # status is set (Phase A §4). That is the safe direction to fail: an
    # observation with evidence but no 'complete' is recoverable, whereas a
    # 'complete' with no evidence is an unfalsifiable claim.
    assert len(fake_db.tables["evidence"]) == 1
    assert fake_db.tables.get("costs", []) == []

    # A live lease means a live worker as far as any other process can tell,
    # so the task is NOT immediately reclaimable. This is exactly the case
    # the old blanket requeue got wrong: it would have redispatched here,
    # while the first worker was still notionally running.
    summary_blocked = await resume_run(pipeline["run_plan_id"], db=fake_db)
    assert summary_blocked.attempted == 0
    assert adapter.call_count == 1
    assert _obs(fake_db, pipeline["task_id"])["status"] == "running"

    # Once the lease expires the worker is presumed dead and claim_task
    # reclaims the task. The provider is unavoidably redialed once (D-033)
    # — but the ledger writes are upserted on observation_id, so no double
    # row appears even though this is the task's second successful pass.
    _expire_lease(fake_db, pipeline["task_id"])
    summary = await resume_run(pipeline["run_plan_id"], db=fake_db)

    assert summary.complete == 1
    assert adapter.call_count == 2
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "complete"
    assert obs["raw_response"]["marker"] == "attempt-2"
    assert len(fake_db.tables["evidence"]) == 1
    assert len(fake_db.tables["costs"]) == 1


@pytest.mark.asyncio
async def test_second_worker_cannot_claim_a_leased_task(fake_db, pipeline, fake_drive, monkeypatch):
    """The application-level half of the exclusivity guarantee.

    The locking half — two workers racing for the same row at the same
    instant — is in tests/test_claim_task_postgres.py, where a real Postgres
    can actually be raced.
    """
    adapter = FakeAdapter([make_success_record()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    first = await resume_run(pipeline["run_plan_id"], db=fake_db, owner="worker-a")
    assert first.complete == 1
    assert adapter.call_count == 1

    # A second worker finds nothing claimable: the task is terminal now.
    second = await resume_run(pipeline["run_plan_id"], db=fake_db, owner="worker-b")
    assert second.attempted == 0
    assert adapter.call_count == 1
    assert len(fake_db.tables["evidence"]) == 1
    assert len(fake_db.tables["costs"]) == 1


@pytest.mark.asyncio
async def test_finalize_is_rejected_after_lease_is_reclaimed(fake_db, pipeline, fake_drive, monkeypatch):
    """A worker whose lease expired must not overwrite the reclaimer's work.

    Simulates the slow-worker case: worker-a claims and calls the provider,
    its lease expires mid-call, worker-b reclaims the task — and worker-a's
    finalize must then match zero rows and be abandoned.
    """
    adapter = FakeAdapter([make_success_record(marker="slow-worker-a")])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    reclaimed = {"done": False}

    def before_execute(query):
        is_finalize_write = (
            query.table_name == "observations"
            and query.op == "update"
            and query.payload is not None
            and "raw_response" in query.payload
        )
        if is_finalize_write and not reclaimed["done"]:
            reclaimed["done"] = True
            # worker-b takes the task out from under worker-a mid-finalize.
            row = _obs(fake_db, pipeline["task_id"])
            row["lease_owner"] = "worker-b"
            row["lease_expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()

    fake_db.before_execute = before_execute

    summary = await resume_run(pipeline["run_plan_id"], db=fake_db, owner="worker-a")

    assert summary.complete == 0
    assert summary.skipped == 1
    obs = _obs(fake_db, pipeline["task_id"])
    # worker-a's result was dropped: the row still belongs to worker-b and
    # never took worker-a's status or payload.
    assert obs["lease_owner"] == "worker-b"
    assert obs["status"] == "running"
    assert obs.get("raw_response") is None
    # No cost row either — worker-a stopped at the rejected finalize.
    assert fake_db.tables.get("costs", []) == []
