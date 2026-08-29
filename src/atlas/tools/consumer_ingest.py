"""Layer B consumer-surface ingest — a Sheet of human captures in, validated
`observations` + `evidence` rows out.

Methodology §8.3 makes consumer-surface checks human-initiated unless a
provider offers an approved automation path, and none currently does. So there
is nothing here to execute: this is not a runner. It is the ingest and
validation path for what a human captured in a browser, writing the rows that
`atlas.calibration.loader.load_cells` will admit to the Layer B frame.

Three commands, mirroring `atlas.tools.rpv_labeling` rather than inventing a
second idiom (design §6 calls them Template / Validate / Commit):

    export    write the capture template — one row per (prompt, replicate) —
              into a Sheet tab, pre-filled with prompt text and prompt_version_id
    validate  read the tab back and check every row; report, change nothing
    import    validate, then write the observations and store the evidence

Two rules govern this module, and both exist because the failure they prevent
is silent rather than loud.

**`surface_layer` is passed explicitly at every insert, never defaulted.**
Migration 0005 gave the column `not null default 'api'`. A write path that
forgets the field does not fail — it lands an API-layer row, putting a human
capture into the AVS scoring frame. That is exactly the inversion D-043 was
written to prevent, reintroduced from the write side. Hence the explicit value
on every payload, and `assert_no_api_layer_leak` re-reading the plan afterwards.

**The observation row is written before its evidence.** `vault.store_evidence`
upserts on `observation_id`, and Postgres treats NULLs as distinct under a
unique index — so evidence written without an observation to hang from appends
a new row on every re-run instead of updating one. §4's re-ingest story depends
on that idempotency reaching the evidence ledger, not just the observation.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from atlas.evidence.vault import EvidenceRecord, sha256_file, store_evidence
from atlas.tools.sheets import read_values, write_values

__all__ = [
    "CONSUMER_SURFACE_MODELS",
    "DATA_CLASS",
    "FROZEN_CORE_VERSION",
    "PROVIDERS",
    "SHEET_COLUMNS",
    "SHEET_LAST_COLUMN",
    "SURFACE_LAYER",
    "CaptureRow",
    "IngestReport",
    "RowError",
    "ValidationReport",
    "assert_no_api_layer_leak",
    "build_template_rows",
    "export_template",
    "frozen_core_prompts",
    "import_from_sheet",
    "parse_sheet",
    "read_values",
    "task_id_for",
    "validate",
]

# The capture surface. Order is load-bearing: export writes in this order and
# validate reads positionally after confirming the header matches.
SHEET_COLUMNS: tuple[str, ...] = (
    # Pre-filled by `export`. The operator does not type these.
    "prompt_version_id",
    "intent_tier",
    "prompt_text",
    "provider",
    "replicate_index",
    # Filled by the operator, left to right in the order the capture happens:
    # paste the answer, record where it came from, attach the screenshot, say
    # when and who.
    "capture_text",
    "surface_url",
    "evidence_path",
    "captured_start",
    "captured_end",
    "operator",
    "notes",
)

_LAST_COLUMN = chr(ord("A") + len(SHEET_COLUMNS) - 1)
SHEET_LAST_COLUMN = _LAST_COLUMN

# Everything the operator fills. A row with none of these set is a cell that
# was not captured — skipped, not an error, and counted toward the absent-cell
# warning. A row with SOME of them set is a half-filled capture, and every
# required column is then enforced.
OPERATOR_COLUMNS: tuple[str, ...] = (
    "capture_text",
    "surface_url",
    "evidence_path",
    "captured_start",
    "captured_end",
    "operator",
    "notes",
)
REQUIRED_OPERATOR_COLUMNS: tuple[str, ...] = (
    "capture_text",
    "evidence_path",
    "captured_start",
    "captured_end",
    "operator",
)

# Migration 0005 (D-043) widened the check constraint to these five. `google_ai`
# is consumer-only by `observations_google_ai_is_consumer_only` (D-042), which
# every row this module writes satisfies by construction.
PROVIDERS: tuple[str, ...] = ("openai", "gemini", "perplexity", "anthropic", "google_ai")

# §5: `model` is NOT NULL with a '' default and a consumer web UI exposes no
# model identifier. '' is honest but loses the surface identity; a fabricated
# model name would be worse. These literals mean "this consumer surface as
# presented on the capture date" and are derived from the provider rather than
# typed by the operator, so the vocabulary cannot drift one row at a time.
# Pending a decision-register entry — design §8 item 2.
CONSUMER_SURFACE_MODELS: dict[str, str] = {
    "openai": "chatgpt-web",
    "gemini": "gemini-web",
    "perplexity": "perplexity-web",
    "anthropic": "claude-web",
    "google_ai": "google-ai-overviews",
}

SURFACE_LAYER = "consumer"

# The evidence is the screenshot of the surface, which is a capture of an AI
# response — the same data class as a Layer A payload. A parameter on
# EvidenceRecord, not a vault default, and checked against migration 0001's
# constraint.
DATA_CLASS = "raw_ai_response"

FROZEN_CORE_VERSION = "frozen-core-samujana-v1"

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def task_id_for(
    run_plan_id: str, provider: str, prompt_version_id: str, replicate_index: int
) -> str:
    """Design §4's scheme, verbatim and readable.

    Deliberately not `planner.deterministic_task_id`: that one hashes, and an
    operator reconciling a sheet against the database needs to read the id and
    see which cell it is. Deterministic either way, which is what §4 requires —
    re-ingesting a corrected capture must update its cell rather than duplicate
    it.

    Provider is in the key for the same reason it is in
    `deterministic_task_id`'s: the measurement unit is the cell
    `(prompt_version_id, provider)` that `atlas.calibration.agreement` pairs
    on, so two surfaces answering the same prompt are two measurements, not one
    measured twice. Without provider they collide on one `task_id` and, since
    ingest upserts on it, the second capture would silently overwrite the
    first.
    """
    return f"consumer:{run_plan_id}:{provider}:{prompt_version_id}:{replicate_index}"


# ---------------------------------------------------------------------
# Rows and errors
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class RowError:
    row_number: int  # 1-based sheet row, header included, so it matches the UI
    column: str | None
    message: str

    def __str__(self) -> str:
        where = f"row {self.row_number}"
        if self.column:
            where += f", column '{self.column}'"
        return f"{where}: {self.message}"


@dataclass(frozen=True)
class CaptureRow:
    row_number: int
    prompt_version_id: str
    provider: str
    replicate_index: int
    capture_text: str
    evidence_path: str
    captured_start: datetime
    captured_end: datetime
    operator: str
    surface_url: str = ""
    notes: str = ""

    @property
    def cell(self) -> tuple[str, str]:
        """The agreement frame's unit: (prompt_version_id, provider), matching
        `atlas.calibration.agreement`'s pairing key."""
        return (self.prompt_version_id, self.provider)

    def to_observation(self, run_plan_id: str) -> dict:
        """The `observations` payload for this capture — design §5's table.

        `surface_layer` is set here, unconditionally, and there is no parameter
        that can turn it off. The envelope holds `capture_text` only: the
        surface URL goes to `evidence.source_reference` and the capture time to
        request_time/completion_time, because `store_evidence` does not write
        `evidence.captured_at` and that column would otherwise record the
        ingest time as the capture time.
        """
        return {
            "task_id": task_id_for(
                run_plan_id, self.provider, self.prompt_version_id, self.replicate_index
            ),
            "run_plan_id": run_plan_id,
            "prompt_version_id": self.prompt_version_id,
            "provider": self.provider,
            "model": CONSUMER_SURFACE_MODELS[self.provider],
            "model_snapshot": None,
            "tool_version": None,
            "replicate_index": self.replicate_index,
            "status": "complete",
            "search_available": None,
            "search_invoked": None,
            "grounding_status": None,
            "raw_response": {"capture_text": self.capture_text},
            "request_time": self.captured_start.isoformat(),
            "completion_time": self.captured_end.isoformat(),
            "input_tokens": None,
            "output_tokens": None,
            "search_tool_units": None,
            "cost_usd": None,
            # A human capture has no metered cost. Unknown is flagged, never
            # treated as zero (Execution Plan §3).
            "is_unknown_cost": True,
            "surface_layer": SURFACE_LAYER,
        }


