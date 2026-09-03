"""Per-session exit-geography verification — the D-099 precondition.

What these tests can and cannot prove. They exercise every branch of the
decision logic against an injected probe, which is the whole of what is
testable without a live IPRoyal session: the pool's actual behaviour is what
D-099's twenty probes measured, and no unit test re-establishes it. Read a
green run here as "the gate refuses what it should refuse", not as evidence
about exit geography.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from atlas.capture.exit_geography import (
    BurnedSessionError,
    ExitGeographyFailure,
    ProxySession,
    SessionLedger,
    require_exit_geography,
    verify_exit_geography,
)

# Shaped after a real row of D-099's evidence (~/evidence/d091/pool-th.jsonl).
TH_BODY = json.dumps(
    {
        "ip": "202.57.176.63",
        "city": "Bangkok",
        "region": "Bangkok",
        "country": "TH",
        "org": "AS7654 Internet Solution & Service Provider Co., Ltd.",
    }
).encode()

# The one miss in twenty: batch one's session that exited US on a TH request.
US_MISS_BODY = json.dumps(
    {
        "ip": "104.28.202.11",
        "city": "Newport Beach",
        "region": "California",
        "country": "US",
        "org": "AS7654 Internet Solution & Service Provider Co., Ltd.",
    }
).encode()

SECRET_URL = "http://user:pass_country-th_session-abc1@proxy.iproyal.example:12321"


def session(session_id: str = "abc1") -> ProxySession:
    return ProxySession(
        session_id=session_id, country_param="th", proxy_url=SECRET_URL
    )


def probe_returning(status: int, body: bytes):
    def probe(url, proxy_url, timeout):
        return status, body

    return probe


def probe_raising(exc: Exception):
    def probe(url, proxy_url, timeout):
        raise exc

    return probe


@pytest.fixture
def ledger() -> SessionLedger:
    """Every test gets its own, so no test can be made to pass or fail by the
    burn state another test left behind in the module default."""
    return SessionLedger()


# ---------------------------------------------------------------------------
# The pass path
# ---------------------------------------------------------------------------


def test_th_exit_passes_and_reports_exit_metadata(ledger):
    check = verify_exit_geography(
        session(), probe=probe_returning(200, TH_BODY), ledger=ledger
    )

    assert check.passed is True
    assert check.observed_country == "TH"
    assert check.ip == "202.57.176.63"
    assert check.city == "Bangkok"
    assert check.org.startswith("AS7654")
    assert check.failure_reason is None
    assert ledger.burned() == frozenset()


def test_pass_hashes_the_raw_response_and_discards_the_file(ledger, tmp_path):
    check = verify_exit_geography(
        session(),
        probe=probe_returning(200, TH_BODY),
        ledger=ledger,
        evidence_dir=str(tmp_path),
    )

    import hashlib

    assert check.payload_hash == hashlib.sha256(TH_BODY).hexdigest()
    assert check.raw_path is None
    assert os.listdir(tmp_path) == []


def test_discard_false_keeps_the_raw_file_for_the_caller(ledger, tmp_path):
    check = verify_exit_geography(
        session(),
        probe=probe_returning(200, TH_BODY),
        ledger=ledger,
        evidence_dir=str(tmp_path),
        discard=False,
    )

    assert check.raw_path is not None
    with open(check.raw_path, "rb") as handle:
        assert handle.read() == TH_BODY


def test_expected_country_is_a_parameter_not_a_hardcoding(ledger):
    """D-089: a second market is a second plan with its own capture
    configuration, so the expected country travels with the call."""
    gb_body = json.dumps({"ip": "1.2.3.4", "country": "GB"}).encode()

    check = verify_exit_geography(
        ProxySession("gb1", "gb", SECRET_URL),
        expected_country="GB",
        probe=probe_returning(200, gb_body),
        ledger=ledger,
    )
    assert check.passed is True


def test_country_comparison_is_case_insensitive(ledger):
    body = json.dumps({"ip": "1.2.3.4", "country": "th"}).encode()
    check = verify_exit_geography(
        session(), probe=probe_returning(200, body), ledger=ledger
    )
    assert check.passed is True
    assert check.observed_country == "TH"


def test_checked_at_is_recorded(ledger):
    stamp = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    check = verify_exit_geography(
        session(), probe=probe_returning(200, TH_BODY), ledger=ledger, now=stamp
    )
    assert check.checked_at == stamp


# ---------------------------------------------------------------------------
# The fail path — every branch burns the session
# ---------------------------------------------------------------------------


def test_wrong_country_fails_and_burns_the_session(ledger):
    check = verify_exit_geography(
        session(), probe=probe_returning(200, US_MISS_BODY), ledger=ledger
    )

    assert check.passed is False
    assert check.observed_country == "US"
    assert "not TH" in check.failure_reason
    assert ledger.is_burned("abc1")


def test_a_failed_session_still_carries_the_hash_and_metadata(ledger):
    """D-099's finding came from keeping the miss, not from discarding it."""
    import hashlib

    check = verify_exit_geography(
        session(), probe=probe_returning(200, US_MISS_BODY), ledger=ledger
    )

    assert check.payload_hash == hashlib.sha256(US_MISS_BODY).hexdigest()
    assert check.ip == "104.28.202.11"
    assert check.city == "Newport Beach"


