"""Fake Supabase double + fixture helpers for induced-failure tests.

Real (deterministic, no network) stand-in for the chained
`db.table(...).select(...).eq(...).in_(...).execute()` interface that
atlas.reconciliation.reconcile and atlas.runners.resume are written against.
Mirrors the two Postgrest/Supabase behaviours the pipeline's idempotency
guarantees actually depend on (confirmed against the installed `postgrest`
2.31.0 package, not assumed):

  - insert/update/upsert default to `Prefer: return=representation`, so
    `.execute().data` is the affected row(s) as they exist after the write —
    this is what lets the runner treat "0 rows returned" from a
    status-guarded UPDATE as "someone else already claimed/finished this".
  - upsert(on_conflict=...) merges into the existing row on a conflict
    instead of erroring or duplicating.

Not a Postgres emulator — no transactions, no real concurrency. Good enough
to prove the application-level idempotency logic in resume.py/reconcile.py
is wired correctly; the actual once-only guarantee for evidence/costs comes
from the unique constraint in migrations/0003, exercised here only via this
fake's matching on_conflict behaviour.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest


@dataclass
class _Result:
    data: list[dict]


class _Query:
    def __init__(self, db: "FakeDB", table_name: str):
        self.db = db
        self.table_name = table_name
        self.filters: list[tuple[str, str, object]] = []
        self.select_cols: list[str] | None = None
        self.op: str | None = None
        self.payload: dict | list[dict] | None = None
        self.on_conflict: str | None = None

    def select(self, cols: str) -> "_Query":
        self.op = self.op or "select"
        self.select_cols = [c.strip() for c in cols.split(",")]
        return self

    def eq(self, col: str, val: object) -> "_Query":
        self.filters.append((col, "eq", val))
        return self

    def in_(self, col: str, values) -> "_Query":
        self.filters.append((col, "in", list(values)))
        return self

    def update(self, payload: dict) -> "_Query":
        self.op = "update"
        self.payload = payload
        return self

    def insert(self, payload) -> "_Query":
        self.op = "insert"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict: str | None = None) -> "_Query":
        self.op = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        return self

    def _rows(self) -> list[dict]:
        return self.db.tables.setdefault(self.table_name, [])

    def _matches(self, row: dict) -> bool:
        for col, op, val in self.filters:
            if op == "eq" and row.get(col) != val:
                return False
            if op == "in" and row.get(col) not in val:
                return False
        return True

    def _project(self, row: dict) -> dict:
        if not self.select_cols or self.select_cols == ["*"]:
            return dict(row)
        return {c: row.get(c) for c in self.select_cols}

    def execute(self) -> _Result:
        if self.db.before_execute is not None:
            self.db.before_execute(self)

        rows = self._rows()

        if self.op in (None, "select"):
            matched = [r for r in rows if self._matches(r)]
            return _Result([self._project(r) for r in matched])

        if self.op == "update":
            matched = [r for r in rows if self._matches(r)]
            for r in matched:
                r.update(self.payload)  # type: ignore[arg-type]
            return _Result([self._project(r) for r in matched])

        if self.op == "insert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            created = []
            for p in payloads:
                row = dict(p)
                row.setdefault("id", str(uuid.uuid4()))
                rows.append(row)
                created.append(row)
            return _Result([self._project(r) for r in created])

        if self.op == "upsert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            results = []
            for p in payloads:
                existing = None
                if self.on_conflict:
                    existing = next((r for r in rows if r.get(self.on_conflict) == p.get(self.on_conflict)), None)
                if existing is not None:
                    existing.update(p)
                    results.append(existing)
                else:
                    row = dict(p)
                    row.setdefault("id", str(uuid.uuid4()))
                    rows.append(row)
                    results.append(row)
            return _Result([self._project(r) for r in results])

        raise NotImplementedError(self.op)


@dataclass
class FakeDB:
    tables: dict[str, list[dict]] = field(default_factory=dict)
    # Optional hook, called with the pending _Query just before it mutates/
    # reads state — tests use this to simulate a process crash landing
    # exactly between "provider call succeeded" and "write committed".
    before_execute: object = None

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def seed(self, name: str, rows: list[dict]) -> None:
        self.tables.setdefault(name, []).extend(dict(r) for r in rows)


@pytest.fixture
def fake_db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def pipeline(fake_db: FakeDB) -> dict:
    """One property/market/prompt_version/run_plan/observation row, wired
    together the way run_planner.plan_run would leave them — status
    'queued', as if the planner already ran and a scheduler dispatch is
    what got delayed or dropped (Operating System §4)."""
    property_id = str(uuid.uuid4())
    market_id = str(uuid.uuid4())
    prompt_version_id = str(uuid.uuid4())
    run_plan_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    observation_id = str(uuid.uuid4())

    fake_db.seed(
        "markets",
        [{"id": market_id, "property_id": property_id, "market_code": "US", "language_code": "en"}],
    )
    fake_db.seed(
        "prompt_versions",
        [
            {
                "id": prompt_version_id,
                "market_id": market_id,
                "set_type": "discovery",
                "version": "v1",
                "prompt_text": "What is Atlas Optimisation?",
                "intent_tier": "D",
            }
        ],
    )
    fake_db.seed(
        "run_plans",
        [{"id": run_plan_id, "property_id": property_id, "run_type": "system_zero", "status": "planned"}],
    )
    fake_db.seed(
        "observations",
        [
            {
                "id": observation_id,
                "task_id": task_id,
                "run_plan_id": run_plan_id,
                "prompt_version_id": prompt_version_id,
                "provider": "openai",
                "replicate_index": 0,
                "status": "queued",
                "retry_number": 0,
            }
        ],
    )

    return {
        "property_id": property_id,
        "market_id": market_id,
        "prompt_version_id": prompt_version_id,
        "run_plan_id": run_plan_id,
        "task_id": task_id,
        "observation_id": observation_id,
    }
