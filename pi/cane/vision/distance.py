"""Explicit none/mock/Pico distance input; never substitute mock for failed hardware."""

from dataclasses import asdict, dataclass
import math
import queue
import time

from cane.model import SensorFrame
from cane.pico_link import SerialPicoLink


@dataclass(frozen=True)
class DistanceObservation:
    sample_id: str
    source: str
    distance_m: float | None
    valid: bool
    received_monotonic: float
    device_ms: int | None = None
    sensor_id: str = "front"
    direction: str = "unknown"
    calibration_id: str = ""
    reason: str = ""

    def fresh(self, now, ttl=0.5):
        return (self.valid and self.distance_m is not None
                and math.isfinite(self.distance_m) and self.distance_m > 0
                and 0 <= now - self.received_monotonic <= ttl)

    def to_dict(self):
        return asdict(self)


class NoDistanceProvider:
    source = "none"
    error = "Distance input disabled"

    def start(self): pass
    def close(self): pass
    def latest(self, now): return None

    def telemetry(self, now):
        return {"source": self.source, "fresh": False, "frame": None}


class MockDistanceProvider(NoDistanceProvider):
    """Timed, deterministic segments; a held reading has distinct 10 Hz sample IDs."""
    source = "mock"
    error = ""

    def __init__(self, segments, clock=time.monotonic):
        self.segments, self.clock = segments, clock
        self.started = None

    def start(self):
        self.started = self.clock()

    def latest(self, now, elapsed=None):
        if self.started is None:
            return None
        elapsed, end = (now-self.started if elapsed is None else elapsed),0.0
        for segment in self.segments:
            end += segment["seconds"]
            if elapsed < end:
                seq = int(elapsed * 10)
                sampled = now-(elapsed-seq/10)-segment.get("age_ms",0)/1000
                cm = segment.get("cm")
                return DistanceObservation(
                    f"mock:{seq}", "mock", cm / 100 if cm is not None else None,
                    segment.get("valid", cm is not None), sampled,
                    direction=segment.get("direction", "unknown"),
                    calibration_id=segment.get("calibration_id", ""), reason="SCRIPTED MOCK")
        return None


class PicoDistanceProvider(NoDistanceProvider):
    source = "pico"

    def __init__(self, port, baud=115200, direction="unknown", calibration_id=""):
        self.link = SerialPicoLink(port, baud)
        self.direction, self.calibration_id = direction, calibration_id
        self.sample = None
        self.telemetry_frame = None
        self.device_ms = None
        self.sequence = 0
        self.last_heartbeat = -100.0
        self.controller_owned = False

    def start(self): self.link.start()
    def close(self): self.link.close()

    def latest(self, now):
        if self.controller_owned:
            self.error = "" if self.sample and self.sample.fresh(now) else "No fresh Pico distance"
            return self.sample
        if now-self.last_heartbeat >= 0.5:
            self.link.heartbeat()
            self.last_heartbeat = now
        while True:
            try:
                frame = self.link.inbox.get_nowait()
            except queue.Empty:
                break
            if isinstance(frame, SensorFrame):
                self.accept(frame)
        self.error = "" if self.sample and self.sample.fresh(now) else "No fresh Pico distance"
        return self.sample

    def accept(self, frame):
        # Device uptime is only for ordering; never subtract it from the Pi clock.
        if self.device_ms is not None and frame.pico_ms <= self.device_ms:
            if frame.pico_ms < self.device_ms:
                self.sample = None
                self.telemetry_frame = None
                self.device_ms = frame.pico_ms
            return False
        self.device_ms = frame.pico_ms
        self.sequence += 1
        value = frame.front_m
        valid = value is not None and math.isfinite(value) and value > 0
        self.sample = DistanceObservation(
            f"pico:{self.sequence}", "pico", value, valid, frame.t,
            frame.pico_ms, direction=self.direction, calibration_id=self.calibration_id,
            reason="" if valid else "Invalid front channel")
        self.telemetry_frame = (self.sequence, frame)
        return True

    def telemetry(self, now):
        latest = self.telemetry_frame
        if latest is None:
            return super().telemetry(now)
        sequence, frame = latest
        age = max(0, now - frame.t)

        def centimeters(value):
            if value is not None and math.isfinite(value) and value > 0:
                return round(value * 100, 1)
            return None

        return {"source": self.source, "fresh": age <= 0.5, "frame": sequence,
                "age_ms": round(age * 1000), "pico_ms": frame.pico_ms,
                "front_cm": centimeters(frame.front_m), "upper_cm": centimeters(frame.up_m),
                "laser_cm": centimeters(frame.tfmini_m), "laser_strength": frame.tfmini_strength}


class ProximityGate:
    def __init__(self, enter=1.0, leave=1.2):
        if leave <= enter:
            raise ValueError("Distance release threshold must exceed entry threshold")
        self.enter, self.leave = enter, leave
        self.near, self.episode = False, 0
        self.last_id = None
        self.count = 0
        self.target = None
        self.entered_at,self.entered_sample_received_at = -100.0,-100.0

    def update(self, sample, now):
        if sample is None or not sample.fresh(now):
            self.count, self.target = 0, None
            return False
        if sample.sample_id == self.last_id:
            return False
        self.last_id = sample.sample_id
        desired = (True if sample.distance_m <= self.enter else
                   False if sample.distance_m >= self.leave else None)
        if desired is None or desired == self.near:
            self.count, self.target = 0, None
            return False
        self.count = self.count + 1 if self.target == desired else 1
        self.target = desired
        if self.count < 2:
            return False
        self.near, self.count = desired, 0
        self.target = None
        if self.near:
            self.episode += 1
            self.entered_at,self.entered_sample_received_at=now,sample.received_monotonic
            return True
        return False
