"""USB provisioning of the configured navigation device while it is idle."""

from __future__ import annotations

import os
from time import monotonic
from typing import ClassVar, Final, Literal
from urllib.parse import urlencode
from uuid import uuid4

import serial
from project import ROOT, Model, config, now, redact, save
from pydantic import ConfigDict, Field, ValidationError

MAX_WIRE_BYTES: Final = 512


class ProvisionFrame(Model):
    """Private configuration sent directly to the device NVS boundary."""

    type: Literal["configure"] = "configure"
    command_id: str
    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(max_length=64, repr=False)
    mcp_url: str = Field(repr=False)
    walking_tool: str
    protocol: str
    coord_policy: str
    route_frame: str
    coord_ref: str
    integration: Literal["true", "false"]
    config_version: str


class ProvisionAck(Model):
    """Correlated device acknowledgment of one configuration command."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")
    type: Literal["ack"]
    command_id: str
    accepted: bool
    reason: str


def provision() -> int:
    """Send local configuration over USB and retain the correlated device ACK."""
    local = config()
    key = os.environ.get(local.mcp.key_env, "")
    if not local.esp32.port or not local.wifi.ssid or not key:
        save(
            ROOT / "evidence/T01/provision_readiness.json",
            {
                "status": "waiting_condition",
                "next_action": "填写本地 Wi-Fi、服务凭据和 ESP32 串口配置",
            },
        )
        return 2
    endpoint = (
        local.mcp.endpoint + ("&" if "?" in local.mcp.endpoint else "?") + urlencode({"key": key})
    )
    frame = ProvisionFrame(
        command_id=uuid4().hex[:12],
        ssid=local.wifi.ssid,
        password=os.environ.get(local.wifi.password_env, ""),
        mcp_url=endpoint,
        walking_tool=local.mcp.walking_tool,
        protocol=local.mcp.protocol_version,
        coord_policy=local.mcp.coordinate_policy,
        route_frame=local.mcp.coordinate_frame,
        coord_ref=local.mcp.coordinate_control_reference,
        integration="true" if local.integration_mode else "false",
        config_version=local.config_version,
    )
    payload = frame.model_dump_json().encode() + b"\n"
    if len(payload) > MAX_WIRE_BYTES + 1:
        print("配置长度需要符合 512 字节设备帧上限")
        return 2
    print("正在通过 USB 写入配置，等待设备 ACK")
    try:
        with serial.Serial(local.esp32.port, 115200, timeout=0.5) as connection:
            _ = connection.write(payload)
            deadline = monotonic() + 15
            while monotonic() < deadline:
                line = connection.read_until(b"\n", size=1025)
                try:
                    ack = ProvisionAck.model_validate_json(line)
                except ValidationError:
                    continue
                if ack.command_id == frame.command_id:
                    save(
                        ROOT / "evidence/T01/provision.json",
                        {
                            "observed_at": now(),
                            "port": local.esp32.port,
                            "ack": ack.model_dump(mode="json"),
                            "config_version": local.config_version,
                        },
                    )
                    return 0 if ack.accepted else 2
    except serial.SerialException as error:
        save(
            ROOT / "evidence/T01/provision_error.json",
            {"reason": redact(str(error)), "observed_at": now()},
        )
    return 2
