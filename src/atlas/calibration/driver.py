"""Plan one Layer A Frozen Core run for one calibration property.

D-056 / calibration-run-driver-DESIGN.md §§1-3. The minimal thing standing
between a finalized Frozen Core prompt set and a §8.4 gate result:

    preflight (§2)
      -> reuse or insert one run_plans row   [this module writes]
      -> plan_run(...)                       [the planner writes observations]
      -> return the run_plan_id

It is deliberately not a run orchestrator. It knows nothing about when to run,
how often, at what cost, or for whom — see §4 for the boundary against
ATLAS_BACKLOG.md P0-06, and §4.1 for the two sequencing problems the real
backlog exposes.

**Layer A only.** `plan_run` writes no `surface_layer`, so every row it plans
takes migration 0005's default of `'api'`. Layer B (consumer-surface) capture
is human-initiated (§8.3) and has no `run_plans` creation path anywhere in the
codebase. That is out of scope here by design and unresolved as ownership —
`atlas.calibration.run.run_gate` needs `consumer_run_plan_ids` alongside the
api ids, so this driver alone does not reach a gate result (D-056).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from atlas.planner.run_planner import plan_run

# The four API adapters. `google_ai` is deliberately absent and is rejected
# rather than ignored — see D-042 and _check_providers below.
DEFAULT_PROVIDERS = ("openai", "gemini", "perplexity", "anthropic")

# D-042 / migration 0005: Google Search AI Overviews has no API surface, and
# `observations_google_ai_is_consumer_only` rejects an 'api' row for it. The
# planner cannot express a consumer row, so this is unplannable here.
CONSUMER_ONLY_PROVIDERS = frozenset({"google_ai"})

# Methodology §6.1: "Frozen Core baseline / validation — n=5 per
# prompt-platform-market". The default is the methodology's; an override is
# recorded on the run plan, never silent.
DEFAULT_REPLICATE_COUNT = 5

# Methodology §7 Frozen Core set size.
MIN_PROMPT_SET_SIZE = 8
MAX_PROMPT_SET_SIZE = 12

# Methodology §6.1 minimum six-hour run window for Frozen Core replicates.
# Written onto the run plan because the value is unrecoverable after the fact;
# enforcing it is P1-02's job, not this driver's (§4).
MIN_WINDOW_HOURS = 6

RUN_TYPE = "frozen_core"
LAYER_API = "api"


class PreflightError(Exception):
    """Preflight refused the run. Carries every failure, not just the first.

    Fail-closed: nothing is written unless every §2 check passes. All failures
    are collected so an operator fixing a prompt set sees the whole list in one
    pass rather than rediscovering it one run at a time.
    """

    def __init__(self, failures: list[str]):
        self.failures = failures
        super().__init__(
            "calibration run preflight failed:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )


@dataclass(frozen=True)
class CalibrationPlan:
    """What the driver planned, or would plan on a dry run.

    `run_plan_id` is the value §3 names as the entire output — it is what
    `run_gate` needs as `api_run_plan_ids=[...]`. It is None on a dry run that
    would have created a new plan, and set on a dry run that would have reused
    one, because "which plan would this touch" is the question a dry run exists
    to answer.
    """

    run_plan_id: str | None
    property_id: str
    market_id: str
    prompt_set_version: str
    prompt_version_ids: tuple[str, ...]
    providers: tuple[str, ...]
    replicate_count: int
    planned_observations: int
    reused: bool
    committed: bool
    window_start: str | None = None
    window_end: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Preflight (§2)
# ---------------------------------------------------------------------------


def _check_layer(layer: str, failures: list[str]) -> None:
    """§2: `layer` is accepted explicitly and rejected unless 'api', so the
    Layer A limit is stated at the call site rather than discovered at a
    constraint violation deep in the planner."""
    if layer != LAYER_API:
        failures.append(
            f"layer={layer!r} is not supported. This driver plans Layer A "
            f"({LAYER_API!r}) only: plan_run writes no surface_layer, so every "
            "row takes migration 0005's 'api' default. Layer B capture is "
            "human-initiated (§8.3) and has no run_plans creation path "
            "(D-056)."
        )


def _check_providers(providers: tuple[str, ...], failures: list[str]) -> None:
    if not providers:
        failures.append("providers is empty — nothing to plan.")
    for provider in providers:
        if provider in CONSUMER_ONLY_PROVIDERS:
            failures.append(
                f"provider {provider!r} cannot be planned. D-042: no API "
                "surface exists for Google Search AI Overviews, so it is "
                "consumer-only in v1.0. Planning it would insert an 'api' row "
                "and violate observations_google_ai_is_consumer_only "
                "(migration 0005). Its captures are Layer B and are recorded "
                "as evidence-only by run_gate, not planned here."
            )


def _load_property(db, property_id: str) -> dict | None:
    rows = (
        db.table("properties")
        .select(
            "id, name, is_calibration_property, is_system_zero, website_url, "
            "google_business_profile_url, review_presence_verified, "
            "review_presence_evidence_ref, third_party_reference_verified, "
            "third_party_reference_evidence_ref"
        )
        .eq("id", property_id)
        .execute()
        .data
    )
    return rows[0] if rows else None


def _check_property(prop: dict | None, property_id: str, failures: list[str]) -> None:
    """§2 check 4 — the property is a calibration property and not System Zero."""
    if prop is None:
        failures.append(f"property {property_id} does not exist.")
        return
    if not prop.get("is_calibration_property"):
        failures.append(
            f"property {prop.get('name')!r} is not flagged "
            "is_calibration_property. §8.4 calibration runs against a "
            "designated calibration property only."
        )
    if prop.get("is_system_zero"):
        failures.append(
            f"property {prop.get('name')!r} is flagged is_system_zero. "
            "Execution Plan §3: System Zero tests engineering only and never "
            "performs hospitality calibration — the two flags are mutually "
            "exclusive by design."
        )


# §8.4 property-selection criteria. The first two were checkable from
# migration 0001; the second two exist only because of D-053 / migration 0008,
# and are the whole reason this check can now cover all four.
_SELECTION_CRITERIA = (
    ("website_url", None, "website"),
    ("google_business_profile_url", None, "Google Business Profile"),
    ("review_presence_verified", "review_presence_evidence_ref", "review presence"),
    (
        "third_party_reference_verified",
        "third_party_reference_evidence_ref",
        "third-party reference",
    ),
)


def _check_selection_criteria(prop: dict | None, failures: list[str]) -> None:
    """§2 check 5 — all four §8.4 property-selection criteria are satisfied.

    A null verification column reads as *unchecked*, not as false, and blocks
    while naming the criterion — that three-state distinction is exactly what
    migration 0008 made nullable columns for.

    The evidence pointer is required alongside a true boolean, not merely
    recorded. Migration 0008's own rationale: "a bare boolean is an assertion
    with no artifact behind it, which is precisely the unverifiable state this
    migration exists to end." Accepting verified=true with a null ref would
    reintroduce it.
    """
    if prop is None:
        return  # already reported by _check_property
    for column, evidence_column, label in _SELECTION_CRITERIA:
        value = prop.get(column)
        if evidence_column is None:
            if not value:
                failures.append(
                    f"§8.4 property-selection criterion {label!r} unmet: "
                    f"properties.{column} is not set."
                )
            continue
        if value is None:
            failures.append(
                f"§8.4 property-selection criterion {label!r} is unchecked: "
                f"properties.{column} is null. Null means nobody has looked "
                "yet, which is not the same as verified-absent — verify it and "
                "store the evidence (D-053)."
            )
        elif value is False:
            failures.append(
                f"§8.4 property-selection criterion {label!r} was verified "
                f"ABSENT: properties.{column} is false. The property does not "
                "meet §8.4 and is not eligible as a calibration property."
            )
        elif not prop.get(evidence_column):
            failures.append(
                f"§8.4 property-selection criterion {label!r} is marked "
                f"verified but properties.{evidence_column} is empty. D-053 "
                "requires the artifact behind the claim, not the claim alone."
            )


def _load_prompts(db, prompt_version_ids: tuple[str, ...]) -> list[dict]:
    return (
        db.table("prompt_versions")
        .select("id, set_type, version, market_id, is_holdout, intent_tier")
        .in_("id", list(prompt_version_ids))
        .execute()
        .data
    )


def _check_prompts(
    prompts: list[dict],
    prompt_version_ids: tuple[str, ...],
    market_id: str,
    failures: list[str],
) -> str | None:
    """§2 checks 1-3. Returns the single prompt-set version if there is one."""
    found = {r["id"] for r in prompts}
    missing = [pid for pid in prompt_version_ids if pid not in found]
    if missing:
        failures.append(
            "prompt_versions rows do not exist: " + ", ".join(sorted(missing))
        )

    # Check 2 — §7 Frozen Core set size. Counted over the ids asked for, so a
    # set that is the right size but partly missing reports both problems.
    count = len(prompt_version_ids)
    if not MIN_PROMPT_SET_SIZE <= count <= MAX_PROMPT_SET_SIZE:
        failures.append(
            f"prompt set has {count} prompts; §7 requires "
            f"{MIN_PROMPT_SET_SIZE}-{MAX_PROMPT_SET_SIZE} for a Frozen Core set."
        )

    wrong_type = sorted(
        r["id"] for r in prompts if r.get("set_type") != "frozen_core"
    )
    if wrong_type:
        detail = ", ".join(
            f"{r['id']} ({r.get('set_type')!r})"
            for r in sorted(prompts, key=lambda r: r["id"])
            if r.get("set_type") != "frozen_core"
        )
        failures.append(f"prompt rows are not set_type='frozen_core': {detail}")

    # Check 3 — hold-out is a §6.3 Benchmark concept with no meaning in a
    # Frozen Core instrument.
    holdouts = sorted(r["id"] for r in prompts if r.get("is_holdout"))
    if holdouts:
        failures.append(
            "prompt rows are flagged is_holdout: "
            + ", ".join(holdouts)
            + ". Hold-out is a §6.3 Benchmark concept and has no meaning in a "
            "Frozen Core set."
        )

    versions = {r.get("version") for r in prompts}
    if len(versions) > 1:
        failures.append(
            "prompt rows span more than one version: "
            + ", ".join(sorted(str(v) for v in versions))
            + ". §7 makes Frozen Core membership immutable between baseline "
            "and validation, so one run plans exactly one set version."
        )

    markets = {r.get("market_id") for r in prompts}
    if len(markets) > 1:
        failures.append(
            "prompt rows span more than one market_id: "
            + ", ".join(sorted(str(m) for m in markets))
        )
    elif markets and market_id not in markets:
        failures.append(
            f"market_id {market_id} does not match the prompt rows' market_id "
            f"{next(iter(markets))}. The market carried to the gate must be "
            "the market the prompts were written for."
        )

    return next(iter(versions)) if len(versions) == 1 else None


# ---------------------------------------------------------------------------
# Run plan reuse (§3 "Re-invocation")
# ---------------------------------------------------------------------------


def _find_reusable_run_plan(
    db, property_id: str, prompt_version_ids: tuple[str, ...]
) -> dict | None:
    """Find an existing frozen_core run plan for this property + prompt set.

    §3: "Given a property and a prompt-set version that already have a
    frozen_core run plan, the driver reuses that plan's id rather than
    inserting a second one." Without this, a second invocation gets a new
    run_plan_id, and `deterministic_task_id` — which is scoped to the run plan
    — silently plans a duplicate full set of observations (§1).

    Matched through the planned observations, because `run_plans` has no
    prompt-set-version column: the identity of a plan's prompt set exists only
    as the distinct prompt_version_ids of the rows planned against it. Flagged
    in the summary as a schema gap worth closing.
    """
    plans = (
        db.table("run_plans")
        .select("id, property_id, run_type, replicate_count, status")
        .eq("property_id", property_id)
        .eq("run_type", RUN_TYPE)
        .execute()
        .data
    )
    if not plans:
        return None

    plan_ids = [p["id"] for p in plans]
    observations = (
        db.table("observations")
        .select("run_plan_id, prompt_version_id")
        .in_("run_plan_id", plan_ids)
        .execute()
        .data
    )

    by_plan: dict[str, set[str]] = {pid: set() for pid in plan_ids}
    for obs in observations:
        by_plan.setdefault(obs["run_plan_id"], set()).add(obs["prompt_version_id"])

    wanted = set(prompt_version_ids)
    for plan in plans:
        if by_plan.get(plan["id"]) == wanted:
            return plan
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def plan_calibration_run(
    db,
    *,
    property_id: str,
    prompt_version_ids: list[str],
    market_id: str,
    layer: str = LAYER_API,
    providers: list[str] | None = None,
    replicate_count: int = DEFAULT_REPLICATE_COUNT,
    window_hours: int = MIN_WINDOW_HOURS,
    commit: bool = False,
    new_plan: bool = False,
    planner=plan_run,
    now: datetime | None = None,
) -> CalibrationPlan:
    """Preflight, then plan one Layer A Frozen Core run. See §§2-3.

    `commit=False` (the default) is a dry run: every check runs and the plan is
    described, but nothing is written — matching the `--commit` idiom in
    scripts/seed_calibration_property.py.

    `new_plan=True` forces a second run plan for a set that already has one. §3:
    "A new run plan is created only when explicitly asked for — a second
    baseline is a deliberate act, not the accidental result of running a
    command twice."

    `planner` is injectable purely for testing, so these tests can assert on
    what the driver asks the planner for without writing observations. (Phase
    A later added an optional keyword-only `db` to `plan_run` as well, so its
    own insert-missing-only behaviour could be tested directly; this seam
    remains the right one for driver-level tests, which care about the call,
    not the write.)
    """
    providers_t = tuple(providers) if providers is not None else DEFAULT_PROVIDERS
    prompt_ids_t = tuple(prompt_version_ids)

    failures: list[str] = []
    _check_layer(layer, failures)
    _check_providers(providers_t, failures)

    if replicate_count < 1:
        failures.append(f"replicate_count must be >= 1, got {replicate_count}.")
    if window_hours < MIN_WINDOW_HOURS:
        failures.append(
            f"window_hours={window_hours} is below the §6.1 minimum "
            f"{MIN_WINDOW_HOURS}-hour run window for Frozen Core replicates."
        )

    duplicates = sorted({p for p in prompt_ids_t if prompt_ids_t.count(p) > 1})
    if duplicates:
        failures.append(
            "prompt_version_ids contains duplicates: " + ", ".join(duplicates)
        )

    prop = _load_property(db, property_id)
    _check_property(prop, property_id, failures)
    _check_selection_criteria(prop, failures)

    prompts = _load_prompts(db, prompt_ids_t)
    version = _check_prompts(prompts, prompt_ids_t, market_id, failures)

    if failures:
        raise PreflightError(failures)

    assert version is not None  # guaranteed: _check_prompts failed otherwise

    notes: list[str] = []
    existing = None if new_plan else _find_reusable_run_plan(db, property_id, prompt_ids_t)

    if existing is not None and existing.get("replicate_count") != replicate_count:
        # Reusing the plan would leave run_plans.replicate_count disagreeing
        # with the task list plan_run writes — §3 requires they cannot differ.
        raise PreflightError(
            [
                (
                    f"run plan {existing['id']} already exists for this property "
                    f"and prompt set with replicate_count="
                    f"{existing.get('replicate_count')}, but this invocation "
                    f"asks for {replicate_count}. Re-run with the matching "
                    "replicate count, or pass new_plan=True to plan a "
                    "deliberate second baseline."
                )
            ]
        )

    planned_observations = len(prompt_ids_t) * len(providers_t) * replicate_count

    if existing is not None:
        notes.append(
            f"reusing existing run plan {existing['id']} "
            f"(status={existing.get('status')!r}); plan_run is "
            "insert-missing-only on task_id, so the repeat adds only absent "
            "rows and leaves executed ones untouched."
        )
    elif new_plan:
        notes.append(
            "new_plan=True — planning a deliberate second baseline for a "
            "property and prompt set that may already have one."
        )

    if not commit:
        notes.append("dry run — nothing written. Re-run with --commit to write.")
        return CalibrationPlan(
            run_plan_id=existing["id"] if existing else None,
            property_id=property_id,
            market_id=market_id,
            prompt_set_version=version,
            prompt_version_ids=prompt_ids_t,
            providers=providers_t,
            replicate_count=replicate_count,
            planned_observations=planned_observations,
            reused=existing is not None,
            committed=False,
            notes=tuple(notes),
        )

    # ---- Step 1 — the run_plans row (§3) ----------------------------------
    if existing is not None:
        run_plan_id = existing["id"]
        window_start = window_end = None
    else:
        start = now or datetime.now(timezone.utc)
        end = start + timedelta(hours=window_hours)
        window_start, window_end = start.isoformat(), end.isoformat()
        run_plan_id = (
            db.table("run_plans")
            .insert(
                {
                    "property_id": property_id,
                    # Migration 0002 vocabulary. Never 'system_zero': that is
                    # reserved for Atlas's own engineering runs, and a
                    # calibration property is a real hospitality property.
                    "run_type": RUN_TYPE,
                    "replicate_count": replicate_count,
                    "status": "planned",
                    # §6.1 minimum six-hour window. Written at plan time
                    # because it is unrecoverable afterwards.
                    "window_start": window_start,
                    "window_end": window_end,
                }
            )
            .execute()
            .data[0]["id"]
        )

    # ---- Step 2 — plan_run writes the observations (§3) --------------------
    tasks = planner(
        run_plan_id,
        list(prompt_ids_t),
        list(providers_t),
        replicate_count,
    )
    notes.append(f"planner wrote {len(tasks)} observation task(s)")

    # ---- Step 3 — return the run_plan_id (§3) ------------------------------
    return CalibrationPlan(
        run_plan_id=run_plan_id,
        property_id=property_id,
        market_id=market_id,
        prompt_set_version=version,
        prompt_version_ids=prompt_ids_t,
        providers=providers_t,
        replicate_count=replicate_count,
        planned_observations=planned_observations,
        reused=existing is not None,
        committed=True,
        window_start=window_start,
        window_end=window_end,
        notes=tuple(notes),
    )
