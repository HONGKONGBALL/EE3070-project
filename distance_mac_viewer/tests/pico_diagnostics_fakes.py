"""Deterministic clock, GPIO/PWM/Timer and input doubles for diagnostics tests."""

class Clock:
    PERIOD = 1 << 30

    def __init__(self):
        self.now = 0

    def ticks_ms(self):
        return self.now % self.PERIOD

    def ticks_diff(self, newer, older):
        return (newer - older + self.PERIOD // 2) % self.PERIOD - self.PERIOD // 2


class Hardware:
    def __init__(self):
        self.levels = {}
        self.pwms = []
        self.timers = []
        hardware = self

        class Pin:
            OUT, IN, PULL_UP = 1, 0, 2

            def __init__(self, gpio, mode=None, pull=None, value=None):
                self.gpio = gpio
                if value is not None:
                    hardware.levels[gpio] = value
                elif pull == self.PULL_UP:
                    hardware.levels.setdefault(gpio, 1)

            def value(self, value=None):
                if value is not None:
                    hardware.levels[self.gpio] = value
                return hardware.levels[self.gpio]

        self.Pin = Pin

        class Timer:
            PERIODIC = 1

            def __init__(self, *, period, mode, callback, hard):
                self.period, self.mode, self.callback, self.hard = period, mode, callback, hard
                self.closed = False
                hardware.timers.append(self)

            def fire(self):
                self.callback(self)

            def deinit(self):
                self.closed = True

        self.Timer = Timer

    def PWM(self, pin, freq, duty_u16):
        hardware = self

        class PWM:
            def __init__(self):
                self.freq, self.duty, self.closed = freq, duty_u16, False

            def duty_u16(self, duty):
                self.duty = duty

            def deinit(self):
                self.closed = True

        pwm = PWM()
        hardware.pwms.append(pwm)
        return pwm


class Input:
    def __init__(self):
        self.data = ''

    def read(self, size):
        text, self.data = self.data[:size], self.data[size:]
        return text


class Poller:
    def __init__(self, stream):
        self.stream = stream

    def register(self, stream, mask):
        assert stream is self.stream and mask == 1

    def poll(self, timeout):
        assert timeout == 0
        return [(self.stream, 1)] if self.stream.data else []
