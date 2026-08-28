"""Provider Adapter Standard — the one observation contract every adapter fills.

Mirrors Operating System §5 field-for-field. An adapter that cannot populate
one of these groups is not done, regardless of whether it returns a response.
Do not add provider-specific fields here — put those in the adapter's own
raw_response payload; this contract is deliberately provider-agnostic so
scoring and reconciliation never need to know which provider produced a row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RunState(str, Enum):
    """Resumable Measurement Pipeline states — Operating System §4.

    Transitions are one-directional except retryable -> queued (requeue) and
    reconciled -> queued (missing task requeued by the reconciliation job).
    A technical failure lands in FAILED or RETRYABLE, never COMPLETE, and is
    never scored as zero.
    """

    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    RETRYABLE = "retryable"
    FAILED = "failed"
    EXCLUDED = "excluded"
    RECONCILED = "reconciled"


class GroundingStatus(str, Enum):
    GROUNDED = "grounded"
    UNGROUNDED_RETRIED_GROUNDED = "ungrounded_retried_grounded"
    UNGROUNDED_INELIGIBLE = "ungrounded_ineligible"


class OutcomeType(str, Enum):
    """Methodology §4.1 Recommendation Position Value outcomes."""

    RANKED = "ranked"
    UNORDERED_POSITIVE = "unordered_positive"
    SOURCE_ONLY_MENTION = "source_only_mention"
    ABSENT = "absent"
    NEGATIVE_MENTION = "negative_mention"
    ENTITY_CONFLICT = "entity_conflict"


# Methodology §4.1 RPV table. Keyed by rank for ranked outcomes; unordered
# positive and the terminal outcomes are looked up directly.
def response_text(raw_response: object) -> str:
    """Best-effort answer text out of an `observations.raw_response` payload.

    The four adapters store four different shapes — Perplexity/Sonar stores a
    Chat Completions dump, OpenAI a Responses API dump, Gemini a Vertex
    GenerateContentResponse dump, Anthropic a {"turns": [...]} wrapper it
    builds itself. This is the one place that knows how to get prose back out
    of any of them, so a second parser never gets written somewhere else.

    **Not a scoring path.** D-034 fixes `recommendations` as the scoring
    engine's only input, and `atlas.scoring.loader` never reads raw_response.
    This exists for human-facing surfaces — the RPV labeling sheet — where a
    person needs to read what the model actually said.

    Never raises. An unrecognised or malformed payload returns a marker
    describing what was found rather than propagating an exception: one odd
    response must not fail a whole export batch.

    Verified against real stored data for Perplexity only (2026-08-28). The
    other three branches are written from each adapter's construction code and
    are untested against live payloads — they will be exercised the first time
    those providers are planned.
    """
    if raw_response is None:
        return ""
    if isinstance(raw_response, str):
        return raw_response
    if not isinstance(raw_response, dict):
        return f"[unreadable raw_response of type {type(raw_response).__name__}]"

    try:
        # Perplexity / Sonar — Chat Completions.
        choices = raw_response.get("choices")
        if isinstance(choices, list) and choices:
            content = (choices[0] or {}).get("message", {}).get("content")
            text = _join_text_parts(content)
            if text:
                return text

        # OpenAI — Responses API. `output_text` is the convenience field;
        # fall back to walking the output items' content parts.
        output_text = raw_response.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text
        output = raw_response.get("output")
        if isinstance(output, list):
            parts = [
                _join_text_parts(item.get("content"))
                for item in output
                if isinstance(item, dict)
            ]
            text = "\n".join(p for p in parts if p)
            if text.strip():
                return text

        # Gemini — Vertex GenerateContentResponse.
        candidates = raw_response.get("candidates")
        if isinstance(candidates, list) and candidates:
            content = (candidates[0] or {}).get("content", {})
            text = _join_text_parts(content.get("parts"))
            if text:
                return text

        # Anthropic — the adapter's own {"turns": [...]} wrapper, every turn
        # kept. Joined in order so a pause_turn exchange reads as one answer.
        turns = raw_response.get("turns")
        if isinstance(turns, list):
            parts = [
                _join_text_parts(turn.get("content"))
                for turn in turns
                if isinstance(turn, dict)
            ]
            text = "\n".join(p for p in parts if p)
            if text.strip():
                return text
    except Exception as exc:  # noqa: BLE001 — a bad payload must not fail the batch
        return f"[could not extract text from raw_response: {type(exc).__name__}: {exc}]"

    return f"[no answer text found in raw_response; top-level keys: {sorted(raw_response)}]"


def _join_text_parts(content: object) -> str:
    """Flatten the several shapes a 'content' field takes: a plain string, or
    a list of parts each carrying `text`."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                value = part.get("text")
                if isinstance(value, str):
                    chunks.append(value)
        return "\n".join(c for c in chunks if c)
    return ""


