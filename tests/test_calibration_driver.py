"""Calibration run driver — preflight, reuse and the run_plans write.

Everything here runs against the FakeDB double in conftest and an injected
fake planner. Nothing touches the live Samujana row, and no real
`prompt_versions` or `run_plans` row is written: the driver's whole purpose is
to be the first thing that writes a frozen_core plan, so a test that reached
the real database would be creating the very row the design says must not
exist yet.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from atlas.calibration.driver import (
    DEFAULT_PROVIDERS,
    MIN_WINDOW_HOURS,
    CalibrationPlan,
    PreflightError,
    plan_calibration_run,
)
from atlas.planner.run_planner import deterministic_task_id


class FakePlanner:
    """Stand-in for `plan_run`, which takes no `db` and calls get_db() itself
    (DESIGN §1). Records its calls and writes the same observation rows the
    real planner would, so reuse detection has something to match on."""

    def __init__(self, db=None):
        self.db = db
        self.calls: list[tuple] = []

    def __call__(self, run_plan_id, prompt_version_ids, providers, replicate_count):
        self.calls.append((run_plan_id, tuple(prompt_version_ids), tuple(providers), replicate_count))
        tasks = [
            {
                "task_id": deterministic_task_id(run_plan_id, pv, provider, i),
                "run_plan_id": run_plan_id,
                "prompt_version_id": pv,
                "provider": provider,
                "replicate_index": i,
                "status": "planned",
                "model": "",
            }
            for pv in prompt_version_ids
            for provider in providers
            for i in range(replicate_count)
        ]
        if self.db is not None:
            self.db.table("observations").upsert(tasks, on_conflict="task_id").execute()
        return tasks


@pytest.fixture
def calibration(fake_db):
    """A property that passes all four §8.4 criteria, plus a 10-prompt
    frozen_core set in one market — the shape the driver is meant to accept."""
    property_id = str(uuid.uuid4())
    market_id = str(uuid.uuid4())

    fake_db.seed(
        "properties",
        [
            {
                "id": property_id,
                "name": "Test Calibration Villa",
                "is_calibration_property": True,
                "is_system_zero": False,
                "website_url": "https://example.test",
                "google_business_profile_url": "https://maps.google.com/?cid=1",
                "review_presence_verified": True,
                "review_presence_evidence_ref": "evidence-review-1",
                "third_party_reference_verified": True,
                "third_party_reference_evidence_ref": "evidence-3p-1",
            }
        ],
    )
    fake_db.seed(
        "markets",
        [{"id": market_id, "property_id": property_id, "market_code": "TH", "language_code": "en"}],
    )

    prompt_ids = [str(uuid.uuid4()) for _ in range(10)]
    fake_db.seed(
        "prompt_versions",
        [
            {
                "id": pid,
                "set_type": "frozen_core",
                "version": "fc-v1.0",
                "prompt_text": f"prompt {n}",
                "intent_tier": "ABCD"[n % 4],
                "market_id": market_id,
                "is_holdout": False,
            }
            for n, pid in enumerate(prompt_ids)
        ],
    )

    return {
        "db": fake_db,
        "property_id": property_id,
        "market_id": market_id,
        "prompt_ids": prompt_ids,
    }


def _run(calibration, *, planner=None, **overrides):
    kwargs = dict(
        property_id=calibration["property_id"],
        prompt_version_ids=calibration["prompt_ids"],
        market_id=calibration["market_id"],
    )
    kwargs.update(overrides)
    return plan_calibration_run(
        calibration["db"], planner=planner or FakePlanner(), **kwargs
    )


def _failures(excinfo) -> str:
    return "\n".join(excinfo.value.failures)


# ---------------------------------------------------------------------------
# The happy path, so the failure tests are known to be failing for their own
# reason rather than a broken fixture.
# ---------------------------------------------------------------------------


def test_dry_run_passes_preflight_and_writes_nothing(calibration):
    db = calibration["db"]
    planner = FakePlanner(db)

    plan = _run(calibration, planner=planner)

    assert isinstance(plan, CalibrationPlan)
    assert plan.committed is False
    assert plan.run_plan_id is None
    assert plan.prompt_set_version == "fc-v1.0"
    # 10 prompts x 4 providers x 5 replicates
    assert plan.planned_observations == 200
    assert plan.providers == DEFAULT_PROVIDERS
    assert planner.calls == []
    assert db.tables.get("run_plans", []) == []
    assert db.tables.get("observations", []) == []


def test_commit_writes_run_plan_and_calls_planner(calibration):
    db = calibration["db"]
    planner = FakePlanner(db)

    plan = _run(calibration, planner=planner, commit=True)

    assert plan.committed is True
    assert plan.reused is False
    run_plans = db.tables["run_plans"]
    assert len(run_plans) == 1
    row = run_plans[0]
    assert row["property_id"] == calibration["property_id"]
    # Migration 0002 vocabulary; never 'system_zero' for a real property.
    assert row["run_type"] == "frozen_core"
    assert row["replicate_count"] == 5
    assert row["status"] == "planned"

    assert len(planner.calls) == 1
    called_plan_id, called_prompts, called_providers, called_n = planner.calls[0]
    assert called_plan_id == row["id"] == plan.run_plan_id
    assert called_prompts == tuple(calibration["prompt_ids"])
    assert called_providers == DEFAULT_PROVIDERS
    assert called_n == 5
    assert len(db.tables["observations"]) == 200


# ---------------------------------------------------------------------------
# §2 preflight — each check's failure path individually.
# ---------------------------------------------------------------------------


def test_preflight_1_rejects_missing_prompt_row(calibration):
    ghost = str(uuid.uuid4())
    ids = calibration["prompt_ids"][:-1] + [ghost]
    with pytest.raises(PreflightError) as exc:
        _run(calibration, prompt_version_ids=ids)
    assert "do not exist" in _failures(exc)
    assert ghost in _failures(exc)


def test_preflight_1_rejects_wrong_set_type(calibration):
    db = calibration["db"]
    stray = calibration["prompt_ids"][3]
    for row in db.tables["prompt_versions"]:
        if row["id"] == stray:
            row["set_type"] = "benchmark"
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert "not set_type='frozen_core'" in _failures(exc)
    assert stray in _failures(exc)


def test_preflight_1_rejects_mixed_versions(calibration):
    db = calibration["db"]
    for row in db.tables["prompt_versions"]:
        if row["id"] == calibration["prompt_ids"][0]:
            row["version"] = "fc-v1.1"
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert "more than one version" in _failures(exc)


def test_preflight_1_rejects_mixed_markets(calibration):
    db = calibration["db"]
    other_market = str(uuid.uuid4())
    for row in db.tables["prompt_versions"]:
        if row["id"] == calibration["prompt_ids"][0]:
            row["market_id"] = other_market
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert "more than one market_id" in _failures(exc)


def test_preflight_1_rejects_market_id_not_matching_prompts(calibration):
    with pytest.raises(PreflightError) as exc:
        _run(calibration, market_id=str(uuid.uuid4()))
    assert "does not match the prompt rows' market_id" in _failures(exc)


def test_preflight_2_rejects_set_below_eight(calibration):
    with pytest.raises(PreflightError) as exc:
        _run(calibration, prompt_version_ids=calibration["prompt_ids"][:7])
    assert "§7 requires 8-12" in _failures(exc)
    assert "has 7 prompts" in _failures(exc)


def test_preflight_2_rejects_set_above_twelve(calibration):
    db = calibration["db"]
    extra = []
    for n in range(3):
        pid = str(uuid.uuid4())
        extra.append(pid)
        db.seed(
            "prompt_versions",
            [
                {
                    "id": pid,
                    "set_type": "frozen_core",
                    "version": "fc-v1.0",
                    "prompt_text": f"extra {n}",
                    "intent_tier": "A",
                    "market_id": calibration["market_id"],
                    "is_holdout": False,
                }
            ],
        )
    with pytest.raises(PreflightError) as exc:
        _run(calibration, prompt_version_ids=calibration["prompt_ids"] + extra)
    assert "has 13 prompts" in _failures(exc)


def test_preflight_3_rejects_holdout_prompt(calibration):
    db = calibration["db"]
    holdout = calibration["prompt_ids"][2]
    for row in db.tables["prompt_versions"]:
        if row["id"] == holdout:
            row["is_holdout"] = True
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert "is_holdout" in _failures(exc)
    assert holdout in _failures(exc)


def test_preflight_4_rejects_non_calibration_property(calibration):
    calibration["db"].tables["properties"][0]["is_calibration_property"] = False
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert "is not flagged is_calibration_property" in _failures(exc)


def test_preflight_4_rejects_system_zero_property(calibration):
    calibration["db"].tables["properties"][0]["is_system_zero"] = True
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert "is_system_zero" in _failures(exc)


def test_preflight_4_rejects_unknown_property(calibration):
    with pytest.raises(PreflightError) as exc:
        _run(calibration, property_id=str(uuid.uuid4()))
    assert "does not exist" in _failures(exc)


@pytest.mark.parametrize(
    "column, label",
    [
        ("website_url", "website"),
        ("google_business_profile_url", "Google Business Profile"),
    ],
)
def test_preflight_5_rejects_missing_pre_0008_criterion(calibration, column, label):
    calibration["db"].tables["properties"][0][column] = None
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert f"criterion {label!r} unmet" in _failures(exc)


@pytest.mark.parametrize(
    "column, label",
    [
        ("review_presence_verified", "review presence"),
        ("third_party_reference_verified", "third-party reference"),
    ],
)
def test_preflight_5_rejects_unchecked_d053_criterion(calibration, column, label):
    """Null is 'nobody has looked yet' — the third state migration 0008 made
    the columns nullable to preserve. It must block, and say which."""
    calibration["db"].tables["properties"][0][column] = None
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert f"criterion {label!r} is unchecked" in _failures(exc)
    assert column in _failures(exc)


@pytest.mark.parametrize(
    "column, label",
    [
        ("review_presence_verified", "review presence"),
        ("third_party_reference_verified", "third-party reference"),
    ],
)
def test_preflight_5_rejects_verified_absent_criterion(calibration, column, label):
    """False is a different state from null and gets a different message: the
    property was examined and does not meet §8.4."""
    calibration["db"].tables["properties"][0][column] = False
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert f"criterion {label!r} was verified ABSENT" in _failures(exc)


@pytest.mark.parametrize(
    "flag, ref",
    [
        ("review_presence_verified", "review_presence_evidence_ref"),
        ("third_party_reference_verified", "third_party_reference_evidence_ref"),
    ],
)
def test_preflight_5_rejects_verified_true_without_evidence(calibration, flag, ref):
    """D-053's reason for existing: a bare boolean is an assertion with no
    artifact behind it."""
    calibration["db"].tables["properties"][0][ref] = None
    with pytest.raises(PreflightError) as exc:
        _run(calibration)
    assert "requires the artifact behind the claim" in _failures(exc)
    assert ref in _failures(exc)


def test_preflight_collects_every_failure_not_just_the_first(calibration):
    prop = calibration["db"].tables["properties"][0]
    prop["is_calibration_property"] = False
    prop["review_presence_verified"] = None
    with pytest.raises(PreflightError) as exc:
        _run(calibration, prompt_version_ids=calibration["prompt_ids"][:5])
    failures = exc.value.failures
    assert len(failures) >= 3
    joined = "\n".join(failures)
    assert "is_calibration_property" in joined
    assert "review presence" in joined
    assert "§7 requires" in joined


def test_preflight_failure_writes_nothing(calibration):
    db = calibration["db"]
    planner = FakePlanner(db)
    db.tables["properties"][0]["is_calibration_property"] = False
    with pytest.raises(PreflightError):
        _run(calibration, planner=planner, commit=True)
    assert db.tables.get("run_plans", []) == []
    assert db.tables.get("observations", []) == []
    assert planner.calls == []


# ---------------------------------------------------------------------------
# D-042 — google_ai is rejected with its reason, not a constraint error.
# ---------------------------------------------------------------------------


def test_google_ai_provider_is_rejected(calibration):
    with pytest.raises(PreflightError) as exc:
        _run(calibration, providers=["openai", "google_ai"])
    failures = _failures(exc)
    assert "google_ai" in failures
    assert "D-042" in failures
    assert "observations_google_ai_is_consumer_only" in failures


def test_google_ai_rejected_before_anything_is_written(calibration):
    db = calibration["db"]
    planner = FakePlanner(db)
    with pytest.raises(PreflightError):
        _run(calibration, planner=planner, providers=["google_ai"], commit=True)
    assert db.tables.get("run_plans", []) == []
    assert planner.calls == []


def test_default_providers_exclude_google_ai(calibration):
    plan = _run(calibration)
    assert "google_ai" not in plan.providers
    assert plan.providers == ("openai", "gemini", "perplexity", "anthropic")


def test_non_api_layer_is_rejected(calibration):
    with pytest.raises(PreflightError) as exc:
        _run(calibration, layer="consumer")
    assert "Layer A" in _failures(exc)
    assert "D-056" in _failures(exc)


# ---------------------------------------------------------------------------
# §3 re-invocation — reuse the run plan rather than planning a second set.
# ---------------------------------------------------------------------------


def test_second_invocation_reuses_the_existing_run_plan(calibration):
    db = calibration["db"]
    planner = FakePlanner(db)

    first = _run(calibration, planner=planner, commit=True)
    second = _run(calibration, planner=planner, commit=True)

    assert second.reused is True
    assert second.run_plan_id == first.run_plan_id
    # One run plan, not two — the whole point of §3's re-invocation rule.
    assert len(db.tables["run_plans"]) == 1
    # deterministic_task_id is scoped to run_plan_id, so reusing the plan means
    # the planner's upsert lands on the same 200 rows instead of adding 200 more.
    assert len(db.tables["observations"]) == 200
    assert len(planner.calls) == 2
    assert planner.calls[0][0] == planner.calls[1][0]


def test_dry_run_reports_the_plan_it_would_reuse(calibration):
    db = calibration["db"]
    planner = FakePlanner(db)
    first = _run(calibration, planner=planner, commit=True)

    plan = _run(calibration, planner=planner)

    assert plan.committed is False
    assert plan.reused is True
    assert plan.run_plan_id == first.run_plan_id
    assert len(planner.calls) == 1  # the dry run did not call the planner


def test_new_plan_flag_creates_a_deliberate_second_baseline(calibration):
    db = calibration["db"]
    planner = FakePlanner(db)
    first = _run(calibration, planner=planner, commit=True)

    second = _run(calibration, planner=planner, commit=True, new_plan=True)

    assert second.reused is False
    assert second.run_plan_id != first.run_plan_id
    assert len(db.tables["run_plans"]) == 2
    assert len(db.tables["observations"]) == 400


def test_a_different_prompt_set_does_not_reuse_the_plan(calibration):
    """Reuse is keyed on property + prompt set. A different set is a different
    instrument and must not land on the first set's plan."""
    db = calibration["db"]
    planner = FakePlanner(db)
    first = _run(calibration, planner=planner, commit=True)

    other_ids = []
    for n in range(10):
        pid = str(uuid.uuid4())
        other_ids.append(pid)
        db.seed(
            "prompt_versions",
            [
                {
                    "id": pid,
                    "set_type": "frozen_core",
                    "version": "fc-v2.0",
                    "prompt_text": f"v2 prompt {n}",
                    "intent_tier": "A",
                    "market_id": calibration["market_id"],
                    "is_holdout": False,
                }
            ],
        )

    second = _run(calibration, planner=planner, commit=True, prompt_version_ids=other_ids)

    assert second.reused is False
    assert second.run_plan_id != first.run_plan_id
    assert len(db.tables["run_plans"]) == 2


