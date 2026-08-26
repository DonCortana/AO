-- 0002: allow 'system_zero' as a run_plans.run_type.
--
-- Gap in 0001: System Zero (Execution Plan §3) is Atlas's own engineering
-- test run — it must flow through the same resumable pipeline as every
-- other run (planned -> queued -> ... -> reconciled) so reconciliation and
-- the smoke test can track it, but it is explicitly never a scored client
-- run and must never be confused with 'discovery' (prompt/market
-- discovery) or any client-facing run type.
--
-- 0001 is already applied to the live project, so this is an additive
-- migration rather than an edit to applied DDL.

alter table run_plans drop constraint run_plans_run_type_check;

alter table run_plans add constraint run_plans_run_type_check
    check (run_type in ('system_zero', 'frozen_core', 'sentinel', 'benchmark_monthly', 'benchmark_quarterly', 'discovery'));
