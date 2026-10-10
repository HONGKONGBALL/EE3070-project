# HC-SR04 ultrasonic ranger (MicroPython).

SOUND_CM_PER_US = 0.0343


def echo_us_to_cm(us):
    """Round-trip echo time -> distance in cm; -1 for timeout/invalid."""
    if us is None or us <= 0:
        return -1
    cm = us * SOUND_CM_PER_US / 2
    if cm < 2 or cm > 400:
        return -1
    return int(cm)


class HCSR04:
    def __init__(self, trig_pin, echo_pin, timeout_us=25000):
        from machine import Pin

        self.trig = Pin(trig_pin, Pin.OUT, value=0)
        self.echo = Pin(echo_pin, Pin.IN)
        self.timeout_us = timeout_us

    def distance_cm(self):
        import time
        from machine import time_pulse_us

        self.trig.value(0)
        time.sleep_us(2)
        self.trig.value(1)
        time.sleep_us(10)
        self.trig.value(0)
        try:
            us = time_pulse_us(self.echo, 1, self.timeout_us)
        except OSError:
            return -1
        return echo_us_to_cm(us)
