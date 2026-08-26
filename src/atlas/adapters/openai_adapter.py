"""OpenAI adapter — Responses API + web_search.

Operating System §5: "OpenAI adapter uses Responses API with web_search and
verifies an actual web search call occurred." Technical Lane step 4 is the
first adapter to build and prove end-to-end (task -> observation -> hash ->
cost row) before any other provider.

Stub only — Week 2 build item. Fill in `observe()` once the run planner and
evidence vault are proven with a single provider (Technical Lane step 3-4).
"""

from __future__ import annotations

from atlas.adapters.base import PromptContext, ProviderAdapter


class OpenAIAdapter(ProviderAdapter):
    provider_name = "openai"

    async def observe(self, prompt: PromptContext, replicate_index: int):
        # TODO(Week 2):
        #   1. Call Responses API with web_search tool forced where supported.
        #   2. Confirm an actual web_search_call (or equivalent source record)
        #      is present in the response — do not trust an ungrounded answer.
        #   3. If ungrounded, retry once with an explicit current-web
        #      instruction; if still ungrounded, return EXCLUDED, never a
        #      silently-scored zero.
        #   4. Parse recommendations per Methodology §4.1 RPV table.
        #   5. Populate every ObservationRecord field group — see base.py.
        raise NotImplementedError("OpenAI adapter not yet implemented — Technical Lane step 4")
