from cane.model import ButtonEvent, SensorFrame
from cane.pico_link import PowerEvent, encode, parse_line


def test_parse_distance_frame():
    m = parse_line(b'{"t":"d","f":123,"u":-1,"g":148,"gs":900,"ms":5}\n', now=1.0)
    assert isinstance(m, SensorFrame)
    assert m.front_m == 1.23 and m.up_m is None and m.tfmini_m == 1.48
    assert m.tfmini_strength == 900 and m.pico_ms == 5


def test_parse_button_and_power():
    assert parse_line('{"t":"b","id":"ok","ev":"long"}', 0) == ButtonEvent(0, "ok", "long")
    assert parse_line('{"t":"p","vsys":4.9}', 0) == PowerEvent(0, 4.9)


def test_garbage_ignored():
    for line in [b"", b"MicroPython v1.22", b"{bad json", b'{"t":"b"}', b'{"t":"zz"}']:
        assert parse_line(line, 0) is None


def test_encode():
    assert encode({"t": "h"}) == b'{"t":"h"}\n'
