"""Pure-logic parts of the Pico code, run on CPython."""

import pytest

import protocol as pico_protocol
from buttons import ButtonLogic
from cane.model import ButtonEvent, SensorFrame
from cane.pico_link import encode, parse_line
from hcsr04 import echo_us_to_cm
from outputs import BUZZER, VIBRATION, PatternPlayer
from tfmini import TFminiParser, make_frame


def test_echo_conversion():
    assert echo_us_to_cm(5831) == 100  # 1 m round trip ~ 5831 us
    assert echo_us_to_cm(-1) == -1 and echo_us_to_cm(0) == -1
    assert echo_us_to_cm(30000) == -1  # > 4 m


def test_tfmini_parser_frames_and_resync():
    p = TFminiParser()
    data = b"\x00\x13" + make_frame(148, 900) + make_frame(150, 1200)[:5]
    assert p.feed(data) == [(148, 900, -256.0)]
    assert p.feed(make_frame(150, 1200)[5:]) == [(150, 1200, -256.0)]


def test_tfmini_bad_checksum_and_weak_signal():
    p = TFminiParser()
    bad = bytearray(make_frame(148, 900))
    bad[8] ^= 0xFF
    assert p.feed(bytes(bad)) == [] and p.bad_checksum == 1
    assert p.feed(make_frame(148, 50))[0][0] == -1       # strength < 100
    assert p.feed(make_frame(148, 65535))[0][0] == -1    # overexposed


def test_tfmini_buffer_bounded():
    p = TFminiParser()
    p.feed(b"\x00" * 1000)
    assert len(p.buf) <= 64


def press(logic, t0, dur, step=5):
    evs = []
    t = t0
    while t < t0 + dur:
        e = logic.update(t, True)
        if e:
            evs.append(e)
        t += step
    for _ in range(10):
        e = logic.update(t, False)
        if e:
            evs.append(e)
        t += step
    return evs, t


def test_button_short_long_and_bounce():
    b = ButtonLogic(long_ms=800, debounce_ms=30)
    evs, t = press(b, 0, 200)
    assert evs == ["short"]
    evs, t = press(b, t, 1200)
    assert evs == ["long"]
    # a 10 ms glitch is ignored
    evs, t = press(b, t, 10)
    assert evs == []


def test_pattern_player():
    p = PatternPlayer()
    p.play([(100, 50), (100, 0)], 0.8, now=1000)
    assert p.level(1000) == 0.8
    assert p.level(1120) == 0.0
    assert p.level(1160) == 0.8
    assert p.active(1200) and not p.active(1300)
    assert p.level(1300) == 0.0


def test_left_right_patterns_differ():
    assert VIBRATION["left"] != VIBRATION["right"]
    assert "alarm" in BUZZER and "power" in BUZZER


def test_protocol_roundtrip_with_pi_parser():
    m = parse_line(pico_protocol.frame(123, -1, 148, 900, 42), now=0)
    assert isinstance(m, SensorFrame) and m.front_m == 1.23 and m.up_m is None
    assert parse_line(pico_protocol.button("dest", "long"), now=0) == ButtonEvent(0, "dest", "long")


@pytest.mark.parametrize("msg", [{"t": "h"}, {"t": "v", "p": "stop"}, {"t": "l", "v": 0.5}, {"t": "z", "p": "beep"}])
def test_pi_commands_parse_on_pico(msg):
    assert pico_protocol.parse_cmd(encode(msg).decode()) == msg


def test_pico_rejects_junk():
    assert pico_protocol.parse_cmd("hello") is None
    assert pico_protocol.parse_cmd('{"t":"reboot"}') is None


def test_line_reader():
    r = pico_protocol.LineReader()
    out = [r.feed(c) for c in '{"t":"h"}\n']
    assert out[-1] == '{"t":"h"}'