def test_reuse_refuses_a_conflicting_replicate_count(calibration):
    """§3 requires the plan row and the task list cannot disagree, so a reuse
    that would change n is refused rather than silently applied."""
    db = calibration["db"]
    planner = FakePlanner(db)
    _run(calibration, planner=planner, commit=True)

    with pytest.raises(PreflightError) as exc:
        _run(calibration, planner=planner, commit=True, replicate_count=3)
    assert "replicate_count" in _failures(exc)
    assert "new_plan=True" in _failures(exc)
    assert len(db.tables["run_plans"]) == 1


# ---------------------------------------------------------------------------
# §6.1 — the run window is written at plan time.
# ---------------------------------------------------------------------------


def test_window_start_and_end_are_written_six_hours_apart(calibration):
    db = calibration["db"]
    frozen = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    plan = _run(calibration, planner=FakePlanner(db), commit=True, now=frozen)

    row = db.tables["run_plans"][0]
    assert row["window_start"] is not None
    assert row["window_end"] is not None
    start = datetime.fromisoformat(row["window_start"])
    end = datetime.fromisoformat(row["window_end"])
    assert start == frozen
    assert (end - start).total_seconds() == MIN_WINDOW_HOURS * 3600
    # and reported back, since the value is unrecoverable after the fact
    assert plan.window_start == row["window_start"]
    assert plan.window_end == row["window_end"]


def test_window_below_the_six_hour_minimum_is_rejected(calibration):
    with pytest.raises(PreflightError) as exc:
        _run(calibration, window_hours=4)
    assert "§6.1 minimum" in _failures(exc)


def test_longer_window_is_honoured(calibration):
    db = calibration["db"]
    frozen = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    _run(calibration, planner=FakePlanner(db), commit=True, now=frozen, window_hours=12)
    row = db.tables["run_plans"][0]
    start = datetime.fromisoformat(row["window_start"])
    end = datetime.fromisoformat(row["window_end"])
    assert (end - start).total_seconds() == 12 * 3600
