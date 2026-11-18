"""The single output arbiter.

Every spoken prompt and every vibration goes through here; nothing else in the
program talks to the speaker or the vibration motor.

Rules:
1. One sound at a time. Lower Priority value wins.
2. A safety (CRITICAL/SAFETY) request pre-empts a less important prompt that is
   currently playing; the interrupted prompt is re-queued (subject to its TTL).
3. For `safety_hold_s` after a safety prompt, NAV/INFO prompts wait (they keep
   their TTL and are dropped if they go stale).
4. The same prompt is not repeated within `repeat_cooldown_s`.
5. Left/right commands are dropped while orientation is uncertain; the user is
   told to stop and face ahead instead.
6. There is no API to clear or override a safety hold from outside.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Protocol

from .model import Direction, OutputRequest, Priority


class AudioSink(Protocol):
    def play(self, clip_ids: list[str], text: str | None = None) -> None: ...
    def stop(self) -> None: ...
    def busy(self) -> bool: ...


HapticSink = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ArbiterEvent:
    t: float
    action: str  # played | preempted | dropped_orientation | dropped_cooldown | expired | queued
    prompt_id: str
    priority: str


class OutputArbiter:
    CRITICAL_COOLDOWN_S = 1.0

    def __init__(
        self,
        audio: AudioSink,
        haptic: HapticSink | None = None,
        safety_hold_s: float = 2.0,
        repeat_cooldown_s: float = 3.0,
        clock: Callable[[], float] = time.monotonic,
        event_sink: Callable[[ArbiterEvent], None] | None = None,
    ) -> None:
        self._audio = audio
        self._haptic = haptic
        self._hold = safety_hold_s
        self._cooldown = repeat_cooldown_s
        self._clock = clock
        self._event_sink = event_sink
        self._lock = threading.RLock()
        self._queue: list[tuple[int, OutputRequest]] = []
        self._seq = 0
        self._current: OutputRequest | None = None
        self._last_safety_t = -1e9
        self._last_played: dict[str, float] = {}
        self._orientation_ok = True
        self.history: deque[ArbiterEvent] = deque(maxlen=200)

    # ------------------------------------------------------------------ state
    def set_orientation_ok(self, ok: bool) -> None:
        with self._lock:
            self._orientation_ok = ok

    @property
    def orientation_ok(self) -> bool:
        return self._orientation_ok

    def safety_hold_active(self, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        with self._lock:
            playing_safety = (
                self._current is not None
                and self._current.priority.is_safety
                and self._audio.busy()
            )
            return playing_safety or (now - self._last_safety_t) < self._hold

    @property
    def current(self) -> OutputRequest | None:
        return self._current

    def pending(self) -> list[str]:
        with self._lock:
            return [r.prompt_id for _, r in sorted(self._queue, key=self._sort_key)]

    def allow_repeat(self, req: OutputRequest) -> None:
        """Let the user explicitly repeat a NAV/INFO prompt inside its cooldown."""
        if not req.priority.is_safety:
            with self._lock:
                self._last_played.pop(req.key, None)

    def discard_navigation(self) -> None:
        """Remove obsolete directions without touching safety requests or hold."""
        with self._lock:
            self._queue = [(seq, req) for seq, req in self._queue if req.priority is not Priority.NAV]
            if self._current is not None and self._current.priority is Priority.NAV:
                self._audio.stop()
                self._log(self._clock(), "cancelled_navigation", self._current)
                self._current = None

    def discard_information(self) -> None:
        """Cancel destination readbacks while retaining every safety output."""
        with self._lock:
            self._queue = [(seq, req) for seq, req in self._queue if req.priority is not Priority.INFO]
            if self._current is not None and self._current.priority is Priority.INFO:
                self._audio.stop()
                self._current = None

    def discard_obsolete_hazards(self, active: set[str]) -> None:
        """Queued range warnings expire when the next fresh frame supersedes them.

        The playing warning and its hold remain intact. Controller-fault warnings
        are deliberately excluded because a clear distance frame is not recovery.
        """
        observed = {"stop_now", "obstacle_front", "obstacle_head", "drop_ahead", "step_up_ahead"}
        with self._lock:
            self._queue = [(seq, req) for seq, req in self._queue
                           if req.prompt_id not in observed or req.prompt_id in active]

    def discard_vision_observations(self, active: set[str]) -> None:
        """Withdraw unsupported camera classifications while retaining the hold.

        These three labels never represent measured distances or a safe path.
        Range hazards and system-fault warnings belong to independent sources.
        """
        observed = {"vision_person", "vision_chair", "vision_bag"}
        with self._lock:
            self._queue = [(seq, req) for seq, req in self._queue
                           if req.prompt_id not in observed or req.prompt_id in active]
            current = self._current
            if current is not None and current.prompt_id in observed and current.prompt_id not in active:
                self._audio.stop()
                self._last_safety_t = self._clock()
                self._log(self._last_safety_t, "expired_observation", current)
                self._current = None

    # ----------------------------------------------------------------- submit
    def submit(self, request: OutputRequest) -> bool:
        now = self._clock()
        req = replace(request, created=now) if not request.created else request
        with self._lock:
            if req.direction is not Direction.NONE and not self._orientation_ok:
                self._log(now, "dropped_orientation", req)
                # tell the user what to do instead of a left/right they cannot trust
                if req.prompt_id != "nav_stop_align":
                    self.submit(OutputRequest("nav_stop_align", Priority.NAV, created=now))
                return False

            cooldown = self.CRITICAL_COOLDOWN_S if req.priority is Priority.CRITICAL else self._cooldown
            last = self._last_played.get(req.key)
            if last is not None and now - last < cooldown:
                self._log(now, "dropped_cooldown", req)
                return False
            if any(r.key == req.key for _, r in self._queue):
                return False

            self._seq += 1
            self._queue.append((self._seq, req))
            self._log(now, "queued", req)

            cur = self._current
            if (
                req.priority.is_safety
                and cur is not None
                and self._audio.busy()
                and req.priority < cur.priority
            ):
                self._audio.stop()
                self._log(now, "preempted", cur)
                # the interrupted prompt is replayed later if it is still fresh
                self._last_played.pop(cur.key, None)
                self._seq += 1
                self._queue.append((self._seq, cur))
                self._current = None
        self.tick()
        return True

    # ------------------------------------------------------------------- tick
    @staticmethod
    def _sort_key(item: tuple[int, OutputRequest]) -> tuple[int, int]:
        seq, req = item
        return (int(req.priority), seq)

    def tick(self) -> OutputRequest | None:
        """Dispatch the next request if the speaker is free. Returns what was played."""
        now = self._clock()
        with self._lock:
            if self._audio.busy():
                return None
            if self._current is not None and self._current.priority.is_safety:
                # hold window counts from the end of the safety clip
                self._last_safety_t = now
            self._current = None
            fresh = []
            for item in self._queue:
                if now - item[1].created > item[1].ttl_s:
                    self._log(now, "expired", item[1])
                else:
                    fresh.append(item)
            self._queue = fresh
            hold = (now - self._last_safety_t) < self._hold
            for item in sorted(self._queue, key=self._sort_key):
                req = item[1]
                if hold and not req.priority.is_safety and req.priority is not Priority.SYSTEM:
                    continue
                if req.direction is not Direction.NONE and not self._orientation_ok:
                    self._queue.remove(item)
                    self._log(now, "dropped_orientation", req)
                    continue
                self._queue.remove(item)
                self._dispatch(now, req)
                return req
            return None

    def _dispatch(self, now: float, req: OutputRequest) -> None:
        self._current = req
        self._last_played[req.key] = now
        if req.priority.is_safety:
            self._last_safety_t = now
        if req.vibration and self._haptic is not None:
            self._haptic(req.vibration)
        self._audio.play([req.prompt_id, *req.extra_clips], req.text)
        self._log(now, "played", req)

    QUIET_ACTIONS = ("queued", "dropped_cooldown")  # too frequent to be useful in history

    def _log(self, now: float, action: str, req: OutputRequest) -> None:
        if action in self.QUIET_ACTIONS:
            return
        event = ArbiterEvent(now, action, req.prompt_id, req.priority.name)
        self.history.append(event)
        if self._event_sink is not None:
            self._event_sink(event)
