"""Perplexity adapter — Sonar API (not the raw Search API).

Operating System §5: "Perplexity adapter uses Sonar for web-grounded
answers. search_results is the canonical source list." Do not substitute
the raw Search API — it returns search results, not a grounded
recommendation response, and would misrepresent what was actually measured.

Built against the classic Sonar Chat Completions endpoint
(api.perplexity.ai/chat/completions), not Perplexity's newer Agent API
(client.responses.create). Deliberate choice, not an oversight: Sonar Chat
Completions is well-documented and stable, and Perplexity has confirmed it
stays available until September 27, 2026 — comfortable runway for a Week 2
build. The Agent API is real but still mid-migration at time of writing
(its default model routing, exact model identifiers, and response shape
were not confirmed with enough certainty to build against safely — see
decision-register.md D-031). Track that deadline; this needs a rebuild
before Sept 27, 2026 regardless of which replacement is chosen.

Uses the OpenAI Python client pointed at Perplexity's base_url — official
Perplexity guidance, not a hack. Verified directly against the installed
SDK (2026-08-26) that its pydantic models use extra="allow", so
Perplexity's non-OpenAI-standard `citations` and `search_results` fields
survive intact as real attributes and through model_dump(), rather than
being silently dropped by strict schema validation.

Grounding contract (Methodology §8.1): Sonar models search by default as
their core behavior — there is no forced tool_choice concept here, unlike
OpenAI. The retry-with-explicit-instruction path exists as a defensive
fallback for the rare case a query is answered from parametric knowledge
without triggering a search, consistent with how every other adapter in
this project treats grounding as verified, not assumed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from openai import OpenAI

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

# Pinned explicitly, never swapped silently (Operating System §5). Base
# tier — cheapest Sonar model that still searches natively, mirroring the
# cost-conscious mid/flash-tier pick made for OpenAI and Gemini. sonar-pro
# is the documented escalation path if source depth/citation count proves
# too thin during calibration.
PERPLEXITY_MODEL = "sonar"
TOOL_VERSION = "sonar-chat-completions-2026-08"
PERPLEXITY_BASE_URL = "https://api.perplexity.ai"


class PerplexityAdapter(ProviderAdapter):
    provider_name = "perplexity"

    def __init__(self) -> None:
        settings = get_settings()
        self._client = OpenAI(api_key=settings.perplexity_api_key, base_url=PERPLEXITY_BASE_URL)

    async def observe(self, prompt: PromptContext, replicate_index: int) -> ObservationRecord:
        request_time = datetime.now(timezone.utc)
        response, retried = self._call_with_grounding_retry(prompt)
        completion_time = datetime.now(timezone.utc)

        search_invoked = self._has_search_results(response)
        if search_invoked:
            grounding_status = (
                GroundingStatus.GROUNDED if not retried else GroundingStatus.UNGROUNDED_RETRIED_GROUNDED
            )
            status = RunState.COMPLETE
        else:
            grounding_status = GroundingStatus.UNGROUNDED_INELIGIBLE
            status = RunState.EXCLUDED  # never silently scored as grounded

        raw_response = response.model_dump(mode="json")

        usage = getattr(response, "usage", None)
        # Sonar Chat Completions uses classic Chat Completions usage field
        # names (prompt_tokens/completion_tokens), not OpenAI Responses
        # API's input_tokens/output_tokens — different from openai_adapter.py
        # on purpose, this is the provider's real field naming.
        input_tokens = getattr(usage, "prompt_tokens", None) or 0
        output_tokens = getattr(usage, "completion_tokens", None) or 0
        search_units = 1 if search_invoked else 0
        cost_usd, is_unknown_cost = compute_cost("perplexity", input_tokens, output_tokens, search_units)

        return ObservationRecord(
            task_id="",  # filled in by the caller from the run planner's deterministic id
            identity=Identity(
                provider=self.provider_name,
                model=PERPLEXITY_MODEL,
                model_snapshot=getattr(response, "model", PERPLEXITY_MODEL),
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
        response = self._client.chat.completions.create(
            model=PERPLEXITY_MODEL,
            messages=[{"role": "user", "content": prompt.prompt_text}],
        )
        if self._has_search_results(response):
            return response, False

        retry_response = self._client.chat.completions.create(
            model=PERPLEXITY_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"{prompt.prompt_text}\n\n"
                        "Use current web search to answer — do not rely on prior knowledge alone."
                    ),
                }
            ],
        )
        return retry_response, True

    @staticmethod
    def _has_search_results(response) -> bool:
        # search_results is the canonical grounding signal per Operating
        # System §5; citations (URL list) checked too as a defensive
        # fallback in case one is populated without the other.
        return bool(getattr(response, "search_results", None)) or bool(getattr(response, "citations", None))

    @staticmethod
    def _extract_citations(response) -> list[dict]:
        source_records = []
        for result in getattr(response, "search_results", None) or []:
            if isinstance(result, dict):
                source_records.append(
                    {
                        "url": result.get("url"),
                        "title": result.get("title"),
                        "snippet": result.get("snippet"),
                        "date": result.get("date"),
                    }
                )
            else:
                source_records.append(
                    {
                        "url": getattr(result, "url", None),
                        "title": getattr(result, "title", None),
                        "snippet": getattr(result, "snippet", None),
                        "date": getattr(result, "date", None),
                    }
                )
        if not source_records:
            # Fallback if search_results is empty but citations (plain URL
            # list) came back populated — keep evidence non-empty rather
            # than silently discarding it.
            for url in getattr(response, "citations", None) or []:
                source_records.append({"url": url, "title": None, "snippet": None, "date": None})
        return source_records