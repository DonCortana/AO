# Phase A — Engine Safety Implementation Spec

Input for the Claude Code (Opus) session. Verified against `atlas_export.zip` as of 2026-08-31; **re-verify against the live repo HEAD before starting.**

**Pre-flight (blocking):** confirm which migrations are actually applied to the live Supabase project. The README states 0005 is written but not applied, while D-053/D-062 reference 0008 behaviour — resolve the true applied state and record it before writing 0009. Number the new migration accordingly.

---

## 1. Migration 0009 — execution-state infrastructure

```sql
-- 0009_task_leases_and_retry_ceiling.sql
-- Execution state becomes database infrastructure (Advisory v1.1 §3.1,
-- Final Sequence Phase A). Leases + retry ceiling enforced server-side.

alter table observations
    add column lease_owner text,
    add column lease_acquired_at timestamptz,
    add column lease_expires_at timestamptz,
    add column attempt_no int not null default 0,
    add column max_attempts int not null default 3,   -- OS retry rule: retryable while < 3
    add column next_attempt_at timestamptz,
    add column provider_request_id text;

create index observations_claimable_idx
    on observations (run_plan_id, status)
    where status in ('queued', 'retryable');

-- Atomic claim. SECURITY DEFINER not required (service role), but keep the
-- function the ONLY claim path — application code never writes 'running'
-- directly again.
create or replace function claim_task(
    p_run_plan_id uuid,
    p_owner text,
    p_lease_seconds int default 600
) returns setof observations
language sql as $$
    update observations o
    set status            = 'running',
        lease_owner       = p_owner,
        lease_acquired_at = now(),
        lease_expires_at  = now() + make_interval(secs => p_lease_seconds),
        attempt_no        = o.attempt_no + 1
    where o.id = (
        select id from observations
        where run_plan_id = p_run_plan_id
          and (
                status = 'queued'
             or (status = 'retryable'
                 and (next_attempt_at is null or next_attempt_at <= now()))
             or (status = 'running' and lease_expires_at < now())  -- expired lease reclaim
          )
          and attempt_no < max_attempts
        order by replicate_index
        limit 1
        for update skip locked
    )
    returning o.*;
$$;
```

Design notes for implementation:

- `attempt_no < max_attempts` in the claim predicate is the retry ceiling — enforced where claims happen, unreachable by application drift. A task at the ceiling that is not complete transitions to `'failed'` (see reconcile below), never loops.
- Expired-lease reclaim inside `claim_task` replaces reconcile's blanket requeue as the *recovery* path for crashed workers; reconcile becomes reporting + failure finalization (below).
- D-033's accepted redial window (provider call succeeds, finalize dies) is unchanged by this design; leases narrow it but do not close it. Do not claim otherwise in comments.
- Finalize (running → terminal) keeps the existing status-guarded UPDATE in `resume.py`, now also guarded on `lease_owner = p_owner` so a worker whose lease expired and was reclaimed cannot finalize over the reclaimer.
- Backoff: on retryable outcomes set `next_attempt_at = now() + (base * 2^attempt_no)` in the finalize write. Base 30s, cap 15min.

## 2. `plan_run` — insert-missing-only

Replace the upsert with an insert that ignores conflicts (supabase-py: `upsert(..., on_conflict="task_id", ignore_duplicates=True)`, which emits `ON CONFLICT DO NOTHING`). Verify against the installed supabase-py version that `ignore_duplicates` produces DO NOTHING and not DO UPDATE; if unavailable, use a Postgres function. **Bug being fixed (verified):** current upsert regresses completed tasks to `status='planned'` AND blanks `model` to `''` on any re-plan. Add a regression test: plan → complete a task (with model set) → re-plan → assert status and model untouched.

## 3. `reconcile_run` — recovery becomes honest

New contract:

- Requeues **only** `running` rows with `lease_expires_at < now()` (crashed workers) that are still under `max_attempts`; expired rows at the ceiling go to `'failed'`.
- `'planned'`/`'queued'`/`'retryable'` rows are *reported* as pending, never touched — they are claimable already.
- Sets `run_plans.status = 'reconciled'` **only** when every observation is terminal (`complete`/`failed`/`excluded`); otherwise leaves run status unchanged and returns completeness. This corrects the current unconditional write (acknowledged in D-063).
- Docstring updated to match actual behaviour (current docstring claims an age check that does not exist).

## 4. `resume.py` — evidence through the vault

Replace the direct `evidence` upsert (currently `manifest_id=None`, `storage_path=None`, zero D-049 provenance columns) with `vault.store_evidence()`: upload first, full provenance, then row. Keep `on_conflict="observation_id"` idempotency (D-032). A score-bearing observation must not reach `complete` without `storage_path` + payload hash + provenance — enforce in the finalize path, and add the freeze-gate check to CI smoke.

## 5. Budget rail — month scope + enforcement point

`check_budget_rail`: filter `costs` to `created_at >= date_trunc('month', now())` (add `created_at` to `costs` if absent — verify schema). Call it in `resume_run` before each batch claim loop, honoring `'alert'`/`'stop'` semantics already documented. Keep lifetime spend available separately; do not conflate (three-cost-concept split is Phase D scope — only the month-scoping bug is Phase A).

## 6. Workflows fail closed + real PR CI

- `system-zero.yml`, `sentinel-weekly.yml`, `benchmark-monthly.yml`: replace TODO echo steps with `exit 1` and disable the schedules (`workflow_dispatch` only) until real runners exist. Green must mean executed.
- New `ci.yml` on pull_request: `pip install -e .[dev]` from a lock file, `ruff check`, `pytest`, migration validation against ephemeral Postgres (apply 0001→HEAD cleanly), smoke test. Required status check on `main`.
- Add the lock file (pip-tools or uv) — pin, don't float.

## 7. Tests (acceptance for Phase A)

1. Two concurrent workers, one queued task → exactly one claim succeeds (real ephemeral Postgres, not a fake — SKIP LOCKED semantics are the thing under test).
2. Expired lease → reclaimable by a second worker; original worker's finalize is rejected.
3. Task at `max_attempts` → never claimable; reconcile finalizes to `failed`.
4. Re-plan regression test (§2 above).
5. Reconcile with incomplete work → run status unchanged; with all-terminal → `reconciled`.
6. Resume finalize without vault evidence → observation cannot reach `complete`.

Existing test suite (143 passing core tests) must stay green throughout.

---

**Out of scope for Phase A** (deliberately — see Action Plan): Agent API adapter, surface-profile registry, measurement_cycle, calibration-scope model, three-cost-concept split, report renderer.