def test_non_200_fails_closed(ledger):
    check = verify_exit_geography(
        session(), probe=probe_returning(407, b""), ledger=ledger
    )
    assert check.passed is False
    assert "HTTP 407" in check.failure_reason
    assert ledger.is_burned("abc1")


def test_non_200_with_a_body_still_hashes_it(ledger):
    """A rejection body is often the one artifact that separates a pool fault
    from a geolocation-source fault. Burning the session without preserving it
    throws away the diagnostic and keeps only the symptom."""
    import hashlib

    body = b'{"error":{"title":"Rate limit exceeded"}}'
    check = verify_exit_geography(
        session(), probe=probe_returning(429, body), ledger=ledger
    )

    assert check.passed is False
    assert "HTTP 429" in check.failure_reason
    assert check.payload_hash == hashlib.sha256(body).hexdigest()


def test_non_200_with_a_body_can_keep_the_raw_file(ledger, tmp_path):
    body = b"<html>407 Proxy Authentication Required</html>"
    check = verify_exit_geography(
        session(),
        probe=probe_returning(407, body),
        ledger=ledger,
        evidence_dir=str(tmp_path),
        discard=False,
    )

    assert check.raw_path is not None
    with open(check.raw_path, "rb") as handle:
        assert handle.read() == body


def test_an_empty_body_has_no_hash_rather_than_the_empty_digest(ledger):
    """SHA-256 of zero bytes is a constant. Storing it would put a value in
    payload_hash that looks like evidence of a response and is evidence only
    that there was none."""
    import hashlib

    empty_digest = hashlib.sha256(b"").hexdigest()

    check = verify_exit_geography(
        session(), probe=probe_returning(407, b""), ledger=ledger
    )

    assert check.payload_hash is None
    assert check.payload_hash != empty_digest


def test_an_empty_body_leaves_no_file_behind(ledger, tmp_path):
    verify_exit_geography(
        session(),
        probe=probe_returning(502, b""),
        ledger=ledger,
        evidence_dir=str(tmp_path),
        discard=False,
    )

    assert os.listdir(tmp_path) == []


def test_a_transport_fault_has_no_hash(ledger):
    """No body exists to hash — absent for a reason, and visible as None."""
    check = verify_exit_geography(
        session(), probe=probe_raising(TimeoutError("read timed out")), ledger=ledger
    )

    assert check.passed is False
    assert check.payload_hash is None
    assert check.raw_path is None


def test_transport_error_fails_closed(ledger):
    check = verify_exit_geography(
        session(), probe=probe_raising(TimeoutError("read timed out")), ledger=ledger
    )
    assert check.passed is False
    assert "TimeoutError" in check.failure_reason
    assert ledger.is_burned("abc1")


def test_unparseable_body_fails_closed(ledger):
    check = verify_exit_geography(
        session(), probe=probe_returning(200, b"<html>blocked</html>"), ledger=ledger
    )
    assert check.passed is False
    assert "not JSON" in check.failure_reason
    assert check.payload_hash is not None  # hashed before it was judged
    assert ledger.is_burned("abc1")


