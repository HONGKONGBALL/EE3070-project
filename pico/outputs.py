# Vibration motor, COB light and buzzer driven by non-blocking patterns.

# pattern: list of (on_ms, off_ms) pulses; duty 0..1 for PWM outputs
VIBRATION = {
    "stop": ([(600, 0)], 1.0),
    "near": ([(150, 100)] * 2, 0.9),
    "head": ([(80, 80)] * 3, 1.0),
    "left": ([(200, 0)], 0.8),               # one pulse = left
    "right": ([(120, 100)] * 2, 0.8),        # two pulses = right
    "rough": ([(60, 60)] * 4, 0.6),
    "cross": ([(300, 200)] * 3, 1.0),
    "fault": ([(100, 100)] * 5, 1.0),
    "ok": ([(80, 0)], 0.7),
}

BUZZER = {
    "beep": [(100, 0)],
    "alarm": [(200, 200)] * 3,   # repeated by main.py while the heartbeat is missing
    "power": [(1500, 0)],        # power-loss: one long tone while the supercap lasts
}


class PatternPlayer:
    """Pure timing logic. level(now) -> 0..1 output level."""

    def __init__(self):
        self.pulses = []
        self.duty = 0.0
        self.start = 0
        self.total = 0

    def play(self, pulses, duty, now):
        self.pulses = list(pulses)
        self.duty = duty
        self.start = now
        self.total = sum(a + b for a, b in pulses)

    def active(self, now):
        return bool(self.pulses) and now - self.start < self.total

    def level(self, now):
        if not self.pulses:
            return 0.0
        t = now - self.start
        for on, off in self.pulses:
            if t < on:
                return self.duty
            t -= on
            if t < off:
                return 0.0
            t -= off
        self.pulses = []
        return 0.0


class PwmOut:
    def __init__(self, pin, freq=1000):
        from machine import PWM, Pin

        self.pwm = PWM(Pin(pin))
        self.pwm.freq(freq)
        self.pwm.duty_u16(0)
        self.last = -1

    def set(self, level):
        v = int(max(0.0, min(1.0, level)) * 65535)
        if v != self.last:
            self.pwm.duty_u16(v)
            self.last = v


class DigitalOut:
    def __init__(self, pin, active_high=True):
        from machine import Pin

        self.pin = Pin(pin, Pin.OUT)
        self.active_high = active_high
        self.set(0)

    def set(self, level):
        on = level > 0
        self.pin.value(1 if on == self.active_high else 0)
