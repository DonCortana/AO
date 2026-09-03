"""Layer B consumer run-plan creation — preflight, layer-scoped reuse, and
the single `run_plans` write.

Everything runs against the FakeDB double in conftest. Nothing touches the
live Samujana rows: the collision these tests are about is real (run plan
`41f71293…` is Layer A with the identical `frozen-core-samujana-v1` set that
Layer B asks for), so it is reconstructed in the fake rather than reached for
in production.

The reuse tests are the point of the module. D-081 moved layer membership
onto `run_plans.surface_layer`, and D-082 put the filter on both sides; these
assert the two directions separately, since the Layer B module originally
guarded only one of them. The Layer A direction lives in
tests/test_calibration_driver.py, next to the function that performs it.
"""

from __future__ import annotations

import uuid

import pytest

from atlas.calibration.consumer_run_plan import (
    CONSUMER_SURFACES,
    DEFAULT_REPLICATE_COUNT,
    SURFACE_LAYER,
    ConsumerPreflightError,
    ConsumerRunPlan,
    create_consumer_run_plan,
)

# The scope these tests plan at unless a test is about scope itself. Four
# surfaces rather than all five of CONSUMER_SURFACES: this is a stand-in for a
# real scope, and picking the whole vocabulary would make a test that should
# fail on a membership difference pass on the fact that nothing is left out.
SCOPE = ("openai", "gemini", "perplexity", "anthropic")

# Layer A's default (driver.DEFAULT_REPLICATE_COUNT). Named here because two
# tests turn on whether it agrees with Layer B's 3: per D-082, a disagreeing
# replicate_count is what makes the cross-layer collision fail loudly, so the
# test that proves the filter works must not be able to pass on the mismatch
# guard instead.
LAYER_A_REPLICATE_COUNT = 5


@pytest.fixture
def calibration(fake_db):
    """A property passing all four §8.4 criteria, plus a 10-prompt frozen_core
    set in one market — the shape both layers' preflight accepts."""
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
        [
            {
                "id": market_id,
                "property_id": property_id,
                "market_code": "TH",
                "language_code": "en",
            }
        ],
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


def _create(calibration, **overrides):
    kwargs = dict(
        property_id=calibration["property_id"],
        prompt_version_ids=calibration["prompt_ids"],
        market_id=calibration["market_id"],
        provider_scope=SCOPE,
    )
    kwargs.update(overrides)
    return create_consumer_run_plan(calibration["db"], **kwargs)


def _seed_layer_a_plan(calibration, *, replicate_count=LAYER_A_REPLICATE_COUNT):
    """The Samujana collision, reconstructed: a Layer A frozen_core plan for
    this property whose observations carry the identical prompt set Layer B is
    about to ask for."""
    db = calibration["db"]
    run_plan_id = str(uuid.uuid4())
    db.seed(
        "run_plans",
        [
            {
                "id": run_plan_id,
                "property_id": calibration["property_id"],
                "run_type": "frozen_core",
                "replicate_count": replicate_count,
                "status": "reconciled",
                "surface_layer": "api",
            }
        ],
    )
    db.seed(
        "observations",
        [
            {
                "id": str(uuid.uuid4()),
                "task_id": f"task-a-{n}",
                "run_plan_id": run_plan_id,
                "prompt_version_id": pid,
                "provider": "openai",
                "replicate_index": 0,
                "status": "complete",
                "surface_layer": "api",
            }
            for n, pid in enumerate(calibration["prompt_ids"])
        ],
    )
    return run_plan_id


def _seed_consumer_observations(calibration, run_plan_id, prompt_ids):
    """Layer B observations against a plan, as consumer_ingest would leave
    them once a capture sheet is ingested."""
    calibration["db"].seed(
        "observations",
        [
            {
                "id": str(uuid.uuid4()),
                "task_id": f"task-{run_plan_id}-{n}",
                "run_plan_id": run_plan_id,
                "prompt_version_id": pid,
                "provider": "openai",
                "replicate_index": 0,
                "status": "complete",
                "surface_layer": "consumer",
            }
            for n, pid in enumerate(prompt_ids)
        ],
    )


# ---------------------------------------------------------------------------
# Happy path, so the failure tests are known to fail for their own reason.
# ---------------------------------------------------------------------------