@dataclass
class ValidationReport:
    valid: list[CaptureRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    uncaptured: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [
            (
                f"{len(self.valid)} capture(s), {len(self.errors)} error(s), "
                f"{len(self.warnings)} warning(s), {self.uncaptured} uncaptured cell(s)"
            )
        ]
        lines.extend(f"  ERROR {error}" for error in self.errors)
        lines.extend(f"  WARN  {warning}" for warning in self.warnings)
        return "\n".join(lines)


@dataclass
class IngestReport:
    validation: ValidationReport
    observations_written: int = 0
    evidence_written: int = 0
    committed: bool = False

    @property
    def ok(self) -> bool:
        return self.validation.ok

    def render(self) -> str:
        lines = [self.validation.render()]
        if self.committed:
            lines.append(
                f"\nwrote {self.observations_written} observation(s) and "
                f"{self.evidence_written} evidence row(s)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------
# Parsing — strict, no coercion
# ---------------------------------------------------------------------


def _text(cell) -> str:
    return "" if cell is None else str(cell).strip()


def _parse_timestamp(raw: str, row_number: int, column: str) -> tuple[datetime | None, RowError | None]:
    """An offset is required. A naive timestamp read as UTC is a silent
    seven-hour error for an operator capturing in TH, and §7 requires a UTC
    timestamp on every evidence record — so this rejects rather than assumes."""
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None, RowError(
            row_number,
            column,
            f"{raw!r} is not an ISO 8601 timestamp. Use e.g. "
            "'2026-08-29T14:03:00+07:00'",
        )
    if value.tzinfo is None:
        return None, RowError(
            row_number,
            column,
            f"{raw!r} has no UTC offset. Add one (e.g. '+07:00') — a naive "
            "timestamp read as UTC would silently move the capture by hours, "
            "and this is the only record of when it happened",
        )
    return value, None


def parse_sheet(values: list[list]) -> tuple[list[CaptureRow], list[RowError], list[dict]]:
    """Parse raw sheet values, collecting every error rather than stopping at
    the first.

    Returns (rows, errors, uncaptured_cells). The uncaptured cells are template
    rows the operator left empty — not errors, but the input to the absent-cell
    warning, which is the condition that drops the frame to n=9.
    """
    if not values:
        return [], [RowError(1, None, "the sheet tab is empty")], []

    header = [_text(c) for c in values[0]]
    if tuple(header[: len(SHEET_COLUMNS)]) != SHEET_COLUMNS:
        return (
            [],
            [
                RowError(
                    1,
                    None,
                    f"header mismatch. Expected {list(SHEET_COLUMNS)}, got {header}. "
                    "Re-run `export` rather than editing the header by hand — the "
                    "importer reads columns positionally",
                )
            ],
            [],
        )

    index = {name: position for position, name in enumerate(SHEET_COLUMNS)}
    rows: list[CaptureRow] = []
    errors: list[RowError] = []
    uncaptured: list[dict] = []

    for offset, raw_row in enumerate(values[1:], start=2):
        cells = [_text(c) for c in raw_row] + [""] * (len(SHEET_COLUMNS) - len(raw_row))

        def get(name: str, _cells: list[str] = cells) -> str:
            """Positional read. `_cells` is bound as a default so this closes
            over *this* row rather than the loop variable."""
            return _cells[index[name]]

        prompt_version_id = get("prompt_version_id")
        captured = any(get(c) for c in OPERATOR_COLUMNS)

        if not captured:
            if not prompt_version_id:
                continue  # a genuinely blank row below the template
            uncaptured.append(
                {
                    "prompt_version_id": prompt_version_id,
                    "provider": get("provider"),
                    "replicate_index": get("replicate_index"),
                }
            )
            continue

        row_errors: list[RowError] = []

        if not _UUID.match(prompt_version_id):
            row_errors.append(
                RowError(offset, "prompt_version_id", f"{prompt_version_id!r} is not a UUID")
            )

        provider = get("provider")
        if provider not in PROVIDERS:
            row_errors.append(
                RowError(
                    offset,
                    "provider",
                    f"{provider!r} is not one of {list(PROVIDERS)} — the "
                    "observations check constraint (migration 0005) would reject it",
                )
            )

        replicate_raw = get("replicate_index")
        replicate_index = None
        try:
            replicate_index = int(float(replicate_raw))
        except ValueError:
            row_errors.append(
                RowError(offset, "replicate_index", f"{replicate_raw!r} is not an integer")
            )
        else:
            if replicate_index < 0:
                row_errors.append(
                    RowError(offset, "replicate_index", f"{replicate_index} must be >= 0")
                )

        for column in REQUIRED_OPERATOR_COLUMNS:
            if not get(column):
                row_errors.append(
                    RowError(
                        offset,
                        column,
                        "must not be blank on a captured row. Clear the whole row "
                        "if this cell was not captured — a half-filled capture is "
                        "not a capture",
                    )
                )

        started, start_error = (None, None)
        ended, end_error = (None, None)
        if get("captured_start"):
            started, start_error = _parse_timestamp(get("captured_start"), offset, "captured_start")
        if get("captured_end"):
            ended, end_error = _parse_timestamp(get("captured_end"), offset, "captured_end")
        row_errors.extend(e for e in (start_error, end_error) if e)

        if started and ended and ended < started:
            row_errors.append(
                RowError(
                    offset,
                    "captured_end",
                    f"capture ends ({ended.isoformat()}) before it starts "
                    f"({started.isoformat()})",
                )
            )

        evidence_path = get("evidence_path")
        if evidence_path:
            path_error = _evidence_path_error(evidence_path, offset)
            if path_error:
                row_errors.append(path_error)

        if row_errors:
            errors.extend(row_errors)
            continue

        rows.append(
            CaptureRow(
                row_number=offset,
                prompt_version_id=prompt_version_id,
                provider=provider,
                replicate_index=replicate_index,  # type: ignore[arg-type]
                capture_text=get("capture_text"),
                evidence_path=evidence_path,
                captured_start=started,  # type: ignore[arg-type]
                captured_end=ended,  # type: ignore[arg-type]
                operator=get("operator"),
                surface_url=get("surface_url"),
                notes=get("notes"),
            )
        )

    return rows, errors, uncaptured


def _evidence_path_error(path: str, row_number: int) -> RowError | None:
    """The screenshot is checked here rather than at upload time.

    `store_evidence` uploads before it writes, so a bad path raises partway
    through a batch — after earlier rows have already landed. Catching it in
    validate keeps the import all-or-nothing.
    """
    if not os.path.isfile(path):
        return RowError(
            row_number,
            "evidence_path",
            f"no file at {path!r}. A capture with no screenshot is unverifiable "
            "and must not be ingestible",
        )
    if os.path.getsize(path) == 0:
        return RowError(
            row_number,
            "evidence_path",
            f"{path!r} is empty — a zero-byte artifact hashes and uploads "
            "cleanly while proving nothing",
        )
    return None


# ---------------------------------------------------------------------
# Validation against the live database
# ---------------------------------------------------------------------


def frozen_core_prompts(db, version: str = FROZEN_CORE_VERSION) -> list[dict]:
    """The verified prompt set, read from the database rather than hardcoded.

    §5: the ten UUIDs are reused, never re-seeded. Reading them back by
    `version` is what makes "reused" checkable — a re-seeded set would show up
    here as eleven-plus rows or as ids the sheet does not name.
    """
    return (
        db.table("prompt_versions")
        .select("id,version,prompt_text,intent_tier,market_id")
        .eq("version", version)
        .order("intent_tier")
        .order("id")
        .execute()
        .data
    )


def validate(
    db,
    values: list[list],
    run_plan_id: str,
    *,
    prompt_set_version: str = FROZEN_CORE_VERSION,
) -> ValidationReport:
    """Full validation. Reads the run plan and the verified prompt set, and
    writes nothing.

    Refuses (design §6): a prompt_version_id outside the verified set, a
    missing evidence reference, missing capture text, a provider outside the
    check constraint, a cell with more replicates than the plan allows.
    Warns: a cell short of the replicate target, and a cell captured not at all.
    """
    rows, errors, uncaptured = parse_sheet(values)
    report = ValidationReport(
        valid=[], errors=list(errors), warnings=[], uncaptured=len(uncaptured)
    )

    prompts = frozen_core_prompts(db, prompt_set_version)
    verified = {p["id"] for p in prompts}
    if not verified:
        report.errors.append(
            RowError(
                1,
                None,
                f"no prompt_versions rows carry version {prompt_set_version!r} — "
                "refusing to ingest against a prompt set this database does not have",
            )
        )
        return report

    plan_rows = db.table("run_plans").select("id,replicate_count").eq("id", run_plan_id).execute().data
    if not plan_rows:
        report.errors.append(
            RowError(1, None, f"run plan {run_plan_id} does not exist")
        )
        return report
    replicate_target = plan_rows[0].get("replicate_count") or 0

    if not rows:
        _add_coverage_warnings(report, [], prompts, replicate_target)
        return report

    # task_id is the upsert key, so two rows synthesising the same one would
    # have the second silently overwrite the first inside a single import.
    # Since §4's scheme carries provider, that now means a genuine operator
    # duplicate: the same (provider, prompt, replicate) entered twice, usually
    # a copied row whose replicate_index was never advanced.
    task_rows_seen: dict[str, CaptureRow] = {}

    for row in rows:
        if row.prompt_version_id not in verified:
            report.errors.append(
                RowError(
                    row.row_number,
                    "prompt_version_id",
                    f"{row.prompt_version_id} is not one of the {len(verified)} "
                    f"prompt versions at {prompt_set_version!r}. The Frozen Core "
                    "ids are reused, never re-seeded (§5)",
                )
            )
            continue

        task_id = task_id_for(
            run_plan_id, row.provider, row.prompt_version_id, row.replicate_index
        )
        previous = task_rows_seen.get(task_id)
        if previous is not None:
            report.errors.append(
                RowError(
                    row.row_number,
                    "replicate_index",
                    f"this row synthesises the same task_id as sheet row "
                    f"{previous.row_number} ({task_id}): same provider, same "
                    "prompt, same replicate_index. The upsert would overwrite "
                    "that row instead of adding a replicate — advance "
                    "replicate_index if this is a second capture",
                )
            )
            continue
        task_rows_seen[task_id] = row

        report.valid.append(row)

    _check_replicate_ceiling(report, replicate_target)
    _add_coverage_warnings(report, report.valid, prompts, replicate_target)
    return report


def _check_replicate_ceiling(report: ValidationReport, replicate_target: int) -> None:
    """More replicates in a cell than the plan declares is a refusal, not a
    warning: the plan is the record of what was measured, and a cell that
    exceeds it means either the plan or the capture is wrong."""
    if replicate_target <= 0:
        return
    counts: dict[tuple[str, str], list[CaptureRow]] = {}
    for row in report.valid:
        counts.setdefault(row.cell, []).append(row)
    for cell, cell_rows in sorted(counts.items()):
        if len(cell_rows) > replicate_target:
            prompt_id, provider = cell
            report.errors.append(
                RowError(
                    min(r.row_number for r in cell_rows),
                    "replicate_index",
                    f"cell ({prompt_id}, {provider}) has {len(cell_rows)} captures "
                    f"but the run plan declares replicate_count={replicate_target}",
                )
            )


def _add_coverage_warnings(
    report: ValidationReport,
    rows: list[CaptureRow],
    prompts: list[dict],
    replicate_target: int,
) -> None:
    """§8.3's n=3 is a target, not a floor — a short cell is recorded and
    warned about, never rejected. A cell captured not at all is the sharper
    warning: it drops the paired frame to n=9, which trips
    `agreement._SMALL_SAMPLE_CELLS` and routes the gate to the §8.4 fallback,
    turning an automatic pass into one that needs a named human reviewer."""
    by_cell: dict[tuple[str, str], int] = {}
    for row in rows:
        by_cell[row.cell] = by_cell.get(row.cell, 0) + 1

    providers = sorted({row.provider for row in rows})

    for cell, count in sorted(by_cell.items()):
        if replicate_target and count < replicate_target:
            prompt_id, provider = cell
            report.warnings.append(
                f"cell ({prompt_id}, {provider}) has {count} replicate(s), "
                f"below the §8.3 target of {replicate_target}. Recorded, not "
                "rejected — the shortfall travels on CellJudgment.replicate_count"
            )

    for provider in providers:
        missing = [p["id"] for p in prompts if (p["id"], provider) not in by_cell]
        for prompt_id in sorted(missing):
            report.warnings.append(
                f"cell ({prompt_id}, {provider}) has no captures at all. Every "
                "missing cell drops the paired frame below "
                f"{len(prompts)} and routes the §8.4 gate to the "
                "fallback route, which needs a named human reviewer"
            )


# ---------------------------------------------------------------------
# Template / import
# ---------------------------------------------------------------------


def build_template_rows(db, provider: str, replicate_count: int, *, prompt_set_version: str = FROZEN_CORE_VERSION) -> list[list]:
    """One row per (prompt, replicate), in tier order.

    Sorted on (intent_tier, prompt_version_id, replicate_index) — the same
    deterministic key `rpv_labeling.build_export_rows` uses, so an operator
    moving between the two sheets reads them in the same order. Tier first
    because Methodology §4.2 weights intents A-D and the capture protocol works
    down them.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"{provider!r} is not one of {list(PROVIDERS)}")

    prompts = sorted(
        frozen_core_prompts(db, prompt_set_version),
        key=lambda p: (p.get("intent_tier") or "", p["id"]),
    )
    rows: list[list] = [list(SHEET_COLUMNS)]
    for prompt in prompts:
        for replicate_index in range(replicate_count):
            rows.append(
                [
                    prompt["id"],
                    prompt.get("intent_tier", ""),
                    prompt.get("prompt_text", ""),
                    provider,
                    replicate_index,
                    "",  # capture_text — operator fills
                    "",  # surface_url
                    "",  # evidence_path
                    "",  # captured_start
                    "",  # captured_end
                    "",  # operator
                    "",  # notes
                ]
            )
    return rows


def export_template(
    db,
    spreadsheet_id: str,
    tab: str,
    *,
    provider: str,
    replicate_count: int,
    prompt_set_version: str = FROZEN_CORE_VERSION,
) -> int:
    """Write the capture template into the tab. Returns the row count."""
    rows = build_template_rows(
        db, provider, replicate_count, prompt_set_version=prompt_set_version
    )
    write_values(spreadsheet_id, f"{tab}!A1", rows)
    return len(rows) - 1


def _market_for_prompts(db, prompts: list[dict]) -> dict[str, dict]:
    """prompt_version_id -> its market row, for the §7 market/language fields
    on the evidence record."""
    market_ids = sorted({p["market_id"] for p in prompts if p.get("market_id")})
    if not market_ids:
        return {}
    markets = (
        db.table("markets")
        .select("id,market_code,language_code")
        .in_("id", market_ids)
        .execute()
        .data
    )
    by_id = {m["id"]: m for m in markets}
    return {p["id"]: by_id.get(p.get("market_id"), {}) for p in prompts}


def _write_observation(db, row: CaptureRow, run_plan_id: str) -> str:
    """Upsert the observation on task_id and return its id.

    The id is what makes the evidence upsert idempotent, so this raises rather
    than returning None if the write comes back empty — writing evidence with a
    NULL observation_id would append a row on every re-run.
    """
    payload = row.to_observation(run_plan_id)
    result = db.table("observations").upsert(payload, on_conflict="task_id").execute()
    if not result.data:
        raise RuntimeError(
            f"upsert of observation {payload['task_id']} returned no row; refusing "
            "to store evidence without an observation_id to attach it to"
        )
    return result.data[0]["id"]


def _store_capture_evidence(
    db,
    row: CaptureRow,
    observation_id: str,
    run_plan_id: str,
    prompt: dict,
    market: dict,
    folder_id: str | None,
) -> None:
    """Upload the screenshot and write its `evidence` row.

    `sha256_file`, not `hash_payload`: a screenshot is bytes, and
    `hash_payload` canonical-JSON-encodes a dict and would raise on it.
    """
    record = EvidenceRecord(
        evidence_id=str(uuid.uuid4()),
        run_id=run_plan_id,
        prompt_version=prompt.get("version", ""),
        provider=row.provider,
        model=CONSUMER_SURFACE_MODELS[row.provider],
        tool_version=None,  # a browser capture has no tool leg
        market=market.get("market_code", ""),
        language=market.get("language_code", ""),
        captured_at=row.captured_start,
        payload_hash=sha256_file(row.evidence_path),
        storage_path=None,  # set by store_evidence from the Drive upload
        data_class=DATA_CLASS,
        operator=row.operator,  # §7's "operator where human capture is used"
        source_reference=row.surface_url,
        observation_id=observation_id,
        manifest_id=None,
    )
    store_evidence(db, record, row.evidence_path, folder_id)


def assert_no_api_layer_leak(db, run_plan_id: str) -> None:
    """Design §6's post-commit assertion.

    `select count(*) ... where run_plan_id = :plan and surface_layer <>
    'consumer'` — read back as rows rather than a count so the failure can name
    the offending observations, which is what an operator needs to fix them.

    Non-zero means a human capture landed in the API scoring frame. There is no
    recovery path here on purpose: this raises, loudly, and the rows are
    corrected deliberately.
    """
    rows = (
        db.table("observations")
        .select("id,task_id,surface_layer")
        .eq("run_plan_id", run_plan_id)
        .execute()
        .data
    )
    leaked = [r for r in rows if r.get("surface_layer") != SURFACE_LAYER]
    if leaked:
        named = ", ".join(f"{r.get('task_id')} ({r.get('surface_layer')!r})" for r in leaked[:10])
        raise RuntimeError(
            f"{len(leaked)} observation(s) under run plan {run_plan_id} are not "
            f"surface_layer='{SURFACE_LAYER}': {named}. A human capture in the API "
            "frame is the D-043 inversion — these rows must be corrected before "
            "this plan is scored"
        )


def import_from_sheet(
    db,
    spreadsheet_id: str,
    tab: str,
    run_plan_id: str,
    *,
    dry_run: bool = True,
    folder_id: str | None = None,
    prompt_set_version: str = FROZEN_CORE_VERSION,
) -> IngestReport:
    """Validate the tab and, unless this is a dry run, write the captures.

    Nothing is written when any row fails. A partial ingest leaves the sheet
    and the database disagreeing about which cells were captured, and a cell
    that exists but was never labeled is invisible to the gate — so a
    half-applied import is not a neutral state.

    Warnings never block: §8.3's n=3 is a target, and refusing a short campaign
    would discard real captures over a shortfall the frame already records.
    """
    values = read_values(spreadsheet_id, f"{tab}!A:{_LAST_COLUMN}")
    report = validate(db, values, run_plan_id, prompt_set_version=prompt_set_version)
    ingest = IngestReport(validation=report)
    if not report.ok or dry_run or not report.valid:
        return ingest

    prompts = {p["id"]: p for p in frozen_core_prompts(db, prompt_set_version)}
    markets = _market_for_prompts(db, list(prompts.values()))

    for row in report.valid:
        observation_id = _write_observation(db, row, run_plan_id)
        ingest.observations_written += 1
        _store_capture_evidence(
            db,
            row,
            observation_id,
            run_plan_id,
            prompts.get(row.prompt_version_id, {}),
            markets.get(row.prompt_version_id, {}),
            folder_id,
        )
        ingest.evidence_written += 1

    ingest.committed = True
    assert_no_api_layer_leak(db, run_plan_id)
    return ingest