def test_json_that_is_not_an_object_fails_closed(ledger):
    check = verify_exit_geography(
        session(), probe=probe_returning(200, b"[]"), ledger=ledger
    )
    assert check.passed is False
    assert "not an object" in check.failure_reason


def test_missing_country_field_fails_closed(ledger):
    body = json.dumps({"ip": "1.2.3.4", "city": "Bangkok"}).encode()
    check = verify_exit_geography(
        session(), probe=probe_returning(200, body), ledger=ledger
    )
    assert check.passed is False
    assert "no usable country field" in check.failure_reason
    assert check.ip == "1.2.3.4"
    assert ledger.is_burned("abc1")


def test_nonsense_country_field_fails_closed(ledger):
    body = json.dumps({"ip": "1.2.3.4", "country": "Thailand"}).encode()
    check = verify_exit_geography(
        session(), probe=probe_returning(200, body), ledger=ledger
    )
    assert check.passed is False
    assert "no usable country field" in check.failure_reason


# ---------------------------------------------------------------------------
# D-099's re-roll rule
# ---------------------------------------------------------------------------


def test_a_burned_session_cannot_be_retried(ledger):
    bad = session("abc1")
    verify_exit_geography(bad, probe=probe_returning(200, US_MISS_BODY), ledger=ledger)

    with pytest.raises(BurnedSessionError) as exc:
        verify_exit_geography(
            bad, probe=probe_returning(200, TH_BODY), ledger=ledger
        )

    assert "new session id" in str(exc.value)


def test_retry_is_refused_even_when_the_pool_would_now_answer_th(ledger):
    """The forbidden recovery is the plausible one: the second probe on the
    same id succeeds, and D-099 still requires the session be discarded. The
    refusal has to hold against a probe that would have passed."""
    bad = session("abc1")
    verify_exit_geography(bad, probe=probe_returning(200, US_MISS_BODY), ledger=ledger)

    calls = []

    def probe(url, proxy_url, timeout):
        calls.append(url)
        return 200, TH_BODY

    with pytest.raises(BurnedSessionError):
        verify_exit_geography(bad, probe=probe, ledger=ledger)

    assert calls == []  # refused before the network, not after


def test_a_fresh_session_id_after_a_failure_is_accepted(ledger):
    verify_exit_geography(
        session("abc1"), probe=probe_returning(200, US_MISS_BODY), ledger=ledger
    )

    check = verify_exit_geography(
        session("def2"), probe=probe_returning(200, TH_BODY), ledger=ledger
    )
    assert check.passed is True
    assert ledger.burned() == frozenset({"abc1"})


def test_require_raises_so_a_caller_cannot_proceed_by_ignoring_the_result(ledger):
    with pytest.raises(ExitGeographyFailure) as exc:
        require_exit_geography(
            session(), probe=probe_returning(200, US_MISS_BODY), ledger=ledger
        )

    assert exc.value.check.observed_country == "US"
    assert "do not retry this one" in str(exc.value)


def test_require_returns_the_check_on_a_pass(ledger):
    check = require_exit_geography(
        session(), probe=probe_returning(200, TH_BODY), ledger=ledger
    )
    assert check.passed is True


# ---------------------------------------------------------------------------
# D-096 — the pool password never travels
# ---------------------------------------------------------------------------


def test_repr_masks_the_proxy_credentials():
    assert "pass_country-th_session-abc1" not in repr(session())
    assert "***:***@" in repr(session())


def test_failure_summary_carries_no_credentials(ledger):
    check = verify_exit_geography(
        session(), probe=probe_returning(200, US_MISS_BODY), ledger=ledger
    )
    assert "pass_country-th" not in check.summary()
    assert "pass_country-th" not in repr(check)


def test_a_transport_error_quoting_the_proxy_url_is_masked(ledger):
    """httpx errors quote the request URL, and for a proxied request that URL
    holds the pool password (D-096)."""
    check = verify_exit_geography(
        session(),
        probe=probe_raising(OSError(f"failed to connect to {SECRET_URL}")),
        ledger=ledger,
    )
    assert "pass_country-th_session-abc1" not in check.failure_reason
    assert "***:***@" in check.failure_reason