def test_commit_writes_one_consumer_run_plan(calibration):
    db = calibration["db"]

    plan = _create(calibration, commit=True)

    assert isinstance(plan, ConsumerRunPlan)
    assert plan.committed is True
    assert plan.reused is False
    assert plan.prompt_set_version == "fc-v1.0"

    rows = db.tables["run_plans"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == plan.run_plan_id
    assert row["run_type"] == "frozen_core"
    assert row["replicate_count"] == DEFAULT_REPLICATE_COUNT == 3
    assert row["status"] == "planned"
    # D-081: written explicitly by this module, never inherited from migration
    # 0010's 'api' default. Asserting the literal rather than SURFACE_LAYER so
    # the test still fails if the constant is redefined.
    assert row["surface_layer"] == "consumer"
    # D-062: retrospective window, never a placeholder at insert.
    assert row["window_start"] is None
    assert row["window_end"] is None

    # Insert-only: no observations are planned (consumer_ingest writes those).
    assert db.tables.get("observations", []) == []


def test_commit_false_writes_nothing(calibration):
    db = calibration["db"]

    plan = _create(calibration)

    assert plan.committed is False
    assert plan.reused is False
    assert plan.run_plan_id is None
    assert "dry run" in " ".join(plan.notes)
    assert db.tables.get("run_plans", []) == []
    assert db.tables.get("observations", []) == []


# ---------------------------------------------------------------------------
# Layer separation — D-081 / D-082. The Layer A direction is asserted in
# tests/test_calibration_driver.py.
# ---------------------------------------------------------------------------


def test_layer_a_plan_is_not_reusable_for_a_layer_b_create(calibration):
    """The Samujana collision. A Layer A plan whose observations carry exactly
    the prompt set Layer B asks for must not be handed to a consumer capture,
    even though the prompt-set match is perfect."""
    db = calibration["db"]
    layer_a_id = _seed_layer_a_plan(calibration)

    plan = _create(calibration, commit=True)

    assert plan.reused is False
    assert plan.run_plan_id != layer_a_id

    rows = {r["id"]: r for r in db.tables["run_plans"]}
    assert len(rows) == 2
    assert rows[layer_a_id]["surface_layer"] == "api"
    assert rows[layer_a_id]["replicate_count"] == LAYER_A_REPLICATE_COUNT
    assert rows[plan.run_plan_id]["surface_layer"] == "consumer"


def test_layer_a_plan_with_agreeing_replicate_count_is_still_not_reused(calibration):
    """D-082: with the live data the collision surfaces as a loud
    ConsumerPreflightError only because 41f71293's replicate_count of 5
    disagrees with Layer B's 3 — the silent merge needs the counts to agree.
    Here they agree, so the mismatch guard cannot fire and the layer filter is
    the only thing standing between a consumer capture and the API run's plan
    id."""
    db = calibration["db"]
    layer_a_id = _seed_layer_a_plan(calibration, replicate_count=3)

    plan = _create(calibration, commit=True, replicate_count=3)

    assert plan.reused is False
    assert plan.run_plan_id != layer_a_id
    assert len(db.tables["run_plans"]) == 2
    assert db.tables["run_plans"][1]["surface_layer"] == "consumer"


# ---------------------------------------------------------------------------
# Reuse within Layer B — the double-insert D-081 closed.
# ---------------------------------------------------------------------------


def test_back_to_back_commit_reuses_the_row_and_does_not_insert_twice(calibration):
    """The fault D-081 names first: this module plans no observations, so
    before 0010 the second invocation had nothing to match on and inserted a
    second row. The plan's own surface_layer is what makes it findable."""
    db = calibration["db"]

    first = _create(calibration, commit=True)
    second = _create(calibration, commit=True)

    assert first.reused is False
    assert second.reused is True
    assert second.run_plan_id == first.run_plan_id
    assert len(db.tables["run_plans"]) == 1
    assert "reusing existing Layer B run plan" in " ".join(second.notes)


def test_dry_run_reports_the_plan_it_would_reuse(calibration):
    db = calibration["db"]
    first = _create(calibration, commit=True)

    plan = _create(calibration)

    assert plan.committed is False
    assert plan.reused is True
    assert plan.run_plan_id == first.run_plan_id
    assert len(db.tables["run_plans"]) == 1


def test_new_plan_flag_forces_a_second_layer_b_plan(calibration):
    db = calibration["db"]
    first = _create(calibration, commit=True)

    second = _create(calibration, commit=True, new_plan=True)

    assert second.reused is False
    assert second.run_plan_id != first.run_plan_id
    assert len(db.tables["run_plans"]) == 2
    assert all(r["surface_layer"] == "consumer" for r in db.tables["run_plans"])


def test_partially_ingested_plan_is_still_found(calibration):
    """D-081's second fault: the observations match is set equality against the
    full prompt set, so a plan with only some captures ingested read as absent
    and got duplicated. The run_plans filter does not depend on the child rows
    at all."""
    db = calibration["db"]
    first = _create(calibration, commit=True)
    db.seed(
        "observations",
        [
            {
                "id": str(uuid.uuid4()),
                "task_id": f"task-b-{n}",
                "run_plan_id": first.run_plan_id,
                "prompt_version_id": pid,
                "provider": "openai",
                "replicate_index": 0,
                "status": "complete",
                "surface_layer": "consumer",
            }
            # Three of ten — a partial ingest.
            for n, pid in enumerate(calibration["prompt_ids"][:3])
        ],
    )

    second = _create(calibration, commit=True)

    assert second.reused is True
    assert second.run_plan_id == first.run_plan_id
    assert len(db.tables["run_plans"]) == 1


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_replicate_count_mismatch_raises(calibration):
    db = calibration["db"]
    _create(calibration, commit=True, replicate_count=3)

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, commit=True, replicate_count=4)

    message = "\n".join(exc.value.failures)
    assert "replicate_count is 3" in message
    assert "asked for 4" in message
    assert "new_plan=True" in message
    # Fail-closed: the refused invocation wrote nothing.
    assert len(db.tables["run_plans"]) == 1


