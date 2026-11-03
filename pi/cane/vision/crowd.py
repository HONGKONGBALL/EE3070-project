"""Bounded visible crowd evidence; not body heading, passability or people/m²."""
from collections import deque
from functools import wraps
from threading import RLock
import time

import cv2
import numpy as np

from .voice import VOICE

DEFAULTS = dict(enabled=True, interval_s=.75, ground_max_age_s=1.3,
                person_size=640, min_people=8, confidence=.20, min_height=.12, min_median_height=.22,
                link_gap_width=.95, link_gap_height=.15, min_occupancy=.18,
                min_cluster_fill=.48, min_horizontal_coverage=.45,
                min_semantic_person_fraction=.70, max_visible_ground_fraction=.15, semantic_pair_s=.75,
                large_group_median_height=.45, large_group_occupancy=.50, large_group_width=.75,
                large_group_semantic_fraction=.40, large_group_max_ground=.25,
                hits=3, observations=4, window_s=4.5, min_span_s=1.2,
                clear_hits=5, clear_span_s=4.0, missing_reset_s=20.0,
                freshness_s=2.0, event_ttl_s=2.0, retry_s=.8, show_person_boxes=False)


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


def crowd_evidence(detections, cfg, semantic=None):
    """Connected groups and union occupancy prevent a count-only/plaza trigger."""
    boxes = []
    confidences = []
    for d in detections:
        if d['label'] != 'person' or d['confidence'] < cfg['confidence']:
            continue
        box = np.clip(np.asarray(d['bbox_normalized'], dtype=float), 0, 1)
        if not np.isfinite(box).all() or box[2] <= box[0] or box[3] - box[1] < cfg['min_height']:
            continue
        boxes.append(box); confidences.append(float(d['confidence']))
    boxes = np.asarray(boxes[:80]).reshape(-1, 4)
    groups = []
    unseen = set(range(len(boxes)))
    while unseen:
        group = [unseen.pop()]
        cursor = 0
        while cursor < len(group):
            i = group[cursor]; cursor += 1
            a = boxes[i]
            for j in list(unseen):
                b = boxes[j]
                dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
                dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
                # Occlusion/perspective makes neighbouring person boxes very
                # different widths. Use the wider visible person as the gap
                # scale; group fill, semantic support and count still apply.
                if (dx <= cfg['link_gap_width'] * max(a[2]-a[0], b[2]-b[0]) and
                        dy <= cfg['link_gap_height'] * max(a[3]-a[1], b[3]-b[1])):
                    unseen.remove(j); group.append(j)
        groups.append(group)
    candidates = []
    for group in groups:
        selected = boxes[group]
        bounds = [float(selected[:,0].min()), float(selected[:,1].min()),
                  float(selected[:,2].max()), float(selected[:,3].max())]
        mask = np.zeros((120,160), np.uint8)
        for box in selected:
            x1,y1,x2,y2 = np.rint(box * [159,119,159,119]).astype(int)
            mask[y1:y2+1,x1:x2+1] = 1
        occupancy = float(mask.mean())
        fill = min(1., occupancy / max(.0001, (bounds[2]-bounds[0])*(bounds[3]-bounds[1])))
        coverage = float(mask.any(axis=0).mean())
        median = float(np.median(selected[:,3]-selected[:,1]))
        # The bounding rectangle includes gaps, shop fronts and pavement even
        # in a walking crowd. Semantic support is a cross-check, not a demand
        # that almost the entire rectangle be labelled as person. Relax that
        # cross-check only when BOTH the median person size and the group's
        # image occupancy/width are large. A couple of large foreground people
        # cannot relax it for a group of mostly small background pedestrians.
        visible_ground = person_fraction = None
        if semantic is not None:
            classes = semantic['class_map']; sh,sw = classes.shape
            x1,y1,x2,y2=np.rint(np.asarray(bounds)*[sw-1,sh-1,sw-1,sh-1]).astype(int)
            patch=classes[y1:y2+1,x1:x2+1]
            visible_ground=float(np.isin(patch,[3,6,11,28,46,52]).mean())
            person_fraction=float((patch==12).mean())
        large_group = (median >= cfg['large_group_median_height'] and
                       occupancy >= cfg['large_group_occupancy'] and coverage >= cfg['large_group_width'])
        person_min = cfg['large_group_semantic_fraction'] if large_group else cfg['min_semantic_person_fraction']
        ground_max = cfg['large_group_max_ground'] if large_group else cfg['max_visible_ground_fraction']
        reasons = []
        for condition, reason in [
            (len(group) >= cfg['min_people'], 'too_few_concentrated_people'),
            (occupancy >= cfg['min_occupancy'], 'low_visible_occupancy'),
            (fill >= cfg['min_cluster_fill'], 'people_too_spread_out'),
            (coverage >= cfg['min_horizontal_coverage'], 'cluster_covers_little_of_view'),
            (median >= cfg['min_median_height'], 'people_small_in_view'),
            (semantic is not None, 'semantic_context_unavailable'),
            (person_fraction is not None and person_fraction >= person_min, 'visible_people_not_tightly_packed'),
            (visible_ground is not None and visible_ground <= ground_max, 'substantial_ground_visible_between_people'),
        ]:
            if not condition: reasons.append(reason)
        candidates.append({'candidate': not reasons, 'reasons': reasons, 'cluster_count': len(group),
                           'occupancy': round(occupancy,4), 'cluster_fill': round(fill,4),
                           'horizontal_coverage': round(coverage,4), 'median_box_height': round(median,4),
                           'semantic_person_fraction':round(person_fraction,4) if person_fraction is not None else None,
                           'visible_ground_fraction':round(visible_ground,4) if visible_ground is not None else None,
                           'mean_person_confidence': round(float(np.mean([confidences[i] for i in group])),3),
                           'large_group_geometry': bool(large_group),
                           'semantic_person_minimum': person_min, 'visible_ground_maximum': ground_max,
                           'bbox_normalized': bounds})
    best = max(candidates, key=lambda c:(c['candidate'],c['cluster_count'],c['occupancy'])) if candidates else {
        'candidate':False, 'reasons':['no_usable_people'], 'cluster_count':0, 'occupancy':0,
        'cluster_fill':0, 'horizontal_coverage':0, 'median_box_height':0, 'mean_person_confidence':None,
        'bbox_normalized':None}
    return {**best, 'person_count':sum(d['label']=='person' for d in detections),
            'usable_person_count':len(boxes), 'group_count':len(groups),
            'context_available':semantic is not None,
            'basis':'visible box concentration/union area, not measured density, walking direction or passability'}


