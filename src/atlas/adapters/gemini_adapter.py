"""Gemini adapter — Interactions API + google_search.

Operating System §5: "Gemini adapter uses current Interactions API with
google_search; use an authorization key, not a standard key." The
authorization key was created ahead of schedule (Execution Plan Action 0,
G0) specifically so this adapter is never built or run against a standard
key, which the API rejects entirely from September 2026.

Also owns the separate, non-scored Gemini Local Discovery / Maps Diagnostic
(Methodology §8.2) — experimental during calibration, stored outside AVS/ARS.
Keep that diagnostic's write path physically separate from observe() below
so it can never accidentally feed a score.

Stub only — Week 2 build item, after the OpenAI adapter proves the pipeline.
"""

from __future__ import annotations

from atlas.adapters.base import PromptContext, ProviderAdapter


class GeminiAdapter(ProviderAdapter):
    provider_name = "gemini"

    async def observe(self, prompt: PromptContext, replicate_index: int):
        # TODO(Week 2):
        #   1. Authenticate with GEMINI_API_KEY — confirm at startup this is
        #      an authorization key (fail loudly, don't silently proceed, if
        #      the key format looks like a legacy standard key).
        #   2. Call Interactions API with google_search grounding.
        #   3. Require an observed search call/result or grounding
        #      annotations before treating the response as grounded.
        #   4. Populate every ObservationRecord field group — see base.py.
        raise NotImplementedError("Gemini adapter not yet implemented — Technical Lane step 5")

    async def maps_diagnostic(self, prompt: PromptContext, coordinates: tuple[float, float] | None):
        """Gemini Local Discovery / Maps Diagnostic — Methodology §8.2.

        Experimental, hospitality-calibration-only. Stores prompt,
        coordinates/locality, place IDs, returned sources, recommendation
        position and cost. MUST NOT write to the `scores` table or otherwise
        enter AVS/ARS — promotion to a scored metric requires a major
        methodology version and a separate calibration decision.
        """
        raise NotImplementedError("Maps diagnostic not yet implemented — run during calibration week only")
