"""Bounded, time-aware front-path hazard episodes; no map or physical depth inference."""

from collections import deque
from dataclasses import dataclass, field
from threading import RLock


FAMILIES = {"possible_puddle": "wet_surface", "damaged_pavement": "damaged_pavement",
            "pothole": "damaged_pavement", "stairs": "stairs", "downstairs": "stairs"}
TASK_LABELS = {"road": {"damaged_pavement"}, "pothole": {"pothole"},
               "puddle": {"possible_puddle"}, "semantic": {"stairs"}, "stairs": {"downstairs"}}
PHRASES = {
    "possible_puddle": ("puddle_ahead", "Possible wet pavement ahead."),
    "damaged_pavement": ("damaged_pavement", "Possible damaged pavement ahead."),
    "pothole": ("pothole_ahead", "Possible pothole ahead."),
    "stairs": ("stairs_ahead", "Possible stairs ahead."),
    "downstairs": ("downstairs_ahead", "Possible steps down ahead. Watch your step."),
}
CONFIRM = {"hits": 2, "observations": 3, "window_s": 3.2, "min_span_s": .3}
EPISODE = {"clear_hits": 3, "clear_span_s": 1.5, "missing_reset_s": 6.0,
           "freshness_s": 1.5, "retry_s": .8, "event_ttl_s": 1.5}


@dataclass
class Signal:
    task: str
    history: deque
    last_frame: int = -1
    last_capture: float = -1e9
    positive: bool = False
    reliable_negative: bool = False
    latest: dict = field(default_factory=dict)
    model: dict = field(default_factory=dict)
    ever_positive: bool = False
    first_candidate_at: float | None = None


@dataclass
class Episode:
    family: str
    episode_id: str
    state: str = "UNSEEN"
    signals: dict = field(default_factory=dict)
    clear_observations: deque = field(default_factory=lambda: deque(maxlen=8))
    first_capture: float | None = None
    first_observed: float | None = None
    last_positive: float = -1e9
    confirmed_at: float | None = None
    announced_rank: int = 0
    completed_rank: int = 0
    pending_rank: int = 0
    event_id: str = ""
    event_label: str = ""
    attempt: int = 0
    last_attempt_capture: float = -1e9
    retry_at: float = -1e9
    delivery: str = "none"
    clear_reason: str = ""


