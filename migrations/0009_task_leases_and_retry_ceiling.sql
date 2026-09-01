-- 0009: execution state becomes database infrastructure.
--
-- Framework Advisory v1.1 §3.1; Action Plan v2.0 Phase A. Implementation
-- spec: docs/phase-a-engine-safety-SPEC.md.
--
-- The shared root cause across every Phase A defect is that execution state
-- was enforced by application convention rather than by the database. The
-- claim was a status-guarded UPDATE in atlas.runners.resume; the retry
-- ceiling did not exist anywhere; recovery was a blanket requeue in
-- atlas.reconciliation.reconcile that could not tell a crashed worker from a
-- healthy in-flight one. Each of those is correct only for as long as every
-- writer remembers to be correct. This migration moves the claim, the lease
-- and the ceiling into Postgres, where application drift cannot reach them.
--
-- Applied state at authoring time (verified structurally against the live
-- project 2026-09-01, since the CLI is not linked here): migrations
-- 0001-0008 are ALL applied. README.md's note that "0005 is written but NOT
-- yet applied" is stale and is corrected in the same change as this file.
-- Migration 0006's own header already recorded 0001-0005 as applied.

-- ---------------------------------------------------------------------
-- Lease + retry-ceiling columns
-- ---------------------------------------------------------------------
--
-- No `attempt_no`. The spec drafted one, but `observations.retry_number`
-- has existed since migration 0001 and means the same thing, and two
-- counters for one concept is precisely the application-convention drift
-- this migration exists to end. `retry_number` becomes the attempt counter
-- and `claim_task` below is the only thing that increments it.
--
-- What that displaces: resume.py previously folded the adapter's *in-call*
-- grounding retry (Execution.retry_number, 1 when an adapter re-asked an
-- ungrounded response) into the same column, conflating "how many times was
-- this task dispatched" with "did the adapter re-ask inside one dispatch".
-- Nothing is lost by dropping that write — the in-call retry is already
-- recorded, and more precisely, by grounding_status =
-- 'ungrounded_retried_grounded'.

alter table observations
    add column lease_owner text,
    add column lease_acquired_at timestamptz,
    add column lease_expires_at timestamptz,
    -- OS §4 retry rule: a task is retryable while its attempt count is
    -- under this ceiling. Not null with a default so a row can never be
    -- claimable-forever through a null comparison.
    add column max_attempts int not null default 3,
    add column next_attempt_at timestamptz,
    add column provider_request_id text;

comment on column observations.retry_number is
    'Attempt counter, incremented by claim_task() and by nothing else. '
    'Migration 0009 repurposed this from a free-form retry tally: it is now '
    'the server-side retry ceiling''s left-hand side (retry_number < '
    'max_attempts). An adapter''s in-call grounding retry is recorded in '
    'grounding_status, not here.';

comment on column observations.lease_expires_at is
    'A running task whose lease has expired is presumed to belong to a dead '
    'worker and is reclaimable by claim_task(). Narrowing, not closing, the '
    'D-033 redial window: a provider call that succeeds while finalize dies '
    'is still re-dialed on reclaim. Do not read this as exactly-once.';

comment on column observations.provider_request_id is
    'Provider-side request identifier where one is exposed, for reconciling '
    'a redial against the provider''s own record. Free text: the four '
    'adapters name this differently and some expose nothing.';

-- ---------------------------------------------------------------------
-- Backfill: the nine pre-existing `running` rows
-- ---------------------------------------------------------------------
--
-- The live project holds 9 observations in `running` (verified 2026-09-01),
-- left there by earlier crashed or abandoned resume passes. They predate
-- leases, so lease_expires_at is null for them, and `lease_expires_at <
-- now()` is NULL — not true — for a null. Without this backfill those rows
-- would be unclaimable by claim_task() AND untouchable by the new
-- reconcile_run (which only requeues expired-lease rows), stranding them
-- with no path to any terminal state.
--
-- Setting the lease to now() makes them immediately reclaimable on the next
-- claim, which is the correct reading: nothing holds a lease on them. The
-- alternative — teaching the predicate that a null lease means expired —
-- was rejected because it makes "no lease" and "expired lease" the same
-- state forever, for the sake of nine rows that exist once.

