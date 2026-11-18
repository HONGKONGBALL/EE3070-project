"""Real loopback HTTP boundaries for explicit pairing page actions."""

from __future__ import annotations

import logging
import secrets
import socket
from dataclasses import dataclass
from http import HTTPStatus
from threading import Thread
from typing import TYPE_CHECKING, Final

import httpx2
import pytest
from pairing_web import create_server
from pairing_web_state import WebController, WebView

if TYPE_CHECKING:
    from collections.abc import Iterator

LOGGER: Final = logging.getLogger(__name__)


def observe_response(response: httpx2.Response) -> None:
    LOGGER.debug("Loopback pairing HTTP %s", response.status_code)


@dataclass(frozen=True, slots=True)
class LiveServer:
    state: WebController
    origin: str
    port: int
    token: str

    def headers(self) -> httpx2.Headers:
        return httpx2.Headers({"Origin": self.origin, "X-Pairing-Token": self.token})


@pytest.fixture
def live_server() -> Iterator[LiveServer]:
    state = WebController(clock=lambda: 1000)
    token = secrets.token_urlsafe(24)
    with create_server(state, token) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield LiveServer(
                state, f"http://127.0.0.1:{server.server_port}", server.server_port, token
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.fixture
def client() -> Iterator[httpx2.Client]:
    limits = httpx2.Limits(max_connections=200, max_keepalive_connections=40, keepalive_expiry=30)
    transport = httpx2.HTTPTransport(
        http2=True,
        retries=3,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    with httpx2.Client(
        transport=transport,
        timeout=httpx2.Timeout(connect=5, read=30, write=10, pool=10),
        follow_redirects=False,
        trust_env=False,
        event_hooks={"response": [observe_response]},
    ) as connection:
        yield connection


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("Host", "untrusted.invalid:1234"),
        ("Origin", "http://untrusted.invalid"),
        ("X-Pairing-Token", "different-capability"),
    ],
)
def test_post_checks_browser_identity_before_queuing_action(
    live_server: LiveServer, client: httpx2.Client, header: str, value: str
) -> None:
    # Given a real loopback server and one mismatching browser identity field.
    headers = live_server.headers()
    headers[header] = value
    # When the browser submits an explicit open intent.
    response = client.post(
        live_server.origin + "/api/action", headers=headers, json={"action": "open"}
    )
    # Then the HTTP boundary retains an empty owner queue.
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert live_server.state.queue.empty()


def test_valid_open_post_queues_exactly_one_action(
    live_server: LiveServer, client: httpx2.Client
) -> None:
    # Given the current page origin and its capability.
    headers = live_server.headers()
    # When one explicit browser open arrives.
    response = client.post(
        live_server.origin + "/api/action", headers=headers, json={"action": "open"}
    )
    # Then HTTP acknowledges queuing one immutable action.
    assert response.status_code == HTTPStatus.ACCEPTED
    assert live_server.state.queue.qsize() == 1


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/", HTTPStatus.OK), ("/api/state", HTTPStatus.OK), ("/api/action", HTTPStatus.NOT_FOUND)],
)
def test_get_reads_page_state_with_empty_action_queue(
    live_server: LiveServer, client: httpx2.Client, path: str, expected: HTTPStatus
) -> None:
    # Given a fresh controller and the page's current browser identity.
    headers = live_server.headers()
    # When a browser reads a page or API resource.
    response = client.get(live_server.origin + path, headers=headers)
    # Then the response belongs to the resource and the action queue stays empty.
    assert response.status_code == expected
    assert live_server.state.queue.empty()


def test_idle_preconnect_allows_another_browser_request(
    live_server: LiveServer, client: httpx2.Client
) -> None:
    # Given a browser preconnection whose request headers remain incomplete.
    with socket.create_connection(("127.0.0.1", live_server.port), timeout=1) as idle:
        idle.sendall(b"GET /api/state HTTP/1.1\r\nHost:")
        # When another browser connection reads the current state during that idle request.
        response = client.get(
            live_server.origin + "/api/state",
            headers=live_server.headers(),
            timeout=httpx2.Timeout(connect=1, read=0.75, write=1, pool=1),
        )
        # Then the threaded server responds within the active request's read budget.
        assert response.status_code == HTTPStatus.OK
        view = WebView.model_validate_json(response.content)
        assert view.status is None
        assert live_server.state.queue.empty()