def test_duplicate_prompt_version_ids_are_rejected(calibration):
    db = calibration["db"]
    duplicated = calibration["prompt_ids"][0]
    ids = calibration["prompt_ids"][:9] + [duplicated]

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, prompt_version_ids=ids, commit=True)

    message = "\n".join(exc.value.failures)
    assert "duplicates" in message
    assert duplicated in message
    assert db.tables.get("run_plans", []) == []


def test_preflight_failure_is_a_consumer_error_not_a_bare_preflight_error(calibration):
    """ConsumerPreflightError subclasses PreflightError so a caller can tell a
    Layer B failure from a Layer A one without parsing message text."""
    from atlas.calibration.driver import PreflightError

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, prompt_version_ids=calibration["prompt_ids"][:3])

    assert isinstance(exc.value, PreflightError)
    assert "§7 requires" in "\n".join(exc.value.failures)


def test_surface_layer_constant_matches_migration_0005_vocabulary():
    """The CHECK on both tables admits exactly 'api' and 'consumer'. A typo
    here would fail at the database, not in preflight."""
    assert SURFACE_LAYER == "consumer"


def test_keyed_match_ignores_child_observations_entirely(calibration):
    """D-084: once a plan records its prompt set, the children stop being
    evidence. A stray observation carrying a prompt outside the set does not
    unmatch the plan — under the old set-equality match it would have."""
    db = calibration["db"]
    first = _create(calibration, commit=True)
    db.seed(
        "observations",
        [
            {
                "id": str(uuid.uuid4()),
                "task_id": "task-foreign-0",
                "run_plan_id": first.run_plan_id,
                "prompt_version_id": str(uuid.uuid4()),
                "provider": "openai",
                "replicate_index": 0,
                "status": "complete",
                "surface_layer": "consumer",
            }
        ],
    )

    second = _create(calibration, commit=True)

    assert second.reused is True
    assert second.run_plan_id == first.run_plan_id
    assert len(db.tables["run_plans"]) == 1


def test_insert_records_the_prompt_set_version(calibration):
    db = calibration["db"]

    plan = _create(calibration, commit=True)

    row = db.tables["run_plans"][0]
    assert row["prompt_set_version"] == "fc-v1.0" == plan.prompt_set_version


