"""Acceptance tests for migration 0009's claim_task(), against real Postgres.

Phase A spec §7.1-§7.3. These deliberately do NOT use the FakeDB double:
the properties under test are FOR UPDATE SKIP LOCKED, row-level locking and
the server-side retry ceiling, none of which a Python dictionary can
exhibit. A fake that "passed" these would be asserting against its own
reimplementation of the thing being verified.

Skipped when ATLAS_TEST_DATABASE_URL is unset, so a developer without a
local server still gets a green suite; CI sets it against an ephemeral
Postgres service and these become required. The same fixture applies every
migration 0001->HEAD from scratch, so a migration that does not apply
cleanly fails here too (spec §6's migration validation).
"""

from __future__ import annotations

import os
import pathlib
import threading

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DSN = os.environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="ATLAS_TEST_DATABASE_URL is not set — real-Postgres claim tests need a live server",
)

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def _connect():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def migrated_db():
    """A schema built by applying every migration in order, from nothing.

    Rebuilt from scratch rather than migrated in place: applying 0001->HEAD
    onto an empty schema is exactly what spec §6 asks CI to validate, so the
    validation and the test fixture are the same act.
    """
    conn = _connect()
    with conn.cursor() as cur:
        cur.execute("drop schema if exists public cascade; create schema public;")
        for path in sorted(MIGRATIONS_DIR.glob("0*.sql")):
            try:
                cur.execute(path.read_text())
            except psycopg2.Error as exc:  # pragma: no cover - failure path is the message
                pytest.fail(f"migration {path.name} failed to apply: {exc}")
    yield conn
    conn.close()


@pytest.fixture
def run_plan(migrated_db):
    """A clean property/market/prompt/run_plan, one per test."""
    with migrated_db.cursor() as cur:
        cur.execute(
            "truncate observations, recommendations, citations, evidence, costs, "
            "run_plans, prompt_versions, markets, properties, clients cascade;"
        )
        cur.execute("insert into clients (name, status) values ('t', 'active') returning id")
        client_id = cur.fetchone()[0]
        cur.execute(
            "insert into properties (client_id, name) values (%s, 'p') returning id", (client_id,)
        )
        property_id = cur.fetchone()[0]
        cur.execute(
            "insert into markets (property_id, market_code, language_code) "
            "values (%s, 'US', 'en') returning id",
            (property_id,),
        )
        market_id = cur.fetchone()[0]
        cur.execute(
            "insert into prompt_versions (market_id, set_type, version, prompt_text, intent_tier) "
            "values (%s, 'discovery', 'v1', 'q', 'D') returning id",
            (market_id,),
        )
        prompt_version_id = cur.fetchone()[0]
        cur.execute(
            "insert into run_plans (property_id, run_type, replicate_count) "
            "values (%s, 'system_zero', 1) returning id",
            (property_id,),
        )
        run_plan_id = cur.fetchone()[0]
    return {"run_plan_id": run_plan_id, "prompt_version_id": prompt_version_id}


def _add_task(conn, run_plan, task_id, **overrides):
    cols = {
        "task_id": task_id,
        "run_plan_id": run_plan["run_plan_id"],
        "prompt_version_id": run_plan["prompt_version_id"],
        "provider": "openai",
        "replicate_index": 0,
        "status": "planned",
    }
    cols.update(overrides)
    names = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    with conn.cursor() as cur:
        cur.execute(
            f"insert into observations ({names}) values ({placeholders}) returning id",
            list(cols.values()),
        )
        return cur.fetchone()[0]


def _status(conn, task_id):
    with conn.cursor() as cur:
        cur.execute(
            "select status, lease_owner, retry_number from observations where task_id = %s",
            (task_id,),
        )
        return cur.fetchone()


# --------------------------------------------------------------------------
# §7.1 — two concurrent workers, one queued task, exactly one claim
# --------------------------------------------------------------------------


