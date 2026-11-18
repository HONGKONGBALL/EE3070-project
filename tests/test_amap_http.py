"""AMap adapter crosses a real local HTTP socket; no production key required."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from cane.nav import amap


@pytest.fixture
def fixture_http():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            requests.append(parse_qs(parsed.query))
            if parsed.path.endswith("walking"):
                payload = {"status": "1", "route": {"paths": [{"distance": "10", "duration": "9", "steps": [
                    {"instruction": "向北直行", "distance": "10", "polyline": "0,0;0,0.00009", "action": "直行"}]}]}}
            elif parsed.path.endswith("convert"):
                payload = {"status": "1", "locations": "0,0.00009"}
            else:
                payload = {"status": "1", "geocodes": [{"location": "0,0.00009"}]}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return None

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_port}", requests
        server.shutdown()
        thread.join(timeout=2)


def test_real_http_fixture_parses_walking_response(fixture_http):
    # Given the actual network adapter pointed at a local HTTP fixture.
    base_url, requests = fixture_http
    client = amap.AmapClient("fixture-key", base_url=base_url)
    # When the walking route crosses a real TCP socket.
    route = client.walking_route((0, 0), (0, 0.00009))
    # Then provider data becomes a usable route and the query is encoded correctly.
    assert route.steps[0].maneuver == "go_straight"
    assert requests[0]["origin"] == ["0.000000,0.000000"]
    assert requests[0]["key"] == ["fixture-key"]


@pytest.mark.parametrize("payload", [[], {"status": "1", "route": []}, {"status": "1", "route": {"paths": [{"distance": "NaN", "steps": []}]}}, {"status": "1", "route": {"paths": [{"steps": [{"polyline": "oops"}]}]}}])
def test_malformed_provider_response_is_typed_error(payload):
    # Given invalid JSON shapes or invalid route coordinates from an external service.
    # When the provider response is parsed.
    # Then only a named adapter error escapes to the app boundary.
    with pytest.raises(amap.AmapError):
        amap.parse_walking(payload)


def test_network_failure_does_not_log_key(caplog):
    # Given a lower-level network error whose message contains an access-bearing URL.
    def fail(url, timeout):
        raise OSError(url)

    client = amap.AmapClient("SECRET-KEY", fetch=fail)
    # When routing fails.
    with caplog.at_level(logging.WARNING), pytest.raises(amap.NetworkError):
        client.walking_route((0, 0), (0, 1))
    # Then neither the request URL nor the exception can expose the secret.
    assert "SECRET-KEY" not in caplog.text


def test_real_http_fixture_converts_coordinates(fixture_http):
    # Given the actual coordinate converter using a loopback HTTP endpoint.
    base_url, requests = fixture_http
    client = amap.AmapClient("fixture-key", base_url=base_url)
    # When the GPS conversion response crosses the socket.
    converted = client.convert_gps(0, 0)
    # Then its typed coordinates and API mode are preserved.
    assert converted == (0, 0.00009)
    assert requests[0]["coordsys"] == ["gps"]


def test_real_http_fixture_geocodes_address(fixture_http):
    # Given the real HTTP adapter and a non-ASCII address.
    base_url, requests = fixture_http
    client = amap.AmapClient("fixture-key", base_url=base_url)
    # When the geocoder is invoked.
    coordinate = client.geocode("演示地点", city="演示城市")
    # Then query encoding and location parsing work over HTTP.
    assert coordinate == (0, 0.00009)
    assert requests[0]["address"] == ["演示地点"]
    assert requests[0]["city"] == ["演示城市"]
