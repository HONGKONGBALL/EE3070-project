"""Read-only toolchain and device inventory."""

from __future__ import annotations

import platform
import sys

from builds import idf_environment
from progress_store import update
from project import ROOT, Model, config, executable, now, run, save
from serial.tools import list_ports


class PortIdentity(Model):
    """Parsed serial-device metadata reported by pyserial."""

    device: str
    description: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    manufacturer: str | None
    product: str | None


class EnvironmentReport(Model):
    """Tool versions, attached devices and execution prerequisites."""

    observed_at: str
    platform: str
    python: str
    ports: tuple[PortIdentity, ...]
    checks: dict[str, bool]
    configuration_source: str
    command_records: tuple[str, ...]


def inspect() -> int:
    """Persist versions, attached serial identities and configuration readiness."""
    local = config()
    commands = [
        ["xcode-select", "-p"],
        ["xcodebuild", "-version"],
        [executable("cmake"), "--version"],
        ["clang++", "--version"],
        ["swift", "--version"],
        [sys.executable, "-m", "serial.tools.list_ports", "-v"],
        ["xcrun", "devicectl", "list", "devices"],
    ]
    idf = ROOT / local.esp32.idf_path / "tools/idf.py"
    if idf.is_file():
        idf_python, _ = idf_environment()
        commands.append([idf_python, str(idf), "--version"])
    records = tuple(run(command, "T01", timeout=45) for command in commands)
    ports = tuple(
        PortIdentity.model_validate(
            {
                "device": port.device,
                "description": port.description,
                "vid": port.vid,
                "pid": port.pid,
                "serial_number": port.serial_number,
                "manufacturer": port.manufacturer,
                "product": port.product,
            }
        )
        for port in list_ports.comports()
    )
    checks = {
        "xcode": records[1].exit_code == 0,
        "cmake": records[2].exit_code == 0,
        "idf_sdk": idf.is_file() and records[-1].exit_code == 0,
        "ios_identity": bool(local.ios.udid and local.ios.team_id),
        "esp32_identity": any(port.device == local.esp32.port for port in ports),
        "pico_identity": bool(local.pico.port),
        "service_configuration": bool(
            local.mcp.walking_tool and (ROOT / "fixtures/mcp/provider.json").is_file()
        ),
        "operator": bool(local.operator.name),
    }
    save(
        ROOT / "evidence/T01/environment.json",
        EnvironmentReport(
            observed_at=now(),
            platform=platform.platform(),
            python=sys.version,
            ports=ports,
            checks=checks,
            configuration_source="config/local.json"
            if (ROOT / "config/local.json").is_file()
            else "config/local.example.json",
            command_records=tuple(record.log for record in records),
        ),
    )
    ready = all(checks.values())
    update(
        "T01",
        "awaiting_review" if ready else "waiting_condition",
        int(100 * sum(checks.values()) / len(checks)),
        records,
        ("evidence/T01/environment.json",),
        "登记设备身份、签名、网络及操作者配置，运行 env 复核",
    )
    return 0 if ready else 2
