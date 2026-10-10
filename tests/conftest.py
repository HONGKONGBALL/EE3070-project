import pytest


class FakeClock:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeAudio:
    """Audio sink that stays busy until the test says the clip ended."""

    def __init__(self) -> None:
        self.played: list[list[str]] = []
        self.stopped = 0
        self._busy = False

    def play(self, clip_ids, text=None):
        self.played.append(list(clip_ids))
        self._busy = True

    def stop(self):
        self.stopped += 1
        self._busy = False

    def busy(self):
        return self._busy

    def finish(self):
        self._busy = False


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def audio():
    return FakeAudio()
