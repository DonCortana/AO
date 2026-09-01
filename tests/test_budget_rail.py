"""Budget rail — Phase A §5, the month-scoping fix.

Operating System §10 specifies a *monthly* budget with an alert at 80% and a
stop at 100%. The rail previously summed every cost row for a property since
the beginning of time and compared that lifetime total against the monthly
figure, so once cumulative spend passed one month's allowance the rail
returned 'stop' permanently — indistinguishable from having no rail, because
a signal that never clears is one everybody learns to ignore.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from atlas.costs.ledger import (
    check_budget_rail,
    lifetime_spend,
    month_start_utc,
    month_to_date_spend,
)
from atlas.runners import resume as resume_module
from atlas.runners.resume import resume_run
from tests.helpers import FakeAdapter, make_success_record

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def property_id() -> str:
    return str(uuid.uuid4())


def _cost(fake_db, property_id: str, amount: float, when: datetime) -> None:
    fake_db.seed(
        "costs",
        [
            {
                "property_id": property_id,
                "provider": "openai",
                "total_cost_usd": amount,
                "created_at": when.isoformat(),
            }
        ],
    )


def test_month_start_utc_truncates_to_first_instant_of_the_month():
    assert month_start_utc(NOW) == "2026-09-01T00:00:00+00:00"


def test_spend_excludes_previous_months(fake_db, property_id):
    _cost(fake_db, property_id, 500.0, datetime(2026, 8, 20, tzinfo=timezone.utc))
    _cost(fake_db, property_id, 400.0, datetime(2026, 7, 3, tzinfo=timezone.utc))
    _cost(fake_db, property_id, 10.0, datetime(2026, 9, 2, tzinfo=timezone.utc))

    assert month_to_date_spend(property_id, db=fake_db, now=NOW) == 10.0
    assert lifetime_spend(property_id, db=fake_db) == 910.0


def test_prior_month_overspend_does_not_pin_the_rail_to_stop(fake_db, property_id):
    """The actual bug. Two heavy months in the past, a quiet current month:
    the rail must read 'ok', not 'stop'."""
    _cost(fake_db, property_id, 900.0, datetime(2026, 7, 10, tzinfo=timezone.utc))
    _cost(fake_db, property_id, 900.0, datetime(2026, 8, 10, tzinfo=timezone.utc))
    _cost(fake_db, property_id, 5.0, datetime(2026, 9, 4, tzinfo=timezone.utc))

    assert check_budget_rail(property_id, 1000.0, db=fake_db, now=NOW) == "ok"
    # Lifetime spend is still available, just not conflated with the rail.
    assert lifetime_spend(property_id, db=fake_db) == 1805.0


def test_rail_thresholds(fake_db, property_id):
    _cost(fake_db, property_id, 790.0, datetime(2026, 9, 5, tzinfo=timezone.utc))
    assert check_budget_rail(property_id, 1000.0, db=fake_db, now=NOW) == "ok"

    _cost(fake_db, property_id, 10.0, datetime(2026, 9, 6, tzinfo=timezone.utc))
    assert check_budget_rail(property_id, 1000.0, db=fake_db, now=NOW) == "alert"

    _cost(fake_db, property_id, 200.0, datetime(2026, 9, 7, tzinfo=timezone.utc))
    assert check_budget_rail(property_id, 1000.0, db=fake_db, now=NOW) == "stop"


def test_zero_or_negative_budget_is_not_a_permanent_stop(fake_db, property_id):
    _cost(fake_db, property_id, 5.0, datetime(2026, 9, 5, tzinfo=timezone.utc))
    assert check_budget_rail(property_id, 0.0, db=fake_db, now=NOW) == "ok"


@pytest.mark.asyncio
async def test_resume_run_stops_claiming_when_the_rail_says_stop(
    fake_db, pipeline, fake_drive, monkeypatch
):
    """Phase A §5: the rail is checked before each batch claim loop, and a
    'stop' reading must actually stop the run rather than being logged."""
    adapter = FakeAdapter([make_success_record()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    fake_db.seed(
        "costs",
        [
            {
                "property_id": pipeline["property_id"],
                "provider": "openai",
                "total_cost_usd": 1000.0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )

    summary = await resume_run(pipeline["run_plan_id"], db=fake_db, monthly_budget_usd=100.0)

    assert summary.budget_state == "stop"
    assert summary.attempted == 0
    assert adapter.call_count == 0, "no provider call may be made past the stop rail"
    assert summary.stopped_reason and "budget rail" in summary.stopped_reason
    # The task was never claimed, so it stays exactly as it was.
    obs = next(r for r in fake_db.tables["observations"] if r["task_id"] == pipeline["task_id"])
    assert obs["status"] == "queued"
    assert obs["retry_number"] == 0


@pytest.mark.asyncio
async def test_resume_run_proceeds_when_the_rail_is_ok(fake_db, pipeline, fake_drive, monkeypatch):
    adapter = FakeAdapter([make_success_record()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    summary = await resume_run(pipeline["run_plan_id"], db=fake_db, monthly_budget_usd=100.0)

    assert summary.budget_state == "ok"
    assert summary.complete == 1
    assert summary.stopped_reason is None


@pytest.mark.asyncio
async def test_rail_is_disabled_and_says_so_when_unconfigured(
    fake_db, pipeline, fake_drive, monkeypatch
):
    """No budget column exists on `properties`, so an unconfigured rail is a
    real state. It must be reported as such rather than silently defaulting
    to a number nobody chose."""
    monkeypatch.delenv("ATLAS_MONTHLY_BUDGET_USD", raising=False)
    adapter = FakeAdapter([make_success_record()])
    monkeypatch.setitem(resume_module.ADAPTERS, "openai", adapter.as_factory())

    summary = await resume_run(pipeline["run_plan_id"], db=fake_db)

    assert summary.budget_state == "not_configured"
    assert summary.complete == 1