def test_two_workers_one_task_exactly_one_claim(migrated_db, run_plan):
    """The core exclusivity guarantee, proved with a real uncommitted lock.

    Worker A claims inside an open transaction and does not commit. While A
    holds the row lock, worker B calls claim_task on a separate connection.
    SKIP LOCKED means B must skip the locked row and come back empty rather
    than blocking on it or claiming it a second time.
    """
    _add_task(migrated_db, run_plan, "solo")

    a = psycopg2.connect(DSN)  # explicit transaction, NOT autocommit
    b = psycopg2.connect(DSN)
    try:
        with a.cursor() as ca, b.cursor() as cb:
            ca.execute("select task_id from claim_task(%s, 'worker-a')", (run_plan["run_plan_id"],))
            claimed_a = ca.fetchall()
            # A's transaction is still open here — the row is locked and
            # uncommitted, which is precisely the window a naive
            # status-guarded UPDATE would let B through.
            cb.execute("select task_id from claim_task(%s, 'worker-b')", (run_plan["run_plan_id"],))
            claimed_b = cb.fetchall()
        a.commit()
        b.commit()
    finally:
        a.close()
        b.close()

    assert len(claimed_a) + len(claimed_b) == 1, (
        f"exactly one worker must claim; got a={claimed_a} b={claimed_b}"
    )
    status, owner, attempts = _status(migrated_db, "solo")
    assert status == "running"
    assert owner == "worker-a"
    # Claimed once means the attempt counter moved exactly once.
    assert attempts == 1


