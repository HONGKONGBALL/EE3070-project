"""Local PTY tests exercise pyserial, without a physical Pico or firmware upload."""

import json
import os
import select
from contextlib import contextmanager

import pytest
from cane.mac import serial as mac_serial


@contextmanager
def pty_link(*, destination_button_only=False):
    master, slave = os.openpty()
    link = mac_serial.MacSerialPicoLink(
        os.ttyname(slave), 115200, retry_s=0.01, destination_button_only=destination_button_only,
    )
    try:
        link.start()
        assert link._opened.wait(2)
        yield link, master
    finally:
        link.close()
        os.close(master)
        os.close(slave)


def test_real_pty_auto_requires_json_handshake_before_control():
    # Given a pyserial connection to a local pseudoterminal, with no received protocol.
    with pty_link() as (link, master):
        link.heartbeat()
        assert select.select([master], [], [], 0)[0] == []
        # When a valid JSON sensor frame arrives in two chunks.
        os.write(master, b'{"t":"d","f":123,')
        os.write(master, b'"u":250,"g":180,"gs":900,"ms":12}\n')
        frame = link.inbox.get(timeout=2)
        link.heartbeat()
        # Then parsing succeeds and only the subsequent heartbeat reaches the peer.
        assert frame.front_m == 1.23
        assert link.protocol_name == "json"
        assert link.controls_available
        assert select.select([master], [], [], 2)[0] == [master]
        assert json.loads(os.read(master, 512)) == {"t": "h"}


def test_real_pty_legacy_never_receives_json_control():
    # Given the distance viewer's legacy protocol on a real local PTY.
    with pty_link() as (link, master):
        os.write(master, b"TF_DATA,1000,-1,120,900\n")
        frame = link.inbox.get(timeout=2)
        # When every host output method is requested.
        link.heartbeat()
        link.vibrate("stop")
        link.light(0.5)
        link.buzz("beep")
        # Then legacy remains input-only and retains centimetre conversion.
        assert frame.tfmini_m == 1.2
        assert link.protocol_name == "legacy"
        assert not link.controls_available
        assert select.select([master], [], [], 0)[0] == []


def test_voice_mode_only_receives_short_destination_press():
    with pty_link(destination_button_only=True) as (link, master):
        os.write(master, b'{"t":"p","vsys":5}\n'
                         b'{"t":"b","id":"ok","ev":"short"}\n'
                         b'{"t":"b","id":"dest","ev":"long"}\n'
                         b'{"t":"b","id":"dest","ev":"short"}\n')
        press = link.inbox.get(timeout=2)
        assert (press.button, press.kind) == ("dest", "short")
        assert link.inbox.empty()
        link.heartbeat()
        link.vibrate("stop")
        assert not link.controls_available
        assert select.select([master], [], [], 0)[0] == []


def test_legacy_channels_expire_independently():
    # Given both ultrasound channels and a TFmini sample at time 10.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    link._consume(b"US_DATA,1,1000,1000,2000\nTF_DATA,1000,-1,120,900\n", 10)
    # When only TFmini keeps reporting after the ultrasonic values expire.
    link._consume(b"TF_DATA,2000,1000,125,950\n", 11.6)
    frames = []
    while not link.inbox.empty():
        frames.append(link.inbox.get_nowait())
    # Then the live TFmini cannot refresh the dead ultrasound channels.
    assert frames[-1].front_m is None
    assert frames[-1].up_m is None
    assert frames[-1].tfmini_m == 1.25


@pytest.mark.parametrize("line", [b'{"t":"d","f":true,"u":2,"g":3}', b'{"t":"d","f":NaN,"u":2,"g":3}', b'{"t":"p","vsys":Infinity}', b'[]', b'{"t":"b","id":"anything","ev":"short"}', b'{"t":"d","f":1}', b'{"t":"d","f":1,"u":2,"g":"3"}'])
def test_invalid_json_cannot_unlock_outputs(line):
    # Given untrusted serial input with invalid types, values or message shape.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    # When the line is fed to the protocol detector.
    link._consume(line + b"\n", 10)
    # Then it produces no event and cannot select the writable protocol.
    assert link.protocol_name == "unknown"
    assert link.inbox.empty()


