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

  - upsert(ignore_duplicates=True) sends `resolution=ignore-duplicates`,
    which PostgREST turns into ON CONFLICT DO NOTHING — the existing row is
    neither updated nor returned. atlas.planner.run_planner depends on this.
  - rpc("claim_task", ...) is emulated in _RpcCall, predicate for predicate
    against the migration 0009 function.

Not a Postgres emulator — no transactions, no real concurrency. Good enough
to prove the application-level idempotency logic in resume.py/reconcile.py
is wired correctly; the actual once-only guarantee for evidence/costs comes
from the unique constraint in migrations/0003, exercised here only via this
fake's matching on_conflict behaviour.

**The concurrency guarantees are not testable here and are not tested here.**
FOR UPDATE SKIP LOCKED, and therefore the "two workers cannot claim the same
task" property, is proved in tests/test_claim_task_postgres.py against a real
ephemeral Postgres. A green run of this fake says the application logic is
wired correctly, never that the locking works.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

# Every table in migration 0001 declares `created_at timestamptz not null
# default now()`. The fake mirrors that so code which orders by created_at
# (e.g. atlas.calibration.store.eligible_platforms picking the latest
# calibration run) behaves here as it does against Postgres. Monotonic rather
# than real-clock so ordering inside a test is deterministic.
_CLOCK = itertools.count()
_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _now() -> str:
    return (_EPOCH + timedelta(seconds=next(_CLOCK))).isoformat()


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
        self.ignore_duplicates: bool = False
        # A list, not a single tuple: PostgREST allows chained .order()
        # calls and applies them left to right as successive sort keys.
        self.order_by: list[tuple[str, bool]] = []

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

    def gte(self, col: str, val: object) -> "_Query":
        self.filters.append((col, "gte", val))
        return self

    def order(self, col: str, desc: bool = False) -> "_Query":
        self.order_by.append((col, desc))
        return self

    def update(self, payload: dict) -> "_Query":
        self.op = "update"
        self.payload = payload
        return self

    def insert(self, payload) -> "_Query":
        self.op = "insert"
        self.payload = payload
        return self

    def upsert(self, payload, on_conflict: str | None = None, ignore_duplicates: bool = False) -> "_Query":
        self.op = "upsert"
        self.payload = payload
        self.on_conflict = on_conflict
        # postgrest 2.31.0 sends `Prefer: resolution=ignore-duplicates` for
        # this, which PostgREST turns into ON CONFLICT DO NOTHING — the
        # existing row is left untouched and is NOT returned. Modelled here
        # because atlas.planner.run_planner depends on exactly that
        # distinction to avoid regressing completed tasks.
        self.ignore_duplicates = ignore_duplicates
        return self

    def _rows(self) -> list[dict]:
        return self.db.tables.setdefault(self.table_name, [])

    def _matches(self, row: dict) -> bool:
        for col, op, val in self.filters:
            if op == "eq" and row.get(col) != val:
                return False
            if op == "in" and row.get(col) not in val:
                return False
            if op == "gte":
                actual = row.get(col)
                # Both sides are ISO 8601 timestamps in every current caller
                # (the month-scoped budget rail). String comparison is only
                # correct for those if the offsets match, so compare parsed
                # datetimes instead.
                if actual is None or _as_utc(actual) < _as_utc(val):
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
            # Applied in reverse with a stable sort, so the first .order()
            # call is the primary key. A missing/NULL column sorts last and
            # never gets compared against a value of another type — Postgres
            # sorts NULLS LAST on ASC, and a test double that raised
            # TypeError here would fail on data the real database accepts.
            for col, desc in reversed(self.order_by):
                matched = sorted(
                    matched,
                    key=lambda r, c=col: (
                        r.get(c) is None,
                        r.get(c) if r.get(c) is not None else 0,
                    ),
                    reverse=desc,
                )
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
                row.setdefault("created_at", _now())
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
                    if self.ignore_duplicates:
                        # DO NOTHING: row untouched, and not part of the
                        # returned representation.
                        continue
                    existing.update(p)
                    results.append(existing)
                else:
                    row = dict(p)
                    row.setdefault("id", str(uuid.uuid4()))
                    row.setdefault("created_at", _now())
                    rows.append(row)
                    results.append(row)
            return _Result([self._project(r) for r in results])

        raise NotImplementedError(self.op)


