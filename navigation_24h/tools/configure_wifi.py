#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2.12,<3"]
# ///
# ─── How to run ───
# python3 navigation_24h/tools/navctl.py configure-wifi
# ──────────────────
"""Collect task Wi-Fi settings through native macOS input dialogs."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import ClassVar, Final

from project import ROOT, LocalConfig, Model
from pydantic import ConfigDict, SecretStr, ValidationError, field_validator
from pydantic_core import PydanticCustomError

SSID_BYTES: Final = 32
PASSWORD_BYTES: Final = 64
PASSWORD_ENV: Final = "NAV_WIFI_PASSWORD"  # noqa: S105 - Environment variable name.
DIALOG_SCRIPT: Final = """tell application "System Events"
activate
set networkEntry to display dialog "测试Wi-Fi（2.4GHz）" ¬
default answer "" buttons {"取消", "继续"} default button "继续" ¬
with title "导航模块 · 网络名称（SSID）"
set passwordEntry to display dialog "输入该测试网络的密码" ¬
default answer "" with hidden answer buttons {"取消", "保存"} default button "保存" ¬
with title "导航模块 · 测试 Wi-Fi"
return (text returned of networkEntry) & linefeed & (text returned of passwordEntry)
end tell"""


class WifiInput(Model):
    """Validated native input with a protected credential representation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", hide_input_in_errors=True
    )
    ssid: str
    password: SecretStr

    @field_validator("ssid")
    @classmethod
    def validate_ssid(cls, value: str) -> str:
        """Require one SSID of up to 32 UTF-8 bytes."""
        if not value or len(value.encode("utf-8")) > SSID_BYTES or "\r" in value or "\n" in value:
            message = "SSID uses 1–32 UTF-8 bytes on one line"
            code = "ssid_format"
            raise PydanticCustomError(code, message)
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr) -> SecretStr:
        """Require a single-line test credential within the device capacity."""
        raw = value.get_secret_value()
        if len(raw.encode("utf-8")) > PASSWORD_BYTES or "\r" in raw or "\n" in raw:
            message = "Wi-Fi password uses up to 64 UTF-8 bytes on one line"
            code = "wifi_password_format"
            raise PydanticCustomError(code, message)
        return value


def _write_private(path: Path, content: str) -> None:
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".wifi-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o600)
            _ = stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            _ = temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def persist_wifi(ssid: str, password: str, root: Path = ROOT) -> None:
    """Save validated Wi-Fi input beside the existing provider credential."""
    parsed = WifiInput(ssid=ssid, password=SecretStr(password))
    directory = root / "config"
    directory.mkdir(parents=True, exist_ok=True)
    local_path = directory / "local.json"
    local = (
        LocalConfig.model_validate_json(local_path.read_text())
        if local_path.is_file()
        else LocalConfig()
    )
    wifi = local.wifi.model_copy(update={"ssid": parsed.ssid, "password_env": PASSWORD_ENV})
    updated = local.model_copy(update={"wifi": wifi})
    secret_path = directory / "secrets.env"
    lines = secret_path.read_text().splitlines() if secret_path.is_file() else []
    retained = [line for line in lines if line.partition("=")[0].strip() != PASSWORD_ENV]
    retained.append(
        PASSWORD_ENV + "=" + json.dumps(parsed.password.get_secret_value(), ensure_ascii=False)
    )
    _write_private(secret_path, "\n".join(retained) + "\n")
    _write_private(local_path, updated.model_dump_json(indent=2) + "\n")


def main() -> int:
    """Collect and save the test network through two native input dialogs."""
    try:
        result = subprocess.run(  # noqa: S603 - Fixed native dialog script.
            ["/usr/bin/osascript", "-e", DIALOG_SCRIPT],
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except (subprocess.TimeoutExpired, OSError):
        print("网络输入窗口已关闭")
        return 2
    if result.returncode != 0:
        print("网络输入窗口已关闭")
        return 2
    ssid, separator, password = result.stdout.removesuffix("\n").partition("\n")
    if not separator:
        print("请填写测试网络名称")
        return 2
    try:
        persist_wifi(ssid, password)
    except ValidationError:
        print("请核对测试网络名称和密码格式")
        return 2
    except OSError:
        print("请检查本地配置目录的访问权限")
        return 2
    print("测试 Wi-Fi 配置已保存")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
