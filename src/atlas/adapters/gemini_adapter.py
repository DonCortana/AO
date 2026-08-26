"""Gemini adapter — Vertex AI, generate_content + google_search grounding.

D-029: This adapter was originally written against the Gemini Developer
API's Interactions API (AI Studio authorization key). It was rewritten to
authenticate via Vertex AI (service-account / Application Default
Credentials, billed through standard Cloud Billing) after AI Studio's
prepay-credits billing UI hit an unresolvable bug — see decision-register.md
D-029 for the full record.

That auth swap forced a second, larger change: as of this rewrite, the
Interactions API is Gemini Developer API only — it is not available on
Vertex AI yet (Google's own Gemini API team confirmed it is on the roadmap
but not shipped). So this adapter no longer calls `client.interactions.
create`; it calls the standard `client.models.generate_content` with the
`google_search` tool, which IS available on Vertex AI today. The grounding
contract from Methodology §8.1 is unchanged — grounded vs excluded, retry-
once-with-explicit-instruction — only the API surface used to implement it
changed.

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

# Pinned explicitly, never swapped silently (Operating System §5).
# TODO(D-029 first live run): confirm this exact model id is available in
# the chosen google_cloud_location on Vertex AI — regional availability can
# lag AI Studio, where this was originally validated.
GEMINI_MODEL = "gemini-3.7-flash"
TOOL_VERSION = "google_search-vertex-2026-08"


class GeminiAdapter(ProviderAdapter):
    provider_name = "gemini"

    def __init__(self) -> None:
        settings = get_settings()
        # Vertex AI auth (D-029) — Application Default Credentials reads
        # GOOGLE_APPLICATION_CREDENTIALS from the environment itself; it is
        # not passed here explicitly. Works both for local dev (.env sets
        # the env var to a local JSON key path) and CI (the GitHub Actions
        # workflow materializes the key to a file at runtime — see
        # .github/workflows/*.yml).
        self._client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )

    async def observe(self, prompt: PromptContext, replicate_index: int) -> ObservationRecord:
        request_time = datetime.now(timezone.utc)
        response, retried = self._call_with_grounding_retry(prompt)
        completion_time = datetime.now(timezone.utc)

        search_invoked = self._has_google_search_call(response)
        if search_invoked:
            grounding_status = (
                GroundingStatus.GROUNDED if not retried else GroundingStatus.UNGROUNDED_RETRIED_GROUNDED
            )
            status = RunState.COMPLETE
        else:
            grounding_status = GroundingStatus.UNGROUNDED_INELIGIBLE
            status = RunState.EXCLUDED  # never silently scored as grounded

        raw_response = (
            response.model_dump() if hasattr(response, "model_dump") else dict(response)
        )

        # Field names confirmed against the installed google-genai SDK's
        # GenerateContentResponseUsageMetadata (2026-08-26) — not a guess.
        # getattr kept defensive anyway so a future SDK rename degrades to
        # is_unknown_cost rather than crashing the adapter.
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) or 0
        output_tokens = getattr(usage, "candidates_token_count", None) or 0
        search_units = 1 if search_invoked else 0
        cost_usd, is_unknown_cost = compute_cost("gemini", input_tokens, output_tokens, search_units)

        return ObservationRecord(
            task_id="",  # filled in by the caller from the run planner's deterministic id
            identity=Identity(
                provider=self.provider_name,
                model=GEMINI_MODEL,
                model_snapshot=getattr(response, "model_version", GEMINI_MODEL),
                tool_version=TOOL_VERSION,
            ),
            prompt=prompt,
            grounding=Grounding(
                search_available=True,
                search_invoked=search_invoked,
                grounding_status=grounding_status,
                source_records=self._extract_citations(response),
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
        # `tools` is not a top-level generate_content() kwarg on the
        # installed google-genai SDK — it must be nested under `config`.
        # Confirmed by inspecting the installed Models.generate_content
        # signature and GenerateContentConfig fields directly (2026-08-26)
        # after the first live run 404'd on this exact mistake.
        grounding_config = {"tools": [{"google_search": {}}]}

        response = self._client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt.prompt_text,
            config=grounding_config,
        )
        if self._has_google_search_call(response):
            return response, False

        retry_response = self._client.models.generate_content(
            model=GEMINI_MODEL,
            contents=(
                f"{prompt.prompt_text}\n\n"
                "Use current Google Search to answer — do not rely on prior knowledge alone."
            ),
            config=grounding_config,
        )
        return retry_response, True

    @staticmethod
    def _grounding_metadata(response):
        # Confirmed against the installed SDK: GenerateContentResponse has
        # no top-level grounding_metadata field — it only lives on
        # candidates[i].grounding_metadata. The direct-attribute check is
        # kept as a harmless fallback in case a future SDK version exposes
        # it both ways.
        direct = getattr(response, "grounding_metadata", None)
        if direct is not None:
            return direct
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            return getattr(candidates[0], "grounding_metadata", None)
        return None

    @classmethod
    def _has_google_search_call(cls, response) -> bool:
        gm = cls._grounding_metadata(response)
        if gm is None:
            return False
        return bool(getattr(gm, "web_search_queries", None)) or bool(getattr(gm, "grounding_chunks", None))

    @classmethod
    def _extract_citations(cls, response) -> list[dict]:
        gm = cls._grounding_metadata(response)
        if gm is None:
            return []
        citations = []
        for chunk in getattr(gm, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            if web is None:
                continue
            citations.append(
                {
                    "url": getattr(web, "uri", None),
                    "title": getattr(web, "title", None),
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