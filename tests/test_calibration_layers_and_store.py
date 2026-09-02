"""Layer separation (D-043) and the eligibility store (D-044).

The failure these tests exist to prevent is the silent one: a Layer B human
capture read as an extra Layer A replicate and averaged into a client-facing
AVS. It raises no error and has no symptom — only a wrong number.
"""

from __future__ import annotations

import uuid

import pytest

from atlas.calibration.loader import load_cells
from atlas.calibration.run import run_gate
from atlas.calibration.store import eligible_platforms, write_calibration_run
from atlas.calibration.types import (
    LAYER_API,
    LAYER_CONSUMER,
    CalibrationRun,
    Contingency,
    KappaStability,
    PassRoute,
    PlatformAgreement,
    PlatformGateResult,
    Verdict,
)
from atlas.scoring import compute_avs, load_period

EXACT = 1e-12


def observation(run_plan_id, prompt_version_id, provider, index, layer, oid=None):
    return {
        "id": oid or str(uuid.uuid4()),
        "run_plan_id": run_plan_id,
        "prompt_version_id": prompt_version_id,
        "provider": provider,
        "replicate_index": index,
        "status": "complete",
        "grounding_status": "grounded",
        "surface_layer": layer,
    }


def recommendation(observation_id, rpv, outcome_type, rank=None, conflict=False):
    return {
        "observation_id": observation_id,
        "rpv": rpv,
        "rank": rank,
        "outcome_type": outcome_type,
        "is_client_entity": True,
        "entity_conflict": conflict,
    }


# ---------------------------------------------------------------------
# D-043 — the silent contamination this filter prevents
# ---------------------------------------------------------------------


def test_consumer_layer_rows_are_never_scored(fake_db):
    """One prompt, one platform. Layer A says rank 1 twice (RPV 1.00). Layer B
    says absent twice. Both sit in the same table under provider='openai'.

    Correct: AVS 100.0 — Layer B is not a replicate of Layer A.
    Contaminated: (1.00 + 1.00 + 0.00 + 0.00) / 4 = 0.50 -> AVS 50.0.

    A 50-point error, from human evidence being mistaken for the instrument.
    """
    run_plan_id = str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())
    fake_db.seed("prompt_versions", [{"id": prompt_version_id, "intent_tier": "A"}])

    api_rows = [
        observation(run_plan_id, prompt_version_id, "openai", i, LAYER_API) for i in range(2)
    ]
    consumer_rows = [
        observation(run_plan_id, prompt_version_id, "openai", i, LAYER_CONSUMER)
        for i in range(2)
    ]
    fake_db.seed("observations", api_rows + consumer_rows)
    fake_db.seed(
        "recommendations",
        [recommendation(r["id"], 1.00, "ranked", rank=1) for r in api_rows]
        + [recommendation(r["id"], 0.00, "absent") for r in consumer_rows],
    )

    period = load_period(fake_db, run_plan_id, "baseline")
    assert len(period.observations) == 2
    assert period.planned_observation_count == 2  # Layer A task list only (D-039)
    assert compute_avs(period.observations, ["openai"]).avs == pytest.approx(100.0, abs=EXACT)


def test_calibration_loader_reads_the_consumer_layer(fake_db):
    run_plan_id = str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())
    rows = [
        observation(run_plan_id, prompt_version_id, "openai", i, LAYER_CONSUMER)
        for i in range(3)
    ]
    fake_db.seed("observations", rows)
    fake_db.seed(
        "recommendations",
        [recommendation(r["id"], 1.00, "ranked", rank=1) for r in rows[:2]]
        + [recommendation(rows[2]["id"], 0.00, "absent")],
    )

    cells = load_cells(fake_db, [run_plan_id], layer=LAYER_CONSUMER)
    assert set(cells) == {"openai"}
    (judgment,) = cells["openai"]
    assert judgment.mentioned is True  # 2 of 3
    assert judgment.replicate_count == 3
    assert judgment.rank == 1.0


