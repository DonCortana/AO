"""Perplexity adapter — Sonar API (not the raw Search API).

Operating System §5: "Perplexity adapter uses Sonar for web-grounded
answers. search_results is the canonical source list." Do not substitute
the raw Search API — it returns search results, not a grounded
recommendation response, and would misrepresent what was actually measured.

Stub only — Week 2 build item.
"""

from __future__ import annotations

from atlas.adapters.base import PromptContext, ProviderAdapter


class PerplexityAdapter(ProviderAdapter):
    provider_name = "perplexity"

    async def observe(self, prompt: PromptContext, replicate_index: int):
        # TODO(Week 2):
        #   1. Call Sonar (not Search API).
        #   2. Treat `search_results` as the canonical source record for
        #      grounding and citation purposes.
        #   3. Populate every ObservationRecord field group — see base.py.
        raise NotImplementedError("Perplexity adapter not yet implemented — Technical Lane step 6")
