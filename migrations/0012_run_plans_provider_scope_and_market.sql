-- 0012: run_plans.provider_scope and run_plans.market_id — two more facts
-- about a run plan recorded ON the plan at plan time, the way 0010 (D-081)
-- recorded its layer and 0011 (D-084) recorded its prompt set.
--
-- Governed by D-086 (provider_scope, its conditional constraint, and the
-- 41f71293 backfill), D-087 (market_id and the provenance fault it closes),
-- D-089 (why market is scalar and not an array), D-090 (the Calibration
-- Manifest that generalises all of these), D-092 (the market backfill value,
-- established from data rather than assumed) and D-094 (what the test suite
-- can and cannot prove about the constraints below). All accepted 2026-09-03
-- and registered in docs/decision-register.md.
--
-- APPLY WITH `psql --single-transaction`. Required by D-081: no migration in
-- this repository opens a transaction of its own, so under psql's default
-- autocommit each ADD COLUMN commits before the assertions below run, and a
-- failed backfill leaves the schema migrated with the data not written. See
-- 0010's DO-block note for the full statement of what the assertions do and
-- do not guarantee.
--
-- ---------------------------------------------------------------------
-- What this closes
-- ---------------------------------------------------------------------
--
-- D-090 states the general rule these three migrations are instances of: a
-- fact about a run plan must be recorded on the plan at plan time, never
-- inferred from the observations it later acquires. 0010 was the first
-- instance, 0011 the second, and this file is the third and fourth in one
-- migration.
--
--   provider_scope (D-086). Nothing on run_plans records the provider list a
--   plan was built with, so the fact that 41f71293 was planned against a
--   single provider — with `DEFAULT_PROVIDERS` in calibration/driver.py
--   naming four, and all four adapter modules present a day before the plan
--   was created — was visible only by counting distinct providers among the
--   plan's 50 children. It was invisible to every preflight, reconciliation
--   and gate check for six days. `plan_calibration_run` writes this column
--   from its own `providers_t`, and scoring refuses when the distinct
--   providers among a plan's complete observations disagree with the
--   recorded scope, on the D-085 derive-and-refuse precedent.
--
--   market_id (D-087). `calibration_results.market_id` is NOT NULL with no
--   default on an append-only score-bearing table, and run_plans has had no
--   market column at all, so the value reaching that column originated as a
--   hand-supplied argument to `compute_avs_for_property` — stored with
--   nothing cross-checking it against the plan being scored, and used to
--   select which eligible-platform list is read. A typo there does not
--   merely mislabel a score; it can compute the score against another
--   market's gate result and return a plausible number.
--
-- ---------------------------------------------------------------------
-- Why one is an array and the other is not — deliberate, not an oversight
-- ---------------------------------------------------------------------
--
-- provider_scope is `text[]`; market_id is a scalar `uuid`. The asymmetry is
-- decided, not accidental, and D-089 exists to record it precisely so a
-- later reader does not "fix" it into symmetry.
--
-- A plan can legitimately span several providers because provider is a
-- genuinely per-observation attribute within one capture configuration. A
-- plan cannot span several markets, because market determines the capture
-- configuration itself: Layer B capture geography is pinned per session by
-- the proxy country parameter (D-066), so a plan spanning two markets could
-- not have one capture configuration, and geography would have to become an
-- observation-level fact — which is the D-081 inversion exactly, a fact
-- about a plan inferred from its children, and the thing this whole sequence
-- of migrations exists to end. Multi-market measurement is therefore N
-- plans, each with its own manifest and capture configuration, aggregated at
-- report level under a rule deferred to Phase C.
--
-- `market_id uuid references markets(id)` also aligns run_plans with the
-- form already carried by calibration_results, prompt_versions and scores,
-- rather than introducing a fourth spelling of the same concept.
--
-- ---------------------------------------------------------------------
-- Live state this migration was written against (verified, not assumed)
-- ---------------------------------------------------------------------
--
--   run_plans: 27 rows, of which exactly one is frozen_core. The other 26
--   are system_zero on the test property; they have no provider scope and no
--   market, and inventing one for them would be the exact inference D-086
--   forbids. They keep NULL in both columns, which is why neither column can
--   be NOT NULL.
--
--     - 41f71293-7466-43a9-a71c-b47bea47a23c — frozen_core, Layer A,
--       prompt_set_version 'frozen-core-samujana-v1', replicate_count 5,
--       status 'reconciled', 50 observations, all status 'complete', with no
--       'planned'/'queued'/'running' remainder anywhere in the database. Not
--       a partial drain. Backfilled below.
--
--   d2b1c8a3-2874-4033-b3f8-87074cd9414d, the Layer B plan formerly
--   described here, was deleted 2026-09-03 under D-098 before this migration
--   was applied. It held zero observations, carried the superseded
--   prompt_set_version 'frozen-core-samujana-v1' (D-097), and was scoped
--   against a one-platform Layer A that D-086 invalidated. Its existence was
--   the only obstacle to a validated constraint; see below.
--
-- ---------------------------------------------------------------------
-- The backfill values are literals here, unlike 0011's
-- ---------------------------------------------------------------------
--
-- 0011 derived 41f71293's prompt_set_version inside the migration, in a
-- reconstruction block, because the value was not known at authoring time
-- and the derivation was the only thing that could establish it. That is not
-- the situation here. Both values below were established against live data
-- before this file was written, and D-086 and D-092 record the derivations
-- in the register:
--
--   provider_scope '{perplexity}' — all 50 observations of 41f71293 carry
--   provider 'perplexity' (D-086). Derived from its own children as a
--   one-time backfill of a closed, reconciled plan where no other source
--   exists; the derivation is recorded in the register rather than left
--   implicit, per D-081's treatment of its own backfill.
--
--   market_id 2d4854b9-5589-44a5-886b-c895e99c7b95 (TH/en) — D-087 left this
--   value open and required it be settled against live data before this
--   migration was written, recording that if it could not be established
--   from a source other than the caller's memory, that absence was itself
--   the finding. D-092 closes it with an established value:
--   `observations.prompt_version_id` -> `prompt_versions.market_id` resolves
--   to that single market uniformly across all 50 observations, with zero
--   null prompt_version_id. A derivation from data on disk, not a
--   reconstruction from recollection. (`evidence.market` and
--   `evidence.language` are null across all 50 rows and are not a source;
--   they are themselves an instance of the D-090 inversion and are left
--   unaddressed here.)
--
-- Writing them as literals under row-count assertions is therefore the
-- honest shape: the assertion's job is to prove the row exists and was
-- written, not to re-perform a derivation that has already been done and
-- recorded.
--
-- ---------------------------------------------------------------------
-- The CHECK constraints, and what they constrain today: nothing
-- ---------------------------------------------------------------------
--
-- Both constraints reuse the conditional shape already present as
-- observations_google_ai_is_consumer_only (0005, D-042) — a table-level
-- named constraint of the form "either this row is not of the kind the rule
-- governs, or the rule holds":
--
--     check (provider <> 'google_ai' or surface_layer = 'consumer')
--
-- Neither constraint binds on any row in the table as it stands. The one
-- remaining frozen_core row, 41f71293, is given both values by the backfill
-- below; the 26 system_zero rows are excluded by the run_type disjunct. The
-- constraints therefore first do work on the NEXT frozen_core plan created —
-- including the four-adapter Samujana re-plan D-086 requires before the
-- §8.4 gate runs.
--
-- They are added VALID (the plain form), which verifies every existing row
-- at DDL time. That is possible only because d2b1c8a3 no longer exists.
-- While it did, "leave d2b1c8a3 NULL" and "add these CHECKs" could not both
-- hold: a validated constraint would have rejected that row and aborted this
-- migration. NOT VALID was considered and rejected under D-098 — it skips
-- the initial scan but is still enforced on UPDATE of the pre-existing
-- violating row, and d2b1c8a3 was status 'planned' and due to be updated on
-- Layer B ingest, so the failure would have surfaced during capture rather
-- than here. Deleting the row was the resolution; see D-098 for the
-- verification that nothing referenced it.
--
-- ---------------------------------------------------------------------
-- Test coverage: there is none, and there cannot be (D-094)
-- ---------------------------------------------------------------------
--
-- `tests.conftest.FakeDB` is `dict[str, list[dict]]` with no DDL, no
-- constraint layer, no transactions and no concurrency. No CHECK, NOT NULL,
-- foreign key or unique constraint introduced by migrations 0001-0012 is
-- exercised by any test that uses it, so a green suite is evidence about
-- application logic only and is not evidence that either constraint below
-- fires. The acceptance criterion for these two constraints is a live
-- negative test against Postgres — an attempted insert of a frozen_core plan
-- with null provider_scope, and one with null market_id, each observed to
-- fail — and not a passing test run. Cited here so the gap is not
-- rediscovered later as a surprise.

