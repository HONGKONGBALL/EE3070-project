"""Replacing and pausing pending routes cannot revive obsolete directions."""

import json

import pytest
from cane.nav.amap import AmapClient, parse_walking
from cane.state_machine import State
from test_app import feed, flush
from test_nav import ROUTE_JSON, A, B, D, lerp

pytest_plugins = ("test_app",)


def prepare_route(app, clock):
    feed(app, clock)
    app._on_location(*A, 5.0, clock())
    app.navigator.start(parse_walking(ROUTE_JSON), D)
    return app.navigator.update(*lerp(A, B, 0.55))[0]


def test_new_destination_immediately_revokes_old_route_while_network_pending(app, clock, monkeypatch):
    # Given an active route and an old right-turn instruction.
    previous = prepare_route(app, clock)
    pending = []
    monkeypatch.setattr(app, "_spawn", lambda work, name: pending.append(work))
    # When a new destination is chosen before its network request completes.
    app.start_navigation(app.cfg.nav.destinations[1])
    # Then neither replay nor new location updates can issue the old route's direction.
    assert app._route_pending
    assert not app.navigator.is_current(previous)
    assert app.navigator.update(*lerp(A, B, 0.85)) == []
    assert len(pending) == 1


def test_pause_cancels_suspended_reroute_and_late_result_cannot_revive_it(app, clock, monkeypatch):
    # Given an off-route session suspended while a replacement request is pending.
    prepare_route(app, clock)
    pending = []
    monkeypatch.setattr(app, "_spawn", lambda work, name: pending.append(work))
    app.navigator.invalidate()
    app._request_route(D, reroute=True)
    # When the user pauses, resumes, and that old request finally completes.
    app.enqueue_control("pause")
    feed(app, clock)
    app.enqueue_control("resume")
    feed(app, clock)
    pending[0]()
    feed(app, clock)
    # Then navigation has clearly ended and requires a fresh destination selection.
    assert app.sm.state is State.STOPPED
    assert not app.navigator.active
    assert not app._route_pending
    assert any(event.get("reason") == "paused_during_route_request" for event in app.logbook.recent)


def test_normal_pause_without_pending_route_preserves_current_session(app, clock):
    # Given an established route with no outstanding network request.
    prepare_route(app, clock)
    # When the user pauses normal guidance.
    app.enqueue_control("pause")
    feed(app, clock)
    # Then normal pause semantics retain the route instead of cancelling it.
    assert app.sm.state is State.PAUSED
    assert app.navigator.active


@pytest.mark.parametrize(("payload", "prompt"), [
    ({"status": "1", "route": {"paths": []}}, "walk_route_unavailable"),
    ({"status": "0", "infocode": "20801"}, "walk_route_unavailable"),
    ({"status": "0", "infocode": "10001"}, "route_failed"),
    ({"status": "0", "infocode": "20803"}, "route_failed"),
])
def test_route_failure_announces_only_the_supported_outcome(app, audio, clock, payload, prompt):
    feed(app, clock)
    app._on_location(*A, 5.0, clock())
    app.amap = AmapClient("fixture", fetch=lambda url, timeout: json.dumps(payload).encode())
    app.start_navigation(app.cfg.nav.destinations[0])
    flush(app, audio, clock)
    assert [prompt] in audio.played
    assert ["nav_start"] not in audio.played
    assert not app.navigator.active


def test_blocked_walking_path_announces_alternatives_and_keeps_navigation_stopped(app, audio, clock):
    path = ROUTE_JSON["route"]["paths"][0]
    blocked = {**path, "steps": [{**path["steps"][0], "action": "禁止行人通行"}]}
    payload = {"status": "1", "route": {"paths": [blocked]}}
    app.amap = AmapClient("fixture", fetch=lambda url, timeout: json.dumps(payload).encode())
    feed(app, clock)
    app._on_location(*A, 5.0, clock())
    app.start_navigation(app.cfg.nav.destinations[0])
    flush(app, audio, clock)
    assert ["walk_route_blocked"] in audio.played
    assert ["nav_start"] not in audio.played
    assert not app.navigator.active


def test_provider_alternative_is_announced_before_navigation(app, audio, clock):
    path = ROUTE_JSON["route"]["paths"][0]
    blocked = {**path, "steps": [{**path["steps"][0], "walk_type": 30}]}
    payload = {"status": "1", "route": {"paths": [blocked, path]}}
    app.amap = AmapClient("fixture", fetch=lambda url, timeout: json.dumps(payload).encode())
    feed(app, clock)
    app._on_location(*A, 5.0, clock())
    app.start_navigation(app.cfg.nav.destinations[0])
    flush(app, audio, clock)
    assert audio.played.index(["walk_route_alternative"]) < audio.played.index(["nav_start"])
    assert app.navigator.active