update observations
    set lease_expires_at = now()
    where status = 'running'
      and lease_expires_at is null;

-- ---------------------------------------------------------------------
-- Claim index
-- ---------------------------------------------------------------------
--
-- Covers every branch of claim_task's predicate, including the
-- expired-lease reclaim of `running` rows — a partial index on just
-- ('queued','retryable') would leave the reclaim branch to a sequential
-- scan of the same table the same function is claiming from.
--
-- 'planned' is in the list because it is a claimable state. plan_run writes
-- 'planned' and, as of this migration, nothing writes 'queued' any more:
-- reconcile_run's blanket planned->queued requeue is exactly what 0009
-- removes. A claim predicate without 'planned' would strand every freshly
-- planned task. 'queued' stays claimable for the rows already sitting in it.
--
-- replicate_index is the second key so the ORDER BY that picks the next
-- task is satisfied by the index rather than by a sort.

create index observations_claimable_idx
    on observations (run_plan_id, replicate_index)
    where status in ('planned', 'queued', 'retryable', 'running');

-- ---------------------------------------------------------------------
-- claim_task: the only path to 'running'
-- ---------------------------------------------------------------------
--
-- FOR UPDATE SKIP LOCKED is the whole point: two workers running this
-- concurrently against one queued task take different rows, or one takes
-- the row and the other takes nothing. Neither blocks, and neither can
-- observe the same row as claimable.
--
-- `retry_number < max_attempts` in the predicate IS the retry ceiling. It
-- sits where claims happen, so no application path can loop past it —
-- including the reclaim branch, which is why a task that burned its last
-- attempt and then crashed is not reclaimed forever. reconcile_run
-- finalizes those to 'failed'.
--
-- SECURITY INVOKER (the default, stated explicitly): every caller is the
-- service role already, so DEFINER would widen the function's authority
-- for no gain. search_path is pinned regardless — an unpinned search_path
-- on a function this privileged is a resolution-hijack surface, and pinning
-- costs nothing.

create or replace function claim_task(
    p_run_plan_id uuid,
    p_owner text,
    p_lease_seconds int default 600
) returns setof observations
language sql
volatile
security invoker
set search_path = public, pg_temp
as $$
    update observations o
    set status            = 'running',
        lease_owner       = p_owner,
        lease_acquired_at = now(),
        lease_expires_at  = now() + make_interval(secs => p_lease_seconds),
        retry_number      = o.retry_number + 1
    where o.id = (
        select id
        from observations
        where run_plan_id = p_run_plan_id
          and (
                status in ('planned', 'queued')
             or (status = 'retryable'
                 and (next_attempt_at is null or next_attempt_at <= now()))
             or (status = 'running' and lease_expires_at < now())
          )
          and retry_number < max_attempts
        order by replicate_index
        limit 1
        for update skip locked
    )
    returning o.*;
$$;

comment on function claim_task(uuid, text, int) is
    'The ONLY path to observations.status = ''running''. Application code '
    'never writes ''running'' directly (Action Plan v2.0 Phase A). Claims one '
    'task per call, newest lease wins, retry ceiling enforced server-side.';

-- ---------------------------------------------------------------------
-- Least privilege on the claim function
-- ---------------------------------------------------------------------
--
-- Postgres grants EXECUTE on a new function to PUBLIC by default, which on
-- Supabase means `anon` and `authenticated` can call it over PostgREST.
-- Nothing would happen if they did — the function is SECURITY INVOKER, RLS
-- is enabled on `observations` from migration 0001, and neither role has a
-- policy — so an anonymous call updates zero rows. That is a safe outcome
-- resting on a second mechanism being correct, and a claim path is not the
-- place to rely on defence in depth when defence in front is one statement.
--
-- Guarded by a role-existence check: `anon` and `authenticated` are Supabase
-- roles that do not exist in a plain Postgres, and an unguarded REVOKE would
-- fail the from-scratch migration validation CI runs against an ephemeral
-- server.

revoke execute on function claim_task(uuid, text, int) from public;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'anon') then
        execute 'revoke execute on function claim_task(uuid, text, int) from anon';
    end if;
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        execute 'revoke execute on function claim_task(uuid, text, int) from authenticated';
    end if;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
        execute 'grant execute on function claim_task(uuid, text, int) to service_role';
    end if;
end
$$;
