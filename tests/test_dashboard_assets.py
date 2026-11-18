"""Read-only dashboard assets and camera preview access."""

from pathlib import Path

import pytest
from cane.web.app import create_app
from dashboard_fakes import PreviewController
from fastapi.testclient import TestClient


@pytest.mark.parametrize("name", ["dashboard.css", "dashboard.js", "dashboard-dom.js", "dashboard-render.js", "dashboard-replay.js"])
def test_dashboard_asset_is_served_when_client_is_local(name: str) -> None:
    # Given a local dashboard browser.
    client = TestClient(create_app(PreviewController(), ["127.0.0.1/32"], None), client=("127.0.0.1", 1))
    # When it loads a declared static asset.
    response = client.get(f"/assets/{name}")
    # Then the resource exists without executable HTML content.
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "text/html" not in response.headers["content-type"]


@pytest.mark.parametrize("path", ["/", "/assets/dashboard.js", "/api/camera/frame.jpg"])
def test_dashboard_resource_is_blocked_when_client_is_remote(path: str) -> None:
    # Given a client outside diagnostics networks.
    client = TestClient(create_app(PreviewController(b"jpeg"), ["127.0.0.1/32"], None), client=("192.168.1.5", 1))
    # When it reads an otherwise available dashboard resource.
    response = client.get(path)
    # Then the resource stays private.
    assert response.status_code == 403


def test_camera_preview_is_unavailable_when_frame_is_missing() -> None:
    # Given an unconnected camera.
    client = TestClient(create_app(PreviewController(), ["127.0.0.1/32"], None), client=("127.0.0.1", 1))
    # When a preview is requested.
    response = client.get("/api/camera/frame.jpg")
    # Then no placeholder image masquerades as live footage.
    assert response.status_code == 503


def test_camera_preview_is_served_when_frame_exists() -> None:
    # Given an available encoded frame.
    frame = b"\xff\xd8image\xff\xd9"
    client = TestClient(create_app(PreviewController(frame), ["127.0.0.1/32"], None), client=("127.0.0.1", 1))
    # When a preview is requested.
    response = client.get("/api/camera/frame.jpg")
    # Then exactly that frame is served without caching.
    assert response.status_code == 200
    assert response.content == frame
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("name", ["phone.html", "diag.html", "unknown.js"])
def test_asset_is_rejected_when_name_is_not_whitelisted(name: str) -> None:
    # Given a local browser.
    client = TestClient(create_app(PreviewController(), ["127.0.0.1/32"], None), client=("127.0.0.1", 1))
    # When it requests a file outside public asset names.
    response = client.get(f"/assets/{name}")
    # Then static file access is denied.
    assert response.status_code == 404


def test_dashboard_markup_is_self_contained_and_has_no_inline_script() -> None:
    # Given the dashboard entry document.
    path = Path(__file__).parents[1] / "pi/cane/web/static/diag.html"
    # When it is read for a restrictive same-origin script policy.
    html = path.read_text(encoding="utf-8")
    # Then scripts and styles are local resources and user-facing controls exist.
    assert '<script type="module" src="/assets/dashboard.js">' in html
    assert '<script>' not in html
    assert 'data-action="stop_navigation"' in html
    assert 'id="replay"' in html