-- ---------------------------------------------------------------------
-- Columns
-- ---------------------------------------------------------------------

alter table run_plans
    add column provider_scope text[];

comment on column run_plans.provider_scope is
    'Provider list this plan was built with, written by '
    'atlas.calibration.driver.plan_calibration_run from its own providers '
    'argument. Null for run types with no provider scope (system_zero). '
    'Recorded rather than inferred from child observations per '
    'decision-register D-086; a Calibration Manifest field under D-090. '
    'Scoring refuses when the distinct providers among a plan''s complete '
    'observations disagree with this value (D-085 derive-and-refuse '
    'precedent).';

alter table run_plans
    add column market_id uuid references markets(id);

comment on column run_plans.market_id is
    'The single market this plan measures. Scalar, never an array: market '
    'determines the capture configuration itself (D-066 pins Layer B '
    'geography per session), so multi-market measurement is N plans, not one '
    'plan with N markets — decision-register D-089, which also records why '
    'this is deliberately not symmetric with provider_scope''s text[]. Null '
    'for run types with no market (system_zero). A Calibration Manifest '
    'field under D-090; read by compute_avs_for_property, which derives the '
    'market from the plan and refuses on disagreement rather than accepting '
    'it as a caller argument (D-087).';

-- ---------------------------------------------------------------------
-- Backfill — 41f71293 only, each column asserted separately
-- ---------------------------------------------------------------------
--
-- Two blocks rather than one combined UPDATE, following 0011's reason: the
-- two values have different provenance — provider_scope from the plan's own
-- observations.provider, market_id through prompt_version_id ->
-- prompt_versions.market_id — and a reviewer should be able to see which is
-- which without unpicking a single statement.
--
-- Named by primary key rather than derived by predicate, as in 0010 and
-- 0011: there is no column or join that identifies these facts about the
-- plan — that absence is the whole reason this migration exists — so any
-- WHERE clause cleverer than the primary key would be inferring the answer
-- it is supposed to be recording.
--
-- Each is wrapped in a row-count assertion because a bare UPDATE whose WHERE
-- matches nothing succeeds silently, leaving the column NULL on a
-- frozen_core plan — which is the exact state these constraints exist to
-- rule out.