class HazardEpisodes:
    """One temporal episode per family in the configured front path, not per box.

    Per-label K-of-N evidence never mixes two models into two independent hits.
    All scene verification and path admission happen before this manager.
    """

    def __init__(self, run_id, config=None, record=None):
        self.run_id, self.config = run_id, config or {}
        self.record = record or (lambda *_a, **_k: None)
        self.lock = RLock()
        self.sequence = {family: 0 for family in set(FAMILIES.values())}
        self.current = {family: Episode(family, "") for family in self.sequence}
        self.seen = {}
        self.invalid_before = -1e9
        self.counters = {key: 0 for key in ("episodes", "confirmed", "events", "retries", "upgrades", "cleared", "skipped", "started", "disabled")}
        self._validate()

    def confirmation(self, label):
        return {**CONFIRM, **({"observations":2} if label=="pothole" else {}),
                **self.config.get("confirmation", {}).get(label, {})}

    def settings(self, family):
        return {**EPISODE, **({"clear_hits":6,"clear_span_s":5.0,"missing_reset_s":10.0} if family=="wet_surface"
                   else {"clear_hits":4,"clear_span_s":3.0} if family=="stairs" else {}),
                **self.config.get("episodes", {}).get(family, {})}

    def phrase(self, label):
        value = self.config.get("phrases", {}).get(label, {})
        default = PHRASES[label]
        return value.get("id", default[0]), value.get("text", default[1])

    def _validate(self):
        for label in FAMILIES:
            c = self.confirmation(label)
            if not (2 <= c["hits"] <= c["observations"] <= 8 and .1 <= c["min_span_s"] < c["window_s"] <= 8):
                raise ValueError("Invalid hazard confirmation settings: " + label)
        for family in self.current:
            c = self.settings(family)
            if not (2 <= c["clear_hits"] <= 8 and 0 < c["clear_span_s"] < c["missing_reset_s"] <= 30
                    and 0 < c["freshness_s"] <= 1.5 and c["retry_s"] > 0 and 0 < c["event_ttl_s"] <= 3):
                raise ValueError("Invalid hazard episode settings: " + family)

    def _new(self, family, captured, now):
        self.sequence[family] += 1
        e = Episode(family, f"{self.run_id}:{family}-{self.sequence[family]}", "CANDIDATE",
                    first_capture=captured, first_observed=now)
        self.current[family] = e
        self.counters["episodes"] += 1
        self.record("hazard_episode_start", episode_id=e.episode_id, warning_family=family,
                    captured_at=captured, observed_at=now, basis="temporal evidence in front image path; not physical object identity")
        return e

    def _clear(self, e, now, reason):
        if e.state in {"UNSEEN", "CLEAR"}: return
        self.record("hazard_episode_end", **self._summary(e, now), ended_at=now, reset_reason=reason)
        e.state, e.clear_reason, e.event_id, e.pending_rank = "CLEAR", reason, "", 0
        self.counters["cleared"] += 1

    def observe(self, task, result, now, *, negative_reliable=True):
        with self.lock:
            if result is None or not result.get("visual_quality", {}).get("usable", True): return
            captured, frame = result["captured_at"], result["frame_id"]
            if captured <= self.invalid_before or not 0 <= now-captured <= 1.5: return
            if (captured, frame) <= self.seen.get(task, (-1e9, -1)): return
            self.seen[task] = (captured, frame)
            positives = {}
            for d in result["detections"]:
                if d["label"] in FAMILIES:
                    old = positives.get(d["label"])
                    if old is None or d["confidence"] > old["confidence"]: positives[d["label"]] = d
            labels = set(TASK_LABELS.get(task, ())) | set(positives)
            labels |= {label for e in self.current.values() for label,s in e.signals.items() if s.task == task}
            touched = set()
            for label in labels:
                family = FAMILIES[label]; e = self.current[family]; d = positives.get(label)
                if d is not None and e.state in {"UNSEEN", "CLEAR"}: e = self._new(family, captured, now)
                if e.state in {"UNSEEN", "CLEAR"}: continue
                c = self.confirmation(label)
                s = e.signals.setdefault(label, Signal(task, deque(maxlen=c["observations"])))
                if captured <= s.last_capture: continue
                s.task, s.last_capture, s.last_frame = task, captured, frame
                s.positive, s.reliable_negative = d is not None, bool(d is None and negative_reliable)
                # Unreliable context losses are missing evidence, never clear votes.
                if d is not None or negative_reliable:
                    s.history.append((captured, d is not None, frame,
                                      d.get("confidence", 0) if d else 0,
                                      d.get("bbox_normalized", [0,0,0,0]) if d else [0,0,0,0]))
                self._prune(s, c, now)
                if d is not None:
                    s.ever_positive = True
                    s.latest = {**d, "captured_at": captured, "frame_id": frame,
                                "candidate_observed_at": d.get("candidate_observed_at", result.get("candidates_observed_at", now)),
                                "verification_completed_at": d.get("verification_completed_at", result.get("verification_completed_at", now))}
                    s.model = {"model_id": result.get("model_id", task), "model_revision": result.get("model_revision")}
                    if s.first_candidate_at is None: s.first_candidate_at = s.latest["candidate_observed_at"]
                    e.last_positive = max(e.last_positive, captured)
                    e.clear_observations.clear()
                    if e.state == "ANNOUNCED": e.state = "ACTIVE"
                touched.add(family)
            for family in touched:
                e = self.current[family]; c = self.settings(family)
                signals = [s for s in e.signals.values() if s.ever_positive]
                if signals and all(s.reliable_negative and 0 <= now-s.last_capture <= c["freshness_s"] for s in signals):
                    if not e.clear_observations or captured > e.clear_observations[-1]: e.clear_observations.append(captured)
                    while e.clear_observations and now-e.clear_observations[0] > c["missing_reset_s"]: e.clear_observations.popleft()
                    if (len(e.clear_observations) >= c["clear_hits"] and
                            e.clear_observations[-1]-e.clear_observations[0] >= c["clear_span_s"]):
                        self._clear(e, now, "reliable_clear_observations")

    @staticmethod
    def _prune(signal, config, now):
        while signal.history and now-signal.history[0][0] > config["window_s"]: signal.history.popleft()

    def _confirmed(self, label, s, now):
        c = self.confirmation(label); self._prune(s, c, now)
        positives = [o for o in s.history if o[1]]
        return (s.positive and len(positives) >= c["hits"] and
                positives[-1][0]-positives[0][0] >= c["min_span_s"] and
                s.last_capture > self.invalid_before and 0 <= now-s.last_capture <= self.settings(FAMILIES[label])["freshness_s"])

    def _rank(self, label, s):
        if label == "downstairs": return 4
        if label == "stairs": return 3
        if label == "pothole":
            c = self.config.get("pothole_urgency", {})
            strong = [o for o in s.history if o[1] and o[3] >= c.get("confidence", .75)
                      and max(0,o[4][2]-o[4][0])*max(0,o[4][3]-o[4][1]) >= c.get("image_area", .01)]
            if len(strong) >= self.confirmation(label)["hits"]: return 4
        return 1

    def step(self, now):
        with self.lock:
            proposals = []
            for e in self.current.values():
                if e.state in {"UNSEEN", "CLEAR"}: continue
                c = self.settings(e.family)
                if now-e.last_positive > c["missing_reset_s"]:
                    self._clear(e, now, "unobserved_timeout; physical exit unknown"); continue
                if e.event_id and not self.valid(e.event_id, now) and e.announced_rank < e.pending_rank:
                    self.delivery(e.event_id, "invalid", now)
                options = [(self._rank(label,s),label,s) for label,s in e.signals.items() if self._confirmed(label,s,now)]
                if not options: continue
                rank,label,s = max(options, key=lambda x:(x[0], x[2].latest["confidence"]))
                if e.confirmed_at is None:
                    e.confirmed_at=now; e.state="CONFIRMED"; self.counters["confirmed"]+=1
                    self.record("hazard_episode_confirmed", episode_id=e.episode_id, warning_family=e.family, confirmed_at=now)
                if e.announced_rank >= rank or (e.event_id and e.pending_rank >= rank): continue
                if now < e.retry_at or s.last_capture <= e.last_attempt_capture: continue
                previous_rank = max(e.announced_rank,e.pending_rank)
                if e.attempt: self.counters["upgrades" if rank>previous_rank and previous_rank else "retries"] += 1
                e.attempt+=1; e.event_id=f"{e.episode_id}:attempt-{e.attempt}"
                e.event_label,e.pending_rank,e.delivery = label,rank,"created"
                e.last_attempt_capture=s.last_capture
                spoken_label = "damaged_pavement" if e.family=="damaged_pavement" and rank==1 else label
                phrase,text = self.phrase(spoken_label)
                priority = "CRITICAL" if rank>=3 else "SAFETY"
                if label=="stairs": priority=self.config.get("stairs_priority","CRITICAL")
                positive_times=[o[0] for o in s.history if o[1]]
                d=s.latest
                event={"schema_version":"2.1","event_id":e.event_id,"kind":"GROUND_HAZARD",
                    "episode_id":e.episode_id,"warning_family":e.family,"phrase_id":phrase,"text":text,
                    "priority":priority,"created_at":now,"confirmed_at":now,"episode_confirmed_at":e.confirmed_at,
                    "ttl_ms":round(c["event_ttl_s"]*1000),"object_class":label,"track_id":e.episode_id,
                    "frame_id":d["frame_id"],"captured_at":d["captured_at"],"confidence":d["confidence"],
                    "bbox_normalized":d["bbox_normalized"],"raw_label":d.get("raw_label",label),
                    "source_model":s.model["model_id"],"source_revision":s.model["model_revision"],
                    "source":"vision","distance_m":None,"first_visible_captured_at":positive_times[0],
                    "first_observed_at":e.first_observed,"first_positive_candidate_at":s.first_candidate_at,
                    "scene_verification_completed_at":d["verification_completed_at"],
                    "confirmation_latency_ms":round((now-positive_times[0])*1000,1),
                    "capture_to_alert_ms":round((now-d["captured_at"])*1000,1),
                    "evidence_labels":[k for k,v in e.signals.items() if self._confirmed(k,v,now)],
                    "evidence":d.get("evidence"),"attempt":e.attempt,"upgrade":bool(previous_rank and rank>previous_rank),
                    "confirmation":{"positive_observations":len(positive_times),"valid_observations":len(s.history),
                        **self.confirmation(label),"basis":"independent captured frames; low-quality/missing excluded; not physical distance"}}
                self.counters["events"]+=1;proposals.append((rank,event))
            return [event for _rank,event in sorted(proposals,key=lambda x:-x[0])]

    def valid(self, event_id, now):
        with self.lock:
            for e in self.current.values():
                if e.event_id!=event_id or e.state in {"UNSEEN","CLEAR"}: continue
                s=e.signals[e.event_label]
                return bool(s.positive and s.last_capture>self.invalid_before and
                            0<=now-s.last_capture<=self.settings(e.family)["freshness_s"])
            return False

    def delivery(self, event_id, action, now):
        with self.lock:
            for e in self.current.values():
                if e.event_id!=event_id: continue
                if action in {"started","disabled","completed"}:
                    e.announced_rank=max(e.announced_rank,e.pending_rank);e.delivery=action;e.state="ANNOUNCED"
                    if action in {"completed", "disabled"}: e.completed_rank=max(e.completed_rank,e.pending_rank)
                    if action in self.counters:self.counters[action]+=1
                elif action in {"expired","invalid","rejected","replaced","interrupted","failed"}:
                    if action in {"failed","interrupted"}:e.announced_rank=e.completed_rank
                    e.delivery=action;e.pending_rank=0;e.event_id=""
                    e.retry_at=now+self.settings(e.family)["retry_s"];self.counters["skipped"]+=1
                elif action=="queued" and e.delivery=="created":e.delivery="queued"
                return

    def suspend(self, now):
        """Invalidate stale speech immediately, but retain identity across short loss."""
        with self.lock:self.invalid_before=max(self.invalid_before,now)

    def reason(self, task, label, now):
        with self.lock:
            e=self.current[FAMILIES[label]];s=e.signals.get(label)
            if s is None or not self._confirmed(label,s,now):return "awaiting_confirmation"
            if e.announced_rank>=self._rank(label,s):return "episode_announced"
            if e.event_id:return "episode_pending_audio"
            if now<e.retry_at:return "episode_retry_wait"
            return "eligible"

    def _summary(self,e,now):
        return {"episode_id":e.episode_id,"warning_family":e.family,"state":e.state,
                "delivery_status":e.delivery,"confirmed_at":e.confirmed_at,"attempts":e.attempt,
                "last_positive_age_ms":round((now-e.last_positive)*1000) if e.last_positive>-1e9 else None,
                "labels_seen":[k for k,s in e.signals.items() if s.ever_positive],
                "clear_reason":e.clear_reason,"currently_relevant":bool(e.event_id and self.valid(e.event_id,now))}

    def status(self,now):
        with self.lock:return {family:self._summary(e,now) for family,e in self.current.items()}
