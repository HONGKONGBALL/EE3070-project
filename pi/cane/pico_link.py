"""Link to the Pico H over USB CDC serial (one JSON object per line).

Pico -> Pi
  {"t":"d","f":123,"u":250,"g":180,"gs":900,"ms":12345}  distances in cm, -1 = invalid
  {"t":"b","id":"ok","ev":"short"}                         button
  {"t":"p","vsys":4.95}                                    power
  {"t":"w","msg":"..."}                                    warning text
Pi -> Pico
  {"t":"h"}            heartbeat (Pico alarms on its own after 3 s without one)
  {"t":"v","p":"stop"} vibration pattern
  {"t":"l","v":0.6}    COB light level 0..1
  {"t":"z","p":"beep"} buzzer pattern
"""

from __future__ import annotations

import json
import logging
import queue
import random
import threading
import time
from dataclasses import dataclass
from typing import Union

from .model import ButtonEvent, SensorFrame

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PowerEvent:
    t: float
    vsys: float


@dataclass(frozen=True)
class PicoWarning:
    t: float
    msg: str


PicoMessage = Union[SensorFrame, ButtonEvent, PowerEvent, PicoWarning]


def _cm(v) -> float | None:
    if v is None or v < 0:
        return None
    return v / 100.0


def parse_line(line: str | bytes, now: float | None = None) -> PicoMessage | None:
    now = time.monotonic() if now is None else now
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        m = json.loads(line)
    except ValueError:
        return None
    t = m.get("t")
    try:
        if t == "d":
            return SensorFrame(
                t=now,
                front_m=_cm(m.get("f")),
                up_m=_cm(m.get("u")),
                tfmini_m=_cm(m.get("g")),
                tfmini_strength=int(m.get("gs", 0)),
                pico_ms=int(m.get("ms", 0)),
            )
        if t == "b":
            return ButtonEvent(now, str(m["id"]), str(m.get("ev", "short")))
        if t == "p":
            return PowerEvent(now, float(m["vsys"]))
        if t == "w":
            return PicoWarning(now, str(m.get("msg", "")))
    except (KeyError, TypeError, ValueError):
        return None
    return None


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode()


class PicoLinkBase:
    def __init__(self) -> None:
        self.inbox: "queue.Queue[PicoMessage]" = queue.Queue()
        self.last_rx = 0.0

    def send(self, msg: dict) -> None:
        raise NotImplementedError

    def heartbeat(self) -> None:
        self.send({"t": "h"})

    def vibrate(self, pattern: str) -> None:
        self.send({"t": "v", "p": pattern})

    def light(self, level: float) -> None:
        self.send({"t": "l", "v": round(max(0.0, min(1.0, level)), 2)})

    def buzz(self, pattern: str) -> None:
        self.send({"t": "z", "p": pattern})

    def start(self) -> None: ...
    def close(self) -> None: ...


class SerialPicoLink(PicoLinkBase):
    def __init__(self, port: str, baud: int = 115200) -> None:
        super().__init__()
        self.port = port
        self.baud = baud
        self._ser = None
        self._stop = threading.Event()
        self._wlock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._reader, daemon=True, name="pico-rx")
        self._thread.start()

    def _open(self):
        import serial  # pyserial

        return serial.Serial(self.port, self.baud, timeout=0.2)

    def _reader(self) -> None:
        while not self._stop.is_set():
            try:
                if self._ser is None:
                    self._ser = self._open()
                    log.info("pico connected on %s", self.port)
                line = self._ser.readline()
            except Exception as e:  # serial.SerialException, OSError
                log.warning("pico link error: %s", e)
                self._ser = None
                time.sleep(1.0)
                continue
            if not line:
                continue
            msg = parse_line(line)
            if msg is not None:
                self.last_rx = time.monotonic()
                self.inbox.put(msg)

    def send(self, msg: dict) -> None:
        with self._wlock:
            if self._ser is None:
                return
            try:
                self._ser.write(encode(msg))
            except Exception as e:
                log.warning("pico write failed: %s", e)
                self._ser = None

    def close(self) -> None:
        self._stop.set()
        if self._ser is not None:
            self._ser.close()


class SimPicoLink(PicoLinkBase):
    """Fake Pico for running without hardware: random obstacles at 20 Hz."""

    def __init__(self, rate_hz: float = 20.0, seed: int | None = None) -> None:
        super().__init__()
        self.rate_hz = rate_hz
        self.sent: list[dict] = []
        self._rng = random.Random(seed)
        self._stop = threading.Event()
        self._front = 3.0

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="sim-pico").start()

    def _run(self) -> None:
        start = time.monotonic()
        while not self._stop.is_set():
            # walk towards an obstacle, then it "moves away"
            self._front -= 0.05
            if self._front < 0.4:
                self._front = self._rng.uniform(2.5, 4.0)
            up = self._rng.choice([-1, -1, -1, 110])
            ms = int((time.monotonic() - start) * 1000)
            line = encode({"t": "d", "f": int(self._front * 100), "u": up, "g": 148, "gs": 800, "ms": ms})
            msg = parse_line(line)
            self.last_rx = time.monotonic()
            self.inbox.put(msg)
            time.sleep(1.0 / self.rate_hz)

    def press(self, button: str, kind: str = "short") -> None:
        self.inbox.put(ButtonEvent(time.monotonic(), button, kind))

    def send(self, msg: dict) -> None:
        self.sent.append(msg)

    def close(self) -> None:
        self._stop.set()
