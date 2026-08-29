"""RPV labeling tool — Google Sheet in, validated `recommendations` rows out.

Execution Plan §10 keeps recommendation parsing "deliberately assisted manual"
until a parser clears the >=95% labelled-sample gate, and D-034 fixes the
`recommendations` table as the scoring engine's only input. Between those two
facts sits a human reading raw AI responses and recording what they saw. This
tool is that step's surface, and it is built to be reused for every client
baseline — not for one calibration cycle.

Three commands:

    export    write one row per unlabelled observation into a Sheet tab, with
              a data-validated dropdown for outcome_type
    validate  read the tab back and check every row; report, change nothing
    import    validate, then insert the validated rows

The governing rule of this module is **reject, never coerce**. A blank where a
rank belongs, a stray "yes" where a boolean belongs, an outcome_type that is
not in the vocabulary — each is an error naming the sheet row, not a value
quietly repaired. A coerced label is indistinguishable from a real one once it
reaches the database, and D-034 exists precisely to keep "we did not parse
this" separable from "we parsed it and found nothing".

RPV is not a sheet column. It is derived from the labelled `outcome_type` and
`rank` through the canonical Methodology §4.1 table (`atlas.scoring.types
.rpv_for`), because a hand-typed RPV is a second chance to make an arithmetic
error about a value the label already determines. `atlas.scoring.loader`
re-checks the stored RPV against the same table when it loads a period, so a
drift between the two would surface at scoring time rather than in a report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from atlas.adapters.base import OutcomeType, response_text
from atlas.config import get_settings
from atlas.scoring.types import rpv_for
from atlas.tools.sheets import batch_update, get_sheet_id, read_values, write_values

__all__ = [
    "OUTCOME_TYPES",
    "SHEET_COLUMNS",
    "SHEET_LAST_COLUMN",
    "LabelRow",
    "LiveSchema",
    "RowError",
    "ValidationReport",
    "export_to_sheet",
    "fetch_live_schema",
    "import_from_sheet",
    "parse_sheet",
    "read_values",
    "validate",
    "verify_vocabulary",
]

# The labeling surface. Order is load-bearing: export writes in this order and
# validate reads positionally after confirming the header matches.
SHEET_COLUMNS: tuple[str, ...] = (
    "observation_id",
    "provider",
    "surface_layer",
    "prompt_version",
    # Reading material, placed before the labeler-filled block so the row reads
    # left-to-right: what was asked, what came back, then what you saw in it.
    # Not written to the database and not tripwires (see CONTEXT_COLUMNS) —
    # raw_response is far too long to compare cell-for-cell, and the labeler is
    # expected to read it rather than preserve it.
    "prompt_text",
    "raw_response",
    "entity_name",
    "is_client_entity",
    "outcome_type",
    "rank",
    "notes",
)

# Columns the labeler fills. The rest are exported context, and validate()
# checks them back against the observation row to catch a mis-pasted id.
# Last sheet column letter, derived so the read ranges cannot drift out of step
# with SHEET_COLUMNS the way a hardcoded "A:I" did.
_LAST_COLUMN = chr(ord("A") + len(SHEET_COLUMNS) - 1)
SHEET_LAST_COLUMN = _LAST_COLUMN

LABELER_COLUMNS = frozenset({"entity_name", "is_client_entity", "outcome_type", "rank", "notes"})
CONTEXT_COLUMNS = frozenset({"provider", "surface_layer", "prompt_version"})

# Methodology §4.1 vocabulary, as aligned to the check constraint by migration
# 0004 (D-035). Verified empirically against the live constraint on 2026-08-27:
# these six are accepted and the legacy 'source_only'/'negative' spellings are
# rejected. PostgREST's OpenAPI schema does not expose check constraints, so
# this list cannot be introspected — run `verify_vocabulary()` to re-confirm it
# against the live database when the schema may have moved.
OUTCOME_TYPES: tuple[str, ...] = tuple(o.value for o in OutcomeType)

# §4.1: only these carry an ordinal position.
RANKED = OutcomeType.RANKED.value
ENTITY_CONFLICT = OutcomeType.ENTITY_CONFLICT.value

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


# ---------------------------------------------------------------------
# Live schema introspection
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class LiveSchema:
    """The `recommendations` table as the live database currently describes it.

    Read from PostgREST's OpenAPI document, so it reflects the deployed schema
    rather than what the migrations in this repo say should be deployed — the
    distinction that matters when a migration is written but not yet applied.
    """

    columns: frozenset[str]
    required: frozenset[str]
    types: dict[str, str]

    def check_writable(self, payload_keys: set[str]) -> list[str]:
        errors = []
        unknown = sorted(payload_keys - self.columns)
        if unknown:
            errors.append(
                f"the live recommendations table has no column(s) {unknown} — "
                "the tool is writing a field the database does not have"
            )
        # `id` and `created_at` are server-defaulted; everything else the
        # database calls required must be present in the payload.
        server_defaulted = {"id", "created_at"}
        missing = sorted((self.required - server_defaulted) - payload_keys)
        if missing:
            errors.append(
                f"the live recommendations table requires column(s) {missing} "
                "that this tool does not supply"
            )
        return errors


def fetch_live_schema() -> LiveSchema:
    settings = get_settings()
    response = httpx.get(
        settings.supabase_url.rstrip("/") + "/rest/v1/",
        headers={
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Accept": "application/openapi+json",
        },
        timeout=30,
    )
    response.raise_for_status()
    spec = response.json()
    definitions = spec.get("definitions") or spec.get("components", {}).get("schemas", {})
    table = definitions.get("recommendations")
    if not table:
        raise RuntimeError(
            "the live database exposes no `recommendations` table — refusing to "
            "import labels against a schema this tool cannot see"
        )
    properties = table.get("properties", {})
    return LiveSchema(
        columns=frozenset(properties),
        required=frozenset(table.get("required", [])),
        types={name: spec.get("format", "") for name, spec in properties.items()},
    )


def verify_vocabulary(db) -> dict[str, bool]:
    """Empirically confirm the outcome_type vocabulary against the live check
    constraint, which PostgREST cannot introspect.

    Writes and immediately deletes one sentinel row per candidate value. Opt-in
    rather than automatic: it mutates the table, so it belongs in a deliberate
    schema check, not in the path of every import.
    """
    sentinel = "__atlas_vocabulary_probe__"
    accepted: dict[str, bool] = {}
    try:
        for value in OUTCOME_TYPES:
            try:
                db.table("recommendations").insert(
                    {
                        "observation_id": None,
                        "entity_name": sentinel,
                        "rpv": 0,
                        "outcome_type": value,
                    }
                ).execute()
                accepted[value] = True
            except Exception:  # noqa: BLE001 — a rejection is the signal here
                accepted[value] = False
    finally:
        db.table("recommendations").delete().eq("entity_name", sentinel).execute()
    return accepted


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
class LabelRow:
    row_number: int
    observation_id: str
    entity_name: str
    is_client_entity: bool
    outcome_type: str
    rank: int | None
    notes: str
    # Exported context, not written to the database. Carried so validate() can
    # check them back against the observation the row names — see
    # _context_mismatches.
    provider: str = ""
    surface_layer: str = ""
    prompt_version: str = ""
    # The response the label is a reading OF. Carried so validate() can check
    # the label against the text it claims to describe — see
    # _content_mismatch. Never part of to_recommendation().
    raw_response: str = ""

    def to_recommendation(self) -> dict:
        """The `recommendations` payload for this label.

        RPV comes from the §4.1 table, never from the sheet. ENTITY_CONFLICT
        has no tabulated RPV (§4.1 excludes it rather than scoring it), so it
        is stored as 0.00 alongside `entity_conflict = true`; the scoring
        engine drops the row on the flag and never reads that number, and
        `rpv_for` returns None for it so the loader's cross-check skips it too.
        """
        rpv = rpv_for(OutcomeType(self.outcome_type), self.rank)
        return {
            "observation_id": self.observation_id,
            "entity_name": self.entity_name,
            "is_client_entity": self.is_client_entity,
            "rank": self.rank,
            "rpv": 0.00 if rpv is None else rpv,
            "outcome_type": self.outcome_type,
            "entity_conflict": self.outcome_type == ENTITY_CONFLICT,
        }


@dataclass
class ValidationReport:
    valid: list[LabelRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    skipped_blank: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [
            (
                f"{len(self.valid)} valid row(s), {len(self.errors)} error(s), "
                f"{self.skipped_blank} blank row(s) skipped"
            )
        ]
        lines.extend(f"  ERROR {error}" for error in self.errors)
        return "\n".join(lines)


# ---------------------------------------------------------------------
# Parsing — strict, no coercion
# ---------------------------------------------------------------------


def _text(cell) -> str:
    return "" if cell is None else str(cell).strip()


def _parse_bool(raw: str, row_number: int, column: str) -> tuple[bool | None, RowError | None]:
    lowered = raw.lower()
    if lowered in _TRUE:
        return True, None
    if lowered in _FALSE:
        return False, None
    return None, RowError(
        row_number,
        column,
        f"{raw!r} is not a boolean. Use TRUE or FALSE — this is not coerced, "
        "because a mislabelled client entity silently changes whose visibility "
        "is being scored",
    )


def _parse_rank(raw: str, outcome_type: str, row_number: int) -> tuple[int | None, RowError | None]:
    """§4.1 tabulates RPV by rank only for ranked outcomes. A rank on any other
    outcome, or a missing rank on a ranked one, is a labelling error."""
    if outcome_type == RANKED:
        if not raw:
            return None, RowError(
                row_number, "rank", "outcome_type 'ranked' requires a rank (D-034, §4.1)"
            )
        try:
            value = int(float(raw))
        except ValueError:
            return None, RowError(row_number, "rank", f"{raw!r} is not an integer rank")
        if value < 1:
            return None, RowError(row_number, "rank", f"rank {value} must be >= 1")
        return value, None

    if raw:
        return None, RowError(
            row_number,
            "rank",
            f"outcome_type {outcome_type!r} must not carry a rank (got {raw!r}). "
            "Only 'ranked' has an ordinal position; an unordered positive is "
            "recorded as 'unordered_positive' with a blank rank",
        )
    return None, None


def parse_sheet(values: list[list]) -> tuple[list[LabelRow], list[RowError], int]:
    """Parse raw sheet values into rows, collecting every error rather than
    stopping at the first."""
    if not values:
        return [], [RowError(1, None, "the sheet tab is empty")], 0

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
            0,
        )

    index = {name: position for position, name in enumerate(SHEET_COLUMNS)}
    rows: list[LabelRow] = []
    errors: list[RowError] = []
    blank = 0

    for offset, raw_row in enumerate(values[1:], start=2):
        cells = [_text(c) for c in raw_row] + [""] * (len(SHEET_COLUMNS) - len(raw_row))

        def get(name: str, _cells: list[str] = cells) -> str:
            """Positional read. `_cells` is bound as a default so this closes
            over *this* row rather than the loop variable."""
            return _cells[index[name]]

        observation_id = get("observation_id")
        labeled = any(get(c) for c in ("entity_name", "is_client_entity", "outcome_type"))
        if not observation_id and not labeled:
            blank += 1
            continue

        row_errors: list[RowError] = []

        if not _UUID.match(observation_id):
            row_errors.append(
                RowError(offset, "observation_id", f"{observation_id!r} is not a UUID")
            )

        entity_name = get("entity_name")
        if not entity_name:
            row_errors.append(RowError(offset, "entity_name", "must not be blank"))

        is_client, bool_error = _parse_bool(get("is_client_entity"), offset, "is_client_entity")
        if bool_error:
            row_errors.append(bool_error)

        outcome_type = get("outcome_type")
        if outcome_type not in OUTCOME_TYPES:
            row_errors.append(
                RowError(
                    offset,
                    "outcome_type",
                    f"{outcome_type!r} is not one of {list(OUTCOME_TYPES)}",
                )
            )
            outcome_type = ""

        rank, rank_error = (None, None)
        if outcome_type:
            rank, rank_error = _parse_rank(get("rank"), outcome_type, offset)
        if rank_error:
            row_errors.append(rank_error)

        if row_errors:
            errors.extend(row_errors)
            continue

        rows.append(
            LabelRow(
                row_number=offset,
                observation_id=observation_id,
                entity_name=entity_name,
                is_client_entity=bool(is_client),
                outcome_type=outcome_type,
                rank=rank,
                notes=get("notes"),
                provider=get("provider"),
                surface_layer=get("surface_layer"),
                prompt_version=get("prompt_version"),
                raw_response=get("raw_response"),
            )
        )

    return rows, errors, blank


# ---------------------------------------------------------------------
# Validation against the live database
# ---------------------------------------------------------------------


def validate(db, values: list[list], *, context: dict[str, dict] | None = None) -> ValidationReport:
    """Full validation. Reads the live schema and the referenced observations,
    and writes nothing."""
    rows, errors, blank = parse_sheet(values)
    report = ValidationReport(valid=[], errors=list(errors), skipped_blank=blank)

    if not rows:
        return report

    # Structural check against the live schema first. If the tool and the
    # deployed table disagree about columns, per-row results are meaningless,
    # so this returns immediately rather than reporting rows that could never
    # be inserted anyway.
    schema = fetch_live_schema()
    structural = schema.check_writable(set(rows[0].to_recommendation()))
    if structural:
        report.errors.extend(RowError(1, None, message) for message in structural)
        return report

    observation_ids = sorted({row.observation_id for row in rows})
    observations = (
        db.table("observations")
        .select("id,provider,surface_layer,prompt_version_id,status")
        .in_("id", observation_ids)
        .execute()
        .data
    )
    by_id = {o["id"]: o for o in observations}

    if context is None:
        context = _prompt_version_labels(db, observations)

    existing = (
        db.table("recommendations")
        .select("observation_id")
        .in_("observation_id", observation_ids)
        .execute()
        .data
    )
    already_labeled = {r["observation_id"] for r in existing}

    client_rows_seen: dict[str, int] = {}
    # (observation_id, entity_name) -> sheet row that first claimed it.
    # The database enforces this pair too (migration 0006, D-048), but a
    # unique violation surfaces as a constraint name mid-insert; the sheet
    # row number is what the labeler can act on.
    entity_rows_seen: dict[tuple[str, str], int] = {}

    for row in rows:
        observation = by_id.get(row.observation_id)
        if observation is None:
            report.errors.append(
                RowError(
                    row.row_number,
                    "observation_id",
                    f"{row.observation_id} does not exist in observations",
                )
            )
            continue

        if row.observation_id in already_labeled:
            report.errors.append(
                RowError(
                    row.row_number,
                    "observation_id",
                    f"observation {row.observation_id} already has recommendation "
                    "row(s). Re-importing would double-label it; delete the "
                    "existing rows deliberately first if this is a correction",
                )
            )
            continue

        mismatches = _context_mismatches(row, observation, context)
        if mismatches:
            report.errors.append(
                RowError(
                    row.row_number,
                    None,
                    "context columns do not match the observation — this usually "
                    f"means the observation_id was mis-pasted. {mismatches}",
                )
            )
            continue

        content_problem = _content_mismatch(row)
        if content_problem:
            report.errors.append(
                RowError(row.row_number, "entity_name", content_problem)
            )
            continue

        if row.is_client_entity:
            previous = client_rows_seen.get(row.observation_id)
            if previous is not None:
                report.errors.append(
                    RowError(
                        row.row_number,
                        "is_client_entity",
                        f"observation {row.observation_id} already has a client-entity "
                        f"row at sheet row {previous}; the parse must emit exactly "
                        "one (D-034)",
                    )
                )
                continue
            client_rows_seen[row.observation_id] = row.row_number

        # D-048: one row per entity per observation. The same competitor
        # entered at two ranks is a parse artifact, not two measurements —
        # §4.1 RPV is a position value held by an entity in an observation, so
        # the entity gets one row at its best (lowest) rank. Matched to the
        # constraint exactly, on the raw strings: a check that normalised case
        # or spacing would reject imports the database would accept.
        previous = entity_rows_seen.get((row.observation_id, row.entity_name))
        if previous is not None:
            report.errors.append(
                RowError(
                    row.row_number,
                    "entity_name",
                    f"observation {row.observation_id} already has a row for "
                    f"{row.entity_name!r} at sheet row {previous}; one row per "
                    "entity per observation (D-048)",
                )
            )
            continue
        entity_rows_seen[(row.observation_id, row.entity_name)] = row.row_number

        report.valid.append(row)

    return report


def _prompt_version_labels(db, observations: list[dict]) -> dict[str, dict]:
    ids = sorted({o["prompt_version_id"] for o in observations if o.get("prompt_version_id")})
    if not ids:
        return {}
    versions = (
        db.table("prompt_versions")
        .select("id,version,prompt_text,intent_tier")
        .in_("id", ids)
        .execute()
        .data
    )
    return {v["id"]: v for v in versions}


# Outcomes that assert the entity WAS named in the response. `absent` asserts
# the opposite and is checked the other way. `entity_conflict` is deliberately
# not checked at all: §4.1 defines it as a wrong entity or name collision, so
# the labeler's entity_name may legitimately differ from the text.
_MENTION_OUTCOMES = frozenset(
    {
        OutcomeType.RANKED.value,
        OutcomeType.UNORDERED_POSITIVE.value,
        OutcomeType.SOURCE_ONLY_MENTION.value,
        OutcomeType.NEGATIVE_MENTION.value,
    }
)

ABSENT = OutcomeType.ABSENT.value


def _content_mismatch(row: LabelRow) -> str | None:
    """Does the label agree with the response text on its own row?

    The tripwire in `_context_mismatches` catches an observation_id pasted from
    the wrong line only when provider/surface_layer/prompt_version differ. On a
    single-provider, single-prompt-set run plan those three are identical on
    every row, so that check cannot fire and a whole sheet can be labelled
    against the wrong rows while validating clean.

    This check closes that hole using the one column that IS unique per row:
    the response the label is supposed to be a reading of. A `ranked` label for
    an entity whose name appears nowhere in that response is either a
    mis-pasted id or a mis-typed name, and both are labelling errors.

    Substring, case-insensitive, on the raw strings — the same deliberately
    unnormalised comparison used for the D-048 duplicate check. It is a
    coarse net on purpose: it catches the systematic failure (an entire block
    of labels sitting against the wrong observations) without trying to
    adjudicate paraphrase, which a human labeler is better at than a
    substring test.

    Returns None when there is nothing to check — a blank raw_response cell
    (the labeler is free to clear their reference columns, per the
    _context_mismatches precedent) or an outcome this rule does not cover.
    """
    response = row.raw_response.lower()
    if not response or not row.entity_name:
        return None

    present = row.entity_name.lower() in response

    if row.outcome_type in _MENTION_OUTCOMES and not present:
        return (
            f"outcome_type {row.outcome_type!r} says {row.entity_name!r} was "
            "named in this response, but that text does not appear anywhere in "
            "this row's raw_response. Either the observation_id belongs to a "
            "different row or the entity name is mistyped — check before "
            "importing, because the label would attach cleanly to the wrong "
            "observation"
        )

    if row.outcome_type == ABSENT and present:
        return (
            f"outcome_type 'absent' says {row.entity_name!r} was not named, but "
            "that text does appear in this row's raw_response. An absent label "
            "on a response that names the entity scores a real mention as 0.00"
        )

    return None


def _context_mismatches(row: LabelRow, observation: dict, context: dict[str, dict]) -> str:
    """The exported context columns are not written to the database — they are
    a tripwire.

    A labeler working a long sheet can paste an observation_id from the wrong
    line. Nothing downstream would catch that: the id is a valid UUID, it
    exists, and the label attaches cleanly to the wrong observation. But the
    provider / surface_layer / prompt_version exported alongside it will no
    longer match the observation the id names, so comparing them turns a silent
    mislabel into a rejected row.

    A blank context cell is not a mismatch — the columns are the labeler's
    reference, and clearing one is untidy rather than wrong.
    """
    version = context.get(observation.get("prompt_version_id") or "", {})
    expected = {
        "provider": observation.get("provider") or "",
        "surface_layer": observation.get("surface_layer") or "",
        "prompt_version": version.get("version") or observation.get("prompt_version_id") or "",
    }
    found = {
        "provider": row.provider,
        "surface_layer": row.surface_layer,
        "prompt_version": row.prompt_version,
    }
    mismatches = [
        f"{name}: sheet says {found[name]!r}, observation says {expected[name]!r}"
        for name in expected
        if found[name] and found[name] != expected[name]
    ]
    return "; ".join(mismatches)


# ---------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------


def unlabelled_observations(db, run_plan_ids: list[str]) -> list[dict]:
    """Complete observations in these run plans that carry no recommendation
    row yet — exactly the D-034 'not yet parsed' set."""
    observations = (
        db.table("observations")
        .select("id,provider,surface_layer,prompt_version_id,status,replicate_index")
        .in_("run_plan_id", run_plan_ids)
        # Explicit and deterministic. Without an ORDER BY the database returns
        # rows in unspecified order, and build_export_rows' sort key
        # (provider, replicate_index) does not mention prompt_version_id — so
        # for a single-provider run plan the sheet came out grouped by
        # REPLICATE, interleaving all ten prompts, with the order inside each
        # replicate block left to the database. A labeler reading top to bottom
        # then sees a different prompt on every row.
        .order("prompt_version_id")
        .order("replicate_index")
        .execute()
        .data
    )
    complete = [o for o in observations if o.get("status") == "complete"]
    if not complete:
        return []
    existing = (
        db.table("recommendations")
        .select("observation_id")
        .in_("observation_id", [o["id"] for o in complete])
        .execute()
        .data
    )
    labeled = {r["observation_id"] for r in existing}
    return [o for o in complete if o["id"] not in labeled]


# Google Sheets caps a single cell at 50,000 characters. A response above that
# would fail the whole write, so it is truncated with a visible marker telling
# the labeler where to read the untruncated text. Truncation is never silent:
# a labeler who cannot see the end of a response must know to go elsewhere for
# it rather than label what happens to fit.
_MAX_CELL_CHARS = 49_000


def _raw_responses(db, observations: list[dict]) -> dict[str, str]:
    """observation_id -> the model's answer TEXT, fetched separately so
    `unlabelled_observations` keeps its narrow selection.

    `observations.raw_response` holds the whole provider response object
    (choices, citations, search_results, usage). A Sheets cell must be a
    scalar, so writing the structure straight in fails the whole batch with
    "Invalid values" — and it would be unreadable to a labeler anyway. The
    prose is extracted through the canonical
    `atlas.adapters.base.response_text`, which is the only place that knows
    the four adapters' payload shapes.
    """
    ids = sorted({o["id"] for o in observations})
    if not ids:
        return {}
    rows = (
        db.table("observations").select("id,raw_response").in_("id", ids).execute().data
    )
    return {r["id"]: response_text(r.get("raw_response")) for r in rows}


def _fit_cell(text: str, observation_id: str) -> str:
    if len(text) <= _MAX_CELL_CHARS:
        return text
    return (
        text[:_MAX_CELL_CHARS]
        + f"\n\n[TRUNCATED at {_MAX_CELL_CHARS} chars for the Sheets cell limit — "
        f"read the full response from observations.raw_response where id={observation_id}]"
    )


def build_export_rows(db, observations: list[dict]) -> list[list]:
    versions = _prompt_version_labels(db, observations)
    raw_responses = _raw_responses(db, observations)
    rows = [list(SHEET_COLUMNS)]
    # Tier first (Methodology §4.1 weights intents A-D, and a labeler reads
    # the sheet in that order), then prompt_version_id and replicate_index to
    # keep each prompt's replicates together, then provider only to make ties
    # within one prompt/replicate deterministic — none of the three leading
    # keys distinguish providers of the same prompt version.
    def _sort_key(o: dict) -> tuple:
        version = versions.get(o.get("prompt_version_id"), {})
        return (
            version.get("intent_tier", ""),
            o.get("prompt_version_id", ""),
            o.get("replicate_index", 0),
            o.get("provider", ""),
        )

    for observation in sorted(observations, key=_sort_key):
        version = versions.get(observation.get("prompt_version_id"), {})
        rows.append(
            [
                observation["id"],
                observation.get("provider", ""),
                observation.get("surface_layer", ""),
                version.get("version", observation.get("prompt_version_id", "")),
                version.get("prompt_text", ""),
                _fit_cell(raw_responses.get(observation["id"], ""), observation["id"]),
                "",  # entity_name — labeler fills
                "",  # is_client_entity
                "",  # outcome_type (dropdown)
                "",  # rank
                "",  # notes
            ]
        )
    return rows


def export_to_sheet(db, run_plan_ids: list[str], spreadsheet_id: str, tab: str) -> int:
    """Write the unlabelled observations into the tab and apply the
    outcome_type dropdown. Returns the number of observation rows written."""
    observations = unlabelled_observations(db, run_plan_ids)
    rows = build_export_rows(db, observations)
    write_values(spreadsheet_id, f"{tab}!A1", rows)
    apply_outcome_type_validation(spreadsheet_id, tab, row_count=len(rows) - 1)
    return len(rows) - 1


def apply_outcome_type_validation(spreadsheet_id: str, tab: str, row_count: int) -> None:
    """Data-validated dropdown on the outcome_type column.

    `strict=True` rejects a typed value outright rather than warning, which is
    the sheet-side half of this module's reject-never-coerce rule.
    """
    if row_count <= 0:
        return
    column = SHEET_COLUMNS.index("outcome_type")
    batch_update(
        spreadsheet_id,
        [
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": get_sheet_id(spreadsheet_id, tab),
                        "startRowIndex": 1,
                        "endRowIndex": row_count + 1,
                        "startColumnIndex": column,
                        "endColumnIndex": column + 1,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": v} for v in OUTCOME_TYPES],
                        },
                        "strict": True,
                        "showCustomUi": True,
                        "inputMessage": (
                            "Methodology §4.1 outcome. 'ranked' needs a rank; "
                            "'unordered_positive' is a positive recommendation with "
                            "no ordinal position; 'source_only_mention' is a citation, "
                            "not a recommendation."
                        ),
                    },
                }
            }
        ],
    )


def import_from_sheet(db, spreadsheet_id: str, tab: str, *, dry_run: bool = True) -> ValidationReport:
    """Validate the tab and, unless this is a dry run, insert the valid rows.

    Nothing is inserted when any row fails. A partial import would leave the
    sheet and the database disagreeing about which observations are labelled,
    and D-034 makes an unlabelled observation semantically different from an
    absent one — so a half-applied import is not a neutral state.
    """
    values = read_values(spreadsheet_id, f"{tab}!A:{_LAST_COLUMN}")
    report = validate(db, values)
    if not report.ok or dry_run or not report.valid:
        return report
    db.table("recommendations").insert(
        [row.to_recommendation() for row in report.valid]
    ).execute()
    return report
