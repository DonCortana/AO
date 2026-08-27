"""Fake adapter + real-SDK-exception builders for induced-failure tests.

The exception types constructed here are the actual installed SDK classes
(openai 3.3.1, anthropic 1.0.0) — confirmed by inspecting the installed
packages directly (2026-08-26): APITimeoutError subclasses
APIConnectionError on both, and APIResponseValidationError takes a real
httpx.Response. Building real instances of these, rather than a generic
Exception subclass, is what makes the test prove atlas.runners.resume
classifies actual provider failure modes correctly, not a fake taxonomy.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import openai

from atlas.adapters.base import (
    Cost,
    Evidence,
    Execution,
    Grounding,
    GroundingStatus,
    Identity,
    ObservationRecord,
    Outcome,
    RunState,
)
from atlas.evidence.vault import hash_payload


class FakeAdapter:
    """Stand-in for a ProviderAdapter: pops one scripted behavior per
    observe() call — either raise it (if it's an exception) or return it."""

    def __init__(self, behaviors: list):
        self._behaviors = list(behaviors)
        self.call_count = 0

    async def observe(self, prompt, replicate_index):
        self.call_count += 1
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior

    def as_factory(self):
        """resume.py does `ADAPTERS[provider]()` — a zero-arg callable
        returning this same instance keeps call_count shared across every
        resume_run() invocation in a test, as if it were one long-lived
        adapter client."""
        return lambda: self


def make_success_record(provider: str = "openai", *, marker: str = "") -> ObservationRecord:
    now = datetime.now(timezone.utc)
    raw_response = {"provider": provider, "marker": marker or "ok"}
    return ObservationRecord(
        task_id="",
        identity=Identity(provider=provider, model="fake-model", model_snapshot="fake-model-2026-08", tool_version="fake-tool"),
        prompt=None,  # not read by resume.py
        grounding=Grounding(
            search_available=True,
            search_invoked=True,
            grounding_status=GroundingStatus.GROUNDED,
        ),
        outcome=Outcome(raw_response=raw_response),
        execution=Execution(
            request_time=now,
            completion_time=now,
            latency_ms=42,
            retry_number=0,
            status=RunState.COMPLETE,
        ),
        cost=Cost(input_tokens=100, output_tokens=200, search_tool_units=1, cost_usd=0.0021, is_unknown_cost=False),
        evidence=Evidence(evidence_id="", payload_hash=hash_payload(raw_response), manifest_id=None),
    )


def make_timeout_error() -> openai.APITimeoutError:
    """A client-side timeout — openai.APITimeoutError, a real
    APIConnectionError subclass. Transient: resume.py should mark the task
    RETRYABLE, not FAILED."""
    request = httpx.Request("POST", "https://api.openai.example/v1/responses")
    return openai.APITimeoutError(request)


def make_malformed_response_error() -> openai.APIResponseValidationError:
    """A response the SDK couldn't validate against its expected schema —
    openai.APIResponseValidationError. Not transient: resume.py should mark
    the task FAILED (Operating System §4: only RETRYABLE is auto-requeued),
    never COMPLETE."""
    request = httpx.Request("POST", "https://api.openai.example/v1/responses")
    response = httpx.Response(200, request=request, json={"unexpected": "shape"})
    return openai.APIResponseValidationError(response=response, body=None)
