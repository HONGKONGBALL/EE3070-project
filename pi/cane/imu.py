"""MPU6050 on the Pi's I2C-1 (GPIO2/3), address 0x68.

Used for: walking/standing detection, cane pitch (to correct the TFmini-S
ground geometry), and the vibration features for ground roughness.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass

G = 9.80665
_PWR_MGMT_1 = 0x6B
_SMPLRT_DIV = 0x19
_CONFIG = 0x1A
_GYRO_CONFIG = 0x1B
_ACCEL_CONFIG = 0x1C
_ACCEL_XOUT_H = 0x3B
_WHO_AM_I = 0x75


@dataclass(frozen=True)
class ImuSample:
    t: float
    ax: float  # m/s^2
    ay: float
    az: float
    gx: float  # deg/s
    gy: float
    gz: float

    @property
    def accel_norm(self) -> float:
        return math.sqrt(self.ax * self.ax + self.ay * self.ay + self.az * self.az)


def _s16(hi: int, lo: int) -> int:
    v = (hi << 8) | lo
    return v - 65536 if v & 0x8000 else v


def decode_block(raw: bytes | list[int], t: float = 0.0) -> ImuSample:
    """Decode the 14-byte block starting at ACCEL_XOUT_H (±4 g, ±500 dps)."""
    a_lsb = 8192.0
    g_lsb = 65.5
    ax, ay, az = (_s16(raw[i], raw[i + 1]) / a_lsb * G for i in (0, 2, 4))
    gx, gy, gz = (_s16(raw[i], raw[i + 1]) / g_lsb for i in (8, 10, 12))
    return ImuSample(t, ax, ay, az, gx, gy, gz)


class Mpu6050:
    def __init__(self, bus: int = 1, address: int = 0x68, rate_hz: int = 200) -> None:
        from smbus2 import SMBus  # only on the Pi

        self.addr = address
        self.bus = SMBus(bus)
        who = self.bus.read_byte_data(address, _WHO_AM_I)
        if who not in (0x68, 0x70, 0x72):  # clones report different IDs
            raise RuntimeError(f"unexpected WHO_AM_I 0x{who:02x}")
        self.bus.write_byte_data(address, _PWR_MGMT_1, 0x01)  # wake, PLL on gyro X
        self.bus.write_byte_data(address, _CONFIG, 0x02)      # DLPF ~94 Hz
        self.bus.write_byte_data(address, _SMPLRT_DIV, max(0, 1000 // rate_hz - 1))
        self.bus.write_byte_data(address, _GYRO_CONFIG, 0x08)   # ±500 dps
        self.bus.write_byte_data(address, _ACCEL_CONFIG, 0x08)  # ±4 g

    def read(self) -> ImuSample:
        raw = self.bus.read_i2c_block_data(self.addr, _ACCEL_XOUT_H, 14)
        return decode_block(raw, time.monotonic())

    def close(self) -> None:
        self.bus.close()


def pitch_deg(s: ImuSample) -> float:
    """Pitch of the sensor's X axis below horizontal, from gravity only.

    Mount the board with +X pointing along the cane towards the tip.
    """
    return math.degrees(math.atan2(-s.ax, math.sqrt(s.ay * s.ay + s.az * s.az)))


class MotionDetector:
    """Walking vs standing from the spread of |a| and gyro over a short window."""

    def __init__(self, window: int = 100, accel_std_walk: float = 0.6, gyro_walk_dps: float = 25.0,
                 hysteresis: float = 0.6) -> None:
        self.buf: deque[ImuSample] = deque(maxlen=window)
        self.accel_std_walk = accel_std_walk
        self.gyro_walk = gyro_walk_dps
        self.hyst = hysteresis
        self.moving = False

    def update(self, s: ImuSample) -> bool:
        self.buf.append(s)
        if len(self.buf) < self.buf.maxlen // 2:
            return self.moving
        norms = [x.accel_norm for x in self.buf]
        mean = sum(norms) / len(norms)
        std = math.sqrt(sum((n - mean) ** 2 for n in norms) / len(norms))
        gyro = max(math.sqrt(x.gx ** 2 + x.gy ** 2 + x.gz ** 2) for x in self.buf)
        if self.moving:
            if std < self.accel_std_walk * self.hyst and gyro < self.gyro_walk * self.hyst:
                self.moving = False
        elif std > self.accel_std_walk or gyro > self.gyro_walk:
            self.moving = True
        return self.moving


class ImuRunner:
    """Samples the MPU6050 in a thread; exposes .moving, .pitch_deg, .window().

    `on_window` is called every `window_s` (used for roughness classification).
    """

    def __init__(self, sensor, rate_hz: int = 200, window_s: float = 1.0, on_window=None) -> None:
        import threading

        self.sensor = sensor
        self.rate_hz = rate_hz
        self.n = max(16, int(rate_hz * window_s))
        self.on_window = on_window
        self.motion = MotionDetector(window=max(20, rate_hz // 2))
        self._norms: deque[float] = deque(maxlen=self.n)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threading = threading
        self.moving = False
        self.pitch_deg: float | None = None
        self.errors = 0

    def window(self) -> list[float]:
        with self._lock:
            return list(self._norms)

    def start(self) -> None:
        self._threading.Thread(target=self._run, daemon=True, name="imu").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        period = 1.0 / self.rate_hz
        count = 0
        while not self._stop.is_set():
            try:
                s = self.sensor.read()
            except OSError:
                self.errors += 1
                time.sleep(0.05)
                continue
            self.moving = self.motion.update(s)
            p = pitch_deg(s)
            self.pitch_deg = p if self.pitch_deg is None else 0.95 * self.pitch_deg + 0.05 * p
            with self._lock:
                self._norms.append(s.accel_norm)
            count += 1
            if self.on_window is not None and count >= self.n:
                count = 0
                self.on_window()
            time.sleep(period)
