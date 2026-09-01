"""Phase A acceptance tests §7.3-§7.6 — the ones that are application logic.

§7.1 and §7.2 (concurrent claims, lease reclaim) are locking guarantees and
live in tests/test_claim_task_postgres.py against a real server. What is
here is behaviour the FakeDB can honestly exercise: the retry-ceiling
finalization, the re-plan regression, the run-status contract, and the
freeze gate that keeps an unevidenced observation out of 'complete'.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from atlas.evidence.vault import freeze_gate_violations
from atlas.planner.run_planner import deterministic_task_id, plan_run
from atlas.reconciliation.reconcile import reconcile_run
from atlas.runners import resume as resume_module
from atlas.runners.resume import resume_run
from tests.helpers import FakeAdapter, make_success_record


def _obs(fake_db, task_id: str) -> dict:
    return next(r for r in fake_db.tables["observations"] if r["task_id"] == task_id)


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


# --------------------------------------------------------------------------
# §7.3 — reconcile finalizes a task at the ceiling to 'failed'
# --------------------------------------------------------------------------


def test_reconcile_finalizes_task_at_retry_ceiling_to_failed(fake_db, pipeline):
    """A task at the ceiling can never be claimed again, so reconcile must
    end it. Otherwise it sits non-terminal forever and the run containing it
    can never complete — the run hangs on a task nothing will ever run."""
    row = _obs(fake_db, pipeline["task_id"])
    row["status"] = "retryable"
    row["retry_number"] = 3
    row["max_attempts"] = 3

    result = reconcile_run(pipeline["run_plan_id"], db=fake_db)

    assert result.failed_task_ids == [pipeline["task_id"]]
    assert _obs(fake_db, pipeline["task_id"])["status"] == "failed"
    assert "retry ceiling" in _obs(fake_db, pipeline["task_id"])["error_code"]
    # Everything is terminal now, so the run itself is finished.
    assert result.run_marked_reconciled is True


@pytest.mark.asyncio
async def test_task_at_ceiling_is_not_redispatched(fake_db, pipeline, fake_drive, monkeypatch):
    """The end-to-end version: a burned-out task must not reach an adapter."""
    adapter = FakeAdapter([make_success_record()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    row = _obs(fake_db, pipeline["task_id"])
    row["status"] = "retryable"
    row["retry_number"] = 3
    row["max_attempts"] = 3

    summary = await resume_run(pipeline["run_plan_id"], db=fake_db)

    assert summary.attempted == 0
    assert adapter.call_count == 0, "a task at the retry ceiling must never be redispatched"
    assert _obs(fake_db, pipeline["task_id"])["status"] == "failed"


# --------------------------------------------------------------------------
# §7.4 — re-plan must not regress completed work
# --------------------------------------------------------------------------


def test_replan_does_not_regress_completed_tasks(fake_db):
    """The verified data-loss bug in §2, as a regression test.

    plan -> complete a task (with a model recorded) -> re-plan. Before the
    fix, the re-plan's upsert became ON CONFLICT DO UPDATE and wrote the
    planner's literal payload over the finished row: status back to
    'planned', model back to ''. A scored observation silently became an
    unplanned one and the model identity behind it was destroyed.
    """
    run_plan_id = str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())

    first = plan_run(run_plan_id, [prompt_version_id], ["openai"], 2, db=fake_db)
    assert len(first) == 2
    assert len(fake_db.tables["observations"]) == 2

    # One task runs to completion and records the model that produced it.
    done_task_id = deterministic_task_id(run_plan_id, prompt_version_id, "openai", 0)
    done = _obs(fake_db, done_task_id)
    done.update(
        {
            "status": "complete",
            "model": "gpt-5.6",
            "model_snapshot": "gpt-5.6-2026-08",
            "raw_response": {"marker": "real work"},
            "retry_number": 1,
        }
    )

    # Re-planning the same run plan — exactly what the calibration driver
    # does when a command is run twice.
    second = plan_run(run_plan_id, [prompt_version_id], ["openai"], 2, db=fake_db)

    assert len(second) == 2
    assert len(fake_db.tables["observations"]) == 2, "re-plan must not duplicate rows"

    survived = _obs(fake_db, done_task_id)
    assert survived["status"] == "complete", "re-plan regressed a completed task to 'planned'"
    assert survived["model"] == "gpt-5.6", "re-plan blanked the model identity"
    assert survived["model_snapshot"] == "gpt-5.6-2026-08"
    assert survived["raw_response"] == {"marker": "real work"}
    assert survived["retry_number"] == 1


def test_replan_adds_only_missing_tasks(fake_db):
    """Widening a plan adds the new rows and leaves the existing ones alone."""
    run_plan_id = str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())

    plan_run(run_plan_id, [prompt_version_id], ["openai"], 1, db=fake_db)
    existing_task_id = deterministic_task_id(run_plan_id, prompt_version_id, "openai", 0)
    _obs(fake_db, existing_task_id)["status"] = "complete"

    plan_run(run_plan_id, [prompt_version_id], ["openai", "anthropic"], 1, db=fake_db)

    assert len(fake_db.tables["observations"]) == 2
    assert _obs(fake_db, existing_task_id)["status"] == "complete"
    new_task_id = deterministic_task_id(run_plan_id, prompt_version_id, "anthropic", 0)
    assert _obs(fake_db, new_task_id)["status"] == "planned"


# --------------------------------------------------------------------------
# §7.5 — run status only becomes 'reconciled' when the work is actually done
# --------------------------------------------------------------------------


def test_reconcile_leaves_run_status_alone_while_work_remains(fake_db, pipeline):
    """The D-063 correction: the old code wrote 'reconciled' unconditionally,
    recording a run as finished while its work was still queued."""
    before = fake_db.tables["run_plans"][0]["status"]
    assert before == "planned"

    result = reconcile_run(pipeline["run_plan_id"], db=fake_db)

    assert result.pending_task_ids == [pipeline["task_id"]]
    assert result.all_terminal is False
    assert result.run_marked_reconciled is False
    assert fake_db.tables["run_plans"][0]["status"] == "planned", (
        "run must not be marked reconciled while work remains"
    )
    assert result.completeness_pct == 0.0


def test_reconcile_marks_run_reconciled_when_all_terminal(fake_db, pipeline):
    _obs(fake_db, pipeline["task_id"])["status"] = "complete"

    result = reconcile_run(pipeline["run_plan_id"], db=fake_db)

    assert result.all_terminal is True
    assert result.run_marked_reconciled is True
    assert fake_db.tables["run_plans"][0]["status"] == "reconciled"
    assert result.completeness_pct == 100.0


def test_reconcile_does_not_touch_claimable_rows(fake_db, pipeline):
    """'planned'/'queued'/'retryable' are claimable as they stand. Requeuing
    them would destroy the backoff in next_attempt_at and re-open the tight
    redispatch loop the ceiling exists to prevent."""
    row = _obs(fake_db, pipeline["task_id"])
    row["status"] = "retryable"
    row["retry_number"] = 1
    backoff = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    row["next_attempt_at"] = backoff

    result = reconcile_run(pipeline["run_plan_id"], db=fake_db)

    after = _obs(fake_db, pipeline["task_id"])
    assert after["status"] == "retryable", "a backing-off task must not be requeued"
    assert after["next_attempt_at"] == backoff, "reconcile destroyed the retry backoff"
    assert result.pending_task_ids == [pipeline["task_id"]]


def test_reconcile_requeues_only_expired_leases(fake_db, pipeline):
    row = _obs(fake_db, pipeline["task_id"])
    row["status"] = "running"
    row["lease_owner"] = "dead-worker"
    row["lease_expires_at"] = _past()

    result = reconcile_run(pipeline["run_plan_id"], db=fake_db)

    assert result.requeued_task_ids == [pipeline["task_id"]]
    after = _obs(fake_db, pipeline["task_id"])
    assert after["status"] == "queued"
    assert after["lease_owner"] is None, "a requeued row must not still name a dead worker"
    assert result.run_marked_reconciled is False


def test_reconcile_leaves_live_leases_alone(fake_db, pipeline):
    """The failure the old blanket requeue caused: yanking a task away from a
    worker that is still healthily running it."""
    row = _obs(fake_db, pipeline["task_id"])
    row["status"] = "running"
    row["lease_owner"] = "live-worker"
    row["lease_expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    result = reconcile_run(pipeline["run_plan_id"], db=fake_db)

    after = _obs(fake_db, pipeline["task_id"])
    assert after["status"] == "running", "a live worker's task was requeued out from under it"
    assert after["lease_owner"] == "live-worker"
    assert result.requeued_task_ids == []
    assert result.pending_task_ids == [pipeline["task_id"]]


# --------------------------------------------------------------------------
# §7.6 — an observation cannot reach 'complete' without vault evidence
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observation_cannot_complete_without_vault_evidence(fake_db, pipeline, monkeypatch):
    """The freeze gate, scoped to a run plan created inside this test.

    Drive upload fails, so `store_evidence` raises before writing any row.
    The observation must not reach 'complete': a completed observation whose
    evidence is a hash of something stored nowhere is an unfalsifiable claim,
    which is exactly what Phase A §4 removes.
    """
    adapter = FakeAdapter([make_success_record()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    def _failing_upload(local_path, folder_id=None, *, record=None):
        raise RuntimeError("drive unavailable")

    monkeypatch.setattr("atlas.evidence.vault.upload_to_drive", _failing_upload)

    summary = await resume_run(pipeline["run_plan_id"], db=fake_db)

    assert summary.complete == 0
    assert summary.retryable == 1
    assert pipeline["task_id"] in summary.unevidenced_task_ids

    obs = _obs(fake_db, pipeline["task_id"])
    assert obs["status"] != "complete"
    assert obs["status"] == "retryable"
    assert fake_db.tables.get("evidence", []) == [], "no evidence row without a stored artifact"
    assert fake_db.tables.get("costs", []) == []

    assert freeze_gate_violations(fake_db, pipeline["run_plan_id"]) == []


@pytest.mark.asyncio
async def test_completed_observation_carries_full_provenance(fake_db, pipeline, fake_drive, monkeypatch):
    """The positive half: a task that does complete has real evidence behind
    it — a storage path, a payload hash, and the D-049 provenance columns."""
    adapter = FakeAdapter([make_success_record()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    summary = await resume_run(pipeline["run_plan_id"], db=fake_db)
    assert summary.complete == 1

    evidence = fake_db.tables["evidence"][0]
    assert evidence["observation_id"] == pipeline["observation_id"]
    assert evidence["storage_path"].startswith("https://drive.example/")
    assert evidence["payload_hash"]
    assert evidence["data_class"] == "raw_ai_response"
    # Operating System §7 / D-049 provenance — the columns the old direct
    # write left entirely unset.
    assert evidence["provider"] == "openai"
    assert evidence["model"] == "fake-model"
    assert evidence["tool_version"] == "fake-tool"
    assert evidence["prompt_version"] == "v1"
    assert evidence["market"] == "US"
    assert evidence["language"] == "en"

    assert freeze_gate_violations(fake_db, pipeline["run_plan_id"]) == []


def test_freeze_gate_detects_a_planted_violation(fake_db, pipeline):
    """The gate must actually fire, not just always return empty.

    Plants the exact shape Phase A §4 exists to eliminate — the state all 67
    pre-Phase-A production rows are in — and asserts the check names it.
    """
    _obs(fake_db, pipeline["task_id"])["status"] = "complete"
    fake_db.seed(
        "evidence",
        [
            {
                "observation_id": pipeline["observation_id"],
                "payload_hash": "abc123",
                "storage_path": None,  # the pre-Phase-A shape
                "provider": None,
                "model": None,
            }
        ],
    )

    violations = freeze_gate_violations(fake_db, pipeline["run_plan_id"])
    assert len(violations) == 1
    assert violations[0]["observation_id"] == pipeline["observation_id"]
    assert "storage_path" in violations[0]["missing"]


def test_freeze_gate_flags_complete_observation_with_no_evidence_row_at_all(fake_db, pipeline):
    _obs(fake_db, pipeline["task_id"])["status"] = "complete"

    violations = freeze_gate_violations(fake_db, pipeline["run_plan_id"])
    assert len(violations) == 1
    assert violations[0]["missing"] == ["evidence row"]
