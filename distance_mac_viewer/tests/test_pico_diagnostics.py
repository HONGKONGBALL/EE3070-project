#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
# How to run: python3 tests/test_pico_diagnostics.py
"""Exercise diagnostics scheduling and protocol without opening real hardware."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import test_distance_reader as sensors

PATH = Path(__file__).resolve().parents[1] / 'pico_diagnostics.py'


from pico_diagnostics_fakes import Clock, Hardware, Input, Poller


def fixture():
    spec = importlib.util.spec_from_file_location('pico_diagnostics_under_test', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hardware, clock, stream, lines = Hardware(), Clock(), Input(), []
    board = module.BoardDiagnostics(hardware, clock, stream, Poller(stream), lines.append)
    return board, hardware, clock, stream, lines


def events(lines):
    return [json.loads(line.partition(',')[2]) for line in lines if line.startswith('HW_DATA,')]


def send(board, stream, text):
    stream.data += text
    while stream.data:
        board.poll()


class DiagnosticsTests(unittest.TestCase):
    def test_readiness_is_emitted_only_after_constructor_returns(self):
        # Given a constructed instance; when its first poll runs; then readiness is emitted exactly once.
        board, hardware, clock, stream, lines = fixture()
        self.assertEqual(lines, [])
        board.poll()
        board.poll()
        self.assertEqual(lines, ['HW_READY,1'])
        board.close()

    def test_failed_input_registration_does_not_leave_timer_running(self):
        # Given unsupported input polling; when constructor fails; then no timer was allocated and outputs stay low.
        spec = importlib.util.spec_from_file_location('pico_diagnostics_init_failure', PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        hardware, clock, stream = Hardware(), Clock(), Input()

        class UnsupportedPoller:
            def register(self, stream, mask):
                raise OSError('unsupported input')

        with self.assertRaisesRegex(OSError, 'unsupported input'):
            module.BoardDiagnostics(hardware, clock, stream, UnsupportedPoller())
        self.assertEqual(hardware.timers, [])
        self.assertEqual(hardware.levels, {14: 0, 15: 0, 16: 0, 25: 0})

    def test_sensor_interrupt_also_closes_diagnostics_timer_outputs_and_uart(self):
        # Given integrated motor control and sensor acquisition; when acquisition is interrupted; then all are cleaned.
        board, hardware, clock, stream, lines = fixture()
        sensor_clock = sensors.Clock()
        sensor_hardware = sensors.Hardware(sensor_clock, [580, 1160], [])
        sensor_hardware.Timer = hardware.Timer
        reader = sensors.load_reader()
        stream.data = 'HW_CMD,16,motor,5000\n'
        diagnostics = []

        def create(machine, clock):
            instance = type(board)(machine, clock, stream, Poller(stream), lines.append)
            diagnostics.append(instance)
            return instance

        reader.BoardDiagnostics = create
        with patch.dict(sys.modules, {'machine': sensor_hardware, 'time': sensor_clock}), redirect_stdout(sensors.Output(1)):
            reader.main()
        self.assertTrue(sensor_hardware.uart.closed)
        self.assertTrue(hardware.timers[-1].closed)
        levels = {pin.gpio: pin.level for pin in sensor_hardware.pins if pin.mode == sensor_hardware.Pin.OUT}
        self.assertEqual({pin: levels[pin] for pin in (2, 4, 14, 15, 16, 25)},
                         {pin: 0 for pin in (2, 4, 14, 15, 16, 25)})
        self.assertEqual(events(lines)[-1]['state'], 'stopped')

    def test_cleanup_uart_survives_diagnostic_ack_write_failure(self):
        # Given a closed USB output during cleanup; when diagnostics.close fails; then UART still deinitializes.
        clock = sensors.Clock()
        hardware = sensors.Hardware(clock, [580, 1160], [])
        reader = sensors.load_reader()

        class Diagnostics:
            def __init__(self, machine, clock):
                self.pin = machine.Pin(14, machine.Pin.OUT, value=1)

            def poll(self):
                return

            def close(self):
                self.pin.value(0)
                raise OSError('USB output closed')

        reader.BoardDiagnostics = Diagnostics
        with patch.dict(sys.modules, {'machine': hardware, 'time': clock}), redirect_stdout(sensors.Output(1)):
            with self.assertRaisesRegex(OSError, 'USB output closed'):
                reader.main()
        self.assertTrue(hardware.uart.closed)
        self.assertEqual([pin.level for pin in hardware.pins if pin.gpio == 14], [0])

    def test_hard_timer_cuts_motor_when_main_loop_is_stalled(self):
        # Given no main-loop progress; when a hard timer reaches the deadline; then GPIO is already low.
        board, hardware, clock, stream, lines = fixture()
        send(board, stream, 'HW_CMD,14,motor,5000\n')
        self.assertTrue(hardware.timers, 'A hardware timer must own the cutoff')
        timer = hardware.timers[0]
        self.assertTrue(timer.hard)
        clock.now = 5000
        before = len(lines)
        timer.fire()
        self.assertEqual(hardware.levels[14], 0)
        self.assertEqual(len(lines), before, 'The IRQ must not serialize or print')
        board.poll()
        self.assertEqual(events(lines)[-1]['state'], 'done')
        board.close()
        self.assertTrue(timer.closed)

    def test_hard_timer_zeros_pwm_when_heartbeat_lost_without_main_poll(self):
        # Given stalled USB/main polling; when heartbeat expires; then PWM duty is zero before cleanup runs.
        board, hardware, clock, stream, lines = fixture()
        send(board, stream, 'HW_CMD,15,buzzer,5000\n')
        self.assertTrue(hardware.timers, 'A hardware timer must own the cutoff')
        clock.now = 2500
        hardware.timers[0].fire()
        self.assertEqual(hardware.pwms[0].duty, 0)
        board.poll()
        self.assertEqual(events(lines)[-1]['state'], 'link_lost')
        self.assertTrue(hardware.pwms[0].closed)

    def test_motor_autooff_at_deadline_with_heartbeat(self):
        # Given a running motor; when the deadline arrives; then output turns off and done is acknowledged.
        board, hardware, clock, stream, lines = fixture()
        send(board, stream, 'HW_CMD,1,motor,5000\n')
        self.assertEqual(hardware.levels[14], 1)
        for stamp in (2000, 4000):
            clock.now = stamp
            send(board, stream, 'HW_PING\n')
        clock.now = 5000
        board.poll()
        self.assertEqual(hardware.levels[14], 0)
        self.assertEqual([event['state'] for event in events(lines)], ['started', 'done'])

    def test_heartbeat_loss_turns_off_and_clears_pwm(self):
        # Given active buzzer PWM; when the host stops pinging; then the driver is deinitialized and low.
        board, hardware, clock, stream, lines = fixture()
        send(board, stream, 'HW_CMD,2,buzzer,5000\n')
        pwm = hardware.pwms[0]
        self.assertEqual((pwm.freq, pwm.duty), (3000, 32768))
        clock.now = 2500
        board.poll()
        self.assertEqual((pwm.closed, pwm.duty, hardware.levels[16]), (True, 0, 0))
        self.assertEqual(events(lines)[-1]['state'], 'link_lost')

    def test_stop_shuts_down_active_before_ack_and_new_test_can_start(self):
        # Given a running COB; when stop arrives; then it is low before the stop acknowledgement.
        board, hardware, clock, stream, lines = fixture()
        send(board, stream, 'HW_CMD,3,cob,3000\nHW_CMD,4,stop,0\nHW_CMD,5,motor,5000\n')
        self.assertEqual([(event['id'], event['state']) for event in events(lines)],
                         [(3, 'started'), (3, 'stopped'), (4, 'done'), (5, 'started')])
        self.assertEqual(hardware.levels[15], 0)
        board.close()
        self.assertEqual(hardware.levels[14], 0)

    def test_invalid_busy_and_fragmented_input_never_enable_wrong_output(self):
        # Given malformed or incomplete commands; when consumed; then only the validated complete command runs.
        board, hardware, clock, stream, lines = fixture()
        send(board, stream, 'HW_CMD,6,motor,5001\nHW_CMD,7,unknown,5000\nHW_CMD,0,motor,5000\nHW_CMD,8,mo')
        self.assertTrue(all(hardware.levels[pin] == 0 for pin in (14, 15, 16, 25)))
        send(board, stream, 'tor,5000\nHW_CMD,9,cob,3000\n')
        self.assertEqual(hardware.levels[14], 1)
        self.assertEqual(hardware.levels[15], 0)
        self.assertEqual(events(lines)[-1]['reason'], 'busy')

    def test_overlong_line_discards_to_newline_then_recovers(self):
        # Given an overlong line containing a command suffix; when terminated; then it cannot execute the suffix.
        board, hardware, clock, stream, lines = fixture()
        send(board, stream, 'x' * 129 + 'HW_CMD,10,motor,5000\n')
        self.assertEqual(hardware.levels[14], 0)
        send(board, stream, 'HW_CMD,11,led,3000\n')
        self.assertEqual(hardware.levels[25], 1)
        clock.now = 500
        board.poll()
        self.assertEqual(hardware.levels[25], 0)

    def test_deadline_handles_ticks_wrap_and_close_clears_all_outputs(self):
        # Given ticks near wrap; when time advances through the deadline; then cleanup remains correct.
        board, hardware, clock, stream, lines = fixture()
        clock.now = Clock.PERIOD - 1000
        send(board, stream, 'HW_CMD,12,cob,3000\n')
        clock.now += 1500
        send(board, stream, 'HW_PING\n')
        clock.now += 1500
        board.poll()
        self.assertEqual(events(lines)[-1]['state'], 'done')
        board.close()
        self.assertTrue(all(hardware.levels[pin] == 0 for pin in (14, 15, 16, 25)))

    def test_buttons_debounce_long_and_release(self):
        # Given bouncing active-low buttons; when stable long press/release occurs; then each edge is emitted once.
        board, hardware, clock, stream, lines = fixture()
        send(board, stream, 'HW_CMD,13,buttons,45000\n')
        hardware.levels[10] = 0
        board.poll()
        clock.now = 10
        hardware.levels[10] = 1
        board.poll()
        clock.now = 20
        hardware.levels[10] = 0
        board.poll()
        clock.now = 50
        board.poll()
        clock.now = 850
        board.poll()
        hardware.levels[10] = 1
        board.poll()
        clock.now = 880
        board.poll()
        button_events = [json.loads(line.partition(',')[2]) for line in lines if line.startswith('HW_BUTTON,')]
        self.assertEqual([event['state'] for event in button_events], ['pressed', 'long', 'released'])
        self.assertEqual(button_events[-1]['duration_ms'], 830)
        self.assertEqual({event['index'] for event in button_events}, {1})


if __name__ == '__main__':
    unittest.main()
