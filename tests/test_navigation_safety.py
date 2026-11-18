"""Navigation cancellation, demo mode and continuous evidence contracts."""

import pytest
from cane.config import NavCfg
from cane.model import Direction
from cane.nav import demo
from cane.nav.amap import AmapError, Route, Step
from cane.nav.navigator import Navigator


def test_demo_rejects_real_phone_coordinates():
    # Given an explicitly synthetic demo adapter.
    client = demo.DemoRouteClient()
    # When asked to navigate from a real Hong Kong location.
    # Then it refuses instead of returning a plausible fabricated route.
    with pytest.raises(AmapError, match="demo"):
        client.walking_route((114.17, 22.33), demo.DEMO_DESTINATION)


def test_demo_route_has_turn_and_crossing():
    # Given the declared demo endpoints.
    client = demo.DemoRouteClient()
    # When a route is requested without keys or network.
    route = client.walking_route(demo.DEMO_ORIGIN, demo.DEMO_DESTINATION)
    # Then navigation includes the intended turn and a crossing handoff.
    assert route.steps[0].maneuver == "turn_right"
    assert route.steps[1].is_crossing
    assert route.polyline[-1] == demo.DEMO_DESTINATION


def test_old_direction_is_revoked_during_reroute():
    # Given a direction already emitted for this route.
    nav = Navigator(NavCfg(), offroute_fixes=1)
    nav.start(demo.DemoRouteClient().walking_route(demo.DEMO_ORIGIN, demo.DEMO_DESTINATION), demo.DEMO_DESTINATION)
    turn = nav.update(0, 60 / 111195)[0]
    assert turn.direction is Direction.RIGHT
    # When a confirmed off-route fix requires replanning.
    output = nav.update(-0.001, 0)
    # Then the old direction cannot be repeated and updates wait for replacement.
    assert output[0].kind == "reroute"
    assert not nav.is_current(turn)
    assert nav.update(0, 60 / 111195) == []


def test_replacement_route_invalidates_previous_output():
    # Given an emitted direction.
    nav = Navigator(NavCfg())
    route = demo.DemoRouteClient().walking_route(demo.DEMO_ORIGIN, demo.DEMO_DESTINATION)
    nav.start(route, demo.DEMO_DESTINATION)
    turn = nav.update(0, 60 / 111195)[0]
    # When the same route is started as a new generation.
    nav.start(route, demo.DEMO_DESTINATION)
    # Then even identical wording from the old generation is stale.
    assert not nav.is_current(turn)


def test_empty_route_rejected_at_start():
    # Given a malformed route with no geometry.
    nav = Navigator(NavCfg())
    # When attempting to start it.
    # Then navigation fails explicitly rather than crashing on the next fix.
    with pytest.raises(AmapError):
        nav.start(Route(0, 0, []), (0, 0))


def test_single_point_step_locates_without_index_error():
    # Given a provider step containing one coordinate.
    nav = Navigator(NavCfg())
    nav.start(Route(100, 100, [Step("walk", 100, [(0, 0)])]), (0, 0.01))
    # When the fix is located.
    position = nav.locate(0, 0)
    # Then its finite point distance is available.
    assert position.off_route_m == 0


def test_step_progression_revokes_previous_turn_without_crossing():
    # Given a direction on a route without a crossing handoff.
    nav = Navigator(NavCfg())
    route = Route(160, 160, [Step("right turn", 100, [demo.DEMO_ORIGIN, demo.DEMO_TURN], action="右转"),
                             Step("walk east", 60, [demo.DEMO_TURN, demo.DEMO_DESTINATION])])
    nav.start(route, demo.DEMO_DESTINATION)
    turn = nav.update(0, 60 / 111195)[0]
    # When the user has progressed beyond that turn onto the next step.
    nav.update(20 / 111195, 100 / 111195)
    # Then its old direction token cannot be repeated.
    assert not nav.is_current(turn)


def test_demo_rejects_nonfinite_destination():
    # Given a nonfinite synthetic destination.
    client = demo.DemoRouteClient()
    # When routing is requested.
    # Then nonfinite coordinates cannot pass the demo boundary.
    with pytest.raises(AmapError):
        client.walking_route(demo.DEMO_ORIGIN, (float("nan"), 0))