def test_source_only_mention_is_not_a_mention(fake_db):
    """§4.1: source-only citations 'are not recommendations'. Counting one as
    presence would let a citation on one layer agree with a recommendation on
    the other and inflate the gate."""
    run_plan_id = str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())
    rows = [
        observation(run_plan_id, prompt_version_id, "openai", i, LAYER_API) for i in range(3)
    ]
    fake_db.seed("observations", rows)
    fake_db.seed(
        "recommendations",
        [recommendation(r["id"], 0.00, "source_only_mention") for r in rows],
    )
    cells = load_cells(fake_db, [run_plan_id], layer=LAYER_API)
    assert cells["openai"][0].mentioned is False


def test_entity_conflict_rows_are_excluded_from_the_frame(fake_db):
    """§4.1 excludes entity conflicts rather than scoring them; they are
    equally not a presence judgment."""
    run_plan_id = str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())
    rows = [
        observation(run_plan_id, prompt_version_id, "openai", i, LAYER_API) for i in range(3)
    ]
    fake_db.seed("observations", rows)
    fake_db.seed(
        "recommendations",
        [recommendation(r["id"], 0.00, "entity_conflict", conflict=True) for r in rows],
    )
    assert load_cells(fake_db, [run_plan_id], layer=LAYER_API) == {}


def test_unparsed_observation_is_not_read_as_absent(fake_db):
    """D-034 carried into the gate: an observation with no recommendation row
    is 'not yet parsed', never a measured non-mention."""
    run_plan_id = str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())
    rows = [
        observation(run_plan_id, prompt_version_id, "openai", i, LAYER_API) for i in range(3)
    ]
    fake_db.seed("observations", rows)
    fake_db.seed("recommendations", [recommendation(rows[0]["id"], 1.00, "ranked", rank=1)])

    cells = load_cells(fake_db, [run_plan_id], layer=LAYER_API)
    judgment = cells["openai"][0]
    # One parsed replicate, mentioned. Had the two unparsed rows been read as
    # non-mentions, the majority would have flipped to absent.
    assert judgment.replicate_count == 1
    assert judgment.mentioned is True


# ---------------------------------------------------------------------
# D-044 — the eligibility store
# ---------------------------------------------------------------------


def gate_result(platform, verdict, route=None, reviewer=None):
    return PlatformGateResult(
        agreement=PlatformAgreement(
            platform=platform,
            n_paired_units=10,
            raw_agreement=0.9,
            cohen_kappa=0.8,
            kappa_stability=KappaStability.STABLE,
            kappa_note="pe=0.5, n=10",
            co_mention_count=10,
            spearman_rho=0.7,
            contingency=Contingency(5, 1, 0, 4),
        ),
        verdict=verdict,
        pass_route=route,
        reviewer=reviewer,
        notes="",
    )


def test_write_then_read_eligible_platforms(fake_db):
    property_id, market_id = str(uuid.uuid4()), str(uuid.uuid4())
    run = CalibrationRun(
        calibration_run_id="cal-2026-08-27-a",
        property_id=property_id,
        market_id=market_id,
        prompt_set_version="accommodation-th-en-v1.0",
        results=(
            gate_result("openai", Verdict.ELIGIBLE, PassRoute.KAPPA),
            gate_result("anthropic", Verdict.ELIGIBLE, PassRoute.KAPPA),
            gate_result("perplexity", Verdict.EVIDENCE_ONLY),
            gate_result("google_ai", Verdict.EVIDENCE_ONLY),
        ),
    )
    written = write_calibration_run(fake_db, run)
    assert len(written) == 4
    assert eligible_platforms(fake_db, property_id, market_id) == ("anthropic", "openai")


def test_latest_run_supersedes_without_erasing(fake_db):
    """Append-only: a re-run writes new rows; the superseded result stays."""
    property_id, market_id = str(uuid.uuid4()), str(uuid.uuid4())
    common = {
        "property_id": property_id,
        "market_id": market_id,
        "prompt_set_version": "accommodation-th-en-v1.0",
    }
    write_calibration_run(
        fake_db,
        CalibrationRun(
            calibration_run_id="cal-01",
            results=(gate_result("openai", Verdict.ELIGIBLE, PassRoute.KAPPA),),
            **common,
        ),
    )
    write_calibration_run(
        fake_db,
        CalibrationRun(
            calibration_run_id="cal-02",
            results=(
                gate_result("openai", Verdict.EVIDENCE_ONLY),
                gate_result("gemini", Verdict.ELIGIBLE, PassRoute.KAPPA),
            ),
            **common,
        ),
    )
    assert eligible_platforms(fake_db, property_id, market_id) == ("gemini",)
    assert len(fake_db.tables["calibration_results"]) == 3  # nothing overwritten