def test_a_plan_recording_a_different_version_is_not_reused(calibration):
    """A row that recorded a *different* version has answered the question and
    is not a fallback candidate, even when its observations match the set
    being asked for."""
    db = calibration["db"]
    other_plan_id = str(uuid.uuid4())
    db.seed(
        "run_plans",
        [
            {
                "id": other_plan_id,
                "property_id": calibration["property_id"],
                "run_type": "frozen_core",
                "replicate_count": DEFAULT_REPLICATE_COUNT,
                "status": "planned",
                "surface_layer": "consumer",
                "provider_scope": list(SCOPE),
                "market_id": calibration["market_id"],
                "prompt_set_version": "fc-v0.9",
            }
        ],
    )
    _seed_consumer_observations(calibration, other_plan_id, calibration["prompt_ids"])

    plan = _create(calibration, commit=True)

    assert plan.reused is False
    assert plan.run_plan_id != other_plan_id
    assert len(db.tables["run_plans"]) == 2


# ---------------------------------------------------------------------------
# The null-fallback branch. run_plans.prompt_set_version is nullable and
# migration 0011 backfilled both live frozen_core rows, so this path has no
# production coverage — these tests are the only thing exercising it.
# ---------------------------------------------------------------------------


def test_null_version_plan_is_reused_via_the_observations_fallback(calibration):
    """A plan predating migration 0011 records no version. It must still be
    found by the old set-equality match rather than silently unmatched, which
    would insert a duplicate against it."""
    db = calibration["db"]
    legacy_id = str(uuid.uuid4())
    db.seed(
        "run_plans",
        [
            {
                "id": legacy_id,
                "property_id": calibration["property_id"],
                "run_type": "frozen_core",
                "replicate_count": DEFAULT_REPLICATE_COUNT,
                "status": "planned",
                "surface_layer": "consumer",
                "provider_scope": list(SCOPE),
                "market_id": calibration["market_id"],
                "prompt_set_version": None,
            }
        ],
    )
    _seed_consumer_observations(calibration, legacy_id, calibration["prompt_ids"])

    plan = _create(calibration, commit=True)

    assert plan.reused is True
    assert plan.run_plan_id == legacy_id
    assert len(db.tables["run_plans"]) == 1


def test_null_version_plan_with_a_different_set_is_not_reused(calibration):
    """The fallback is still set equality, so a null-version plan carrying a
    different prompt set does not match."""
    db = calibration["db"]
    legacy_id = str(uuid.uuid4())
    db.seed(
        "run_plans",
        [
            {
                "id": legacy_id,
                "property_id": calibration["property_id"],
                "run_type": "frozen_core",
                "replicate_count": DEFAULT_REPLICATE_COUNT,
                "status": "planned",
                "surface_layer": "consumer",
                "provider_scope": list(SCOPE),
                "market_id": calibration["market_id"],
                "prompt_set_version": None,
            }
        ],
    )
    _seed_consumer_observations(calibration, legacy_id, [str(uuid.uuid4()) for _ in range(10)])

    plan = _create(calibration, commit=True)

    assert plan.reused is False
    assert plan.run_plan_id != legacy_id
    assert len(db.tables["run_plans"]) == 2


def test_null_version_plan_with_no_observations_is_not_reused(calibration):
    """The fault D-084 closed, in the one shape that still exists: a null row
    with nothing ingested is unmatchable. Recorded so the fallback's remaining
    limit is visible rather than assumed away — it is the reason the column is
    written at insert."""
    db = calibration["db"]
    legacy_id = str(uuid.uuid4())
    db.seed(
        "run_plans",
        [
            {
                "id": legacy_id,
                "property_id": calibration["property_id"],
                "run_type": "frozen_core",
                "replicate_count": DEFAULT_REPLICATE_COUNT,
                "status": "planned",
                "surface_layer": "consumer",
                "provider_scope": list(SCOPE),
                "market_id": calibration["market_id"],
                "prompt_set_version": None,
            }
        ],
    )

    plan = _create(calibration, commit=True)

    assert plan.reused is False
    assert len(db.tables["run_plans"]) == 2


def test_a_keyed_plan_wins_over_a_matching_null_plan(calibration):
    """Both paths are live at once when a null row and a keyed row coexist.
    The keyed row is preferred; the fallback is consulted only when no keyed
    row exists at all."""
    db = calibration["db"]
    legacy_id = str(uuid.uuid4())
    db.seed(
        "run_plans",
        [
            {
                "id": legacy_id,
                "property_id": calibration["property_id"],
                "run_type": "frozen_core",
                "replicate_count": DEFAULT_REPLICATE_COUNT,
                "status": "planned",
                "surface_layer": "consumer",
                "provider_scope": list(SCOPE),
                "market_id": calibration["market_id"],
                "prompt_set_version": None,
            }
        ],
    )
    _seed_consumer_observations(calibration, legacy_id, calibration["prompt_ids"])
    keyed = _create(calibration, commit=True, new_plan=True)

    reused = _create(calibration, commit=True)

    assert reused.reused is True
    assert reused.run_plan_id == keyed.run_plan_id != legacy_id


