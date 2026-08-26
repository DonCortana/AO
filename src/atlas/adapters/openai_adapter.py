"""OpenAI adapter — Responses API + web_search.

Operating System §5: "OpenAI adapter uses Responses API with web_search and
verifies an actual web search call occurred." First provider built per
Technical Lane step 4 — everything after this (Gemini, Perplexity,
Anthropic) follows the same shape once this one is proven against a real
Supabase project.

Grounding contract (Methodology §8.1): force the search tool where the
provider allows it; if that doesn't produce a web_search_call, retry once
with an explicit current-web instruction; if it still doesn't ground, the
caller marks the observation EXCLUDED rather than silently scoring an
ungrounded answer. OpenAI's tool_choice forcing for web_search has known
reliability gaps (it doesn't always guarantee a call), so this adapter
never trusts the parameter alone — it always re-checks response.output.
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

# Pinned explicitly, never swapped silently (Operating System §5: "Silent
# model swaps are prohibited"). Changing this is a reviewed code change,
# not an env var flip — bump TOOL_VERSION alongside it if tool behaviour
# changes materially, and log the change as a decision (docs/decision-register.md)
# if it's material enough to trigger a methodology compatibility review.
OPENAI_MODEL = "gpt-5.6"  # TODO(Week 2): confirm this is the intended pin before first real run
TOOL_VERSION = "web_search-2026-08"


class OpenAIAdapter(ProviderAdapter):
    provider_name = "openai"

    def __init__(self) -> None:
        settings = get_settings()
        self._client = OpenAI(api_key=settings.openai_api_key)

    async def observe(self, prompt: PromptContext, replicate_index: int) -> ObservationRecord:
        request_time = datetime.now(timezone.utc)
        response, retried = self._call_with_grounding_retry(prompt)
        completion_time = datetime.now(timezone.utc)

        search_invoked = self._has_web_search_call(response)
        if search_invoked:
            grounding_status = (
                GroundingStatus.GROUNDED if not retried else GroundingStatus.UNGROUNDED_RETRIED_GROUNDED
            )
            status = RunState.COMPLETE
        else:
            grounding_status = GroundingStatus.UNGROUNDED_INELIGIBLE
            status = RunState.EXCLUDED  # never silently scored as grounded

        # mode="json" — not just model_dump() — so any datetime (or other
        # non-JSON-native) field is coerced to a plain JSON-safe type before
        # it reaches the evidence vault's hash_payload()/json.dumps() call.
        # Hasn't been observed to crash on OpenAI's response shape yet, but
        # the Gemini adapter hit exactly this failure live on Vertex AI's
        # create_time field — fixing it here too rather than waiting to
        # find out this adapter has the same latent bug.
        raw_response = response.model_dump(mode="json")

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        search_units = 1 if search_invoked else 0
        cost_usd, is_unknown_cost = compute_cost("openai", input_tokens, output_tokens, search_units)

        return ObservationRecord(
            task_id="",  # filled in by the caller from the run planner's deterministic id
            identity=Identity(
                provider=self.provider_name,
                model=OPENAI_MODEL,
                model_snapshot=getattr(response, "model", OPENAI_MODEL),
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
                # Recommendation parsing (Methodology §4.1 RPV assignment) is
                # deliberately not done here. Execution Plan §9 (Operating
                # Metrics): parser is "assisted manual first" — keeping
                # parsing out of the adapter means a parser change never
                # requires touching provider call logic, and every early
                # observation gets a human-reviewed parse before anything
                # is trusted unattended.
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
        response = self._client.responses.create(
            model=OPENAI_MODEL,
            tools=[{"type": "web_search"}],
            tool_choice={"type": "web_search"},  # forced; verify below, don't trust this alone
            input=prompt.prompt_text,
        )
        if self._has_web_search_call(response):
            return response, False

        retry_response = self._client.responses.create(
            model=OPENAI_MODEL,
            tools=[{"type": "web_search"}],
            tool_choice="auto",
            input=(
                f"{prompt.prompt_text}\n\n"
                "Use current web search to answer — do not rely on prior knowledge alone."
            ),
        )
        return retry_response, True

    @staticmethod
    def _has_web_search_call(response) -> bool:
        return any(
            getattr(item, "type", None) == "web_search_call" and getattr(item, "status", None) == "completed"
            for item in response.output
        )

    @staticmethod
    def _extract_citations(response) -> list[dict]:
        citations = []
        for item in response.output:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                for annotation in getattr(content, "annotations", []) or []:
                    if getattr(annotation, "type", None) == "url_citation":
                        citations.append(
                            {
                                "url": annotation.url,
                                "title": getattr(annotation, "title", None),
                                "start_index": getattr(annotation, "start_index", None),
                                "end_index": getattr(annotation, "end_index", None),
                            }
                        )
        return citations