# Debounced push buttons with short / long press detection.


class ButtonLogic:
    """Pure logic: call update(now_ms, pressed) often; returns 'short', 'long' or None.

    'long' fires once while still held (so the user gets feedback without
    releasing); releasing after a long press does not also send 'short'.
    """

    def __init__(self, long_ms=800, debounce_ms=30):
        self.long_ms = long_ms
        self.debounce_ms = debounce_ms
        self.state = False
        self.raw = False
        self.raw_since = 0
        self.down_at = 0
        self.long_sent = False

    def update(self, now, pressed):
        if pressed != self.raw:
            self.raw = pressed
            self.raw_since = now
        if self.raw != self.state and now - self.raw_since >= self.debounce_ms:
            self.state = self.raw
            if self.state:
                self.down_at = now
                self.long_sent = False
            else:
                if not self.long_sent:
                    return "short"
        if self.state and not self.long_sent and now - self.down_at >= self.long_ms:
            self.long_sent = True
            return "long"
        return None


class Buttons:
    def __init__(self, pins, long_ms=800, debounce_ms=30):
        from machine import Pin

        self.items = [(name, Pin(p, Pin.IN, Pin.PULL_UP), ButtonLogic(long_ms, debounce_ms))
                      for name, p in pins.items()]

    def poll(self, now):
        events = []
        for name, pin, logic in self.items:
            ev = logic.update(now, pin.value() == 0)  # active low
            if ev:
                events.append((name, ev))
        return events
