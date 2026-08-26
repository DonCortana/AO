"""Anthropic adapter — Messages API + web_search server tool.

Operating System §5: "Anthropic adapter uses Messages API with a supported
web_search server tool and handles pause_turn / continuation behaviour."

Stub only — Week 2 build item, built last in the provider sequence per
Technical Lane (step 7), after pause_turn handling can be tested against a
working pipeline.
"""

from __future__ import annotations

from atlas.adapters.base import PromptContext, ProviderAdapter


class AnthropicAdapter(ProviderAdapter):
    provider_name = "anthropic"

    async def observe(self, prompt: PromptContext, replicate_index: int):
        # TODO(Week 2):
        #   1. Call Messages API with the web_search server tool.
        #   2. Require server tool use / search result blocks before
        #      treating the response as grounded.
        #   3. Handle pause_turn by continuing the turn rather than treating
        #      a paused response as complete or as a failure.
        #   4. Populate every ObservationRecord field group — see base.py.
        raise NotImplementedError("Anthropic adapter not yet implemented — Technical Lane step 7")
