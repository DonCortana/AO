-- 0004: align recommendations.outcome_type to Methodology §4.1 naming.
--
-- Forced by decision-register.md D-035. Migration 0001 wrote this check
-- constraint with 'source_only' and 'negative', while Methodology §4.1 and
-- the OutcomeType enum in atlas/adapters/base.py both name those outcomes
-- SOURCE_ONLY_MENTION and NEGATIVE_MENTION. D-034 makes this table the
-- scoring engine's only input, so the two vocabularies had to converge
-- before a loader could read the column. Resolved toward the methodology's
-- own names rather than a translation layer inside the loader.
--
-- No data migration is needed: the live recommendations table was verified
-- empty (0 rows) before this was written. Apply this BEFORE the first
-- manual RPV row is entered — atlas.scoring.loader raises on a legacy
-- value and names this file rather than silently accepting it.

alter table recommendations drop constraint recommendations_outcome_type_check;

alter table recommendations add constraint recommendations_outcome_type_check check (
    outcome_type in (
        'ranked',
        'unordered_positive',
        'source_only_mention',
        'absent',
        'negative_mention',
        'entity_conflict'
    )
);
