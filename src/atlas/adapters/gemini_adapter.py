"""Gemini adapter — Interactions API + google_search.

Operating System §5: "Gemini adapter uses current Interactions API with
google_search; use an authorization key, not a standard key." The
authorization key was created ahead of schedule (Execution Plan Action 0,
G0) specifically so this adapter is never run against a standard key,
which the API rejects entirely from September 2026.

Grounding contract (Methodology §8.1): same shape as the OpenAI adapter,
with one real difference — Gemini's `tools` parameter has no documented
forced-invocation equivalent to OpenAI's `tool_choice`. The retry-with-
explicit-instruction path below is this provider's PRIMARY grounding
mechanism, not a fallback of last resort.

Also owns the separate, non-scored Gemini Local Discovery / Maps
Diagnostic (Methodology §8.2) — experimental during calibration, stored
outside AVS/ARS. Its write path is kept physically separate from
observe() below so it can never accidentally feed a score.
"""

from __future__ import annotations

from datetime import datetime, timezone

from google import genai

from atlas.adapters.base import (
    Cost,
    Evidence,
    Execution,
    Grounding,
    GroundingStatus,
    Identity,
    ObservationRecord,
    Outcome,
    PromptContext,
    ProviderAdapter,
    RunState,
)
from atlas.config import get_settings
from atlas.costs.ledger import compute_cost
from atlas.evidence.vault import hash_payload

# Pinned explicitly, never swapped silently (Operating System §5). Flash
# tier chosen for cost, mirroring the OpenAI adapter's mid-tier pick —
# revisit if Pro-tier accuracy is needed for calibration.
GEMINI_MODEL = "gemini-3.7-flash"  # TODO(Week 2): confirm this is the intended pin before first real run
TOOL_VERSION = "google_search-2026-08"


class GeminiAdapter(ProviderAdapter):
    provider_name = "gemini"

    def __init__(self) -> None:
        settings = get_settings()
        # Authorization key created at G0 — the SDK has no separate init
        # path for authorization vs standard keys; the distinction is
        # entirely server-side, in how the key was created in AI Studio.
        self._client = genai.Client(api_key=settings.gemini_api_key)

    async def observe(self, prompt: PromptContext, replicate_index: int) -> ObservationRecord:
        request_time = datetime.now(timezone.utc)
        interaction, retried = self._call_with_grounding_retry(prompt)
        completion_time = datetime.now(timezone.utc)

        search_invoked = self._has_google_search_call(interaction)
        if search_invoked:
            grounding_status = (
                GroundingStatus.GROUNDED if not retried else GroundingStatus.UNGROUNDED_RETRIED_GROUNDED
            )
            status = RunState.COMPLETE
        else:
            grounding_status = GroundingStatus.UNGROUNDED_INELIGIBLE
            status = RunState.EXCLUDED  # never silently scored as grounded

        raw_response = (
            interaction.model_dump() if hasattr(interaction, "model_dump") else dict(interaction)
        )

        # TODO(Week 2): confirm exact usage field names against a live
        # response — not clearly documented at time of writing. Defensive
        # getattr chain so a missing/renamed field degrades to
        # is_unknown_cost rather than crashing the adapter.
        usage = getattr(interaction, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None) or 0
        output_tokens = getattr(usage, "output_tokens", None) or getattr(usage, "candidates_tokens", None) or 0
        search_units = 1 if search_invoked else 0
        cost_usd, is_unknown_cost = compute_cost("gemini", input_tokens, output_tokens, search_units)

        return ObservationRecord(
            task_id="",  # filled in by the caller from the run planner's deterministic id
            identity=Identity(
                provider=self.provider_name,
                model=GEMINI_MODEL,
                model_snapshot=getattr(interaction, "model", GEMINI_MODEL),
                tool_version=TOOL_VERSION,
            ),
            prompt=prompt,
            grounding=Grounding(
                search_available=True,
                search_invoked=search_invoked,
                grounding_status=grounding_status,
                source_records=self._extract_citations(interaction),
            ),
            outcome=Outcome(
                raw_response=raw_response,
                # Recommendation parsing deliberately not done here — see
                # the identical note in openai_adapter.py.
            ),
            execution=Execution(
                request_time=request_time,
                completion_time=completion_time,
                latency_ms=int((completion_time - request_time).total_seconds() * 1000),
                retry_number=1 if retried else 0,
                status=status,
            ),
            cost=Cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                search_tool_units=search_units,
                cost_usd=cost_usd,
                is_unknown_cost=is_unknown_cost,
            ),
            evidence=Evidence(
                evidence_id="",  # filled in when the evidence vault writes the row
                payload_hash=hash_payload(raw_response),
                manifest_id=None,
            ),
        )

    def _call_with_grounding_retry(self, prompt: PromptContext):
        interaction = self._client.interactions.create(
            model=GEMINI_MODEL,
            input=prompt.prompt_text,
            tools=[{"type": "google_search"}],
        )
        if self._has_google_search_call(interaction):
            return interaction, False

        retry_interaction = self._client.interactions.create(
            model=GEMINI_MODEL,
            input=(
                f"{prompt.prompt_text}\n\n"
                "Use current Google Search to answer — do not rely on prior knowledge alone."
            ),
            tools=[{"type": "google_search"}],
        )
        return retry_interaction, True

    @staticmethod
    def _has_google_search_call(interaction) -> bool:
        return any(
            getattr(step, "type", None) == "google_search_call" for step in getattr(interaction, "steps", []) or []
        )

    @staticmethod
    def _extract_citations(interaction) -> list[dict]:
        citations = []
        for step in getattr(interaction, "steps", []) or []:
            if getattr(step, "type", None) != "model_output":
                continue
            for content_block in getattr(step, "content", []) or []:
                if getattr(content_block, "type", None) != "text":
                    continue
                for annotation in getattr(content_block, "annotations", []) or []:
                    if getattr(annotation, "type", None) == "url_citation":
                        citations.append(
                            {
                                "url": annotation.url,
                                "start_index": getattr(annotation, "start_index", None),
                                "end_index": getattr(annotation, "end_index", None),
                            }
                        )
        return citations

    async def maps_diagnostic(self, prompt: PromptContext, coordinates: tuple[float, float] | None):
        """Gemini Local Discovery / Maps Diagnostic — Methodology §8.2.

        Experimental, hospitality-calibration-only. Stores prompt,
        coordinates/locality, place IDs, returned sources, recommendation
        position and cost. MUST NOT write to the `scores` table or otherwise
        enter AVS/ARS — promotion to a scored metric requires a major
        methodology version and a separate calibration decision.
        """
        raise NotImplementedError("Maps diagnostic not yet implemented — run during calibration week only")
