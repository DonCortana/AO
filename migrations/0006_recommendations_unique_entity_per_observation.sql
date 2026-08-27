-- 0006: one recommendation row per entity per observation, enforced.
--
-- Forced by decision-register.md D-048, written before this file.
--
-- The only writer today is atlas.tools.rpv_labeling, which rejects an import
-- naming any observation that already has recommendation rows. That guard is
-- check-then-act: it reads the already-labeled set once and inserts after, so
-- two imports overlapping in time both validate against the same pre-insert
-- state and both write. It holds only because there is one operator running
-- one deliberate import at a time — an operational fact, not a schema rule,
-- and one that stops being true when labeling volume exceeds one person.
--
-- The failure it lets through is silent. A second CLIENT-entity row per
-- observation is caught loudly at scoring time (atlas.scoring.loader, D-034),
-- but competitor rows are checked nowhere: a duplicated competitor simply
-- double-counts that entity in §9 Share of Voice, with no error.
--
-- Rows with a null observation_id are not covered — Postgres treats NULLs as
-- distinct under a unique constraint. Accepted per D-048: such a row is
-- already outside every scoring path, all of which join through
-- observation_id.
--
-- No application change accompanies this. If a duplicate pair somehow exists
-- at apply time the migration FAILS rather than discarding a row, which is
-- the intended outcome — a human decides which row is right.
--
-- 0001-0005 are already applied to the live project, so this is additive.

alter table recommendations
    add constraint recommendations_observation_id_entity_name_key
    unique (observation_id, entity_name);

comment on constraint recommendations_observation_id_entity_name_key on recommendations is
    'D-048: one row per entity per observation. A response listing the same '
    'entity twice is labeled as a single row at its best (lowest) rank — '
    '§4.1 RPV is a position value held by an entity in an observation.';
