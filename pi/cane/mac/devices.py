"""Explicit selection of the one Pico serial connection."""

from collections.abc import Sequence
from dataclasses import dataclass

from serial.tools import list_ports


@dataclass(frozen=True, slots=True)
class DeviceSelectionError(RuntimeError):
    reason: str

    def __str__(self) -> str:
        return self.reason


def serial_candidates(*, pico_only: bool = False) -> list[str]:
    """Return USB modem/serial ports without opening or interrupting a board."""
    return sorted(
        port.device
        for port in list_ports.comports()
        if ("usbmodem" in port.device.lower() or "usbserial" in port.device.lower())
        and (port.vid == 0x2E8A if pico_only else port.vid != 0x303A)
    )


def choose_serial_port(requested: str, candidates: Sequence[str] | None = None) -> str:
    """Require an unambiguous physical port; an absent board is an error."""
    if requested != "auto":
        return requested
    ports = serial_candidates() if candidates is None else candidates
    if len(ports) == 1:
        return ports[0]
    if not ports:
        raise DeviceSelectionError("未发现 Pico USB 串口。请接数据线，或显式使用 --sim 软件模拟。")
    raise DeviceSelectionError("发现多个串口，请用 --port 明确选择：" + ", ".join(ports))
