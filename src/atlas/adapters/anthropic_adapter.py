"""Anthropic adapter — Messages API + web_search server tool.

Operating System §5: "Anthropic adapter uses Messages API with a supported
web_search server tool and handles pause_turn / continuation behaviour."

Grounding contract (Methodology §8.1): same shape as the other adapters,
with the same real limitation as Gemini — Anthropic does not document a
reliable forced-invocation equivalent to OpenAI's tool_choice for the
web_search server tool (it's left to the model's judgment). The retry-
with-explicit-instruction path is this provider's PRIMARY grounding
mechanism, not a fallback of last resort.

pause_turn handling (Operating System §5, this adapter's other named
requirement): a long-running search can make the API return
stop_reason="pause_turn" instead of "end_turn". This is NOT an error and
NOT a signal to stop — per Anthropic's docs, the caller must resend the
paused assistant message back unchanged in a new request and keep looping
until stop_reason is no longer "pause_turn". A single messages.create()
call does not loop internally. _call_with_grounding_retry below owns that
loop; every turn's response is kept (not just the final one) so the
evidence record reflects the full multi-turn exchange, and token usage is
summed across every turn actually billed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from anthropic import Anthropic

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

# Pinned explicitly, never swapped silently (Operating System §5). Matches
# the model already priced in PROVIDER_PRICING["anthropic"] in config.py —
# not a fresh guess, reuses the pin this project already committed to.
ANTHROPIC_MODEL = "claude-sonnet-5"
# Dated tool version string, as Anthropic versions the web_search tool
# itself (web_search_20250305 / 20260209 / 20260318 have all existed) —
# pinned to the most recent documented version at time of writing.
WEB_SEARCH_TOOL_TYPE = "web_search_20260318"
TOOL_VERSION = WEB_SEARCH_TOOL_TYPE

# Defensive cap on the pause_turn continuation loop — a real multi-step
# research turn is expected to take a handful of pauses, not dozens. Caps
# runaway cost/latency if something upstream misbehaves rather than looping
# forever; surfaces as "not grounded after retry" (EXCLUDED), never a silent
# infinite hang.
MAX_PAUSE_TURN_CONTINUATIONS = 10


class AnthropicAdapter(ProviderAdapter):
    provider_name = "anthropic"

    def __init__(self) -> None:
        settings = get_settings()
        self._client = Anthropic(api_key=settings.anthropic_api_key)

    async def observe(self, prompt: PromptContext, replicate_index: int) -> ObservationRecord:
        request_time = datetime.now(timezone.utc)
        turns, retried = self._call_with_grounding_retry(prompt)
        completion_time = datetime.now(timezone.utc)

        search_invoked = self._has_web_search_call(turns)
        if search_invoked:
            grounding_status = (
                GroundingStatus.GROUNDED if not retried else GroundingStatus.UNGROUNDED_RETRIED_GROUNDED
            )
            status = RunState.COMPLETE
        else:
            grounding_status = GroundingStatus.UNGROUNDED_INELIGIBLE
            status = RunState.EXCLUDED  # never silently scored as grounded

        # Every turn kept, not just the last — a pause_turn exchange is
        # meaningless evidence with only its final leg. mode="json" for the
        # same reason it's needed elsewhere: coerce any non-JSON-native
        # field (datetimes etc.) before hash_payload()'s json.dumps() call.
        raw_response = {
            "turns": [
                turn.model_dump(mode="json") if hasattr(turn, "model_dump") else dict(turn) for turn in turns
            ]
        }

        input_tokens = 0
        output_tokens = 0
        for turn in turns:
            usage = getattr(turn, "usage", None)
            input_tokens += getattr(usage, "input_tokens", 0) or 0
            output_tokens += getattr(usage, "output_tokens", 0) or 0

        search_units = 1 if search_invoked else 0
        cost_usd, is_unknown_cost = compute_cost("anthropic", input_tokens, output_tokens, search_units)

        last_turn = turns[-1]

        return ObservationRecord(
            task_id="",  # filled in by the caller from the run planner's deterministic id
            identity=Identity(
                provider=self.provider_name,
                model=ANTHROPIC_MODEL,
                model_snapshot=getattr(last_turn, "model", ANTHROPIC_MODEL),
                tool_version=TOOL_VERSION,
            ),
            prompt=prompt,
            grounding=Grounding(
                search_available=True,
                search_invoked=search_invoked,
                grounding_status=grounding_status,
                source_records=self._extract_citations(turns),
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
        turns = self._run_pause_turn_loop(prompt.prompt_text)
        if self._has_web_search_call(turns):
            return turns, False

        retry_turns = self._run_pause_turn_loop(
            f"{prompt.prompt_text}\n\n"
            "Use current web search to answer — do not rely on prior knowledge alone."
        )
        return retry_turns, True

    def _run_pause_turn_loop(self, input_text: str) -> list:
        messages = [{"role": "user", "content": input_text}]
        turns = []
        for _ in range(MAX_PAUSE_TURN_CONTINUATIONS):
            response = self._client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=4096,
                messages=messages,
                tools=[{"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", "max_uses": 5}],
            )
            turns.append(response)
            if response.stop_reason != "pause_turn":
                break
            # Per Anthropic's docs: resend the paused assistant message back
            # unchanged in a new request to continue the same turn.
            messages.append({"role": "assistant", "content": response.content})
        return turns

    @staticmethod
    def _has_web_search_call(turns) -> bool:
        for turn in turns:
            for block in getattr(turn, "content", []) or []:
                if getattr(block, "type", None) == "web_search_tool_result":
                    return True
        return False

    @staticmethod
    def _extract_citations(turns) -> list[dict]:
        citations = []
        for turn in turns:
            for block in getattr(turn, "content", []) or []:
                if getattr(block, "type", None) != "text":
                    continue
                for citation in getattr(block, "citations", None) or []:
                    if getattr(citation, "type", None) == "web_search_result_location":
                        citations.append(
                            {
                                "url": getattr(citation, "url", None),
                                "title": getattr(citation, "title", None),
                                "cited_text": getattr(citation, "cited_text", None),
                            }
                        )
        return citations