RPV_BY_RANK = {1: 1.00, 2: 0.80, 3: 0.65, 4: 0.45, 5: 0.45, **{r: 0.25 for r in range(6, 11)}}
RPV_UNORDERED_POSITIVE = 0.30
RPV_ZERO_OUTCOMES = {
    OutcomeType.SOURCE_ONLY_MENTION,
    OutcomeType.ABSENT,
    OutcomeType.NEGATIVE_MENTION,
}


@dataclass
class Identity:
    provider: str  # 'openai' | 'gemini' | 'perplexity' | 'anthropic'
    model: str
    model_snapshot: str | None
    tool_version: str | None


@dataclass
class PromptContext:
    prompt_id: str
    prompt_version: str
    prompt_text: str
    market: str
    language: str
    intent_tier: str  # 'A' | 'B' | 'C' | 'D'
    set_type: str  # 'frozen_core' | 'sentinel' | 'benchmark' | 'discovery'


@dataclass
class Grounding:
    search_available: bool
    search_invoked: bool
    grounding_status: GroundingStatus
    source_records: list[dict] = field(default_factory=list)


@dataclass
class Outcome:
    raw_response: dict
    parsed_recommendations: list[dict] = field(default_factory=list)
    source_only_mentions: list[dict] = field(default_factory=list)
    negative_mentions: list[dict] = field(default_factory=list)
    entity_conflicts: list[dict] = field(default_factory=list)


@dataclass
class Execution:
    request_time: datetime
    completion_time: datetime | None
    latency_ms: int | None
    retry_number: int
    status: RunState
    error_code: str | None = None


@dataclass
class Cost:
    input_tokens: int
    output_tokens: int
    search_tool_units: int
    cost_usd: float
    is_unknown_cost: bool = False  # unknown cost is flagged, never silently zero


@dataclass
class Evidence:
    evidence_id: str
    payload_hash: str  # SHA-256, see atlas.evidence.vault
    manifest_id: str | None


@dataclass
class ObservationRecord:
    """One complete, storable observation — the unit every adapter returns."""

    task_id: str  # deterministic; idempotent (atlas.planner.run_planner)
    identity: Identity
    prompt: PromptContext
    grounding: Grounding
    outcome: Outcome
    execution: Execution
    cost: Cost
    evidence: Evidence


class ProviderAdapter:
    """Common adapter interface. Every provider adapter subclasses this."""

    provider_name: str

    async def observe(self, prompt: PromptContext, replicate_index: int) -> ObservationRecord:
        """Run one replicate observation and return a fully populated record.

        Must never return a record with status=COMPLETE unless grounding was
        verified (Methodology §8.1): force the search tool where the provider
        allows it; otherwise retry once with explicit current-web instruction;
        if grounding still doesn't occur, return status=EXCLUDED with
        grounding_status=UNGROUNDED_INELIGIBLE rather than scoring it grounded.
        """
        raise NotImplementedError
