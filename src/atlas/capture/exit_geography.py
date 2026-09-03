"""Per-session exit-IP geography verification — the D-099 precondition.

D-099 resolved D-091: the existing GB-purchased IPRoyal pool does reach TH
through the `_country-th` password parameter, at 19/20 across two ten-session
probe batches. The one miss exited US (org AS7654, geolocated Newport Beach
CA) and its cause is unconfirmed — either an exit-node geolocation error on
that node or transient bad routing; the same org returned correct TH exits
elsewhere in the same batch. A GB control batch run alongside was 10/10 with
zero leakage, so the miss is specific to the TH parameter rather than a
general pool fault.

Because the miss rate is non-zero and unexplained, D-099 makes capture
geography a fact each session must *demonstrate*, not one the configuration
is trusted to deliver:

    per-session exit-IP geography verification ... is required before any
    session's observations are treated as valid Layer B evidence, with
    sessions landing outside TH discarded and re-rolled under a new session
    id rather than retried under the same one.

This module is that verification. It is a library, not a script: D-099 names
the step a precondition of Layer B capture, so it has to be callable from
whatever drives capture rather than run by hand beside it.

## What "do not retry under the same id" means here, mechanically

A failed session is **burned**. `SessionLedger` records every session id this
process has seen fail, and `verify_exit_geography` refuses a burned id
outright — a caller that catches the failure and loops on the same
`ProxySession` gets `BurnedSessionError`, not a second probe. The re-roll is
the caller's job (a new `session_id`); the ledger's job is to make the wrong
recovery impossible to write by accident rather than merely discouraged in
prose.

The ledger is per-process. It cannot know about a session burned in another
process or on another day, and it is not a substitute for the re-roll rule
itself — it enforces the rule within the run that observed the failure, which
is the window in which the mistake is actually available to be made.

## Fail-closed, including on transport faults

A timeout, a non-200, an unparseable body or a response with no country field
are all failures, and all burn the session. None of them is *proof* that the
exit is outside TH — but D-099's rule is about what may be treated as valid
evidence, and an unverified session has not met the precondition regardless
of why. The cheap correct action is a fresh session; the expensive wrong one
is a capture whose geography nobody established.

## Hashing, and what is deliberately not stored

Whenever a response body is received, its raw bytes are hashed with
`vault.sha256_file` — the bytes-on-disk hash, not `hash_payload`'s
canonical-JSON one, because what is being attested is the response as
observed rather than a dict Atlas built from it. The file is then unlinked by
default and only the digest survives on `ExitGeographyCheck.payload_hash`.

**Hashing is not conditional on the check passing, or on HTTP 200.** An
earlier version hashed only after a 200, which meant a non-200 carrying a body
— the pool itself rejecting the request, a geolocation source returning an
error document — burned the session with nothing preserved: no hash, no
evidence of what was actually said. That is backwards. A rejection body is
frequently the most diagnostic artifact the check produces, and D-099's own
finding came from keeping the anomalous responses rather than discarding
them. `payload_hash` is therefore populated on every branch downstream of a
received body, pass or fail.

Two cases have no hash, necessarily rather than by choice, and both are
visible as `payload_hash is None`:

- a transport fault, where no body exists to hash;
- a response with an empty body, where a digest would be the SHA-256 of zero
  bytes — a constant, identical for every such response, attesting nothing.
  Recorded as absent rather than as a hash that looks like evidence.

This writes nothing to the database. `evidence` rows are observation-scoped
(D-049's provenance columns are per-observation), and a capture-configuration
check belongs to a *session*, which no table models — the check runs before
any observation exists. Persisting it is therefore left to the caller, which
is the party that knows which run plan and which cells the session went on to
produce. Pass `discard=False` to keep the raw file for that purpose.

Capture geography is a D-090 Calibration Manifest field, and `run_plans`
records no column for it; this module does not add one. Recording the
verified geography on the plan is a schema question for the manifest work,
not something to infer here.

## Credentials

`ProxySession.proxy_url` carries the pool password, which is where the
`_country-xx` and `_session-xxxx` parameters live. D-096 makes rendering a
secret into any transcript an exposure event in its own right, so the URL is
masked in `repr` and never appears in a returned field, a note or an
exception message. Only the session id and the country parameter travel.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from atlas.evidence.vault import sha256_file

# D-099's own probe target. Named as a constant rather than inlined so the
# "equivalent geolocation source" the decision allows is a one-line
# substitution with a visible history, not an edit buried in a call.
#
# KNOWN OPERATIONAL DEPENDENCY, not a defect: this is a single unauthenticated
# third-party endpoint with no fallback, and because the check fails closed,
# a rate-limit or a response-shape change here burns every session regardless
# of pool health — the failure presents as a dead proxy pool while the pool is
# fine. `url` is a per-call parameter so a second source can be substituted
# without a code change; choosing and sequencing one is a capture-pipeline
# decision, not something to settle inside this primitive.
GEOLOCATION_URL = "https://ipinfo.io/json"

# The ratified calibration market for Samujana is TH/en (D-088), and D-091
# required capture geography be conformed to it. This is the default, not a
# hardcoding: a second market is a second plan (D-089) with its own capture
# configuration, and it passes its own expected country.
EXPECTED_COUNTRY = "TH"

DEFAULT_TIMEOUT_SECONDS = 20.0

# ISO 3166-1 alpha-2, which is what ipinfo.io's `country` field returns.
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


class ExitGeographyError(RuntimeError):
    """Base for every refusal this module raises."""


class BurnedSessionError(ExitGeographyError):
    """A session id that has already failed verification was offered again.

    D-099 requires a fresh session id, not a retry of the failed one. Raised
    instead of re-probing so the forbidden recovery cannot be reached by
    catching `ExitGeographyFailure` and looping.
    """


class ExitGeographyFailure(ExitGeographyError):
    """Verification did not pass. Carries the check so a caller can log the
    observed geography (or the transport fault) without re-running it."""

    def __init__(self, check: "ExitGeographyCheck") -> None:
        self.check = check
        super().__init__(check.summary())


def _mask(url: str) -> str:
    """Everything between the scheme and the host is credentials."""
    return re.sub(r"://[^@/]*@", "://***:***@", url)


@dataclass(frozen=True)
class ProxySession:
    """One proxy session's identity and configuration.

    `session_id` is the value carried in the pool password's `_session-xxxx`
    parameter — the thing D-099 says must change after a failure. It is
    supplied rather than generated here: the caller owns session lifecycle,
    and a generator inside the verifier would make "re-roll" look like
    something verification does on the caller's behalf, which is exactly the
    retry-in-place this module exists to prevent.
    """

    session_id: str
    country_param: str
    proxy_url: str

    def __repr__(self) -> str:  # never render the password (D-096)
        return (
            f"ProxySession(session_id={self.session_id!r}, "
            f"country_param={self.country_param!r}, "
            f"proxy_url={_mask(self.proxy_url)!r})"
        )


@dataclass(frozen=True)
class ExitGeographyCheck:
    """Pass/fail plus the exit metadata that justified it.

    `payload_hash` is present on a failure too whenever a body was actually
    received — a session that exited in the wrong country is still evidence
    of what the pool did, and D-099's own finding came from keeping exactly
    those rows.
    """

    session_id: str
    expected_country: str
    passed: bool
    checked_at: datetime
    observed_country: str | None = None
    ip: str | None = None
    city: str | None = None
    region: str | None = None
    org: str | None = None
    payload_hash: str | None = None
    raw_path: str | None = None
    failure_reason: str | None = None

    def summary(self) -> str:
        if self.passed:
            return (
                f"session {self.session_id}: exit {self.ip} resolves to "
                f"{self.observed_country} as required."
            )
        where = self.observed_country or "unknown"
        return (
            f"session {self.session_id}: expected exit country "
            f"{self.expected_country}, observed {where} "
            f"({self.failure_reason}). D-099: discard this session and "
            "re-roll under a NEW session id — do not retry this one."
        )


class SessionLedger:
    """Session ids this process has seen fail. See the module docstring."""

    def __init__(self) -> None:
        self._burned: set[str] = set()

    def burn(self, session_id: str) -> None:
        self._burned.add(session_id)

    def is_burned(self, session_id: str) -> bool:
        return session_id in self._burned

    def burned(self) -> frozenset[str]:
        return frozenset(self._burned)


# The default ledger. Shared deliberately: two call sites in one process that
# each kept their own would each allow the retry the other had already ruled
# out. Tests pass their own instance.
LEDGER = SessionLedger()


class Probe(Protocol):
    """The one network operation this module performs, injectable so the
    fail-closed branches are testable without a proxy or a live pool."""

    def __call__(self, url: str, proxy_url: str, timeout: float) -> tuple[int, bytes]:
        ...


def httpx_probe(url: str, proxy_url: str, timeout: float) -> tuple[int, bytes]:
    """Default `Probe`. Imported lazily so importing this module — which the
    capture driver does at startup — does not require httpx to be installed
    for callers that inject their own probe."""
    import httpx

    with httpx.Client(proxy=proxy_url, timeout=timeout, follow_redirects=False) as client:
        response = client.get(url)
        return response.status_code, response.content


def verify_exit_geography(
    session: ProxySession,
    *,
    expected_country: str = EXPECTED_COUNTRY,
    url: str = GEOLOCATION_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    probe: Probe | Callable[..., tuple[int, bytes]] = httpx_probe,
    ledger: SessionLedger | None = None,
    evidence_dir: str | None = None,
    discard: bool = True,
    now: datetime | None = None,
) -> ExitGeographyCheck:
    """Probe `session`'s exit IP and report whether it lands in
    `expected_country`.

    Returns the check rather than raising on a failed geography — a caller
    that wants the D-099 gate as control flow uses `require_exit_geography`.
    A failure burns the session id either way.

    Raises `BurnedSessionError` if `session.session_id` has already failed.
    """
    led = LEDGER if ledger is None else ledger
    stamp = now or datetime.now(timezone.utc)

    if led.is_burned(session.session_id):
        raise BurnedSessionError(
            f"session {session.session_id} already failed exit-geography "
            "verification. D-099 requires a new session id, not a retry of "
            "this one."
        )

    expected = expected_country.upper()

    def fail(reason: str, **fields) -> ExitGeographyCheck:
        led.burn(session.session_id)
        return ExitGeographyCheck(
            session_id=session.session_id,
            expected_country=expected,
            passed=False,
            checked_at=stamp,
            failure_reason=reason,
            **fields,
        )

    try:
        status, body = probe(url, session.proxy_url, timeout)
    except Exception as exc:  # noqa: BLE001 — every transport fault is a failure
        # Type and message only. An exception from an HTTP client can carry the
        # request URL, and for a proxied request that URL holds the pool
        # password (D-096).
        return fail(f"probe raised {type(exc).__name__}: {_mask(str(exc))}")

    # Hash before anything is decided about the body, and before it is
    # discarded — the digest attests what was received, not what parsed, and
    # not whether the status line was one this check likes.
    payload_hash, raw_path = _hash_body(body, session.session_id, evidence_dir, discard)

    if status != 200:
        # A rejection body is preserved by hash like any other. It is often
        # the one artifact that distinguishes a pool fault from a geolocation
        # source fault, which is exactly the distinction D-099 left open.
        return fail(
            f"geolocation source returned HTTP {status}",
            payload_hash=payload_hash,
            raw_path=raw_path,
        )

    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        return fail(
            f"geolocation response is not JSON: {type(exc).__name__}",
            payload_hash=payload_hash,
            raw_path=raw_path,
        )

    if not isinstance(payload, dict):
        return fail(
            f"geolocation response is {type(payload).__name__}, not an object",
            payload_hash=payload_hash,
            raw_path=raw_path,
        )

    observed = payload.get("country")
    metadata = {
        "ip": _text(payload.get("ip")),
        "city": _text(payload.get("city")),
        "region": _text(payload.get("region")),
        "org": _text(payload.get("org")),
        "payload_hash": payload_hash,
        "raw_path": raw_path,
    }

    if not isinstance(observed, str) or not _COUNTRY_RE.match(observed):
        return fail(
            f"geolocation response carries no usable country field "
            f"(got {observed!r})",
            **metadata,
        )

    observed = observed.upper()
    if observed != expected:
        return fail(
            f"exit landed in {observed}, not {expected}",
            observed_country=observed,
            **metadata,
        )

    return ExitGeographyCheck(
        session_id=session.session_id,
        expected_country=expected,
        passed=True,
        checked_at=stamp,
        observed_country=observed,
        **metadata,
    )


def require_exit_geography(
    session: ProxySession, **kwargs
) -> ExitGeographyCheck:
    """`verify_exit_geography`, but a failed check stops the caller.

    This is the form the capture driver calls. D-099 makes verification a
    precondition rather than a report, so the default entry point for a
    pipeline is the one that cannot be ignored by not reading a return value.
    """
    check = verify_exit_geography(session, **kwargs)
    if not check.passed:
        raise ExitGeographyFailure(check)
    return check


def _hash_body(
    body: bytes, session_id: str, evidence_dir: str | None, discard: bool
) -> tuple[str | None, str | None]:
    """Write the raw bytes, hash the file, and unlink it unless kept.

    `sha256_file` rather than `hash_payload`: the artifact is a response as
    received, and hashing a dict Atlas decoded from it would attest Atlas's
    parse instead of the pool's answer.

    Returns `(None, None)` for an empty body. The SHA-256 of zero bytes is a
    well-known constant, so storing it would put a value in `payload_hash`
    that looks like evidence of a response and is evidence only that there
    was none.
    """
    if not body:
        return None, None

    directory = evidence_dir or tempfile.gettempdir()
    os.makedirs(directory, exist_ok=True)
    fd, path = tempfile.mkstemp(
        prefix=f"exit-geo-{session_id}-", suffix=".json", dir=directory
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        digest = sha256_file(path)
    except BaseException:
        _unlink(path)
        raise

    if discard:
        _unlink(path)
        return digest, None
    return digest, path


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
