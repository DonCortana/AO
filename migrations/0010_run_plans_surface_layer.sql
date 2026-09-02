-- 0010: run_plans.surface_layer — Layer A/Layer B separation at the PLAN
-- level, mirroring what 0005 (D-043) established at the observation level.
--
-- Governed by D-081 (this column, its vocabulary, and the backfill below),
-- alongside D-080 (the Layer B run_plans creation path this column exists to
-- make sound) and D-082 (the reuse filters on both layers that read it). All
-- three accepted 2026-09-01 and registered in docs/decision-register.md.
--
-- APPLY WITH `psql --single-transaction`. Required by D-081, not optional
-- here — see the note above the DO block for what depends on it.
--
-- The gap being closed, as D-081 states it. Both layers decide plan reuse by
-- inference — matching a run plan on the set of prompt_version_ids its child
-- observations carry — and run_plans records no layer of its own, so the
-- row's own answer cannot be consulted. D-081 names three distinct ways that
-- inference is unsound:
--
--   1. An uncaptured plan is indistinguishable from an absent one.
--      `consumer_run_plan` is insert-only and plans no observations, so a
--      freshly created Layer B plan has none until a human capture is
--      ingested. Two back-to-back --commit invocations both read "no match"
--      and insert two rows.
--   2. The match is set equality against the full prompt set, so a partially
--      ingested plan also reads as absent — the same double-insert, no
--      longer confined to the zero-ingest window.
--   3. It cannot guard the Layer A direction at all.
--      `driver._find_reusable_run_plan` carries no layer filter, so once
--      Layer B captures are ingested a Layer A re-plan can match the Layer B
--      plan (D-082, which adds the filters on both sides).
--
-- This column supplies the answer all three lack.
--
-- Column definition and CHECK vocabulary are copied from 0005's
-- observations.surface_layer, deliberately, so the two tables cannot drift
-- apart:
--
--     alter table observations
--         add column surface_layer text not null default 'api'
--         check (surface_layer in ('api', 'consumer'));
--
-- Default 'api' carries 0005's rationale unchanged: every pre-existing
-- run_plans row keeps its current meaning with no data migration, and a
-- writer that does not know about this column cannot accidentally create a
-- consumer plan.
--
-- 0005's second observations constraint (google_ai implies consumer) has no
-- analogue here: run_plans carries no provider column.
--
-- ---------------------------------------------------------------------
-- Live state this migration was written against (verified, not assumed)
-- ---------------------------------------------------------------------
--
--   run_plans: 28 rows.
--     - 41f71293-7466-43a9-a71c-b47bea47a23c — frozen_core, Layer A,
--       replicate_count 5, status 'reconciled'. Correctly takes the 'api'
--       default; its 50 observations are all already surface_layer='api',
--       so plan and observations agree with no further backfill.
--     - d2b1c8a3-2874-4033-b3f8-87074cd9414d — frozen_core, Layer B,
--       replicate_count 3, status 'planned'. The one row the default gets
--       wrong, and the sole target of the backfill below. Zero observations,
--       which is exactly the insert-only condition described above.
--     - The remaining 26 rows are system_zero on the test property and
--       correctly take the 'api' default.
--
--   Those two are the only frozen_core rows in the table, so the backfill
--   below is complete: no other Layer B plan exists to be missed.

alter table run_plans
    add column surface_layer text not null default 'api'
    check (surface_layer in ('api', 'consumer'));

comment on column run_plans.surface_layer is
    'Methodology §8.1 Layer A (api) vs §8.3 Layer B (consumer), at plan level. '
    'Mirrors observations.surface_layer (migration 0005, decision-register '
    'D-043). Read by atlas.calibration.consumer_run_plan and '
    'atlas.calibration.driver when matching an existing plan for reuse.';

-- The one existing Layer B plan, reclassified out of the backfilled 'api'
-- default. Named by primary key rather than derived by predicate: there is no
-- column or join that identifies this row as Layer B — that absence is the
-- whole reason this migration exists — so any WHERE clause cleverer than the
-- primary key would be inferring the answer it is supposed to be recording.
--
-- Wrapped in a row-count assertion because a bare UPDATE whose WHERE matches
-- nothing succeeds silently and would leave the plan misclassified as 'api',
-- which is the exact failure this migration was written to prevent.
--
-- WHAT THE ASSERTION DOES AND DOES NOT GUARANTEE. It always aborts the
-- statement and reports a non-zero exit; it does NOT, by itself, undo the
-- ALTER TABLE above. No migration in this repository opens a transaction of
-- its own, so under psql's default autocommit each statement commits as it
-- completes and the ADD COLUMN would already be durable by the time this
-- block raises — leaving the schema migrated and the backfill not done, the
-- half-applied state the assertion is meant to rule out. Atomicity therefore
-- comes from the invocation, not from this file: applied with
-- `psql --single-transaction` (required by D-081) the raise rolls the ADD
-- COLUMN back with it and the migration is all-or-nothing. Applied without
-- that flag, a failure here needs manual cleanup.

do $$
declare
    n int;
begin
    update run_plans
       set surface_layer = 'consumer'
     where id = 'd2b1c8a3-2874-4033-b3f8-87074cd9414d';
    get diagnostics n = row_count;
    if n <> 1 then
        raise exception
            'expected exactly 1 Layer B run_plans row to backfill, updated %', n;
    end if;
end $$;
