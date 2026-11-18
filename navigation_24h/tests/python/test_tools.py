"""Observable tool behavior and malformed evidence boundary coverage."""

from __future__ import annotations

import pytest
from acceptance import CaseResult, summarize
from host_gate import Event, Gate, Identity
from metrics import distribution, percentile
from project import LocalConfig, redact
from pydantic import ValidationError


def test_redacts_service_url_and_authorization() -> None:
    # Given an authenticated provider URL.
    text = "https://mcp.amap.com/mcp?key=sensitive&route=1 Authorization: BearerSecret"
    # When output crosses the evidence boundary.
    result = redact(text)
    # Then public fields survive and credentials are redacted.
    assert result == "https://mcp.amap.com/mcp?key=[redacted]&route=1 Authorization: [redacted]"


def test_rejects_unrecognized_protocol_version() -> None:
    # Given a local configuration with an unsupported protocol revision.
    payload = '{"mcp":{"protocol_version":"1900-01-01"}}'
    # When configuration is parsed.
    with pytest.raises(ValidationError):
        # Then the boundary provides a typed configuration error.
        _ = LocalConfig.model_validate_json(payload)


def test_metrics_nearest_rank_percentile() -> None:
    # Given 100 latency samples.
    samples = tuple(float(index) for index in range(1, 101))
    # When P95 is computed.
    observed = percentile(samples, 95)
    # Then the nearest-rank sample is reported.
    assert observed == 95


def test_host_gate_rejects_expired_event() -> None:
    # Given the current navigation identity and a 2-second event.
    identity = Identity(session_id="s1", generation=1, route_version=1)
    gate = Gate(identity)
    event = Event(
        type="maneuver",
        session_id="s1",
        generation=1,
        route_version=1,
        event_id=1,
        ttl_ms=2000,
        issued_at_ms=100,
        fix_age_ms=100,
        fix_seq=2,
    )
    # When an event reaches the output boundary after its deadline.
    accepted = gate.accept(event, received_at_ms=100, now_ms=2100)
    # Then the output reports expiry.
    assert accepted.reason == "EVENT_EXPIRED"


def test_package_acceptance_requires_device_evidence() -> None:
    # Given a host-only partial case observation.
    result = CaseResult(
        case_id="TC01", result="partial", mode="host", evidence=("evidence/host.json",)
    )
    # When the acceptance summary is computed.
    observed = summarize((result,))
    # Then only completed assertions count as passed.
    assert observed.passed == 0


def test_redacts_bearer_scheme_and_quoted_password() -> None:
    # Given two credential forms used by service clients.
    raw = 'Authorization: Bearer secret-value\n{"password":"two word secret"}'
    # When the evidence boundary prepares its output.
    cleaned = redact(raw)
    # Then each complete secret is covered.
    assert "secret-value" not in cleaned
    assert "two word secret" not in cleaned


def test_metrics_count_matches_finite_percentile_population() -> None:
    # Given a measurement stream containing two finite latencies and NaN.
    values = (1.0, float("nan"), 3.0)
    # When a distribution is produced.
    observed = distribution(values)
    # Then its count and percentiles describe the same samples.
    assert observed.count == 2
    assert observed.p50 == 1.0
    assert observed.p95 == 3.0


def test_summary_counts_unbacked_pass_as_partial() -> None:
    # Given a reported pass whose evidence path requires collection.
    record = CaseResult(case_id="TC01", result="passed", mode="device", evidence=())
    # When the release summary is evaluated.
    observed = summarize((record,))
    # Then the case retains partial coverage.
    assert observed.passed == 0
    assert observed.partial == 1
    assert observed.total == 1


def test_host_gate_rejects_negative_transit_age() -> None:
    # Given the current identity and a fresh navigation event.
    gate = Gate(Identity(session_id="s", generation=1, route_version=1))
    event = Event(
        type="maneuver",
        session_id="s",
        generation=1,
        route_version=1,
        event_id=1,
        ttl_ms=2000,
        issued_at_ms=100,
        fix_seq=1,
    )
    # When the transport supplies an invalid delay estimate.
    result = gate.accept(event, received_at_ms=100, now_ms=200, transit_age_ms=-1)
    # Then output requires a valid timing reference.
    assert result.accepted is False


def test_summary_requires_readable_evidence_file() -> None:
    # Given a case whose evidence file requires collection.
    result = CaseResult(
        case_id="TC01",
        result="passed",
        mode="device",
        evidence=("evidence/collection-required.json",),
    )
    # When acceptance is summarized.
    observed = summarize((result,))
    # Then its observation remains partial until evidence is present.
    assert observed.passed == 0
    assert observed.partial == 1
