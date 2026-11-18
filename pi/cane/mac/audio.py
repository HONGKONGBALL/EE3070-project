"""Local WAV playback using macOS afplay through the single output arbiter."""

import subprocess
from pathlib import Path

from cane.audio import AplayAudio, clip_path


class MacAudio(AplayAudio):
    """Own mutable player state; only the arbiter may submit or interrupt audio."""

    def __init__(self, audio_dir: str | Path, lang: str) -> None:
        super().__init__(audio_dir, lang, player="afplay")
        self.error = ""
        self.last_completed: str | None = None

    def _cmds(self, clip_ids: list[str], text: str | None) -> list[list[str]]:
        # Route read-backs use generated clips too. Never run arbitrary text as code.
        commands: list[list[str]] = []
        missing: list[str] = []
        for clip_id in clip_ids:
            path = clip_path(self.audio_dir, self.lang, clip_id)
            if path.is_file():
                commands.append(["afplay", str(path)])
            else:
                missing.append(clip_id)
        self.error = "缺少本地提示音：" + ", ".join(missing) if missing else ""
        return commands

    def _run(self, cmds: list[list[str]]) -> None:
        for command in cmds:
            if self._stop.is_set():
                return
            try:
                with self._lock:
                    self._proc = subprocess.Popen(
                        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                    )
                _, error = self._proc.communicate(timeout=30)
                if self._proc.returncode and not self._stop.is_set():
                    self.error = error.decode("utf-8", errors="replace")[:300]
                elif not self._stop.is_set():
                    self.last_completed = Path(command[-1]).stem
            except (OSError, subprocess.TimeoutExpired) as error:
                self.error = str(error)
                if self._proc is not None:
                    self._proc.kill()
                    self._proc.wait()
                return
            finally:
                with self._lock:
                    self._proc = None
