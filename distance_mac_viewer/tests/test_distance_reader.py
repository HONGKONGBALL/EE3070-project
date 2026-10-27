#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
# How to run: python3 tests/test_distance_reader.py
"""Test combined acquisition without opening a serial port or touching GPIO."""

from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

READER_PATH = Path(__file__).resolve().parents[1] / "distance_pico_stream.py"


class UnsupportedBufferDeletion(TypeError):
    """MicroPython rejects deleting individual bytearray elements or slices."""


class NoDeleteBytearray(bytearray):
    def __delitem__(self, key: int | slice) -> None:
        raise UnsupportedBufferDeletion


def load_reader() -> ModuleType:
    assert READER_PATH.is_file(), "Combined acquisition is not implemented"
    spec = importlib.util.spec_from_file_location("distance_reader_under_test", READER_PATH)
    assert spec is not None and spec.loader is not None
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader


def tf_frame(distance: int, strength: int) -> bytes:
    payload = bytes([89, 89, distance & 255, distance >> 8,
                     strength & 255, strength >> 8, 0, 8])
    return payload + bytes([sum(payload) & 255])


class Clock(ModuleType):
    """Mutable simulated time advances during sleeps and pulse measurement."""

    PERIOD = 1 << 30

    def __init__(self, start: int = 0, interrupt: bool = False) -> None:
        super().__init__("time")
        self.now = start
        self.interrupt = interrupt

    def ticks_us(self) -> int:
        return self.now % self.PERIOD

    def ticks_ms(self) -> int:
        return (self.now // 1000) % self.PERIOD

    def ticks_diff(self, new: int, old: int) -> int:
        return (new - old + self.PERIOD // 2) % self.PERIOD - self.PERIOD // 2

    def sleep_us(self, duration: int) -> None:
        self.now += duration
        if self.interrupt and duration == 10:
            raise KeyboardInterrupt

    def sleep_ms(self, duration: int) -> None:
        self.now += duration * 1000


class UART:
    """Mutable receive FIFO releases byte chunks at their simulated arrival time."""

    def __init__(self, clock: Clock, arrivals: list[tuple[int, bytes]]) -> None:
        self.clock = clock
        self.arrivals = list(arrivals)
        self.buffer = bytearray()
        self.polls: list[int] = []
        self.closed = False

    def any(self) -> int:
        self.polls.append(self.clock.now)
        while self.arrivals and self.arrivals[0][0] <= self.clock.now:
            self.buffer.extend(self.arrivals.pop(0)[1])
        return len(self.buffer)

    def read(self, size: int) -> bytes:
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def deinit(self) -> None:
        self.closed = True


class Hardware(ModuleType):
    """Mutable GPIO and UART simulation records the externally visible schedule."""

    def __init__(self, clock: Clock, pulses: list[int], arrivals: list[tuple[int, bytes]]) -> None:
        super().__init__("machine")
        self.clock = clock
        self.pulses = iter(pulses)
        self.timeout_duration = 30000
        self.uart = UART(clock, arrivals)
        self.uart_config: tuple[int, int, int, int, int, int] | None = None
        self.edges: list[tuple[int, int, int]] = []
        self.pins: list[Pin] = []
        hardware = self

        class Pin:
            OUT = 1
            IN = 0

            def __init__(self, gpio: int, mode: int = -1, value: int = 0) -> None:
                self.gpio, self.mode, self.level = gpio, mode, value
                hardware.pins.append(self)

            def value(self, level: int | None = None) -> int:
                if level is not None:
                    self.level = level
                    hardware.edges.append((self.gpio, level, clock.now))
                return self.level

        self.Pin = Pin

    def UART(self, number: int, baudrate: int, *, tx, rx, rxbuf: int, timeout: int) -> UART:
        self.uart_config = (number, baudrate, tx.gpio, rx.gpio, rxbuf, timeout)
        return self.uart

    def time_pulse_us(self, echo, level: int, timeout: int) -> int:
        assert echo.gpio in (3, 5) and level == 1 and timeout == 30000
        pulse = next(self.pulses)
        self.clock.now += pulse if pulse > 0 else self.timeout_duration
        return pulse


class Output(io.StringIO):
    def __init__(self, pairs: int) -> None:
        super().__init__()
        self.pairs = pairs

    def write(self, text: str) -> int:
        count = super().write(text)
        if text == "\n" and self.getvalue().count("US_DATA,") >= self.pairs:
            raise KeyboardInterrupt
        return count


def run_main(arrivals: list[tuple[int, bytes]], pulses: list[int], pairs: int = 1,
             start: int = 0, interrupt: bool = False) -> tuple[Hardware, str]:
    clock = Clock(start, interrupt)
    hardware = Hardware(clock, pulses, arrivals)
    output = Output(pairs)
    with patch.dict(sys.modules, {"machine": hardware, "time": clock}), redirect_stdout(output):
        load_reader().main()
    return hardware, output.getvalue()


class DistanceReaderTests(unittest.TestCase):
    def test_import_when_loaded_does_not_open_uart_or_gpio(self) -> None:
        # Given a fresh hardware simulation; when importing; then no hardware is configured.
        hardware = Hardware(Clock(), [], [])
        with patch.dict(sys.modules, {"machine": hardware}):
            load_reader()
        self.assertEqual((hardware.pins, hardware.uart_config), ([], None))

    def test_tf_when_fragmented_preserves_partial_header_and_payload(self) -> None:
        # Given fragments including a split header; when fed; then raw sentinel values survive.
        frame = tf_frame(65535, 0)
        parser = load_reader().TFParser()
        parsed = [pair for chunk in (frame[:1], frame[1:4], frame[4:])
                  for pair in parser.feed(chunk)]
        self.assertEqual(parsed, [(65535, 0)])

    def test_tf_when_checksum_is_bad_resynchronizes_to_later_valid_frame(self) -> None:
        # Given noise and a corrupt candidate; when fed; then only the valid frame is emitted.
        bad = tf_frame(89, 89)
        parser = load_reader().TFParser()
        parsed = list(parser.feed(b"noiseY" + bad[:-1] + bytes([bad[-1] ^ 1]) + tf_frame(123, 456)))
        self.assertEqual(parsed, [(123, 456)])

    def test_tf_when_buffer_disallows_deletion_parses_and_resynchronizes(self) -> None:
        # Given MicroPython's buffer limitation; when feeding; then both paths avoid deletion.
        reader = load_reader()
        for prefix in (b"", b"noise"):
            with self.subTest(prefix=prefix), patch.dict(reader.__dict__, {"bytearray": NoDeleteBytearray}):
                parser = reader.TFParser()
                parsed = list(parser.feed(prefix + tf_frame(123, 456)))
                self.assertEqual(parsed, [(123, 456)])

    def test_us_when_no_tf_exists_keeps_pair_schedule_and_protocol(self) -> None:
        # Given no TF bytes; when two pairs run; then US output and >=65ms spacing continue.
        hardware, output = run_main([], [-1, -2, 580, 1160], pairs=2)
        lines = output.splitlines()
        self.assertEqual([line.split(",")[1:] for line in lines],
                         [["1", "110", "-1", "-2"], ["2", "241", "580", "1160"]])
        starts = [tick for _, level, tick in hardware.edges if level == 1]
        self.assertTrue(all(later - earlier >= 65000 for earlier, later in zip(starts, starts[1:])))
        self.assertEqual(hardware.uart_config, (1, 115200, 8, 9, 2048, 0))

    def test_tf_when_us_times_out_drains_backlog_then_polls_during_idle(self) -> None:
        # Given TF arrivals during blocked US calls and idle; when running; then both streams progress.
        arrivals = [(10000, tf_frame(100, 10)), (20000, tf_frame(101, 11)),
                    (45000, tf_frame(102, 12))]
        hardware, output = run_main(arrivals, [-1, -2])
        tf_lines = [line.split(",") for line in output.splitlines() if line.startswith("TF_DATA,")]
        self.assertEqual([line[3:] for line in tf_lines], [["100", "10"], ["101", "11"], ["102", "12"]])
        self.assertEqual([line[2] for line in tf_lines[:2]], ["-1", "0"])
        self.assertEqual(tf_lines[0][1], tf_lines[1][1])
        self.assertTrue(any(40000 <= tick <= 41000 for tick in hardware.uart.polls))
        self.assertIn("US_DATA,1,110,-1,-2\n", output)

    def test_schedule_when_ticks_wrap_keeps_trigger_spacing(self) -> None:
        # Given a wrapping clock; when a pair runs; then both trigger starts stay 80ms apart.
        hardware, _ = run_main([], [580, 1160], start=Clock.PERIOD - 40000)
        starts = [tick for _, level, tick in hardware.edges if level == 1]
        self.assertTrue(80000 <= starts[1] - starts[0] <= 81000)

    def test_backlog_when_us_blocks_60ms_drains_across_bounded_uart_reads(self) -> None:
        # Given a 360-byte backlog during a worst-case pulse wait; when running; then nothing is lost.
        payload = b"".join(tf_frame(100 + index, 200) for index in range(40))
        clock = Clock()
        hardware = Hardware(clock, [-2, -1], [(10000, payload)])
        hardware.timeout_duration = 60000
        output = Output(1)
        with patch.dict(sys.modules, {"machine": hardware, "time": clock}), redirect_stdout(output):
            load_reader().main()
        tf_lines = [line.split(",") for line in output.getvalue().splitlines()
                    if line.startswith("TF_DATA,")]
        self.assertEqual([int(line[3]) for line in tf_lines], list(range(100, 140)))
        self.assertEqual({line[1] for line in tf_lines}, {"60012", "61012"})
        starts = [tick for _, level, tick in hardware.edges if level == 1]
        self.assertTrue(starts[1] - starts[0] >= 65000)
        self.assertIn("US_DATA,1,140,-2,-1\n", output.getvalue())

    def test_tf_delta_when_ticks_wrap_uses_elapsed_parse_time(self) -> None:
        # Given TF bytes on either side of a wrap; when polled; then delta stays positive.
        start = Clock.PERIOD - 10
        clock = Clock(start)
        uart = UART(clock, [(start, tf_frame(123, 456)), (start + 30, tf_frame(124, 457))])
        stream = load_reader().TFStream(uart, clock)
        output = io.StringIO()
        with redirect_stdout(output):
            for stamp in (start, start + 30):
                clock.now = stamp
                stream.poll()
        self.assertEqual([line.split(",")[1:3] for line in output.getvalue().splitlines()],
                         [[str(start), "-1"], ["20", "30"]])

    def test_interrupt_when_trigger_high_cleans_outputs_and_uart(self) -> None:
        # Given interruption during trigger high; when main exits; then triggers are low and UART closes.
        hardware, output = run_main([], [], interrupt=True)
        self.assertEqual([(pin.gpio, pin.level) for pin in hardware.pins if pin.mode == hardware.Pin.OUT],
                         [(2, 0), (4, 0)])
        self.assertTrue(hardware.uart.closed)
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()
