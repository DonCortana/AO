-- 0011: run_plans.prompt_set_version — record the plan's prompt set on the
-- plan, the way 0010 (D-081) recorded its layer.
--
-- Governed by D-084 (this column and its backfill), which depends on D-083
-- (prompt-set identity is total: the set at a version is exactly the rows of
-- prompt_versions carrying that (version, set_type, market_id), checked at
-- plan time) and is depended on by D-085 (run_gate derives and cross-checks
-- the version rather than trusting a hand-typed argument). All three accepted
-- 2026-09-02 and registered in docs/decision-register.md.
--
-- APPLY WITH `psql --single-transaction`. Required by D-081: no migration in
-- this repository opens a transaction of its own, so under psql's default
-- autocommit the ADD COLUMN commits before the assertions below run and a
-- failed backfill leaves the schema migrated with the data not written. See
-- 0010's DO-block note for the full statement of what the assertions do and
-- do not guarantee.
--
-- What this closes. 0010 recorded a plan's layer and closed the third of the
-- three faults D-081 named in the old reuse logic. The other two survived it,
-- because the prompt-set half of the match still ran against child
-- observations:
--
--   1. `consumer_run_plan` is insert-only and plans no observations, so a
--      freshly created Layer B plan has an empty prompt set and reads as
--      absent — two back-to-back --commit invocations insert two rows.
--   2. The match is set equality against the full prompt set, so a partially
--      ingested plan reads as absent too.
--
-- Both are inference over child rows standing in for a fact nobody recorded.
-- driver.py's own `_find_reusable_run_plan` docstring already conceded the
-- point — that a plan's prompt set exists only as "the distinct
-- prompt_version_ids of the rows planned against it" — and flagged it as a
-- schema gap worth closing. This is that closure. Both creation paths already
-- compute the version through `_check_prompts` and discard it at insert
-- (driver.py:487/:540, consumer_run_plan.py:324/:365); D-084 has them write
-- it, and reuse then keys on
-- (property_id, run_type, surface_layer, prompt_set_version) with no
-- observations join at all.
--
-- Rejected alternative, recorded because it was tried: relaxing the Layer B
-- match from set equality to subset. It closes both faults and makes reuse
-- non-monotonic — an empty plan matches any prompt set, so the same
-- invocation answers differently before and after ingest. That is the class
-- of unsoundness D-081 was written against, not an instance of closing it.
--
-- ---------------------------------------------------------------------
-- Column shape
-- ---------------------------------------------------------------------
--
-- Nullable, and deliberately unlike 0005/0010's `surface_layer`. There is no
-- value that is correct for every existing row: the 26 system_zero rows and
-- scripts/smoke_test.py's plan have no prompt-set concept at all, and a
-- default would assert one where none exists. Null here means "this plan has
-- no prompt set", not "unknown" — the reuse path reads the column when
-- present and falls back to the observations match only when it is null.
--
-- No CHECK constraint: per D-083 a version's validity is a property of the
-- prompt_versions rows carrying it, enforced at plan time by the completeness
-- check, not a vocabulary this column can enumerate. No index either — the
-- table holds 28 rows and the reuse query is already filtered by property_id.

alter table run_plans
    add column prompt_set_version text;

comment on column run_plans.prompt_set_version is
    'Methodology §7 prompt-set version this plan measures — the identity '
    'carried to the §8.4 gate (§9). Null for run types with no prompt set '
    '(system_zero). Recorded rather than inferred from child observations '
    'per decision-register D-084; identity semantics per D-083.';

-- ---------------------------------------------------------------------
-- Backfill — the two frozen_core rows, each asserted separately
-- ---------------------------------------------------------------------
--
-- Live state this migration was written against: run_plans holds 28 rows, of
-- which exactly two are frozen_core. The other 26 are system_zero on the test
-- property and correctly keep NULL — they have no prompt set to record.
--
-- The two are backfilled under separate assertions, not one combined
-- statement, because their provenance differs and a reviewer should be able
-- to see which is which. Same reason 0010 named its row by primary key: there
-- is no predicate that identifies these rows' prompt sets, since the absence
-- of one is what this column exists to end.