def test_no_calibration_raises_rather_than_returning_empty(fake_db):
    with pytest.raises(ValueError, match="gate has not been run"):
        eligible_platforms(fake_db, str(uuid.uuid4()), str(uuid.uuid4()))


def test_all_platforms_evidence_only_is_not_an_avs_of_zero(fake_db):
    property_id, market_id = str(uuid.uuid4()), str(uuid.uuid4())
    write_calibration_run(
        fake_db,
        CalibrationRun(
            calibration_run_id="cal-01",
            property_id=property_id,
            market_id=market_id,
            prompt_set_version="v1",
            results=(gate_result("openai", Verdict.EVIDENCE_ONLY),),
        ),
    )
    with pytest.raises(ValueError, match="never an AVS of 0"):
        eligible_platforms(fake_db, property_id, market_id)


def test_empty_run_is_refused(fake_db):
    with pytest.raises(ValueError, match="no platform results"):
        write_calibration_run(
            fake_db,
            CalibrationRun(
                calibration_run_id="cal-01",
                property_id=str(uuid.uuid4()),
                market_id=str(uuid.uuid4()),
                prompt_set_version="v1",
            ),
        )


# ---------------------------------------------------------------------
# D-042 — Google AI through the whole pipeline
# ---------------------------------------------------------------------


def test_google_ai_is_recorded_as_structurally_undefined(fake_db):
    """Its exclusion must be a stored, dated fact — not a silent absence."""
    api_plan, consumer_plan = str(uuid.uuid4()), str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())

    api_rows = [
        observation(api_plan, prompt_version_id, "openai", i, LAYER_API) for i in range(3)
    ]
    consumer_rows = [
        observation(consumer_plan, prompt_version_id, "openai", i, LAYER_CONSUMER)
        for i in range(3)
    ] + [
        observation(consumer_plan, prompt_version_id, "google_ai", i, LAYER_CONSUMER)
        for i in range(3)
    ]
    fake_db.seed("observations", api_rows + consumer_rows)
    fake_db.seed(
        "recommendations",
        [recommendation(r["id"], 1.00, "ranked", rank=1) for r in api_rows + consumer_rows],
    )
    # D-085: run_gate reads the plans to derive prompt_set_version, so the rows
    # the observations point at have to exist here as they do behind the real
    # foreign key. Both record the version the gate is being told to stamp.
    fake_db.seed(
        "run_plans",
        [
            {
                "id": api_plan,
                "run_type": "frozen_core",
                "surface_layer": LAYER_API,
                "prompt_set_version": "accommodation-th-en-v1.0",
            },
            {
                "id": consumer_plan,
                "run_type": "frozen_core",
                "surface_layer": LAYER_CONSUMER,
                "prompt_set_version": "accommodation-th-en-v1.0",
            },
        ],
    )

    run = run_gate(
        fake_db,
        calibration_run_id="cal-01",
        property_id=str(uuid.uuid4()),
        market_id=str(uuid.uuid4()),
        prompt_set_version="accommodation-th-en-v1.0",
        api_run_plan_ids=[api_plan],
        consumer_run_plan_ids=[consumer_plan],
    )

    by_platform = {r.platform: r for r in run.results}
    assert set(by_platform) == {"openai", "google_ai"}

    google = by_platform["google_ai"]
    assert google.verdict is Verdict.EVIDENCE_ONLY
    assert google.agreement.n_paired_units == 0
    assert "D-042" in google.notes
    assert "undefined for it rather than failed" in google.notes
    assert "google_ai" not in run.eligible_platforms


# ---------------------------------------------------------------------
# D-085 — the gate derives prompt_set_version from its plans and refuses
# on disagreement. Guards a permanent wrong provenance stamp on an
# append-only, score-bearing row (calibration_results, §9).
# ---------------------------------------------------------------------


