"""Isolated iPhone destination and automatic provisioning command boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import builds
import pytest
from project import IOSConfig, LocalConfig, RunRecord

if TYPE_CHECKING:
    from pathlib import Path

    from progress_store import Status
    from pydantic import BaseModel, JsonValue


@dataclass(frozen=True, slots=True)
class IOSBuildCase:
    """Configured iPhone identity and expected Xcode command intent."""

    ios: IOSConfig
    destination: str
    provisioning: bool


@pytest.mark.parametrize(
    "case",
    [
        IOSBuildCase(
            IOSConfig(team_id="TESTTEAM", udid="test-device"),
            "id=test-device",
            provisioning=True,
        ),
        IOSBuildCase(IOSConfig(team_id="TESTTEAM"), "generic/platform=iOS", provisioning=False),
        IOSBuildCase(IOSConfig(udid="test-device"), "id=test-device", provisioning=False),
        IOSBuildCase(IOSConfig(), "generic/platform=iOS", provisioning=False),
    ],
)
def test_ios_build_targets_configured_device_and_prepares_its_profile(
    case: IOSBuildCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an isolated workspace and parsed synthetic signing/device identity.
    commands: list[tuple[str, ...]] = []
    updates: list[str] = []
    saved: list[Path] = []

    def run(command: list[str], group: str) -> RunRecord:
        commands.append(tuple(command))
        return RunRecord(
            command=tuple(command),
            started_at="2026-11-18T00:00:00+08:00",
            duration_ms=1,
            exit_code=0,
            stdout="build completed",
            stderr="",
            log=f"evidence/{group}/synthetic.json",
        )

    def update(
        task_id: str,
        _status: Status,
        _completion: int,
        _records: tuple[RunRecord, ...],
        _artifacts: tuple[str, ...],
        _next_action: str,
    ) -> None:
        updates.append(task_id)

    def save(path: Path, _value: BaseModel | JsonValue) -> None:
        saved.append(path)

    monkeypatch.setattr(builds, "ROOT", tmp_path)
    monkeypatch.setattr(builds, "config", lambda: LocalConfig(ios=case.ios))
    monkeypatch.setattr(builds, "run", run)
    monkeypatch.setattr(builds, "update", update)
    monkeypatch.setattr(builds, "save", save)
    # When: the public build boundary prepares its Xcode invocation.
    result = builds.build("ios-device")
    # Then: destination and profile preparation follow the configured identity.
    assert result == 0
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index("-destination") + 1] == case.destination
    assert command.count("-allowProvisioningUpdates") == int(case.provisioning)
    assert command.count("-allowProvisioningDeviceRegistration") == int(case.provisioning)
    assert (f"DEVELOPMENT_TEAM={case.ios.team_id}" in command) is bool(case.ios.team_id)
    assert updates == ["T05"]
    assert saved == [tmp_path / "evidence/T05/build.json"]
