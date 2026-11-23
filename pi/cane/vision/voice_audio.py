"""Microphone capture and endpoint selection on Windows and Raspberry Pi."""

from dataclasses import dataclass
from contextlib import contextmanager
import io
import json
import platform
from queue import Empty, Full, Queue
import re
import subprocess
from threading import Event, Thread
import wave

import numpy as np


ASR_RATE = 16000
BLOCK_SECONDS = 0.10


@dataclass(frozen=True)
class Endpoint:
    id: str
    name: str
    rate: int
    channels: int


def read_wave(data):
    with wave.open(io.BytesIO(data), 'rb') as stream:
        if stream.getsampwidth() != 2 or stream.getcomptype() != 'NONE':
            raise ValueError('Use a WAV with 16-bit PCM audio')
        samples = stream.readframes(stream.getnframes())
        return samples, stream.getframerate(), stream.getnchannels()


def convert_pcm(samples, source_rate, channels, target_rate, target_channels=1):
    pcm = np.frombuffer(samples, dtype='<i2').reshape(-1, channels)
    if source_rate == target_rate and channels == target_channels:
        return samples
    mono = pcm.mean(axis=1)
    if source_rate != target_rate and mono.size:
        count = round(mono.size * target_rate / source_rate)
        mono = np.interp(np.arange(count) * source_rate / target_rate, np.arange(mono.size), mono)
    return np.repeat(mono.astype('<i2'), target_channels).tobytes()


def select_endpoint(endpoints, choice, kind):
    if choice is None:
        matches = [d for d in endpoints if re.search(r'air\s*pods', d.name, re.I)]
        hands_free = [d for d in matches if re.search(r'hands.?free|headset|hfp|hsp', d.name, re.I)]
        matches = hands_free or matches
    else:
        matches = [d for d in endpoints if d.id == choice]
        matches = matches or [d for d in endpoints if choice.casefold() in d.name.casefold()]
    if len(matches) == 1:
        return matches[0]
    label = choice if choice is not None else 'AirPods'
    if not matches:
        raise RuntimeError(f'{label} {kind} is unavailable. Check the active system audio endpoints.')
    raise RuntimeError(f'Multiple {label} {kind} endpoints match. Configure the exact audio endpoint ID.')


class CaptureBuffer:
    def __init__(self):
        self.blocks = Queue(maxsize=8)
        self.failed = Event()

    def put(self, block):
        try:
            self.blocks.put_nowait(block)
        except Full:
            self.discard_one()
            self.blocks.put_nowait(block)

    def discard_one(self):
        try:
            self.blocks.get_nowait()
        except Empty:
            pass

    def read(self, timeout):
        if self.failed.is_set():
            raise RuntimeError('Microphone disconnected')
        try:
            return self.blocks.get(timeout=timeout)
        except Empty:
            return b''


class WindowsAudio:
    def __init__(self):
        import sounddevice as sd

        self.sd = sd

    def endpoints(self, kind):
        sd = self.sd
        hosts = sd.query_hostapis()
        endpoints = []
        for index, row in enumerate(sd.query_devices()):
            channels = row[f'max_{kind}_channels']
            if 'WASAPI' in hosts[row['hostapi']]['name'] and channels:
                endpoints.append(Endpoint(str(index), row['name'], round(row['default_samplerate']), min(2, channels)))
        return endpoints

    @contextmanager
    def stream(self, device):
        buffer = CaptureBuffer()

        def capture(data, _frames, _time, _status):
            buffer.put(bytes(data))

        frame_count = round(device.rate * BLOCK_SECONDS)
        with self.sd.RawInputStream(device=int(device.id), samplerate=device.rate, channels=device.channels,
                                   blocksize=frame_count, dtype='int16', callback=capture) as stream:
            def read(timeout):
                if not stream.active:
                    raise RuntimeError('Microphone disconnected')
                return convert_pcm(buffer.read(timeout), device.rate, device.channels, ASR_RATE)
            yield read

class PulseAudio:
    @staticmethod
    def _nodes(nodes):
        response = subprocess.run(['pactl', '-f', 'json', 'list', nodes], capture_output=True,
                                  text=True, encoding='utf-8', check=True, timeout=5)
        return json.loads(response.stdout)

    def endpoints(self, kind):
        nodes = 'sources' if kind == 'input' else 'sinks'
        return [Endpoint(row['name'], row['description'], ASR_RATE, 1)
                for row in self._nodes(nodes) if not row['name'].endswith('.monitor')]

    def microphone_for_output(self, target):
        if not target:
            response = subprocess.run(['pactl', 'get-default-sink'], capture_output=True,
                                      text=True, encoding='utf-8', check=True, timeout=5)
            target = response.stdout.strip()
        sink = next((row for row in self._nodes('sinks') if row['name'] == target), {})
        device_id = sink.get('properties', {}).get('device.id')
        if device_id is not None:
            inputs = [row for row in self._nodes('sources')
                      if not row['name'].endswith('.monitor')
                      and row.get('properties', {}).get('device.id') == device_id]
            if len(inputs) == 1:
                row = inputs[0]
                return Endpoint(row['name'], row['description'], ASR_RATE, 1)
        raise RuntimeError('The selected audio output has no unique microphone')

    @contextmanager
    def stream(self, device):
        buffer = CaptureBuffer()
        cmd = ['parec', '--raw', '--latency-msec=100', '--device', device.id,
               '--format=s16le', '--rate=16000', '--channels=1']
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            def capture():
                while block := proc.stdout.read(round(ASR_RATE * BLOCK_SECONDS) * 2):
                    buffer.put(block)
                buffer.failed.set()

            worker = Thread(target=capture, daemon=True)
            worker.start()
            try:
                yield buffer.read
            finally:
                if proc.poll() is None:
                    proc.terminate()
                worker.join(timeout=1)
                if worker.is_alive():
                    proc.kill()
                    worker.join(timeout=1)

def local_audio():
    if platform.system() == 'Windows':
        return WindowsAudio()
    if platform.system() == 'Linux':
        return PulseAudio()
    raise RuntimeError('Audio capture supports Windows and Linux')