def _as_utc(value):
    """Parse a timestamp the way PostgREST returns one. Mirrors
    atlas.reconciliation.reconcile._as_utc — kept local so the fake does not
    depend on the module it is used to test."""
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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

    def rpc(self, fn: str, params: dict) -> "_RpcCall":
        if fn != "claim_task":
            raise NotImplementedError(f"FakeDB has no emulation for rpc({fn!r})")
        return _RpcCall(self, params)


class _RpcCall:
    """Emulates the migration 0009 `claim_task()` function.

    Mirrors that function's predicate clause for clause — the claimable
    statuses, the retry ceiling, the backoff gate and the expired-lease
    reclaim — so application logic written against the real RPC is exercised
    honestly here.

    What it deliberately does NOT emulate is FOR UPDATE SKIP LOCKED, because
    there is nothing to emulate: this fake is single-threaded and has no
    transactions. The concurrency guarantee itself is proved against a real
    ephemeral Postgres in tests/test_claim_task_postgres.py, which is the
    only place it can be proved. Do not read a passing test here as evidence
    that two workers cannot double-claim.
    """

    def __init__(self, db: "FakeDB", params: dict):
        self.db = db
        self.params = params

    def execute(self) -> _Result:
        now = datetime.now(timezone.utc)
        run_plan_id = self.params["p_run_plan_id"]
        owner = self.params["p_owner"]
        lease_seconds = self.params.get("p_lease_seconds", 600)

        def claimable(row: dict) -> bool:
            if row.get("run_plan_id") != run_plan_id:
                return False
            if (row.get("retry_number") or 0) >= (row.get("max_attempts") or 3):
                return False
            status = row.get("status")
            if status in ("planned", "queued"):
                return True
            if status == "retryable":
                nxt = _as_utc(row.get("next_attempt_at"))
                return nxt is None or nxt <= now
            if status == "running":
                expires = _as_utc(row.get("lease_expires_at"))
                return expires is not None and expires < now
            return False

        rows = self.db.tables.setdefault("observations", [])
        candidates = sorted(
            (r for r in rows if claimable(r)),
            key=lambda r: r.get("replicate_index") or 0,
        )
        if not candidates:
            return _Result([])

        row = candidates[0]
        row["status"] = "running"
        row["lease_owner"] = owner
        row["lease_acquired_at"] = now.isoformat()
        row["lease_expires_at"] = (now + timedelta(seconds=lease_seconds)).isoformat()
        row["retry_number"] = (row.get("retry_number") or 0) + 1
        return _Result([dict(row)])


@pytest.fixture
def fake_db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def fake_drive(monkeypatch) -> list[dict]:
    """Stub out the Google Drive leg of the Evidence Vault, nothing else.

    Deliberately patches `vault.upload_to_drive` rather than
    `vault.store_evidence`. The real `store_evidence` still runs, so the
    evidence row it writes — and every Operating System §7 provenance column
    on it — is the genuine article and can be asserted on. Stubbing
    store_evidence itself would make the Phase A §4 tests assert against a
    mock of the exact behaviour under test.

    Returns the list of uploads performed, newest last.
    """
    uploads: list[dict] = []

    def _upload(local_path, folder_id=None, *, record=None):
        with open(local_path, encoding="utf-8") as handle:
            payload = handle.read()
        uploads.append(
            {
                "local_path": local_path,
                "folder_id": folder_id,
                "record": record,
                "payload": payload,
            }
        )
        return f"https://drive.example/file/{record.payload_hash}" if record else "https://drive.example/file/anon"

    monkeypatch.setattr("atlas.evidence.vault.upload_to_drive", _upload)
    return uploads


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
                # As migration 0009 leaves a freshly planned row.
                "retry_number": 0,
                "max_attempts": 3,
                "lease_owner": None,
                "lease_acquired_at": None,
                "lease_expires_at": None,
                "next_attempt_at": None,
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