def test_an_ambiguous_key_is_refused_not_resolved(calibration):
    """Layer B side of the same rule. new_plan=True is what makes two plans at
    one key possible, so the ambiguity it creates is refused rather than
    silently resolved toward the first."""
    db = calibration["db"]
    first = _create(calibration, commit=True)
    second = _create(calibration, commit=True, new_plan=True)

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, commit=True)

    message = "\n".join(exc.value.failures)
    assert first.run_plan_id in message
    assert second.run_plan_id in message
    assert "run_plan_id" in message
    # Fail-closed: no third row.
    assert len(db.tables["run_plans"]) == 2


def test_run_plan_id_resolves_an_ambiguous_key(calibration):
    db = calibration["db"]
    _create(calibration, commit=True)
    wanted = _create(calibration, commit=True, new_plan=True)

    plan = _create(calibration, commit=True, run_plan_id=wanted.run_plan_id)

    assert plan.reused is True
    assert plan.run_plan_id == wanted.run_plan_id
    assert len(db.tables["run_plans"]) == 2


def test_run_plan_id_naming_a_layer_a_plan_is_refused(calibration):
    """The named plan still has to be one this invocation could have reused —
    naming it is not a way past the D-082 layer filter."""
    layer_a_id = _seed_layer_a_plan(calibration, replicate_count=DEFAULT_REPLICATE_COUNT)
    calibration["db"].tables["run_plans"][0]["prompt_set_version"] = "fc-v1.0"

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, commit=True, run_plan_id=layer_a_id)

    assert "surface_layer='api'" in "\n".join(exc.value.failures)


def test_run_plan_id_and_new_plan_together_are_refused(calibration):
    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, run_plan_id=str(uuid.uuid4()), new_plan=True)
    assert "mutually exclusive" in "\n".join(exc.value.failures)


# ---------------------------------------------------------------------------
# Manifest fields — provider_scope and market_id (D-086/D-087/D-089/D-090,
# migration 0012). Two VALIDATED CHECK constraints reject a frozen_core plan
# with either column null, and FakeDB emulates no constraint at all (D-094),
# so what these prove is that the module writes the values — not that the
# database would have refused it. The negative half of that is a live test.
# ---------------------------------------------------------------------------


def test_insert_records_provider_scope_and_market_id(calibration):
    db = calibration["db"]

    plan = _create(calibration, commit=True)

    row = db.tables["run_plans"][0]
    assert row["provider_scope"] == list(SCOPE)
    assert row["market_id"] == calibration["market_id"]
    assert plan.provider_scope == SCOPE
    assert plan.market_id == calibration["market_id"]


def test_provider_scope_is_written_as_a_list_not_a_tuple(calibration):
    """The column is text[] and the Supabase client JSON-encodes the value;
    a tuple is not JSON."""
    db = calibration["db"]
    _create(calibration, commit=True, provider_scope=("openai", "gemini"))

    assert db.tables["run_plans"][0]["provider_scope"] == ["openai", "gemini"]


def test_dry_run_reports_the_scope_and_market_it_would_write(calibration):
    plan = _create(calibration, commit=False)

    assert plan.committed is False
    assert plan.provider_scope == SCOPE
    assert plan.market_id == calibration["market_id"]


def test_empty_provider_scope_is_refused(calibration):
    """Migration 0012's CHECK tests for NULL only, so an empty array satisfies
    the database and would be recorded as this plan's scope."""
    db = calibration["db"]

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, commit=True, provider_scope=())

    assert "provider_scope is empty" in "\n".join(exc.value.failures)
    assert db.tables.get("run_plans", []) == []


def test_provider_scope_outside_the_migration_0005_vocabulary_is_refused(calibration):
    db = calibration["db"]

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, commit=True, provider_scope=("openai", "chatgpt"))

    message = "\n".join(exc.value.failures)
    assert "chatgpt" in message
    assert "openai" not in message.split("outside")[0]
    assert db.tables.get("run_plans", []) == []


