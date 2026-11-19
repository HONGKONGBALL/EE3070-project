"""Local-only image questions over Ollama's native API."""

import base64
from dataclasses import dataclass
import http.client
import io
import json
from pathlib import Path
import re
import threading
import time

from PIL import Image, ImageOps


MODEL = 'qwen3.5:0.8b'
MODELS = (MODEL, 'qwen3.5:2b')
QUESTION = 'What is in front of me? Describe the visible surroundings briefly.'
PROMPTS = {
    'legacy': ('Describe the visible scene to a blind pedestrian in 20 to 40 English words. '
               'Give one or two sentences about visible objects and surfaces. '
               'If uncertain about an object, use "appears to be". '
               'Omit safety commentary, travel instructions, and distance estimates.'),
    'concise': ('Answer only the question using visible evidence. Use at most two short, natural English '
                'sentences. Left and right are from the viewer’s perspective. If unclear, say you can’t tell. '
                'Never guess an object, location, or distance, declare a path safe, or tell someone to cross.'),
    'grounded': ('Answer the specific question in one or two short sentences of spoken English. '
                 'Name the visible item or describe only the part you can see. If it is too dark or hidden, '
                 'say you can’t tell. Use left, center, or right from the viewer’s perspective only when relevant. '
                 'For a scene question, name the type of place only if the image supports it. '
                 'Skip unrelated details and do not infer distance, exact location, or whether a path is safe. '
                 'For a safety or road-crossing question, say you cannot determine that from one image.'),
}
SAFETY_REPLY = 'I can’t tell from a photo whether it’s safe to proceed.'
SAFETY_QUESTION = re.compile(
    r'\bsafe\s+(?:to|for me to)\s+(?:cross|walk|go|enter|proceed|continue|keep walking)\b'
    r'|\b(?:can|could|should|may)\s+i\s+(?:cross|walk|go|enter|proceed|continue|keep walking)\b'
    r'|\b(?:is|does)\s+(?:the|this|that|my)\s+(?:path|route|crossing|walkway)\b[^?]{0,32}\b(?:safe|clear)\b',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InferenceSettings:
    prompt: str = 'legacy'
    image_size: int = 512
    max_tokens: int = 64
    context: int = 1024
    temperature: float = 0

    def __post_init__(self):
        if (self.prompt not in PROMPTS or self.image_size < 1 or self.max_tokens < 1
                or self.context < 256 or not 0 <= self.temperature <= 1):
            raise ValueError('Invalid inference settings')


class OllamaError(RuntimeError):
    pass


@dataclass
class Result:
    success: bool = False
    answer: str = ''
    error: str = ''
    wall_s: float = 0
    first_text_s: float | None = None
    model_load_s: float | None = None
    prompt_eval_s: float | None = None
    image_eval_s: float | None = None
    image_prepare_s: float | None = None
    server_total_s: float | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    tokens_per_s: float | None = None
    thinking_present: bool = False
    done_reason: str | None = None
    image_pixels: tuple[int, int] | None = None
    policy_response: bool = False


class OllamaVisionProvider:
    _inference_lock = threading.Lock()

    def __init__(self, port=11434, timeout=120, threads=3, *, model=MODEL, settings=None):
        if not 1 <= port <= 65535 or timeout <= 0 or threads < 1:
            raise ValueError('Invalid port, timeout or thread count')
        if model not in MODELS:
            raise ValueError('Choose an supported model tag')
        self.port, self.timeout, self.threads = port, timeout, threads
        self.model = model
        self.settings = settings or InferenceSettings()

    def _send(self, path, payload=None):
        # Direct loopback HTTP. No proxy, redirects, credentials or remote fallback.
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        try:
            conn.request('GET' if payload is None else 'POST', path,
                         None if payload is None else json.dumps(payload),
                         {'Content-Type': 'application/json'})
            response = conn.getresponse()
            if response.status != 200:
                message = response.read(4096).decode('utf-8', errors='replace')
                raise OllamaError(f'Ollama HTTP {response.status}: {message}')
            return conn, response
        except BaseException:
            conn.close()
            raise

    def answer_question(self, question, image, *, keep_alive='2m'):
        if SAFETY_QUESTION.search(question):
            return Result(success=True, answer=SAFETY_REPLY, policy_response=True)
        return self.describe(question, image, keep_alive=keep_alive)

    def describe(self, question=QUESTION, image=None, *, keep_alive='2m'):
        """Accept a PIL image or a local image path. Return answer and timings."""
        if not self._inference_lock.acquire(blocking=False):
            return Result(error='An inference is already running')
        start = time.monotonic()
        result = Result()
        try:
            settings = self.settings
            payload = {'model': self.model, 'prompt': question, 'think': False, 'stream': True, 'keep_alive': keep_alive,
                       'options': {'num_ctx': settings.context, 'num_predict': settings.max_tokens,
                                   'num_thread': self.threads, 'num_gpu': 0, 'temperature': settings.temperature,
                                   'seed': 42}}
            if image is not None:
                payload['system'] = PROMPTS[settings.prompt]
                prepare_started = time.monotonic()
                if isinstance(image, (str, Path)):
                    with Image.open(image) as source:
                        encoded, result.image_pixels = self._jpeg(source)
                else:
                    encoded, result.image_pixels = self._jpeg(image)
                result.image_prepare_s = time.monotonic() - prepare_started
                payload['images'] = [encoded]
            conn, response = self._send('/api/generate', payload)
            try:
                parts = []
                for line in response:
                    if time.monotonic() - start > self.timeout:
                        raise TimeoutError('Inference deadline exceeded')
                    packet = json.loads(line)
                    if packet.get('error'):
                        raise OllamaError(str(packet['error']))
                    result.thinking_present |= bool(packet.get('thinking'))
                    fragment = packet.get('response', '')
                    if fragment:
                        if result.first_text_s is None and fragment.strip():
                            result.first_text_s = time.monotonic() - start
                        parts.append(fragment)
                    if packet.get('done'):
                        result.answer = ' '.join(''.join(parts).split())
                        if not result.answer:
                            raise OllamaError('Model returned no answer text')
                        result.done_reason = packet.get('done_reason')
                        for field, key in [('model_load_s', 'load_duration'), ('prompt_eval_s', 'prompt_eval_duration'),
                                           ('image_eval_s', 'image_eval_duration'), ('server_total_s', 'total_duration')]:
                            if key in packet:
                                setattr(result, field, packet[key] / 1e9)
                        result.prompt_tokens = packet.get('prompt_eval_count')
                        result.output_tokens = packet.get('eval_count')
                        if result.output_tokens and packet.get('eval_duration'):
                            result.tokens_per_s = result.output_tokens * 1e9 / packet['eval_duration']
                        result.success = True
                        break
                else:
                    raise OllamaError('Model response ended before completion')
            finally:
                conn.close()
        except TimeoutError:
            result.error = f'Local inference timed out after {self.timeout:g}s'
        except (OSError, http.client.HTTPException, ValueError, OllamaError) as exc:
            result.error = str(exc)
        finally:
            result.wall_s = time.monotonic() - start
            self._inference_lock.release()
        return result

    def _jpeg(self, image):
        image = ImageOps.exif_transpose(image).convert('RGB')
        image.thumbnail((self.settings.image_size, self.settings.image_size), Image.Resampling.LANCZOS)
        with io.BytesIO() as buffer:
            image.save(buffer, format='JPEG', quality=85)
            return base64.b64encode(buffer.getvalue()).decode('ascii'), image.size
