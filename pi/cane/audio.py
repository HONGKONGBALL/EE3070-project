"""Playback of pre-generated prompt clips through the USB sound card.

Only the OutputArbiter calls these classes.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

log = logging.getLogger(__name__)


def clip_path(audio_dir: str | Path, lang: str, clip_id: str) -> Path:
    return Path(audio_dir) / lang / f"{clip_id}.wav"


def wav_duration(path: str | Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def load_durations(audio_dir: str | Path, langs: tuple[str, ...] = ("cmn", "yue", "en")) -> dict[str, dict[str, float]]:
    """{lang: {clip_id: seconds}} for every .wav found."""
    out: dict[str, dict[str, float]] = {}
    for lang in langs:
        d = Path(audio_dir) / lang
        out[lang] = {p.stem: wav_duration(p) for p in sorted(d.glob("*.wav"))} if d.is_dir() else {}
    return out


class AplayAudio:
    """Plays a sequence of WAV clips with `aplay`. stop() interrupts immediately.

    `tts_cmd` (e.g. ["espeak-ng", "-v", "zh"]) is only used for live route
    read-back text; safety prompts are always local clips.
    """

    def __init__(
        self,
        audio_dir: str | Path,
        lang: str,
        device: str = "default",
        player: str = "aplay",
        tts_cmd: list[str] | None = None,
    ) -> None:
        self.audio_dir = Path(audio_dir)
        self.lang = lang
        self.device = device
        self.player = player
        self.tts_cmd = tts_cmd
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        if shutil.which(player) is None:
            log.warning("%s not found; audio will be silent", player)

    def _cmds(self, clip_ids: list[str], text: str | None) -> list[list[str]]:
        cmds = []
        for cid in clip_ids:
            p = clip_path(self.audio_dir, self.lang, cid)
            if p.exists():
                cmds.append([self.player, "-q", "-D", self.device, str(p)])
            else:
                log.warning("missing clip %s", p)
        if text and self.tts_cmd:
            cmds.append([*self.tts_cmd, text])
        return cmds

    def play(self, clip_ids: list[str], text: str | None = None) -> None:
        self.stop()
        cmds = self._cmds(clip_ids, text)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(cmds,), daemon=True)
        self._thread.start()

    def _run(self, cmds: list[list[str]]) -> None:
        for cmd in cmds:
            if self._stop.is_set():
                return
            try:
                with self._lock:
                    self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self._proc.wait()
            except OSError as e:
                log.error("playback failed: %s", e)
                return
            finally:
                with self._lock:
                    self._proc = None

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class ConsoleAudio:
    """Simulation sink: prints the prompt text and stays busy for its duration."""

    def __init__(self, lang: str, durations: dict[str, float] | None = None, default_s: float = 0.8,
                 clock=time.monotonic) -> None:
        from .prompts import CATALOG

        self._catalog = CATALOG
        self.lang = lang
        self.durations = durations or {}
        self.default_s = default_s
        self._clock = clock
        self._until = 0.0
        self.played: list[list[str]] = []

    def play(self, clip_ids: list[str], text: str | None = None) -> None:
        words = [self._catalog[c].text(self.lang) if c in self._catalog else c for c in clip_ids]
        if text:
            words.append(text)
        print(f"[speak:{self.lang}] {' '.join(words)}", flush=True)
        self.played.append(list(clip_ids))
        self._until = self._clock() + sum(self.durations.get(c, self.default_s) for c in clip_ids)

    def stop(self) -> None:
        self._until = 0.0

    def busy(self) -> bool:
        return self._clock() < self._until