def test_oversized_line_is_discarded_until_newline():
    # Given an oversized partial line followed by valid-looking trailing JSON.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    link._consume(b"x" * 2000 + b'{"t":"p","vsys":5}\n', 10)
    # When the next complete, valid message arrives.
    link._consume(b'{"t":"p","vsys":4.95}\n', 11)
    # Then only the new line is accepted and buffering remains bounded.
    assert link.inbox.qsize() == 1
    assert link.inbox.get_nowait().vsys == 4.95
    assert len(link._buffer) <= 512


def test_inbox_is_bounded_under_sensor_flood():
    # Given more valid sensor packets than the bounded event queue can hold.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    # When the receiver is faster than the application consumer.
    for value in range(200):
        link._consume(f'{{"t":"d","f":{value},"u":-1,"g":-1}}\n'.encode(), 10)
    # Then memory stays bounded while the latest measurement survives.
    assert link.inbox.qsize() <= 64
    frames = list(link.inbox.queue)
    assert frames[-1].front_m == 1.99


def test_real_pty_close_releases_reader_and_handle():
    # Given a running pyserial connection to a local PTY.
    with pty_link() as (link, _master):
        handle = link._ser
        # When the host shuts down.
        link.close()
        # Then its serial descriptor and reader thread are released.
        assert not link.connected
        assert not handle.is_open
        assert not link._thread.is_alive()


def test_legacy_tf_expires_when_only_ultrasound_remains_alive():
    # Given a TFmini distance followed by continuing ultrasound data.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    link._consume(b"TF_DATA,1000,-1,120,900\n", 10)
    # When TFmini has been silent for more than 1.5 seconds.
    link._consume(b"US_DATA,1,1000,1000,2000\n", 11.6)
    # Then the newly emitted ultrasound frame cannot retain the old laser value.
    frame = list(link.inbox.queue)[-1]
    assert frame.front_m == pytest.approx(0.1715)
    assert frame.up_m == pytest.approx(0.343)
    assert frame.tfmini_m is None
    assert frame.tfmini_strength == 0


def test_idle_legacy_channels_expire_without_another_packet():
    # Given one valid legacy sample.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    link._consume(b"TF_DATA,1000,-1,120,900\n", 10)
    # When the expiry timer runs after all channels have stopped.
    link._expire_legacy(11.6)
    # Then the invalidation frame clears values without inventing a fresh reception.
    frame = list(link.inbox.queue)[-1]
    assert frame.tfmini_m is None
    assert frame.t == 10
    assert link.last_rx == 10


@pytest.mark.parametrize("first,second,protocol", [(b"TF_DATA,1000,-1,120,900\n", b'{"t":"p","vsys":5}\n', "legacy"), (b'{"t":"p","vsys":5}\n', b"TF_DATA,1000,-1,120,900\n", "json")])
def test_connection_protocol_cannot_be_switched_by_mixed_stream(first, second, protocol):
    # Given a connection already identified by its first valid protocol message.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    link._consume(first, 10)
    # When bytes from the other protocol appear.
    link._consume(second, 11)
    # Then they neither change capabilities nor create additional messages.
    assert link.protocol_name == protocol
    assert link.inbox.qsize() == 1


def test_read_failure_closes_each_handle_before_bounded_retry(monkeypatch):
    # Given a fake serial boundary whose reads fail like a disconnected USB handle.
    opened = []

    class FailedPort:
        def __init__(self, *args, **kwargs):
            assert kwargs["exclusive"] is True
            if opened:
                assert opened[-1].closed
            self.closed = False
            opened.append(self)

        def read(self, count):
            raise mac_serial.pyserial.SerialException("USB disconnected")

        def close(self):
            self.closed = True

    monkeypatch.setattr(mac_serial.pyserial, "Serial", FailedPort)
    link = mac_serial.MacSerialPicoLink("fake-only", retry_s=0.01, max_retries=2)
    # When the reader exhausts the finite reconnection budget.
    link.start()
    link._thread.join(timeout=2)
    # Then all old descriptors are closed and no simulation is substituted.
    assert not link._thread.is_alive()
    assert len(opened) == 3
    assert all(port.closed for port in opened)
    assert not link.connected
    assert link.protocol_name == "unknown"
    assert link.inbox.empty()


