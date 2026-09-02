"""Plan (insert-only) one Layer B consumer-surface run plan.

Companion to `atlas.calibration.driver` (Layer A), not a replacement for it.
D-056 named "no run_plans creation path for consumer-surface capture" as
open; this module closes that gap for the creation step only:

    preflight (property + prompt checks, shared with Layer A where the
               check is layer-agnostic; see "Reuse posture" below)
      -> reuse or insert one run_plans row   [this module writes]
      -> return the run_plan_id

**Insert-only, no observations planned.** Layer A's `plan_calibration_run`
inserts the `run_plans` row and then calls `plan_run` to write one
`observations` row per (prompt, provider, replicate) ahead of capture,
because the resume runner drains that queue. Layer B has no runner:
capture is a human in a browser (§8.3), and `atlas.tools.consumer_ingest`
writes its own `observations` rows directly from the capture sheet at
ingest time, each carrying `surface_layer='consumer'`. Planning rows here in
advance would invent observations the ingest path never reads and is never
asked to reconcile against — this module writes the `run_plans` row and
nothing else.

**window_start / window_end are always null at insert (D-062).** Layer A
computes a forward-looking window at creation because the API call is about
to be scheduled into it. Layer B capture is retrospective — the true window
is only known once every replicate has actually been captured — so this
module never writes a placeholder value. Setting the real window and
transitioning `status` to `'complete'` (D-063) is a separate, later update
this module does not perform.

**status is always 'planned' at insert.** Matches Layer A's convention
(`driver.py` writes `'planned'` too). D-063 records that nothing currently
reads `run_plans.status` on the ingest path, so this is consistency with the
existing vocabulary, not a verified contract.

## Reuse posture against `atlas.calibration.driver`

Shared directly (layer-agnostic — property and prompt-set checks do not
mention API adapters, providers, or `surface_layer` anywhere in their body):
`_load_property`, `_check_property`, `_check_selection_criteria`,
`_load_prompts`, `_check_prompts`, `MIN_PROMPT_SET_SIZE`,
`MAX_PROMPT_SET_SIZE`, `RUN_TYPE`, `PreflightError`.

Not shared, by design:

- `_check_layer` / `_check_providers` — Layer A's four-adapter provider
  vocabulary has no meaning for a table (`run_plans`) that carries no
  provider column, and its `layer='api'` gate has none for a module that is
  unconditionally Layer B: this module does not accept a `layer` argument to
  disagree with. (`run_plans` does now carry `surface_layer` as of D-081, but
  this module *sets* it rather than gating on a caller-supplied value.)
- `MIN_WINDOW_HOURS` / window-hours enforcement — inapplicable per D-062:
  there is no window at creation to validate a minimum against.
- **`_find_reusable_run_plan` — deliberately NOT reused, rewritten below as
  `_find_reusable_consumer_run_plan`.** This was verified against live data,
  not assumed. `_find_reusable_run_plan` matched a `frozen_core` run_plans
  row for a property by the set of `prompt_version_id`s its already-planned
  observations carry, with no `surface_layer` filter — because Layer A was
  the only layer it was ever asked to disambiguate. Samujana already has the
  exact collision that left unguarded: run_plans row
  `41f71293-7466-43a9-a71c-b47bea47a23c` is the Layer A frozen_core plan for
  this property, and its observations carry the identical 10-prompt
  `frozen-core-samujana-v1` set Layer B asks for. Calling the Layer A
  function unmodified here would report that row as "reusable" for a Layer B
  create and hand a consumer capture the API run's `run_plan_id` — the
  Layer A/Layer B merge migration 0005 (D-043) exists to prevent at the
  `observations` level, reappearing one level up. D-082 adds the matching
  filter to Layer A's function too, so the guard now runs in both
  directions rather than only this one.

  **What the collision would actually look like today (D-082).** With the
  current data it would raise `ConsumerPreflightError`, not merge silently:
  `41f71293`'s `replicate_count` of 5 disagrees with Layer B's 3, and the
  mismatch guard below rejects a reused plan whose count differs from the
  one asked for. The silent case — a consumer capture quietly filed under
  the API run's plan id — requires the two replicate counts to agree, which
  is a data coincidence and not a property of the code. Treat the loud
  failure as luck, not protection.

  **The gap that made this necessary is closed by D-081 (migration 0010).**
  Reuse was decided purely by inference over child observations, and
  `run_plans` recorded no layer of its own, so the row's own answer could
  not be consulted. That inference was unsound three ways: (1) this module
  is insert-only, so a freshly created Layer B plan has zero observations
  until a capture is ingested through `consumer_ingest.py`, making an
  uncaptured plan indistinguishable from an absent one — two back-to-back
  `--commit` invocations both read "no match" and inserted two rows; (2) the
  match is set equality against the *full* prompt set, so a partially
  ingested plan also read as absent — the same double-insert, not confined
  to the zero-ingest window as the previous version of this note claimed;
  and (3) it could not guard the Layer A direction at all. `run_plans`
  now carries `surface_layer` (NOT NULL DEFAULT 'api', vocabulary mirroring
  `observations` per D-043), and both queries below filter on it, so a plan's
  layer is read from the plan rather than inferred from what has been
  captured against it.

  The filters closed (3). Faults (1) and (2) needed the prompt set recorded
  on the plan the same way, which is D-084 and migration 0011: reuse now keys
  on (property_id, run_type, surface_layer, prompt_set_version) and touches no
  child rows, so an uncaptured or partially captured plan is found rather than
  duplicated. The observations match survives only as a fallback for rows
  whose column is null. See the function's own docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atlas.calibration.driver import (
    RUN_TYPE,
    PreflightError,
    _ambiguous_key_failure,
    _check_prompts,
    _check_property,
    _check_selection_criteria,
    _load_named_run_plan,
    _load_prompts,
    _load_property,
)

# Methodology §8.3: "repeated enough to estimate agreement; target n=3 when
# operationally feasible." A target, not a floor — the ingest path
# (consumer_ingest.validate) already accepts n=1/n=2 cells and warns rather
# than rejects. This is what gets written to run_plans.replicate_count.
DEFAULT_REPLICATE_COUNT = 3

SURFACE_LAYER = "consumer"


class ConsumerPreflightError(PreflightError):
    """Same shape as `atlas.calibration.driver.PreflightError` (fail-closed,
    all failures collected, not just the first). Subclassed rather than
    reusing the parent class directly so a caller can distinguish a Layer B
    preflight failure from a Layer A one without inspecting message text."""


@dataclass(frozen=True)
class ConsumerRunPlan:
    """What this module planned, or would plan on a dry run.

    No `providers` or `window_*` fields, unlike Layer A's `CalibrationPlan`:
    `run_plans` carries no provider column, and window_start/window_end are
    always null at this stage (D-062) — there is nothing to report until the
    later post-capture update writes them.
    """

    run_plan_id: str | None
    property_id: str
    prompt_set_version: str
    prompt_version_ids: tuple[str, ...]
    replicate_count: int
    reused: bool
    committed: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Run plan reuse — Layer B-scoped. See module docstring "Reuse posture".
# ---------------------------------------------------------------------------


def _find_reusable_consumer_run_plan(
    db,
    property_id: str,
    prompt_version_ids: tuple[str, ...],
    prompt_set_version: str,
) -> dict | None:
    """Find an existing Layer B `frozen_core` run plan for this property and
    prompt set.

    Two `surface_layer='consumer'` filters, not one (D-081/D-082). The filter
    on `run_plans` is what makes this correct for a plan with no observations
    yet: it matches the plan's own recorded layer, so an uncaptured Layer B
    plan is found rather than mistaken for an absent one. The filter on
    `observations` keeps the prompt-set match from reading another layer's
    rows. See the module docstring for the Samujana collision this guards
    against.

    **D-084 closes D-081's faults (1) and (2) here, and this module is where
    they bit hardest.** Being insert-only, it plans no observations, so the old
    set-equality match against child rows could never find a plan it had just
    created: an uncaptured plan (fault 1) and a partially ingested one (fault
    2) both read as absent and duplicated on the next `--commit`. Keying on
    `run_plans.prompt_set_version` removes the child rows from the question
    entirely, so a plan is findable the moment it is written.

    Relaxing the match to a subset was tried and rejected on the way here: an
    empty plan would match *any* prompt set, making reuse non-monotonic — the
    same invocation answering differently before and after ingest — which is
    the class of unsoundness D-081 exists to retire, not an instance of
    closing it (D-084).

    Raises `ConsumerPreflightError` when more than one plan matches the key:
    `new_plan=True` makes that legitimately possible, and D-084 records the key
    without a tie-break, so the caller names the plan it means with
    `run_plan_id` instead of having one chosen for it.

    The observations fallback survives only for rows whose column is null, of
    which this property has none: migration 0011 backfilled
    d2b1c8a3-2874-4033-b3f8-87074cd9414d, the very row that used to be
    unfindable. It is kept because nothing constrains a future writer to
    populate the column, and a null row silently unmatched would reintroduce
    the double-insert it was added to end.
    """
    plans = (
        db.table("run_plans")
        .select(
            "id, property_id, run_type, replicate_count, status, "
            "surface_layer, prompt_set_version, planned_at"
        )
        .eq("property_id", property_id)
        .eq("run_type", RUN_TYPE)
        .eq("surface_layer", SURFACE_LAYER)
        .execute()
        .data
    )
    if not plans:
        return None

    keyed = [p for p in plans if p.get("prompt_set_version") == prompt_set_version]
    if len(keyed) > 1:
        raise ConsumerPreflightError(
            [_ambiguous_key_failure(keyed, prompt_set_version)]
        )
    if keyed:
        return keyed[0]

    legacy = [p for p in plans if p.get("prompt_set_version") is None]
    if not legacy:
        return None

    legacy_ids = [p["id"] for p in legacy]
    observations = (
        db.table("observations")
        .select("run_plan_id, prompt_version_id")
        .in_("run_plan_id", legacy_ids)
        .eq("surface_layer", SURFACE_LAYER)
        .execute()
        .data
    )

    by_plan: dict[str, set[str]] = {pid: set() for pid in legacy_ids}
    for obs in observations:
        by_plan.setdefault(obs["run_plan_id"], set()).add(obs["prompt_version_id"])

    wanted = set(prompt_version_ids)
    for plan in legacy:
        if by_plan.get(plan["id"]) == wanted:
            return plan
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def create_consumer_run_plan(
    db,
    *,
    property_id: str,
    prompt_version_ids: list[str],
    market_id: str,
    replicate_count: int = DEFAULT_REPLICATE_COUNT,
    commit: bool = False,
    new_plan: bool = False,
    run_plan_id: str | None = None,
) -> ConsumerRunPlan:
    """Preflight, then insert (or reuse) one Layer B run plan.

    `commit=False` (the default) is a dry run: every check runs and the plan
    is described, but nothing is written — matching
    `driver.plan_calibration_run`'s `--commit` idiom.

    `new_plan=True` forces a second run plan for a property + prompt set that
    already has a Layer B plan, mirroring Layer A's flag of the same name and
    the same rationale: a second baseline is a deliberate act.

    `run_plan_id` names the plan to reuse, mirroring Layer A again. It is how
    an ambiguous D-084 key is resolved once `new_plan=True` has produced two
    plans at the same key. Mutually exclusive with `new_plan`.
    """
    prompt_ids_t = tuple(prompt_version_ids)

    failures: list[str] = []

    if run_plan_id is not None and new_plan:
        failures.append(
            "run_plan_id and new_plan=True are mutually exclusive: one names an "
            "existing plan to reuse, the other asks for a new one."
        )

    if replicate_count < 1:
        failures.append(f"replicate_count must be >= 1, got {replicate_count}.")

    duplicates = sorted({p for p in prompt_ids_t if prompt_ids_t.count(p) > 1})
    if duplicates:
        failures.append(
            "prompt_version_ids contains duplicates: " + ", ".join(duplicates)
        )

    prop = _load_property(db, property_id)
    _check_property(prop, property_id, failures)
    _check_selection_criteria(prop, failures)

    prompts = _load_prompts(db, prompt_ids_t)
    version = _check_prompts(db, prompts, prompt_ids_t, market_id, failures)

    if failures:
        raise ConsumerPreflightError(failures)

    if version is None:
        # Unreachable: _check_prompts returns None only when the prompt rows
        # span more than one version, which it also records as a failure, and
        # `failures` was just raised on. Kept as a raise rather than an
        # assert because `python -O` strips asserts, and a silent None here
        # would be written to the gate as the prompt-set version.
        raise RuntimeError(
            "internal invariant violated: prompt-set version is None after "
            "preflight reported no failures."
        )

    notes: list[str] = []
    if new_plan:
        existing = None
    elif run_plan_id is not None:
        named_failures: list[str] = []
        existing = _load_named_run_plan(
            db,
            run_plan_id,
            property_id=property_id,
            surface_layer=SURFACE_LAYER,
            prompt_set_version=version,
            failures=named_failures,
        )
        if named_failures:
            raise ConsumerPreflightError(named_failures)
    else:
        existing = _find_reusable_consumer_run_plan(
            db, property_id, prompt_ids_t, version
        )

    if existing is not None and existing.get("replicate_count") != replicate_count:
        raise ConsumerPreflightError(
            [
                (
                    f"Layer B run plan {existing['id']} already exists for this "
                    f"property and prompt set with replicate_count="
                    f"{existing.get('replicate_count')}, but this invocation "
                    f"asks for {replicate_count}. Re-run with the matching "
                    "replicate count, or pass new_plan=True to plan a "
                    "deliberate second Layer B run."
                )
            ]
        )

    if existing is not None:
        notes.append(
            f"reusing existing Layer B run plan {existing['id']} "
            f"(status={existing.get('status')!r})."
        )
    elif new_plan:
        notes.append(
            "new_plan=True — planning a deliberate second Layer B run plan "
            "for a property and prompt set that may already have one."
        )
    else:
        notes.append(
            "no existing Layer B run plan found for this property and prompt "
            "set. Since D-084 this keys on run_plans.prompt_set_version, so a "
            "plan created but not yet captured against is found rather than "
            "duplicated."
        )

    if not commit:
        notes.append("dry run — nothing written. Re-run with --commit to write.")
        return ConsumerRunPlan(
            run_plan_id=existing["id"] if existing else None,
            property_id=property_id,
            prompt_set_version=version,
            prompt_version_ids=prompt_ids_t,
            replicate_count=replicate_count,
            reused=existing is not None,
            committed=False,
            notes=tuple(notes),
        )

    if existing is not None:
        run_plan_id = existing["id"]
    else:
        run_plan_id = (
            db.table("run_plans")
            .insert(
                {
                    "property_id": property_id,
                    "run_type": RUN_TYPE,
                    "replicate_count": replicate_count,
                    "status": "planned",
                    # D-081: written explicitly, never left to migration
                    # 0010's 'api' default — this is the one writer in the
                    # codebase whose rows must NOT take that default.
                    "surface_layer": SURFACE_LAYER,
                    # D-084: recorded at insert, which is what makes this
                    # plan findable before any capture is ingested against it.
                    "prompt_set_version": version,
                    # D-062: retrospective window, unknown until every
                    # replicate is captured. Never a placeholder.
                    "window_start": None,
                    "window_end": None,
                }
            )
            .execute()
            .data[0]["id"]
        )
        notes.append(
            "window_start/window_end left null (D-062); update both, and "
            "transition status to 'complete' (D-063), once all "
            f"{replicate_count} replicates are captured and ingested."
        )

    return ConsumerRunPlan(
        run_plan_id=run_plan_id,
        property_id=property_id,
        prompt_set_version=version,
        prompt_version_ids=prompt_ids_t,
        replicate_count=replicate_count,
        reused=existing is not None,
        committed=True,
        notes=tuple(notes),
    )