-- 41f71293 — Layer A, replicate_count 5, status 'reconciled'.
-- RECONSTRUCTED: the value is derived here, by this block, from that plan's
-- own observations — not written as a literal and asserted to be a
-- reconstruction. The distinction is the point. A primary-key UPDATE that
-- reports one affected row proves only that the row exists; it says nothing
-- about where the value came from, so a hand-verified literal would still
-- claim "RECONSTRUCTED" on a rebuilt or repopulated database where nothing
-- reconstructed it.
--
-- The derivation restates, as an executable assertion, what was verified by
-- hand in psql: the plan's observations resolve to exactly one distinct
-- prompt_versions.version. That is the assertion D-084 actually needs — a
-- single version is what makes "the version this plan measures" a
-- well-defined value at all, and its failure means the premise this backfill
-- rests on is not true of the database being migrated, so the right outcome
-- is a refusal rather than a write.
--
-- The prompt-count check below is a sanity bound, not a contract. The number
-- of distinct prompt_version_ids is a property of the observations that
-- happen to exist, not of the plan: nothing records a set size on run_plans
-- (it carries replicate_count only — the absence this migration exists to
-- end), and the live value of 10 was never written down as a guarantee. So it
-- asserts §7's Frozen Core floor of 8 rather than an exact 10, which would
-- turn an incidental fact into a condition for applying the migration.
do $$
declare
    versions text[];
    distinct_prompts int;
    derived text;
    n int;
begin
    select array_agg(distinct pv.version), count(distinct o.prompt_version_id)
      into versions, distinct_prompts
      from observations o
      join prompt_versions pv on pv.id = o.prompt_version_id
     where o.run_plan_id = '41f71293-7466-43a9-a71c-b47bea47a23c';

    -- array_agg over zero rows yields NULL, so this also catches a plan whose
    -- observations are missing entirely — the case that would otherwise write
    -- a NULL prompt_set_version and read downstream as "no prompt set".
    if versions is null or array_length(versions, 1) is distinct from 1 then
        raise exception
            'cannot reconstruct prompt_set_version for 41f71293: expected '
            'exactly one distinct prompt_versions.version across its '
            'observations, found %', versions;
    end if;

    if distinct_prompts < 8 then
        raise exception
            'cannot reconstruct prompt_set_version for 41f71293: expected at '
            'least 8 distinct prompt_version_ids across its observations '
            '(§7 Frozen Core set size floor), found %',
            distinct_prompts;
    end if;

    derived := versions[1];
    raise notice 'reconstructed prompt_set_version for 41f71293: %', derived;

    update run_plans
       set prompt_set_version = derived
     where id = '41f71293-7466-43a9-a71c-b47bea47a23c';
    get diagnostics n = row_count;
    if n <> 1 then
        raise exception
            'expected exactly 1 Layer A run_plans row to backfill, updated %', n;
    end if;
end $$;

-- d2b1c8a3 — Layer B, replicate_count 3, status 'planned'.
-- OPERATOR-SUPPLIED, not reconstructed: this row has zero observations, so
-- there is nothing in the database to derive the value from. It is also
-- precisely the row whose fallback path is broken — an uncaptured Layer B
-- plan is the case that reads as absent and duplicates — which is why it is
-- given a value here rather than left null to fall back.
do $$
declare
    n int;
begin
    update run_plans
       set prompt_set_version = 'frozen-core-samujana-v1'
     where id = 'd2b1c8a3-2874-4033-b3f8-87074cd9414d';
    get diagnostics n = row_count;
    if n <> 1 then
        raise exception
            'expected exactly 1 Layer B run_plans row to backfill, updated %', n;
    end if;
end $$;