def test_write_failure_closes_old_handle_and_revokes_protocol():
    # Given a writable, identified JSON connection whose write now fails.
    class FailedWriter:
        closed = False

        def write(self, payload):
            raise mac_serial.pyserial.SerialException("USB disconnected")

        def close(self):
            self.closed = True

    port = FailedWriter()
    link = mac_serial.MacSerialPicoLink("fake-only")
    link._ser, link.connected, link.protocol_name = port, True, "json"
    # When the application requests an ordinary heartbeat.
    link.heartbeat()
    # Then the failed handle is closed and output capability is revoked.
    assert port.closed
    assert link._ser is None
    assert not link.connected
    assert link.protocol_name == "unknown"


def test_huge_json_integer_is_rejected_without_overflow():
    # Given an integer legal in JSON but too large to convert to a float.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    # When the serial voltage field receives it within the line-size limit.
    link._consume(b'{"t":"p","vsys":' + b"9" * 350 + b"}\n", 10)
    # Then the receiver remains alive and the message cannot identify the protocol.
    assert link.protocol_name == "unknown"
    assert link.inbox.empty()


def test_real_pty_exclusive_lock_prevents_second_host():
    # Given a local PTY already owned exclusively by the Mac link.
    # When another host attempts to open the same port exclusively.
    # Then pyserial refuses that competing owner.
    with (pty_link() as (link, _master), pytest.raises(mac_serial.pyserial.SerialException),
          mac_serial.pyserial.Serial(link.port, 115200, timeout=0.1, exclusive=True)):
        raise AssertionError("exclusive port unexpectedly opened twice")


def test_real_pty_disconnect_closes_old_descriptor_and_stops_retrying():
    # Given an actual pyserial stream over a local PTY, standing in for USB.
    master, slave = os.openpty()
    link = mac_serial.MacSerialPicoLink(os.ttyname(slave), retry_s=0.01, max_retries=1)
    try:
        link.start()
        assert link._opened.wait(2)
        handle = link._ser
        # When the peer endpoint disappears like a USB disconnect.
        os.close(master)
        master = None
        link._thread.join(timeout=2)
        # Then the old descriptor closes and bounded retries end without simulation.
        assert not handle.is_open
        assert not link.connected
        assert not link._thread.is_alive()
        assert link.status()["failures"] == 2
    finally:
        link.close()
        if master is not None:
            os.close(master)
        os.close(slave)


def test_legacy_timeout_minus_two_clears_only_that_ultrasound_channel():
    # Given the existing distance stream's -2 timeout code on one ranger.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    # When the other ranger still has a valid echo.
    link._consume(b"US_DATA,1,1000,-2,2000\n", 10)
    # Then the timeout does not discard its valid peer channel or protocol identity.
    frame = link.inbox.get_nowait()
    assert frame.front_m is None
    assert frame.up_m == pytest.approx(0.343)
    assert link.protocol_name == "legacy"


@pytest.mark.parametrize("raw,strength", [(0, 1000), (65532, 1000), (65534, 1000), (65535, 1000), (100, 99), (100, 65535)])
def test_legacy_tf_invalid_reading_becomes_none(raw, strength):
    # Given a known TFmini invalid raw reading or unreliable signal strength.
    link = mac_serial.MacSerialPicoLink("not-a-real-device")
    # When that packet arrives from the legacy distance firmware.
    link._consume(f"TF_DATA,1000,-1,{raw},{strength}\n".encode(), 10)
    # Then it remains an identified input-only stream with no invented distance.
    frame = link.inbox.get_nowait()
    assert frame.tfmini_m is None
    assert frame.tfmini_strength == 0
    assert link.protocol_name == "legacy"
