"""On-demand PipeWire output selection; no device polling in the vision loop."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import tomllib


def connected_outputs():
    try:
        result = subprocess.run(['pw-dump'], capture_output=True, text=True,
                                check=True, timeout=2)
        objects = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError('Cannot read Pi audio outputs. Check PipeWire.') from exc
    outputs = []
    for obj in objects:
        props = (obj.get('info') or {}).get('props', {})
        name = props.get('node.name', '')
        if props.get('media.class') != 'Audio/Sink' or not name or name == 'auto_null':
            continue
        transport = ('Bluetooth' if name.startswith('bluez_output.') else
                     'USB' if props.get('device.bus') == 'usb' or '.usb-' in name else 'Audio')
        outputs.append({'target': name, 'name': props.get('node.description') or props.get('node.nick') or name,
                        'transport': transport})
    return sorted(outputs, key=lambda o: (o['transport'], o['name']))


def save_audio_settings(path, enabled, target):
    """Replace only enabled/target in [audio], preserve all detection settings."""
    path = Path(path)
    source = path.read_text(encoding='utf8')
    original = tomllib.loads(source)
    match = re.search(r'^\[audio\][ \t]*(?:#[^\n]*)?\n(.*?)(?=^\[|\Z)', source, re.M | re.S)
    if not match:
        raise RuntimeError('Audio configuration section not found')
    body = match.group(1)
    for key, value in [('enabled', 'true' if enabled else 'false'), ('target', json.dumps(target))]:
        pattern = r'^' + key + r'[ \t]*=[^\n]*'
        if re.search(pattern, body, re.M):
            body = re.sub(pattern, lambda _m: key + ' = ' + value, body, count=1, flags=re.M)
        else:
            body = key + ' = ' + value + '\n' + body
    updated = source[:match.start(1)] + body + source[match.end(1):]
    parsed = tomllib.loads(updated)
    expected = {**original, 'audio': {**original['audio'], 'enabled': enabled, 'target': target}}
    if parsed != expected:
        raise RuntimeError('Audio configuration validation failed')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(updated)
        temporary.chmod(path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


class AudioOutputControl:
    def __init__(self, speech, config_path):
        self.speech, self.config_path = speech, config_path
        self.lock = threading.Lock()

    def status(self):
        return {'outputs': connected_outputs(), 'enabled': self.speech.sink.enabled,
                'target': self.speech.sink.target}

    def select(self, request):
        if not isinstance(request, dict) or type(request.get('enabled')) is not bool or not isinstance(request.get('target'), str):
            raise ValueError('Select an output and an enabled setting')
        enabled, target = request['enabled'], request['target']
        with self.lock:
            if enabled and target not in {o['target'] for o in connected_outputs()}:
                raise ValueError('Selected headphones are not connected. Refresh outputs.')
            if not self.config_path:
                raise RuntimeError('No writable runtime configuration was provided')
            save_audio_settings(self.config_path, enabled, target)
            self.speech.configure_output(enabled, target)
        return {'enabled': enabled, 'target': target, 'saved': True}
