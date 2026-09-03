"""Acceptance tests for migration 0012's two CHECK constraints and
`run_plans.market_id`'s foreign key, against real Postgres.

**This file exists because D-094 says a green suite is not evidence here.**
D-094 records, in the register, that `tests.conftest.FakeDB` is
`dict[str, list[dict]]` with no DDL, no constraint layer, no transactions and
no concurrency, so no CHECK, NOT NULL, foreign key or unique constraint
introduced by migrations 0001-0012 is exercised by any test that uses it. It
then names the acceptance criterion for 0012's two constraints explicitly:

    an attempted insert of a frozen_core plan with null provider_scope, and
    one with null market_id, each observed to fail — and not a passing test
    run.

That criterion was outstanding until this file. `tests/test_consumer_run_plan.py`
proves `create_consumer_run_plan` *writes* both columns; it cannot prove the
database would have refused the insert if it hadn't, because FakeDB accepts
anything. The two halves are complementary and neither substitutes for the
other.

The foreign key on `market_id` is tested here too, though D-094 did not name
it. It is the same class of fact — enforced by the database, invisible to the
fake — and a plan carrying a market id that resolves to no `markets` row would
satisfy both CHECKs while being exactly the mislabelled-score hazard D-087
describes.

Skipped when ATLAS_TEST_DATABASE_URL is unset, matching
tests/test_claim_task_postgres.py, so a developer without a local server still
gets a green suite. CI sets it and asserts these did not silently skip.

Fixtures duplicate that file's `migrated_db`/schema-from-scratch pattern
rather than importing from it: both files are self-contained by the repo's
existing convention, and a shared fixture would put the "apply every migration
from nothing" act — which is itself spec §6's migration validation — behind an
import.
"""

from __future__ import annotations

import os
import pathlib
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

DSN = os.environ.get("ATLAS_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="ATLAS_TEST_DATABASE_URL is not set — real-Postgres constraint tests "
    "need a live server",
)

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"

# Postgres SQLSTATEs. Asserted by code rather than by message text: the
# messages are localised and version-dependent, the codes are neither.
CHECK_VIOLATION = "23514"
FOREIGN_KEY_VIOLATION = "23503"


@pytest.fixture(scope="module")
def migrated_db():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
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
def fixtures(migrated_db):
    """One clean property + market per test."""
    with migrated_db.cursor() as cur:
        cur.execute(
            "truncate observations, recommendations, citations, evidence, costs, "
            "run_plans, prompt_versions, markets, properties, clients cascade;"
        )
        cur.execute("insert into clients (name, status) values ('t', 'active') returning id")
        client_id = cur.fetchone()[0]
        cur.execute(
            "insert into properties (client_id, name) values (%s, 'p') returning id",
            (client_id,),
        )
        property_id = cur.fetchone()[0]
        cur.execute(
            "insert into markets (property_id, market_code, language_code) "
            "values (%s, 'TH', 'en') returning id",
            (property_id,),
        )
        market_id = cur.fetchone()[0]
    return {"property_id": property_id, "market_id": market_id}


def _insert_plan(conn, **cols):
    names = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    with conn.cursor() as cur:
        cur.execute(
            f"insert into run_plans ({names}) values ({placeholders}) returning id",
            list(cols.values()),
        )
        return cur.fetchone()[0]


def _layer_b_plan(fixtures, **overrides):
    """The payload `consumer_run_plan.create_consumer_run_plan` writes."""
    cols = {
        "property_id": fixtures["property_id"],
        "run_type": "frozen_core",
        "replicate_count": 3,
        "status": "planned",
        "surface_layer": "consumer",
        "prompt_set_version": "frozen-core-samujana-v2",
        "provider_scope": ["openai", "gemini", "anthropic"],
        "market_id": fixtures["market_id"],
        "window_start": None,
        "window_end": None,
    }
    cols.update(overrides)
    return cols


# ---------------------------------------------------------------------------
# Positive control first, so a negative that "passes" is known to be failing
# for its own reason and not because every insert in this file is rejected.
# ---------------------------------------------------------------------------


