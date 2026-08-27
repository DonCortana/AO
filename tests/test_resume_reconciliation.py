"""Induced-failure tests for atlas.runners.resume + atlas.reconciliation.reconcile.

Technical Lane step 8: prove the pipeline actually recovers from a timeout,
a malformed provider response, and a mid-run crash — not just that the code
reads as if it would. Each test deliberately breaks one thing and asserts
the database ends up in the correct state, using the real installed SDK
exception types (see tests/helpers.py).
"""

from __future__ import annotations

import pytest

from atlas.reconciliation.reconcile import reconcile_run
from atlas.runners import resume as resume_module
from atlas.runners.resume import _resume_one_task, resume_run
from tests.helpers import FakeAdapter, make_malformed_response_error, make_success_record, make_timeout_error


def _obs(fake_db, task_id: str) -> dict:
    return next(r for r in fake_db.tables["observations"] if r["task_id"] == task_id)


@pytest.mark.asyncio
async def test_timeout_then_recovers_on_next_resume(fake_db, pipeline, monkeypatch):
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

    summary_2 = await resume_run(pipeline["run_plan_id"], db=fake_db)
    assert summary_2.complete == 1
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "complete"
    assert adapter.call_count == 2
    assert len(fake_db.tables["evidence"]) == 1
    assert len(fake_db.tables["costs"]) == 1
    assert fake_db.tables["evidence"][0]["observation_id"] == pipeline["observation_id"]
    assert fake_db.tables["costs"][0]["observation_id"] == pipeline["observation_id"]


@pytest.mark.asyncio
async def test_malformed_response_marks_failed_never_complete(fake_db, pipeline, monkeypatch):
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

    # FAILED is not auto-requeued (Operating System §4) — a further
    # reconcile pass must leave it alone, unlike RETRYABLE.
    reconcile_run(pipeline["run_plan_id"], db=fake_db)
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "failed"


@pytest.mark.asyncio
async def test_mid_run_crash_resumes_without_double_charge(fake_db, pipeline, monkeypatch):
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

    # The provider was actually called, but the crash means the write never
    # landed — task is stuck in 'running', exactly the state a dropped
    # scheduled run leaves behind (Operating System §4).
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "running"
    assert adapter.call_count == 1
    assert fake_db.tables.get("evidence", []) == []
    assert fake_db.tables.get("costs", []) == []

    # A later resume (which reconciles first) picks the stuck task back up.
    # The provider is unavoidably redialed once (D-033) — but the ledger
    # writes are upserted on observation_id, so no double row appears even
    # though this is the task's second full successful pass.
    summary = await resume_run(pipeline["run_plan_id"], db=fake_db)

    assert summary.complete == 1
    assert adapter.call_count == 2
    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] == "complete"
    assert obs["raw_response"]["marker"] == "attempt-2"
    assert len(fake_db.tables["evidence"]) == 1
    assert len(fake_db.tables["costs"]) == 1


@pytest.mark.asyncio
async def test_claim_is_exclusive_no_double_dispatch(fake_db, pipeline, monkeypatch):
    adapter = FakeAdapter([make_success_record()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    candidate = {
        "task_id": pipeline["task_id"],
        "run_plan_id": pipeline["run_plan_id"],
        "provider": "openai",
        "prompt_version_id": pipeline["prompt_version_id"],
        "replicate_index": 0,
        "retry_number": 0,
    }
    prompt_versions = {row["id"]: row for row in fake_db.tables["prompt_versions"]}
    markets = {row["id"]: row for row in fake_db.tables["markets"]}

    first = await _resume_one_task(fake_db, candidate, pipeline["property_id"], prompt_versions, markets)
    assert first == "complete"
    assert adapter.call_count == 1

    # A second "concurrent" runner picks up the same candidate row (as if
    # its own candidates SELECT ran just before the first runner's claim
    # committed) — its claim UPDATE matches zero rows because status is no
    # longer queued/retryable, so it must not call the provider again.
    second = await _resume_one_task(fake_db, candidate, pipeline["property_id"], prompt_versions, markets)
    assert second == "skipped"
    assert adapter.call_count == 1
    assert len(fake_db.tables["evidence"]) == 1
    assert len(fake_db.tables["costs"]) == 1
