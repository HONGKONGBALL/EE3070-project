"""Project paths, configuration boundaries and persisted execution evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Final, Literal, assert_never
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

ROOT: Final = Path(__file__).resolve().parents[1]
H0: Final = "2026-11-17T17:59:57+08:00"
TZ: Final = ZoneInfo("Asia/Hong_Kong")
SENSITIVE_VALUE_KEYS: Final = frozenset(
    {
        "password",
        "key",
        "token",
        "authorization",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "secret",
        "mcp_key",
    }
)


class Model(BaseModel):
    """Immutable parsed boundary value."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class RunRecord(Model):
    """One actual subprocess invocation."""

    command: tuple[str, ...]
    started_at: str
    duration_ms: float
    exit_code: int
    stdout: str
    stderr: str
    log: str


class IOSConfig(Model):
    """iPhone signing and installation identity."""

    udid: str = ""
    team_id: str = ""
    bundle_id: str = "hk.ee3070.NavigationCompanion"


class ESPConfig(Model):
    """ESP32 board identity and local SDK paths."""

    port: str = ""
    board: str = "Seeed XIAO ESP32S3"
    psram_mb: int = 8
    idf_path: str = ".toolchain/esp-idf"
    tools_path: str = ".toolchain/espressif"


class PicoConfig(Model):
    """Pico serial identity and confirmation GPIO."""

    port: str = ""
    button_pin: int = Field(default=14, ge=0, le=28)


class WifiConfig(Model):
    """Wireless network and password environment reference."""

    ssid: str = ""
    password_env: str = "NAV_WIFI_PASSWORD"  # noqa: S105 - Environment variable name.


class MCPConfig(Model):
    """Provider capabilities and verified coordinate semantics."""

    alias: str = "amap-walking"
    endpoint: str = "https://mcp.amap.com/mcp"
    key_env: str = "AMAP_MAPS_API_KEY"
    protocol_version: Literal["2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"] = "2025-11-25"
    walking_tool: str = ""
    coordinate_frame: Literal["WGS84", "GCJ02"] = "GCJ02"
    coordinate_verified: bool = False
    coordinate_policy: Literal["review_required", "verified_identity", "verified_gcj02"] = (
        "review_required"
    )
    coordinate_control_reference: str = ""


class OperatorConfig(Model):
    """Device operator and available test windows."""

    name: str = ""
    availability: str = ""


class LocalConfig(Model):
    """Versioned local deployment configuration."""

    config_version: str = "navigation-1"
    integration_mode: bool = False
    ios: IOSConfig = IOSConfig()
    esp32: ESPConfig = ESPConfig()
    pico: PicoConfig = PicoConfig()
    wifi: WifiConfig = WifiConfig()
    mcp: MCPConfig = MCPConfig()
    operator: OperatorConfig = OperatorConfig()


def now() -> str:
    """Return the current Hong Kong execution time."""
    return datetime.now(TZ).isoformat(timespec="seconds")


def config() -> LocalConfig:
    """Parse the local configuration or construct its initial template values."""
    local = ROOT / "config/local.json"
    return LocalConfig.model_validate_json(local.read_text()) if local.is_file() else LocalConfig()


def redact(text: str) -> str:
    """Redact credential values and known URL/header credential forms."""
    for variable in ("AMAP_MAPS_API_KEY", "NAV_WIFI_PASSWORD"):
        value = os.environ.get(variable, "")
        if value:
            text = text.replace(value, "[redacted]")
    cleaned = re.sub(
        r'(?i)(authorization[\s"\']*[:=][\s"\']*)(?:Bearer|Basic)\s+[^\s"\',}]+',
        r"\1[redacted]",
        text,
    )
    cleaned = re.sub(
        r'(?i)((?:key|token|password|authorization)["\']?\s*:\s*)(["\'])(.*?)\2',
        r"\1\2[redacted]\2",
        cleaned,
    )
    return re.sub(
        r"(?i)((?:key|token|password|authorization)[\s\"']*[:=][\s\"']*)([^&\s\"',}]+)",
        r"\1[redacted]",
        cleaned,
    )


def redact_json(value: JsonValue) -> JsonValue:
    """Build a redacted JSON value tree while preserving its scalar and container types."""
    match value:
        case str():
            return redact(value)
        case list():
            return [redact_json(item) for item in value]
        case dict():
            cleaned: dict[str, JsonValue] = {}
            for key, item in value.items():
                match item:
                    case str() if key.casefold() in SENSITIVE_VALUE_KEYS:
                        cleaned[key] = "[redacted]"
                    case str() | bool() | int() | float() | list() | dict() | None:
                        cleaned[key] = redact_json(item)
                    case unreachable:
                        assert_never(unreachable)
            return cleaned
        case bool() | int() | float() | None:
            return value
        case unreachable:
            assert_never(unreachable)


def save(path: Path, value: BaseModel | JsonValue) -> None:
    """Redact structured string values and atomically serialize valid JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    match value:
        case BaseModel():
            parsed = TypeAdapter(JsonValue).validate_json(value.model_dump_json())
        case str() | bool() | int() | float() | list() | dict() | None:
            parsed = value
        case unreachable:
            assert_never(unreachable)
    body = json.dumps(redact_json(parsed), ensure_ascii=False, indent=2)
    temporary = path.with_suffix(path.suffix + ".tmp")
    _ = temporary.write_text(body + "\n")
    _ = temporary.replace(path)


def digest(path: Path) -> str:
    """Compute the SHA-256 identity of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def executable(name: str) -> str:
    """Resolve a bundled build tool before consulting the process PATH."""
    bundled = ROOT / ".venv/bin" / name
    return str(bundled) if bundled.is_file() else shutil.which(name) or name


def run(
    command: list[str],
    group: str,
    *,
    timeout: int = 1800,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> RunRecord:
    """Run one command and retain output, exit code and monotonic elapsed time."""
    stamp = now()
    begin = time.monotonic_ns()
    try:
        result = subprocess.run(  # noqa: S603 - Argument vector is recorded by the caller.
            command, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False
        )
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except FileNotFoundError as error:
        code, stdout, stderr = 127, "", str(error)
    except subprocess.TimeoutExpired as error:
        code = 124
        stdout = _timeout_text(error.stdout)
        stderr = _timeout_text(error.stderr) + f"\nCommand exceeded {error.timeout} seconds"
    duration = (time.monotonic_ns() - begin) / 1_000_000
    log = ROOT / "evidence" / group / f"{uuid4().hex[:12]}.json"
    record = RunRecord(
        command=tuple(command),
        started_at=stamp,
        duration_ms=duration,
        exit_code=code,
        stdout=redact(stdout),
        stderr=redact(stderr),
        log=str(log.relative_to(ROOT)),
    )
    save(log, record)
    print(f"{group}: exit={code} {record.log}")
    return record


def _timeout_text(output: str | bytes | None) -> str:
    match output:
        case str():
            return output
        case bytes():
            return output.decode("utf-8", errors="replace")
        case None:
            return ""
        case unreachable:
            assert_never(unreachable)
