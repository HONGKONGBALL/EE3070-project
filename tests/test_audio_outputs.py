"""Output switching preserves detector settings and never selects microphones/dummy."""
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import tomllib
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
from cane.vision import audio_outputs as outputs
from cane.vision.speech import PipeWireAudio, Speech


def inventory(monkeypatch, nodes):
    objects = [{'info': {'props': props}} for props in nodes]
    monkeypatch.setattr(outputs.subprocess, 'run', lambda cmd, **kw: SimpleNamespace(stdout=json.dumps(objects)))


def test_only_connected_outputs_are_selectable(monkeypatch):
    inventory(monkeypatch, [
        {'media.class': 'Audio/Sink', 'node.name': 'auto_null'},
        {'media.class': 'Audio/Source', 'node.name': 'webcam_microphone'},
        {'media.class': 'Audio/Sink', 'node.name': 'bluez_output.airpods.1', 'node.description': 'AirPods'},
        {'media.class': 'Audio/Sink', 'node.name': 'alsa_output.usb-HyperX.stereo', 'node.description': 'HyperX'},
    ])
    result = outputs.connected_outputs()
    assert [(o['name'], o['transport']) for o in result] == [('AirPods', 'Bluetooth'), ('HyperX', 'USB')]


def test_pipewire_absent_is_reported_not_a_fake_device(monkeypatch):
    def missing(*_a, **_kw): raise FileNotFoundError('pw-dump')
    monkeypatch.setattr(outputs.subprocess, 'run', missing)
    with pytest.raises(RuntimeError, match='PipeWire'): outputs.connected_outputs()


@pytest.fixture
def config(tmp_path):
    path = tmp_path / 'camera.toml'
    path.write_text('[audio]\nenabled = false\ndirectory = "audio/en"\ntarget = ""\nordinary_interval_s = 1.5\n\n[road]\nmax_fps = 1.0\nconfidence = 0.4\n\n[warning.confirmation.pothole]\nhits = 2\nobservations = 2\n')
    return path


def test_bluetooth_usb_and_mute_survive_config_reload(monkeypatch, config, tmp_path):
    before = tomllib.loads(config.read_text())
    inventory(monkeypatch, [
        {'media.class': 'Audio/Sink', 'node.name': 'bluez_output.airpods.1'},
        {'media.class': 'Audio/Sink', 'node.name': 'alsa_output.usb-HyperX.stereo'},
    ])
    speech = Speech(tmp_path, False)
    control = outputs.AudioOutputControl(speech, config)
    for target in ['bluez_output.airpods.1', 'alsa_output.usb-HyperX.stereo']:
        control.select({'enabled': True, 'target': target})
        saved = tomllib.loads(config.read_text())
        assert saved['audio'] == {**before['audio'], 'enabled': True, 'target': target}
        assert saved['warning'] == before['warning'] and saved['road'] == before['road']
        assert speech.sink.enabled and speech.sink.target == target
    inventory(monkeypatch, [])
    control.select({'enabled': False, 'target': speech.sink.target})
    assert not speech.sink.enabled and speech.state == 'disabled'


@pytest.mark.parametrize('selection', [
    {'enabled': True, 'target': 'disconnected_airpods'},
    {'enabled': 'true', 'target': 'anything'},
    {'enabled': True, 'target': 55}, [],
])
def test_invalid_selection_keeps_running_output_and_file(monkeypatch, config, tmp_path, selection):
    inventory(monkeypatch, [])
    before = config.read_bytes()
    speech = Speech(tmp_path, False)
    control = outputs.AudioOutputControl(speech, config)
    with pytest.raises(ValueError): control.select(selection)
    assert config.read_bytes() == before
    assert speech.sink.target == '' and not speech.sink.enabled


def test_unwritable_config_keeps_running_output(monkeypatch, config, tmp_path):
    inventory(monkeypatch, [{'media.class': 'Audio/Sink', 'node.name': 'bluez_output.airpods.1'}])
    def failed(*_args): raise PermissionError('read only')
    monkeypatch.setattr(outputs, 'save_audio_settings', failed)
    speech = Speech(tmp_path, False)
    with pytest.raises(PermissionError):
        outputs.AudioOutputControl(speech, config).select({'enabled': True, 'target': 'bluez_output.airpods.1'})
    assert not speech.sink.enabled and speech.sink.target == ''


@pytest.mark.parametrize('target', ['bluez_output.airpods.1', 'alsa_output.usb-HyperX.stereo'])
def test_player_uses_selected_stable_name(monkeypatch, tmp_path, target):
    commands = []
    class Player:
        returncode = 0
        def communicate(self, **kw): return b'', b''
        def poll(self): return 0
    def launch(command, **kw): commands.append(command); return Player()
    monkeypatch.setattr('cane.vision.speech.subprocess.Popen', launch)
    sink = PipeWireAudio(tmp_path, True, target, lambda *_a, **_kw: None)
    sink.play(['stairs_ahead'])
    sink.thread.join(timeout=1)
    assert commands == [['pw-play', '--target', target, str(tmp_path / 'stairs_ahead.wav')]]
    assert sink.last_played['phrase_id'] == 'stairs_ahead'


def test_route_change_cancels_old_queue(monkeypatch, tmp_path):
    speech = Speech(tmp_path, False)
    speech.sink.busy = lambda: True
    speech.submit({'phrase_id': 'puddle_ahead', 'priority': 'SAFETY', 'event_id': 'old'})
    assert speech.arbiter.pending()
    speech.configure_output(True, 'bluez_output.airpods.1')
    assert speech.arbiter.pending() == []
    assert speech.sink.target == 'bluez_output.airpods.1'
    assert any(d['event_id'] == 'old' and d['action'] == 'invalid' for d in speech.deliveries)


def test_http_switch_and_no_inventory_poll_on_normal_status(monkeypatch, config, tmp_path):
    from cane.vision.demo import handler_for
    inventory(monkeypatch, [{'media.class': 'Audio/Sink', 'node.name': 'bluez_output.airpods.1'}])
    speech = Speech(tmp_path, False)
    app = SimpleNamespace(audio_outputs=outputs.AudioOutputControl(speech, config), status=lambda: {'ready': True})
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(base + '/audio-outputs') as response:
            assert json.load(response)['outputs'][0]['transport'] == 'Bluetooth'
        request = urllib.request.Request(base + '/audio-output', data=json.dumps({'enabled': True, 'target': 'bluez_output.airpods.1'}).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request) as response: assert json.load(response)['saved']
        def unexpected(): raise AssertionError('Device enumeration in status poll')
        monkeypatch.setattr(outputs, 'connected_outputs', unexpected)
        with urllib.request.urlopen(base + '/status') as response: assert json.load(response)['ready']
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + '/audio-output', data=b'{}'))
        assert error.value.code == 415
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