def test_many_workers_never_double_claim(migrated_db, run_plan):
    """Threaded version: 8 workers race for 20 tasks, every task claimed once."""
    task_count = 20
    for i in range(task_count):
        _add_task(migrated_db, run_plan, f"t{i:02d}", replicate_index=i)

    claims: list[tuple[str, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(name: str):
        conn = psycopg2.connect(DSN)
        conn.autocommit = True
        try:
            barrier.wait()
            while True:
                with conn.cursor() as cur:
                    cur.execute(
                        "select task_id from claim_task(%s, %s)", (run_plan["run_plan_id"], name)
                    )
                    row = cur.fetchone()
                if row is None:
                    return
                with lock:
                    claims.append((row[0], name))
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claimed_ids = [task_id for task_id, _ in claims]
    assert len(claimed_ids) == task_count, "every task should be claimed exactly once"
    assert len(set(claimed_ids)) == task_count, f"a task was claimed twice: {claims}"


# --------------------------------------------------------------------------
# §7.2 — expired lease is reclaimable; the original worker cannot finalize
# --------------------------------------------------------------------------


def test_expired_lease_reclaimed_and_original_finalize_rejected(migrated_db, run_plan):
    _add_task(migrated_db, run_plan, "leased")

    with migrated_db.cursor() as cur:
        cur.execute("select task_id from claim_task(%s, 'worker-a')", (run_plan["run_plan_id"],))
        assert cur.fetchone()[0] == "leased"

        # While worker-a's lease is live, nobody else can take the task.
        cur.execute("select task_id from claim_task(%s, 'worker-b')", (run_plan["run_plan_id"],))
        assert cur.fetchall() == [], "a live lease must not be reclaimable"

        # worker-a stalls; its lease expires.
        cur.execute(
            "update observations set lease_expires_at = now() - interval '1 second' "
            "where task_id = 'leased'"
        )

        cur.execute("select task_id from claim_task(%s, 'worker-b')", (run_plan["run_plan_id"],))
        assert cur.fetchone()[0] == "leased", "an expired lease must be reclaimable"

        # worker-a now wakes up and tries to finalize. This is the exact
        # guarded UPDATE atlas.runners.resume issues, lease_owner included.
        cur.execute(
            "update observations set status = 'complete' "
            "where task_id = 'leased' and status = 'running' and lease_owner = %s "
            "returning task_id",
            ("worker-a",),
        )
        assert cur.fetchall() == [], "the reclaimed worker's finalize must match zero rows"

    status, owner, attempts = _status(migrated_db, "leased")
    assert status == "running", "worker-a must not have completed the task"
    assert owner == "worker-b", "the task belongs to the reclaimer"
    assert attempts == 2, "each claim increments the attempt counter"

    # worker-b's finalize, by contrast, is accepted.
    with migrated_db.cursor() as cur:
        cur.execute(
            "update observations set status = 'complete' "
            "where task_id = 'leased' and status = 'running' and lease_owner = %s "
            "returning task_id",
            ("worker-b",),
        )
        assert cur.fetchone()[0] == "leased"


# --------------------------------------------------------------------------
# §7.3 — a task at max_attempts is never claimable
# --------------------------------------------------------------------------


def test_task_at_retry_ceiling_is_never_claimable(migrated_db, run_plan):
    _add_task(migrated_db, run_plan, "burned", status="retryable", retry_number=3, max_attempts=3)

    with migrated_db.cursor() as cur:
        cur.execute("select task_id from claim_task(%s, 'worker-a')", (run_plan["run_plan_id"],))
        assert cur.fetchall() == [], "a task at the ceiling must not be claimable"

        # Nor via the expired-lease reclaim branch — the ceiling applies to
        # every branch of the predicate, not just the retryable one.
        cur.execute(
            "update observations set status = 'running', "
            "lease_expires_at = now() - interval '1 minute' where task_id = 'burned'"
        )
        cur.execute("select task_id from claim_task(%s, 'worker-b')", (run_plan["run_plan_id"],))
        assert cur.fetchall() == [], "the ceiling must also block expired-lease reclaim"

    # One attempt below the ceiling, the same row is claimable — proving the
    # block above is the ceiling and not some unrelated predicate failure.
    with migrated_db.cursor() as cur:
        cur.execute("update observations set retry_number = 2 where task_id = 'burned'")
        cur.execute("select task_id from claim_task(%s, 'worker-c')", (run_plan["run_plan_id"],))
        assert cur.fetchone()[0] == "burned"


def test_backoff_window_gates_retryable_claims(migrated_db, run_plan):
    """next_attempt_at is what stops a failing task spinning in a tight loop."""
    _add_task(
        migrated_db,
        run_plan,
        "backing-off",
        status="retryable",
        retry_number=1,
    )
    with migrated_db.cursor() as cur:
        cur.execute(
            "update observations set next_attempt_at = now() + interval '5 minutes' "
            "where task_id = 'backing-off'"
        )
        cur.execute("select task_id from claim_task(%s, 'w')", (run_plan["run_plan_id"],))
        assert cur.fetchall() == [], "a task inside its backoff window must not be claimable"

        cur.execute(
            "update observations set next_attempt_at = now() - interval '1 second' "
            "where task_id = 'backing-off'"
        )
        cur.execute("select task_id from claim_task(%s, 'w')", (run_plan["run_plan_id"],))
        assert cur.fetchone()[0] == "backing-off"


def test_planned_rows_are_claimable(migrated_db, run_plan):
    """Regression guard for a hole the spec's draft predicate would have left.

    plan_run writes 'planned', and reconcile no longer promotes anything to
    'queued'. If claim_task did not accept 'planned', every freshly planned
    task would strand and no run could ever start.
    """
    _add_task(migrated_db, run_plan, "fresh", status="planned")
    with migrated_db.cursor() as cur:
        cur.execute("select task_id from claim_task(%s, 'w')", (run_plan["run_plan_id"],))
        assert cur.fetchone()[0] == "fresh"


def test_claim_order_follows_replicate_index(migrated_db, run_plan):
    for i in (2, 0, 1):
        _add_task(migrated_db, run_plan, f"r{i}", replicate_index=i)
    seen = []
    with migrated_db.cursor() as cur:
        for _ in range(3):
            cur.execute(
                "select replicate_index from claim_task(%s, 'w')", (run_plan["run_plan_id"],)
            )
            seen.append(cur.fetchone()[0])
    assert seen == [0, 1, 2]