class CrowdAwareness:
    def __init__(self, run_id, config=None, record=None):
        self.cfg = {**DEFAULTS, **(config or {})}
        c = self.cfg
        if not (.5 <= c['interval_s'] <= 5 and 2 <= c['hits'] <= c['observations'] <= 8
                and 0 < c['min_span_s'] < c['window_s'] <= 10
                and 2 <= c['clear_hits'] <= 8 and 0 < c['clear_span_s'] < c['missing_reset_s'] <= 60):
            raise ValueError('Invalid crowd timing configuration')
        self.run_id, self.record = run_id, record or (lambda *_a, **_k: None)
        self.lock = RLock()
        self.history = deque(maxlen=c['observations']); self.clear = deque(maxlen=8)
        self.last_frame = -1; self.last_capture = -1e9; self.last_positive = -1e9
        self.invalid_before = -1e9; self.sequence = 0; self.episode_id = ''
        self.event_id = ''; self.attempt = 0; self.announced = False; self.completed = False
        self.retry_at = -1e9; self.attempt_capture = -1e9; self.current_positive = False
        self.voice_after_capture = -1e9
        self.evidence = {'candidate':False,'person_count':0,'reasons':['no_person_observation']}
        self.processing_ms = 0.; self.delivery_state = 'none'; self.model_id = ''

    def _reset(self, reason):
        if self.episode_id:
            self.record('crowd_episode_end', episode_id=self.episode_id, reason=reason)
        self.history.clear(); self.clear.clear(); self.episode_id = ''; self.event_id = ''
        self.announced = self.completed = False; self.attempt = 0
        self.attempt_capture = self.retry_at = -1e9; self.delivery_state = 'none'

    @_locked
    def invalidate(self, now):
        self.invalid_before = max(now,self.invalid_before)
        self.history.clear(); self.clear.clear(); self.current_positive = False
        # Short swing/quality loss is not a physical exit and does not reset identity.

    @_locked
    def observe(self, result, now, semantic=None):
        if result is None or result['frame_id'] <= self.last_frame:
            return
        captured = result['captured_at']
        if not result.get('visual_quality',{}).get('usable',True) or not 0 <= now-captured <= self.cfg['freshness_s'] or captured <= self.invalid_before:
            # Consume this rejected result once. Moving the camera invalidation
            # barrier to every render's "now" would reject all later in-flight
            # detections after a short outage, despite their fresh captures.
            self.last_frame=result['frame_id']
            self.history.clear();self.clear.clear();self.current_positive=False
            return
        if (semantic is None or abs(semantic['captured_at']-captured) > self.cfg['semantic_pair_s']
                or not 0 <= now-semantic['captured_at'] <= self.cfg['freshness_s']):
            # The two existing workers publish asynchronously. Briefly waiting
            # for a matching semantic frame is unknown evidence, not a negative
            # observation: keep already admitted history until its normal window
            # expires. Camera/quality faults and stale results still invalidate.
            self.current_positive=False
            self.evidence={'candidate':False,'person_count':len(result['detections']),
                           'context_available':False,'reasons':['awaiting_matching_semantic_observation']}
            return
        self.last_frame = result['frame_id']
        started = time.perf_counter()
        if captured-self.last_capture > max(1.6,self.cfg['interval_s']*1.6):
            self.clear.clear()
        self.last_capture = captured
        self.model_id = result.get('model_id','person-detector')
        self.evidence = crowd_evidence(result['detections'],self.cfg,semantic)
        self.current_positive = self.evidence['candidate']
        if self.current_positive:
            if not self.episode_id:
                self.sequence += 1; self.episode_id = f'{self.run_id}:crowd-{self.sequence}'
                self.record('crowd_episode_start',episode_id=self.episode_id)
            self.last_positive = captured; self.clear.clear()
        elif self.episode_id:
            self.clear.append(captured)
            if len(self.clear) >= self.cfg['clear_hits'] and self.clear[-1]-self.clear[0] >= self.cfg['clear_span_s']:
                self._reset('reliable_non_crowded_observations')
        self.history.append((captured,self.current_positive))
        while self.history and now-self.history[0][0] > self.cfg['window_s']:self.history.popleft()
        self.processing_ms = (time.perf_counter()-started)*1000

    @_locked
    def confirmed(self, now):
        positive = [t for t,yes in self.history if yes and now-t <= self.cfg['window_s']]
        return (self.current_positive and len(positive) >= self.cfg['hits'] and
                positive[-1]-positive[0] >= self.cfg['min_span_s'] and
                self.last_capture > self.invalid_before and 0 <= now-self.last_capture <= self.cfg['freshness_s'])

    @_locked
    def defer_speech(self, now):
        # Voice scheduling only: keep detection evidence, confirmation and episode
        # identity intact, but never replay a request captured before ground speech.
        self.voice_after_capture = max(self.voice_after_capture, now)

    @_locked
    def step(self, now):
        if self.episode_id and now-self.last_positive > self.cfg['missing_reset_s']:
            self._reset('long_unobserved_gap; physical identity unknown')
        if self.event_id and not self.valid(self.event_id,now) and not self.announced:
            self.delivery(self.event_id,'invalid',now)
        if (not self.confirmed(now) or self.announced or self.event_id or now < self.retry_at
                or self.last_capture <= self.attempt_capture or self.last_capture <= self.voice_after_capture):
            return []
        self.attempt += 1; self.attempt_capture = self.last_capture
        self.event_id = f'{self.episode_id}:attempt-{self.attempt}'
        self.delivery_state = 'created'
        phrase,text = VOICE['CROWD']
        return [{'kind':'CROWD','warning_category':'CROWD','warning_family':'crowd',
                 'event_id':self.event_id,'episode_id':self.episode_id,'track_id':self.episode_id,
                 'phrase_id':phrase,'text':text,'priority':'INFO','severity':'SAFETY','created_at':now,
                 'ttl_ms':round(self.cfg['event_ttl_s']*1000),'captured_at':self.last_capture,
                 'frame_id':self.last_frame,'object_class':'crowd','distance_m':None,'source':'vision',
                 'source_model':self.model_id,'evidence':dict(self.evidence),'attempt':self.attempt}]

    @_locked
    def valid(self, event_id, now):
        # One short negative/occlusion is not a proven exit. Keep an already
        # confirmed request only within the bounded last-dense-evidence age.
        # Camera/quality invalidation still cancels immediately.
        return bool(event_id == self.event_id and self.attempt_capture > self.voice_after_capture
                    and self.last_positive > self.invalid_before
                    and 0 <= now-self.last_positive <= self.cfg['freshness_s'])

    @_locked
    def delivery(self, event_id, action, now):
        if not event_id or event_id != self.event_id:return
        if action == 'queued' and self.delivery_state != 'created':return
        self.delivery_state = action
        if action in {'started','completed','disabled'}:
            self.announced = True
            if action in {'completed','disabled'}:self.completed = True
        elif action in {'invalid','expired','rejected','replaced','failed','interrupted'}:
            self.event_id = ''; self.retry_at = now+self.cfg['retry_s']
            if action in {'failed','interrupted'}:self.announced = self.completed

    @_locked
    def status(self, now):
        fresh = self.last_capture > self.invalid_before and 0 <= now-self.last_capture <= self.cfg['freshness_s']
        voice_deferred = self.current_positive and self.last_capture <= self.voice_after_capture
        state = ('UNKNOWN' if not fresh or not self.evidence.get('context_available') else 'ANNOUNCED' if self.announced else 'PENDING_AUDIO' if self.event_id
                 else 'DEFERRED' if voice_deferred else 'CONFIRMED' if self.confirmed(now) else 'CANDIDATE' if self.current_positive else 'NOT_CROWDED')
        return {'enabled':True,'state':state,'evidence':dict(self.evidence),'episode_id':self.episode_id,
                'delivery':self.delivery_state,'processing_ms':round(self.processing_ms,3),
                'frame_id':self.last_frame,'fresh':fresh,'confirmed':self.confirmed(now),
                'voice_after_capture':self.voice_after_capture, 'voice_deferred':voice_deferred,
                'reason':'stale_or_unavailable_person_observation' if not fresh else
                         'same_episode_already_handled' if self.announced else
                         'awaiting_new_crowd_observation_after_ground_speech' if voice_deferred else
                         'awaiting_temporal_confirmation' if self.current_positive and not self.confirmed(now) else
                         'dense_visible_group' if self.current_positive else '; '.join(self.evidence['reasons'])}