def _gate_fixture(fake_db, *, api_version, consumer_version, seed_plans=True):
    api_plan, consumer_plan = str(uuid.uuid4()), str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())
    api_rows = [
        observation(api_plan, prompt_version_id, "openai", i, LAYER_API) for i in range(3)
    ]
    consumer_rows = [
        observation(consumer_plan, prompt_version_id, "openai", i, LAYER_CONSUMER)
        for i in range(3)
    ]
    fake_db.seed("observations", api_rows + consumer_rows)
    fake_db.seed(
        "recommendations",
        [recommendation(r["id"], 1.00, "ranked", rank=1) for r in api_rows + consumer_rows],
    )
    if seed_plans:
        fake_db.seed(
            "run_plans",
            [
                {
                    "id": api_plan,
                    "run_type": "frozen_core",
                    "surface_layer": LAYER_API,
                    "prompt_set_version": api_version,
                },
                {
                    "id": consumer_plan,
                    "run_type": "frozen_core",
                    "surface_layer": LAYER_CONSUMER,
                    "prompt_set_version": consumer_version,
                },
            ],
        )
    return api_plan, consumer_plan


def _run_gate(fake_db, api_plan, consumer_plan, claimed):
    return run_gate(
        fake_db,
        calibration_run_id="cal-01",
        property_id=str(uuid.uuid4()),
        market_id=str(uuid.uuid4()),
        prompt_set_version=claimed,
        api_run_plan_ids=[api_plan],
        consumer_run_plan_ids=[consumer_plan],
        persist=False,
    )


def test_gate_accepts_a_version_its_plans_agree_with(fake_db):
    plans = _gate_fixture(fake_db, api_version="v1.0", consumer_version="v1.0")
    run = _run_gate(fake_db, *plans, "v1.0")
    assert run.prompt_set_version == "v1.0"


def test_gate_refuses_a_version_its_plans_contradict(fake_db):
    """The typo case. Nothing downstream would have noticed."""
    plans = _gate_fixture(fake_db, api_version="v1.0", consumer_version="v1.0")
    with pytest.raises(ValueError) as exc:
        _run_gate(fake_db, *plans, "v1.O")
    assert "record 'v1.0'" in str(exc.value)
    assert "D-085" in str(exc.value)


def test_gate_refuses_when_the_two_layers_measure_different_sets(fake_db):
    """A paired §8.4 comparison between plans measuring different prompt sets
    is not a comparison at all."""
    plans = _gate_fixture(fake_db, api_version="v1.0", consumer_version="v2.0")
    with pytest.raises(ValueError) as exc:
        _run_gate(fake_db, *plans, "v1.0")
    assert "more than one prompt_set_version" in str(exc.value)


def test_gate_refuses_a_plan_with_no_recorded_version(fake_db):
    """D-084's column is nullable. The gate cannot derive what was never
    recorded, so it refuses rather than falling back to the argument."""
    plans = _gate_fixture(fake_db, api_version="v1.0", consumer_version=None)
    with pytest.raises(ValueError) as exc:
        _run_gate(fake_db, *plans, "v1.0")
    assert "record no prompt_set_version" in str(exc.value)


def test_gate_refuses_a_run_plan_id_that_does_not_exist(fake_db):
    plans = _gate_fixture(
        fake_db, api_version="v1.0", consumer_version="v1.0", seed_plans=False
    )
    with pytest.raises(ValueError) as exc:
        _run_gate(fake_db, *plans, "v1.0")
    assert "do not exist" in str(exc.value)


def test_gate_checks_the_version_before_writing_anything(fake_db):
    """Fail-closed: the refusal lands before load_cells, the gate maths and
    write_calibration_run, so a contradicted call leaves no partial record."""
    plans = _gate_fixture(fake_db, api_version="v1.0", consumer_version="v1.0")
    with pytest.raises(ValueError):
        run_gate(
            fake_db,
            calibration_run_id="cal-01",
            property_id=str(uuid.uuid4()),
            market_id=str(uuid.uuid4()),
            prompt_set_version="wrong",
            api_run_plan_ids=[plans[0]],
            consumer_run_plan_ids=[plans[1]],
            persist=True,
        )
    assert fake_db.tables.get("calibration_results", []) == []


def test_gate_refuses_a_blank_recorded_version(fake_db):
    """'' is a different mistake from NULL — something wrote an empty version
    rather than nobody writing one — and both are refused."""
    plans = _gate_fixture(fake_db, api_version="v1.0", consumer_version="   ")
    with pytest.raises(ValueError) as exc:
        _run_gate(fake_db, *plans, "v1.0")
    assert "record no prompt_set_version" in str(exc.value)
