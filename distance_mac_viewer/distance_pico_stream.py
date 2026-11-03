"""RAM-only MicroPython acquisition for TFmini-S plus two HC-SR04 sensors.

Run through USB REPL without saving main.py. UART1 uses GP8 TX / GP9 RX at
115200 baud; front ultrasonic uses GP2/GP3 and upper uses GP4/GP5. No other
GPIO is configured and no commands are sent to TFmini-S.

TF_DATA,ticks_us,delta_us,raw_distance,strength
US_DATA,sequence,ticks_ms,front_echo_us,upper_echo_us

TF timestamps describe Pico parsing, not sensor measurement or UART arrival.
Ultrasonic echo calls can block for 30-60ms; buffered TF frames may therefore
be parsed together with tiny/zero deltas. Do not infer TF frame rate from them.
Raw TF sentinel values and ultrasonic negative/zero statuses remain unchanged.
Ctrl-C leaves triggers low and releases UART. Only built-in modules are used.
"""

FRONT_TRIGGER_GPIO = 2
FRONT_ECHO_GPIO = 3
UPPER_TRIGGER_GPIO = 4
UPPER_ECHO_GPIO = 5
ECHO_TIMEOUT_US = 30000
SENSOR_SLOT_US = 80000
UART_RX_BUFFER = 2048
UART_READ_LIMIT = 256


class TFParser:
    """Retain incomplete packets and resynchronize after noise or bad checksums."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        """Yield checksum-valid raw distance/strength pairs from received bytes."""
        self.buffer.extend(data)
        while len(self.buffer) >= 9:
            if (self.buffer[0] != 0x59 or self.buffer[1] != 0x59
                    or (sum(self.buffer[:8]) & 0xFF) != self.buffer[8]):
                self.buffer = self.buffer[1:]
                continue
            distance = self.buffer[2] | (self.buffer[3] << 8)
            strength = self.buffer[4] | (self.buffer[5] << 8)
            self.buffer = self.buffer[9:]
            yield distance, strength


class TFStream:
    """Drain bounded UART batches and emit TF frames with parse-time timestamps."""

    def __init__(self, uart, clock):
        self.uart = uart
        self.clock = clock
        self.parser = TFParser()
        self.previous_tick = None

    def poll(self):
        """Read available bytes without waiting for a complete sensor packet."""
        available = self.uart.any()
        if not available:
            return
        data = self.uart.read(min(available, UART_READ_LIMIT))
        if not data:
            return
        for distance, strength in self.parser.feed(data):
            tick = self.clock.ticks_us()
            delta = -1 if self.previous_tick is None else self.clock.ticks_diff(tick, self.previous_tick)
            self.previous_tick = tick
            print("TF_DATA,{},{},{},{}".format(tick, delta, distance, strength))


def _measure(trigger, echo, machine, clock):
    """Measure a new echo while rejecting an already-high stale echo input."""
    trigger.value(0)
    clock.sleep_us(2)
    if echo.value():
        return -1
    trigger.value(1)
    clock.sleep_us(10)
    trigger.value(0)
    return machine.time_pulse_us(echo, 1, ECHO_TIMEOUT_US)


def main():
    """Interleave UART polling with alternating 80ms ultrasonic acquisition slots."""
    import machine
    import time

    front_trigger = machine.Pin(FRONT_TRIGGER_GPIO, machine.Pin.OUT, value=0)
    front_echo = machine.Pin(FRONT_ECHO_GPIO, machine.Pin.IN)
    upper_trigger = machine.Pin(UPPER_TRIGGER_GPIO, machine.Pin.OUT, value=0)
    upper_echo = machine.Pin(UPPER_ECHO_GPIO, machine.Pin.IN)
    uart = None
    try:
        uart = machine.UART(1, 115200, tx=machine.Pin(8), rx=machine.Pin(9),
                            rxbuf=UART_RX_BUFFER, timeout=0)
        tf_stream = TFStream(uart, time)
        previous_start = None
        front_next = True
        front_us = 0
        sequence = 1
        while True:
            tf_stream.poll()
            tick = time.ticks_us()
            if previous_start is None or time.ticks_diff(tick, previous_start) >= SENSOR_SLOT_US:
                previous_start = tick
                if front_next:
                    front_us = _measure(front_trigger, front_echo, machine, time)
                else:
                    upper_us = _measure(upper_trigger, upper_echo, machine, time)
                    print("US_DATA,{},{},{},{}".format(sequence, time.ticks_ms(), front_us, upper_us))
                    sequence += 1
                front_next = not front_next
                tf_stream.poll()
            time.sleep_ms(1)
    except KeyboardInterrupt:
        return
    finally:
        front_trigger.value(0)
        upper_trigger.value(0)
        if uart is not None:
            uart.deinit()


if __name__ == "__main__":
    main()
