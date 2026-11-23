"""Native Wi-Fi input persistence and local secret decoding contracts."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest
from configure_wifi import persist_wifi
from project import LocalConfig
from pydantic import ValidationError
from secrets_config import load

if TYPE_CHECKING:
    from pathlib import Path


def test_loader_round_trips_json_quoted_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an encoded password with literal outer quotes, spaces and backslashes.
    password = '  "wifi \\ secret"  '  # noqa: S105 - Test fixture credential.
    path = tmp_path / "secrets.env"
    _ = path.write_text("NAV_WIFI_PASSWORD=" + json.dumps(password) + "\n")
    monkeypatch.delenv("NAV_WIFI_PASSWORD", raising=False)
    # When the task loads its environment.
    load(path)
    # Then the exact entered password is available to the firmware provisioner.
    assert os.environ["NAV_WIFI_PASSWORD"] == password


def test_wifi_save_preserves_key_and_owner_only_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the task's provider credential and local configuration.
    folder = tmp_path / "config"
    folder.mkdir()
    _ = (folder / "secrets.env").write_text("AMAP_MAPS_API_KEY=example-key\n")
    _ = (folder / "local.json").write_text(LocalConfig().model_dump_json())
    password = ' "quoted wifi" \\ '  # noqa: S105 - Test fixture credential.
    # When native input is persisted.
    persist_wifi("测试网络", password, tmp_path)
    # Then SSID, credential reference and exact password remain associated.
    local = LocalConfig.model_validate_json((folder / "local.json").read_text())
    assert local.wifi.ssid == "测试网络"
    assert "AMAP_MAPS_API_KEY=example-key" in (folder / "secrets.env").read_text()
    monkeypatch.delenv("NAV_WIFI_PASSWORD", raising=False)
    load(folder / "secrets.env")
    assert os.environ["NAV_WIFI_PASSWORD"] == password
    assert (folder / "secrets.env").stat().st_mode & 0o777 == 0o600
    assert (folder / "local.json").stat().st_mode & 0o777 == 0o600


def test_empty_wifi_password_is_persisted_for_open_access_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an open test network.
    # When its empty password is persisted.
    persist_wifi("OpenTest", "", tmp_path)
    monkeypatch.delenv("NAV_WIFI_PASSWORD", raising=False)
    load(tmp_path / "config/secrets.env")
    # Then the credential value remains the empty string.
    assert os.environ["NAV_WIFI_PASSWORD"] == ""


@pytest.mark.parametrize(
    ("ssid", "password"),
    [
        ("test\nnetwork", "password"),
        ("test", "password\rvalue"),
        ("测" * 11, "password"),
        ("test", "p" * 65),
    ],
)
def test_wifi_input_boundary_requires_single_line_and_byte_limits(
    tmp_path: Path, ssid: str, password: str
) -> None:
    # Given a malformed native input value.
    # When the local persistence boundary parses it.
    with pytest.raises(ValidationError):
        # Then the boundary returns a typed input error.
        persist_wifi(ssid, password, tmp_path)
