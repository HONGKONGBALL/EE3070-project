"""Exercise player ownership during concurrent controls without audio hardware."""

from collections.abc import Callable
from pathlib import Path
import subprocess
import sys
import threading
from types import TracebackType

import pytest

from cane.vision.speech import Speech


class ObservedControlLock:
    """Retain real locking while exposing the submitter's acquisition attempt."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.submit_attempted = threading.Event()
        self.submit_blocked = False

    def __enter__(self) -> "ObservedControlLock":
        acquired = self.lock.acquire(blocking=False)
        if threading.current_thread().name == "http-submit":
            self.submit_blocked = not acquired
            self.submit_attempted.set()
        if not acquired:
            self.lock.acquire()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.lock.release()


@pytest.mark.parametrize("stop_method", [Speech.stop, Speech.close], ids=["stop", "close"])
def test_new_player_remains_owned_when_submit_overlaps_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stop_method: Callable[[Speech], None],
) -> None:
    # Given: a real child process represents the current PipeWire player.
    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def launch(_command: list[str], *, stdout: int, stderr: int) -> subprocess.Popen[bytes]:
        process = popen(
            [sys.executable, "-c", "import threading; threading.Event().wait()"],
            stdout=stdout,
            stderr=stderr,
        )
        processes.append(process)
        return process

    monkeypatch.setattr("cane.vision.speech.subprocess.Popen", launch)
    stopped_old_player = threading.Event()
    release_stop = threading.Event()

    def record(kind: str, **_fields: str | float | bool | None) -> None:
        if kind == "audio_stopped" and threading.current_thread().name == "render-stop":
            stopped_old_player.set()
            release_stop.wait(timeout=5)

    audio = Speech(tmp_path, True, record=record)
    control_lock = ObservedControlLock()
    audio.control_lock = control_lock
    audio.submit({"phrase_id": "puddle_ahead", "event_id": "visual:1", "priority": "SAFETY"})
    stopper = threading.Thread(target=stop_method, args=(audio,), name="render-stop")
    submitter = threading.Thread(
        target=audio.submit, args=({"phrase_id": "chair_ahead", "ttl_ms": 1500},),
        name="http-submit",
    )
    try:
        # When: stop has reaped the old child but has not yet cleared its references,
        # and the HTTP control attempts to submit another phrase.
        stopper.start()
        assert stopped_old_player.wait(timeout=2)
        submitter.start()
        assert control_lock.submit_attempted.wait(timeout=2)
        if not control_lock.submit_blocked:
            # Reproduce the old bug exactly: the new child starts before stop returns.
            submitter.join(timeout=2)
            assert not submitter.is_alive()
        release_stop.set()
        stopper.join(timeout=2)
        submitter.join(timeout=2)
        assert not stopper.is_alive() and not submitter.is_alive()

        # Then: the child remains observable and the next close terminates it.
        assert len(processes) == 2
        new_player = processes[1]
        assert new_player.poll() is None
        assert audio.busy()
        assert audio.sink.proc is new_player
        audio.close()
        assert new_player.poll() is not None
        assert not audio.busy()
    finally:
        release_stop.set()
        stopper.join(timeout=2)
        if submitter.ident is not None:
            submitter.join(timeout=2)
        audio.close()
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