-- provider_scope. All 50 observations of this plan carry provider
-- 'perplexity' (D-086), verified against live data before this file was
-- written.
do $$
declare
    n int;
begin
    update run_plans
       set provider_scope = '{perplexity}'::text[]
     where id = '41f71293-7466-43a9-a71c-b47bea47a23c';
    get diagnostics n = row_count;
    if n <> 1 then
        raise exception
            'expected exactly 1 Layer A run_plans row to backfill '
            'provider_scope, updated %', n;
    end if;
end $$;

-- market_id. 2d4854b9-5589-44a5-886b-c895e99c7b95 is TH/en, resolved through
-- prompt_version_id -> prompt_versions.market_id uniformly across all 50
-- observations with zero null prompt_version_id (D-092), verified against
-- live data before this file was written.
do $$
declare
    n int;
begin
    update run_plans
       set market_id = '2d4854b9-5589-44a5-886b-c895e99c7b95'
     where id = '41f71293-7466-43a9-a71c-b47bea47a23c';
    get diagnostics n = row_count;
    if n <> 1 then
        raise exception
            'expected exactly 1 Layer A run_plans row to backfill '
            'market_id, updated %', n;
    end if;
end $$;

-- Only one frozen_core row exists to backfill. d2b1c8a3, the Layer B plan,
-- was deleted under D-098 before this migration was applied.

-- ---------------------------------------------------------------------
-- Constraints — added after the backfill, validated (see header)
-- ---------------------------------------------------------------------

alter table run_plans add constraint run_plans_frozen_core_has_provider_scope check (
    run_type <> 'frozen_core' or provider_scope is not null
);

alter table run_plans add constraint run_plans_frozen_core_has_market_id check (
    run_type <> 'frozen_core' or market_id is not null
);
