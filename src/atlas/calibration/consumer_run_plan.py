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
  vocabulary and its `layer='api'` gate have no meaning for a table
  (`run_plans`) that carries no provider or layer column at all. This module
  is unconditionally Layer B; there is nothing for these checks to gate.
- `MIN_WINDOW_HOURS` / window-hours enforcement — inapplicable per D-062:
  there is no window at creation to validate a minimum against.
- **`_find_reusable_run_plan` — deliberately NOT reused, rewritten below as
  `_find_reusable_consumer_run_plan`.** This was verified against live data,
  not assumed. `_find_reusable_run_plan` matches a `frozen_core` run_plans
  row for a property by the set of `prompt_version_id`s its already-planned
  observations carry, with no `surface_layer` filter — because Layer A was
  the only layer it was ever asked to disambiguate. Samujana already has the
  exact collision that leaves unguarded: run_plans row
  `41f71293-7466-43a9-a71c-b47bea47a23c` is the Layer A frozen_core plan for
  this property, and its observations carry the identical 10-prompt
  `frozen-core-samujana-v1` set Layer B asks for. Calling the Layer A
  function unmodified here would report that row as "reusable" for a Layer B
  create and hand a consumer capture the API run's `run_plan_id` — silently
  merging the two layers under one plan id, which is precisely the
  Layer A/Layer B confusion migration 0005 (D-043) exists to prevent at the
  `observations` level. The fix is one added filter,
  `observations.surface_layer = 'consumer'`, on the same matching logic.

  **Known residual gap, flagged rather than hidden:** because this module is
  insert-only, a freshly created Layer B run plan has zero observations
  until a human capture is later ingested through `consumer_ingest.py`. The
  observations-based reuse check therefore cannot distinguish "no Layer B
  plan exists yet" from "a Layer B plan exists but nothing has been ingested
  against it yet" — both look like no match. Two `--commit` invocations for
  the same property and prompt set, run back to back before any capture is
  ingested, will insert two `run_plans` rows. `run_plans` has no column
  recording which layer a plan was created for, so there is no way to check
  the row's own record of the answer directly. Layer A avoids this gap
  because its planner writes observations synchronously in the same call
  that inserts the row; Layer B has no analogous synchronous write to key
  off. Worth closing with a `run_plans.surface_layer` (or similar) column if
  this script ends up being invoked more than once per property in practice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atlas.calibration.driver import (
    RUN_TYPE,
    PreflightError,
    _check_prompts,
    _check_property,
    _check_selection_criteria,
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
    db, property_id: str, prompt_version_ids: tuple[str, ...]
) -> dict | None:
    """Find an existing Layer B `frozen_core` run plan for this property and
    prompt set. Same matching strategy as
    `driver._find_reusable_run_plan`, plus a `surface_layer='consumer'`
    filter on the observations query — the filter Layer A's version has no
    reason to carry and Layer B cannot safely go without. See the module
    docstring for the concrete Samujana collision this guards against, and
    the residual gap this does not cover (an as-yet-uningested plan).
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
        .eq("surface_layer", SURFACE_LAYER)
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


def create_consumer_run_plan(
    db,
    *,
    property_id: str,
    prompt_version_ids: list[str],
    market_id: str,
    replicate_count: int = DEFAULT_REPLICATE_COUNT,
    commit: bool = False,
    new_plan: bool = False,
) -> ConsumerRunPlan:
    """Preflight, then insert (or reuse) one Layer B run plan.

    `commit=False` (the default) is a dry run: every check runs and the plan
    is described, but nothing is written — matching
    `driver.plan_calibration_run`'s `--commit` idiom.

    `new_plan=True` forces a second run plan for a property + prompt set that
    already has a Layer B plan, mirroring Layer A's flag of the same name and
    the same rationale: a second baseline is a deliberate act.
    """
    prompt_ids_t = tuple(prompt_version_ids)

    failures: list[str] = []

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
    version = _check_prompts(prompts, prompt_ids_t, market_id, failures)

    if failures:
        raise ConsumerPreflightError(failures)

    assert version is not None  # guaranteed: _check_prompts failed otherwise

    notes: list[str] = []
    existing = (
        None
        if new_plan
        else _find_reusable_consumer_run_plan(db, property_id, prompt_ids_t)
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
            "no existing Layer B plan found with ingested observations "
            "matching this prompt set. If one was created but never "
            "captured against, this will still insert a new row — see "
            "'Known residual gap' in this module's docstring."
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
