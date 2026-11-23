"""Actual host, ESP-IDF and Xcode toolchain invocations."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Literal, assert_never

from progress_store import update
from project import ROOT, RunRecord, config, digest, executable, run, save


def idf_environment() -> tuple[str, dict[str, str]]:
    """Resolve the installed IDF Python environment and cross-compiler bins."""
    local = config()
    tools = ROOT / local.esp32.tools_path
    env = dict(os.environ)
    env["IDF_PATH"] = str(ROOT / local.esp32.idf_path)
    env["IDF_TOOLS_PATH"] = str(tools)
    rom_versions = sorted((tools / "tools/esp-rom-elfs").glob("*"))
    if rom_versions:
        env["ESP_ROM_ELF_DIR"] = str(rom_versions[-1])
    python_envs = sorted((tools / "python_env").glob("idf*_env"))
    python = str(python_envs[-1] / "bin/python") if python_envs else sys.executable
    bins = [str(Path(python).parent), str(ROOT / ".venv/bin")]
    bins.extend(
        str(path.parent)
        for path in (tools / "tools").glob("**/bin/*")
        if path.name
        in {"xtensa-esp32s3-elf-gcc", "xtensa-esp-elf-gcc", "riscv32-esp-elf-gcc", "openocd"}
    )
    env["PATH"] = os.pathsep.join([*dict.fromkeys(bins), env.get("PATH", "")])
    if python_envs:
        env["IDF_PYTHON_ENV_PATH"] = str(python_envs[-1])
    return python, env


def host() -> tuple[RunRecord, ...]:
    """Configure, compile and run the independent host assertions."""
    records: list[RunRecord] = []
    for command in (
        [
            executable("cmake"),
            "-S",
            str(ROOT),
            "-B",
            str(ROOT / "build/host"),
            "-G",
            "Ninja",
            f"-DCMAKE_MAKE_PROGRAM={executable('ninja')}",
        ],
        [executable("cmake"), "--build", str(ROOT / "build/host")],
        [
            executable("ctest"),
            "--test-dir",
            str(ROOT / "build/host"),
            "--output-on-failure",
            "--output-junit",
            str(ROOT / "evidence/host/junit.xml"),
        ],
    ):
        host_env = dict(os.environ)
        record = run(command, "host", env=host_env)
        records.append(record)
        if record.exit_code:
            break
    return tuple(records)


def build(target: Literal["host", "esp32", "ios-device"]) -> int:
    """Build the requested deliverable and record its real artifact digests."""
    local = config()
    match target:
        case "host":
            records = host()
            return max(record.exit_code for record in records)
        case "esp32":
            python, env = idf_environment()
            source = hashlib.sha256()
            for path in sorted((ROOT / "firmware").rglob("*")):
                if (
                    path.is_file()
                    and "build" not in path.relative_to(ROOT / "firmware").parts
                    and path.name != "sdkconfig"
                ):
                    source.update(str(path.relative_to(ROOT)).encode())
                    source.update(path.read_bytes())
            version = "nav1-" + source.hexdigest()[:12]
            records = (
                run(
                    [
                        python,
                        str(ROOT / local.esp32.idf_path / "tools/idf.py"),
                        "-C",
                        str(ROOT / "firmware"),
                        "-DIDF_TARGET=esp32s3",
                        f"-DPROJECT_VER={version}",
                        "build",
                    ],
                    "T04",
                    env=env,
                ),
            )
            artifacts = tuple(path for path in (ROOT / "firmware/build").glob("*.bin"))
            task = "T04"
        case "ios-device":
            command = [
                "xcodebuild",
                "-project",
                str(ROOT / "ios/NavigationCompanion.xcodeproj"),
                "-scheme",
                "NavigationCompanion",
                "-configuration",
                "Debug",
                "-sdk",
                "iphoneos",
                "-destination",
                f"id={local.ios.udid}" if local.ios.udid else "generic/platform=iOS",
                "-derivedDataPath",
                str(ROOT / "ios/DerivedData"),
                "build",
            ]
            if local.ios.team_id:
                command.append(f"DEVELOPMENT_TEAM={local.ios.team_id}")
            if local.ios.team_id and local.ios.udid:
                command.extend(
                    ["-allowProvisioningUpdates", "-allowProvisioningDeviceRegistration"]
                )
            records = (run(command, "T05"),)
            artifacts = tuple(
                (ROOT / "ios/DerivedData/Build/Products/Debug-iphoneos").glob("*.app/*")
            )
            task = "T05"
        case unreachable:
            assert_never(unreachable)
    code = records[-1].exit_code
    if code:
        artifacts = ()
    save(
        ROOT / "evidence" / task / "build.json",
        {
            "target": target,
            "exit_code": code,
            "command_record": records[-1].log,
            "artifacts": [
                {"path": str(path.relative_to(ROOT)), "sha256": digest(path)}
                for path in artifacts
                if path.is_file()
            ],
        },
    )
    update(
        task,
        "running" if code == 0 else "waiting_condition",
        75 if code == 0 else 40,
        records,
        tuple(str(path.relative_to(ROOT)) for path in artifacts),
        "执行设备安装及链路验收" if code == 0 else "按构建日志准备工具链后定向复测",
    )
    return code


def install_ios() -> int:
    """Install the signed App on the configured iPhone."""
    local = config()
    if not local.ios.udid:
        print("请在 config/local.json 登记 iPhone UDID")
        return 2
    app = ROOT / "ios/DerivedData/Build/Products/Debug-iphoneos/NavigationCompanion.app"
    return run(
        ["xcrun", "devicectl", "device", "install", "app", "--device", local.ios.udid, str(app)],
        "T05",
    ).exit_code


def flash_esp32() -> int:
    """Flash the configured ESP32 through its registered serial interface."""
    local = config()
    if not local.esp32.port:
        print("请在 config/local.json 登记 ESP32 串口")
        return 2
    python, env = idf_environment()
    return run(
        [
            python,
            str(ROOT / local.esp32.idf_path / "tools/idf.py"),
            "-C",
            str(ROOT / "firmware"),
            "-p",
            local.esp32.port,
            "flash",
        ],
        "T07",
        env=env,
    ).exit_code
