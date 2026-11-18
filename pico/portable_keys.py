"""Forward the cane's three navigation buttons over the Pico UART."""

from buttons import Buttons


KEY_PINS = {"confirm": 10, "record": 11, "next": 12}
KEY_LINES = {"confirm": b"K1\n", "record": b"K2\n", "next": b"K3\n"}


def write_key(uart, name, kind):
    if kind != "short":
        return
    message = KEY_LINES.get(name)
    if message is not None:
        uart.write(message)


def run():
    from machine import Pin, UART
    from time import sleep_ms, ticks_diff, ticks_ms

    uart = UART(0, baudrate=115200, bits=8, parity=None, stop=1, tx=Pin(0), rx=Pin(1))
    buttons = Buttons(KEY_PINS, long_ms=800, debounce_ms=30)
    previous = ticks_ms()
    elapsed = 0
    while True:
        now = ticks_ms()
        elapsed += ticks_diff(now, previous)
        previous = now
        for name, kind in buttons.poll(elapsed):
            write_key(uart, name, kind)
        sleep_ms(5)


if __name__ == "__main__":
    run()
