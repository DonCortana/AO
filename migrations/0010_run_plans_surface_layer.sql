-- 0010: run_plans.surface_layer — Layer A/Layer B separation at the PLAN
-- level, mirroring what 0005 (D-043) established at the observation level.
--
-- GOVERNANCE NOTE — READ BEFORE APPLYING. The change this file implements is
-- attributed in the working branch to D-081 (with D-080 and D-082 as
-- neighbours). As of this file's authoring, docs/decision-register.md ends at
-- D-079 and none of D-080/D-081/D-082 exist in the register or in
-- docs/draft-decisions-pending.md. Methodology §10 requires a decision be
-- documented BEFORE implementation. This migration is therefore staged and
-- unapplied pending registration of the governing decision; the references
-- below are forward references, not citations of accepted entries.
--
-- The gap being closed, stated independently of the decision numbering:
-- `atlas.calibration.consumer_run_plan` is insert-only and plans no
-- observations, so its reuse check — which matches a run plan by the set of
-- prompt_version_ids on that plan's observations — cannot distinguish "no
-- Layer B plan exists" from "a Layer B plan exists but nothing has been
-- ingested against it yet". run_plans carries no record of which layer a plan
-- was created for, so the row's own answer cannot be consulted. This column
-- supplies that answer.
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
-- which is the exact failure this migration was written to prevent. Raising
-- here aborts the enclosing transaction, taking the ADD COLUMN with it, so a
-- surprise leaves the schema untouched rather than half-migrated.

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
