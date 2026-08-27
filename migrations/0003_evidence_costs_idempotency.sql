-- 0003: one evidence row and one cost row per observation, enforced.
--
-- Forced by Technical Lane step 8 (retry/idempotency/reconciliation,
-- decision-register.md D-032): a resumed/retried run must never double-
-- write the evidence or cost ledger for a task_id it already finished.
-- A plain "SELECT then INSERT if missing" check in application code is a
-- check-then-act race between two runner invocations (a manual recovery
-- dispatch overlapping a scheduled run, for example). A real unique
-- constraint closes that race at the database level instead of trusting
-- application-level timing: the runner upserts on_conflict=observation_id,
-- so a duplicate write becomes a no-op merge rather than a second row.
--
-- 0001/0002 are already applied to the live project, so this is additive.

alter table evidence add constraint evidence_observation_id_key unique (observation_id);
alter table costs add constraint costs_observation_id_key unique (observation_id);