def test_duplicate_surfaces_in_provider_scope_are_refused(calibration):
    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, commit=True, provider_scope=("openai", "openai"))

    assert "provider_scope contains duplicates" in "\n".join(exc.value.failures)


def test_google_ai_is_legal_in_a_layer_b_scope(calibration):
    """Consumer-only under observations_google_ai_is_consumer_only (D-042/
    D-043), so it is legal here and would be illegal at Layer A. Whether it is
    *pairable* at the §8.4 gate is D-090's parity question, not this module's."""
    db = calibration["db"]

    _create(calibration, commit=True, provider_scope=("openai", "google_ai"))

    assert db.tables["run_plans"][0]["provider_scope"] == ["openai", "google_ai"]


def test_every_consumer_surface_constant_is_acceptable(calibration):
    _create(calibration, commit=True, provider_scope=CONSUMER_SURFACES)


def test_consumer_surfaces_matches_migration_0005_vocabulary():
    assert set(CONSUMER_SURFACES) == {
        "openai",
        "gemini",
        "perplexity",
        "anthropic",
        "google_ai",
    }


# ---------------------------------------------------------------------------
# Reuse must agree on the manifest, not on replicate_count alone
# ---------------------------------------------------------------------------


def test_reuse_with_a_different_provider_scope_is_refused(calibration):
    db = calibration["db"]
    _create(calibration, commit=True, provider_scope=("openai", "gemini"))

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, commit=True, provider_scope=("openai", "anthropic"))

    message = "\n".join(exc.value.failures)
    assert "provider_scope is [gemini, openai]" in message
    assert "asked for [anthropic, openai]" in message
    assert len(db.tables["run_plans"]) == 1


def test_reuse_ignores_provider_scope_ordering(calibration):
    """The column is a set of surfaces, not a sequence — an ordering
    difference is not a disagreement."""
    db = calibration["db"]
    first = _create(calibration, commit=True, provider_scope=("openai", "gemini"))

    second = _create(calibration, commit=True, provider_scope=("gemini", "openai"))

    assert second.reused is True
    assert second.run_plan_id == first.run_plan_id
    assert len(db.tables["run_plans"]) == 1


def test_a_plan_with_a_null_provider_scope_is_not_silently_reused(calibration):
    """A pre-migration-0012 row. D-098 deleted the only live one rather than
    backfilling it, because its correct scope was not recoverable — so a null
    here means "nobody can say", which is not agreement."""
    db = calibration["db"]
    legacy_id = str(uuid.uuid4())
    db.seed(
        "run_plans",
        [
            {
                "id": legacy_id,
                "property_id": calibration["property_id"],
                "run_type": "frozen_core",
                "replicate_count": DEFAULT_REPLICATE_COUNT,
                "status": "planned",
                "surface_layer": "consumer",
                "provider_scope": None,
                "market_id": None,
                "prompt_set_version": "fc-v1.0",
            }
        ],
    )

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(calibration, commit=True)

    message = "\n".join(exc.value.failures)
    assert "provider_scope is [null]" in message
    assert len(db.tables["run_plans"]) == 1


def test_every_manifest_disagreement_is_reported_at_once(calibration):
    """Fail-closed in the same shape as preflight itself: one re-run shows the
    whole disagreement rather than one field per attempt."""
    _create(
        calibration,
        commit=True,
        replicate_count=3,
        provider_scope=("openai", "gemini"),
    )

    with pytest.raises(ConsumerPreflightError) as exc:
        _create(
            calibration,
            commit=True,
            replicate_count=4,
            provider_scope=("anthropic",),
        )

    message = "\n".join(exc.value.failures)
    assert "replicate_count is 3" in message
    assert "provider_scope is [gemini, openai]" in message


def test_new_plan_bypasses_the_manifest_guard(calibration):
    """A deliberate second Layer B run at a different scope is what
    new_plan=True is for; the guard governs reuse, not creation."""
    db = calibration["db"]
    _create(calibration, commit=True, provider_scope=("openai",))

    plan = _create(
        calibration, commit=True, new_plan=True, provider_scope=("anthropic",)
    )

    assert plan.reused is False
    assert len(db.tables["run_plans"]) == 2
    assert db.tables["run_plans"][1]["provider_scope"] == ["anthropic"]