def test_the_layer_b_payload_this_module_writes_is_accepted(migrated_db, fixtures):
    """Byte-for-byte the column set `create_consumer_run_plan` inserts. If
    this fails, the module writes a row Postgres will not take — which is the
    state the whole of migration 0012 left it in until it was fixed."""
    plan_id = _insert_plan(migrated_db, **_layer_b_plan(fixtures))

    with migrated_db.cursor() as cur:
        cur.execute(
            "select provider_scope, market_id from run_plans where id = %s", (plan_id,)
        )
        provider_scope, market_id = cur.fetchone()

    assert provider_scope == ["openai", "gemini", "anthropic"]
    assert str(market_id) == str(fixtures["market_id"])


# ---------------------------------------------------------------------------
# D-094's named acceptance criterion
# ---------------------------------------------------------------------------


def test_frozen_core_plan_with_null_provider_scope_is_refused(migrated_db, fixtures):
    with pytest.raises(psycopg2.Error) as exc:
        _insert_plan(migrated_db, **_layer_b_plan(fixtures, provider_scope=None))

    assert exc.value.pgcode == CHECK_VIOLATION
    assert "run_plans_frozen_core_has_provider_scope" in str(exc.value)


def test_frozen_core_plan_with_null_market_id_is_refused(migrated_db, fixtures):
    with pytest.raises(psycopg2.Error) as exc:
        _insert_plan(migrated_db, **_layer_b_plan(fixtures, market_id=None))

    assert exc.value.pgcode == CHECK_VIOLATION
    assert "run_plans_frozen_core_has_market_id" in str(exc.value)


def test_a_layer_a_frozen_core_plan_is_held_to_the_same_constraints(migrated_db, fixtures):
    """The constraints key on run_type, not on surface_layer, so Layer A is
    bound identically. This is the insert `plan_calibration_run` currently
    attempts — see the open item on its provider_scope write gap."""
    with pytest.raises(psycopg2.Error) as exc:
        _insert_plan(
            migrated_db,
            property_id=fixtures["property_id"],
            run_type="frozen_core",
            replicate_count=5,
            status="planned",
            prompt_set_version="frozen-core-samujana-v2",
        )

    assert exc.value.pgcode == CHECK_VIOLATION


# ---------------------------------------------------------------------------
# The foreign key — a market id that resolves to nothing
# ---------------------------------------------------------------------------


def test_a_market_id_referencing_no_markets_row_is_refused(migrated_db, fixtures):
    """Well-formed uuid, both CHECKs satisfied, no such market. D-087's hazard
    is a market value that is wrong rather than absent, and the FK is the only
    thing standing between a typo and a score computed against another
    market's eligible-platform list."""
    orphan = str(uuid.uuid4())

    with pytest.raises(psycopg2.Error) as exc:
        _insert_plan(migrated_db, **_layer_b_plan(fixtures, market_id=orphan))

    assert exc.value.pgcode == FOREIGN_KEY_VIOLATION
    assert "market" in str(exc.value).lower()


def test_a_malformed_market_id_is_refused_by_the_column_type(migrated_db, fixtures):
    """Not a uuid at all. Distinct from the FK case: this never reaches the
    constraint, the type does it."""
    with pytest.raises(psycopg2.Error):
        _insert_plan(migrated_db, **_layer_b_plan(fixtures, market_id="TH/en"))


# ---------------------------------------------------------------------------
# The constraints are conditional, not blanket — D-086/D-087 could not make
# either column NOT NULL because 26 system_zero rows have no meaningful value.
# ---------------------------------------------------------------------------


def test_a_system_zero_plan_may_leave_both_columns_null(migrated_db, fixtures):
    plan_id = _insert_plan(
        migrated_db,
        property_id=fixtures["property_id"],
        run_type="system_zero",
        replicate_count=1,
    )

    with migrated_db.cursor() as cur:
        cur.execute(
            "select provider_scope, market_id from run_plans where id = %s", (plan_id,)
        )
        assert cur.fetchone() == (None, None)


def test_an_empty_provider_scope_array_satisfies_the_check(migrated_db, fixtures):
    """The reason `create_consumer_run_plan` validates emptiness itself rather
    than leaving it to the database: `is not null` is true of `{}`. This test
    pins the database's actual behaviour so the application-side check is not
    later removed as redundant."""
    plan_id = _insert_plan(migrated_db, **_layer_b_plan(fixtures, provider_scope=[]))

    with migrated_db.cursor() as cur:
        cur.execute("select provider_scope from run_plans where id = %s", (plan_id,))
        assert cur.fetchone()[0] == []
