"""Serial capture, release identity and physical confirmation behavior."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from zipfile import ZipFile

import pytest
from acceptance import Summary
from capture import decode_line
from host_gate import Event, Identity
from host_sim import ClockAck, HostSession, StatusRequest
from packaging_release import archive_project, snapshot_paths
from pico_bridge import Bridge, ButtonEvent
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path


def test_malformed_serial_frame_retains_input_identity() -> None:
    # Given a truncated serial JSON frame.
    raw = b'{"type":\xff\n'
    # When it crosses the capture boundary.
    observed = decode_line(raw, "locked", 100)
    # Then it retains timing, input hash and the malformed disposition.
    assert observed.valid_json is False
    assert observed.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert observed.segment == "locked"


def test_snapshot_selects_source_examples_and_build_artifacts(tmp_path: Path) -> None:
    # Given source, local credentials and generated cache beside a real artifact path.
    for name in (
        "tools/run.py",
        "config/local.example.json",
        "config/local.json",
        "ios/.build/cache.json",
        "firmware/build/navigation.bin",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(name)
    # When the snapshot enumerates its inputs.
    paths = {str(path.relative_to(tmp_path)) for path in snapshot_paths(tmp_path)}
    # Then reviewable sources and the build product are selected.
    assert paths == {"tools/run.py", "config/local.example.json", "firmware/build/navigation.bin"}


def test_archive_manifest_matches_archived_file_bytes(tmp_path: Path) -> None:
    # Given one source file and the observed acceptance summary.
    source = tmp_path / "tools/core.py"
    source.parent.mkdir()
    _ = source.write_text("print('navigation')\n")
    archive = tmp_path / "release/project.zip"
    summary = Summary(total=60, passed=0, failed=0, partial=0, awaiting=60)
    # When the snapshot is written.
    manifest = archive_project(tmp_path, archive, summary)
    # Then both archive and file identities match their actual bytes.
    assert manifest.archive_sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
    with ZipFile(archive) as bundle:
        entry = next(item for item in manifest.files if item.path == "tools/core.py")
        assert entry.sha256 == hashlib.sha256(bundle.read(entry.path)).hexdigest()


def test_host_requires_clock_reference_for_event() -> None:
    # Given a current firmware status with a navigation event.
    host = HostSession()
    _ = host.receive(
        '{"type":"status","session_id":"s","generation":1,"route_version":1,"state":"TURN","device_monotonic_ms":10}',
        100,
    )
    # When its event arrives before clock exchange.
    reply = host.receive(
        '{"type":"maneuver","session_id":"s","generation":1,"route_version":1,"event_id":1,"ttl_ms":2000,"issued_at_ms":10,"fix_seq":1}',
        101,
    )
    # Then the host asks for a timing reference.
    assert reply is not None
    assert reply.accepted is False
    assert reply.reason == "TIMING_REFERENCE"


def test_pico_press_confirms_current_event_once() -> None:
    # Given a live current turn and a debounced button press.
    bridge = Bridge()
    bridge.host.gate.identity = Identity(session_id="s", generation=2, route_version=3)
    bridge.host.state = "TURN"
    bridge.host.current_event = Event(
        type="maneuver",
        session_id="s",
        generation=2,
        route_version=3,
        event_id=5,
        ttl_ms=2000,
        issued_at_ms=100,
        fix_seq=4,
    )
    bridge.host.event_deadline = 2000
    press = ButtonEvent(
        type="button",
        boot_id="pico",
        sequence=1,
        stable_ms=40,
        pressed_at_ms=10,
        session_id="s",
        generation=2,
        route_version=3,
        event_id=5,
    )
    # When the press is delivered twice.
    first = bridge.confirmation(press, 1000)
    second = bridge.confirmation(press, 1001)
    # Then only the first delivery names the current event.
    assert first is not None
    assert first.event_id == 5
    assert first.generation == 2
    assert second is None


def test_pico_press_at_event_deadline_requires_current_prompt() -> None:
    # Given a turn whose event has reached its expiry boundary.
    bridge = Bridge()
    bridge.host.gate.identity = Identity(session_id="s", generation=1, route_version=1)
    bridge.host.state = "TURN"
    bridge.host.current_event = Event(
        type="maneuver",
        session_id="s",
        generation=1,
        route_version=1,
        event_id=1,
        ttl_ms=2000,
        issued_at_ms=0,
        fix_seq=1,
    )
    bridge.host.event_deadline = 2000
    press = ButtonEvent(
        type="button",
        boot_id="pico",
        sequence=1,
        stable_ms=40,
        pressed_at_ms=10,
        session_id="s",
        generation=1,
        route_version=1,
        event_id=1,
    )
    # When the user presses at the deadline.
    result = bridge.confirmation(press, 2000)
    # Then a fresh prompt is required for confirmation.
    assert result is None


def test_button_boundary_requires_debounce_interval() -> None:
    # Given a press shorter than the firmware debounce interval.
    raw = (
        '{"type":"button","boot_id":"pico","sequence":1,"stable_ms":10,'
        '"pressed_at_ms":0,"session_id":"s","generation":1,"route_version":1,"event_id":1}'
    )
    # When the bridge parses it.
    with pytest.raises(ValidationError):
        # Then it requires a stable physical press.
        _ = ButtonEvent.model_validate_json(raw)


def test_time_sync_uses_device_issued_age_for_event_ttl() -> None:
    # Given a synchronized host and device clock with a 100ms round trip.
    host = HostSession()
    request = StatusRequest.model_validate_json(host.status_request(1000))
    ack = ClockAck(
        command_id=request.command_id, phone_send_monotonic_ms=1000, device_monotonic_ms=500
    )
    _ = host.receive('{"type":"ack",' + ack.model_dump_json()[1:], 1100)
    _ = host.receive(
        '{"type":"status","session_id":"s","generation":1,"route_version":1,"state":"TURN","device_monotonic_ms":600}',
        1100,
    )
    # When an event issued 2.1 seconds earlier reaches the host.
    reply = host.receive(
        '{"type":"maneuver","session_id":"s","generation":1,"route_version":1,"event_id":1,"ttl_ms":2000,"issued_at_ms":500,"fix_seq":1}',
        3100,
    )
    # Then its TTL is evaluated from the synchronized issued time.
    assert reply is not None
    assert reply.reason == "EVENT_EXPIRED"


def test_pico_button_context_matches_current_navigation_event() -> None:
    # Given a current turn and a physical press associated with a prior route.
    bridge = Bridge()
    bridge.host.gate.identity = Identity(session_id="s", generation=1, route_version=2)
    bridge.host.state = "TURN"
    bridge.host.current_event = Event(
        type="maneuver",
        session_id="s",
        generation=1,
        route_version=2,
        event_id=2,
        ttl_ms=2000,
        issued_at_ms=0,
        fix_seq=1,
    )
    bridge.host.event_deadline = 2000
    press = ButtonEvent(
        type="button",
        boot_id="pico",
        sequence=1,
        stable_ms=40,
        pressed_at_ms=10,
        session_id="s",
        generation=1,
        route_version=1,
        event_id=1,
    )
    # When the buffered press arrives during the current turn.
    observed = bridge.confirmation(press, 1000)
    # Then confirmation awaits a press carrying the current context.
    assert observed is None
