"""Crowd geometry/episodes and real warning-queue contracts, not field accuracy."""
from types import SimpleNamespace
import numpy as np
import pytest

from cane.vision.crowd import CrowdAwareness, crowd_evidence, DEFAULTS
from cane.vision.episodes import HazardEpisodes
from cane.vision.speech import Speech
from cane.vision.voice import VOICE


def dense(dx=0):
    return [{'label':'person','confidence':.9,'bbox_normalized':[.02+i*.095+dx,.25,.24+i*.095+dx,.95]}
            for i in range(8)]


def semantic(at, floor=False):
    return {'captured_at':at,'class_map':np.full((120,160),3 if floor else 12,np.uint8)}


def observe(manager, frame, at, detections=None, quality=True, floor=False):
    manager.observe({'frame_id':frame,'captured_at':at,'model_id':'qa-person',
                     'detections':dense() if detections is None else detections,
                     'visual_quality':{'usable':quality}},at,semantic(at,floor))
    return manager.step(at)


def test_dense_group_needs_geometry_and_semantic_packing_not_count():
    assert crowd_evidence(dense(),DEFAULTS,semantic(1))['candidate']
    spread=[{'label':'person','confidence':.9,'bbox_normalized':[.03+i*.12,.3,.09+i*.12,.8]} for i in range(8)]
    assert not crowd_evidence(spread,DEFAULTS,semantic(1))['candidate']
    assert not crowd_evidence(dense(),DEFAULTS,semantic(1,True))['candidate']
    assert not crowd_evidence(dense()[:2],DEFAULTS,semantic(1))['candidate']
    # Extra non-person boxes cannot turn sparse pedestrians into a crowd.
    assert not crowd_evidence(dense()[:2]+[{'label':'chair','confidence':.99}]*30,DEFAULTS,semantic(1))['candidate']


def test_low_confidence_small_or_unpaired_observations_are_not_dense_evidence():
    weak=[{**d,'confidence':.1} for d in dense()]
    tiny=[{**d,'bbox_normalized':[d['bbox_normalized'][0],.4,d['bbox_normalized'][2],.46]} for d in dense()]
    assert not crowd_evidence(weak,DEFAULTS,semantic(1))['candidate']
    assert not crowd_evidence(tiny,DEFAULTS,semantic(1))['candidate']
    m=CrowdAwareness('qa')
    result={'frame_id':1,'captured_at':1,'detections':dense()}
    m.observe(result,1,semantic(.1));assert not m.step(1)
    assert m.status(1)['state']=='UNKNOWN'
    # A matching semantic frame may subsequently admit that person frame once.
    m.observe(result,1,semantic(1));m.observe(result,1,semantic(1))
    assert len(m.history)==1


def test_adjacent_rows_form_one_eight_person_group_without_lowering_count():
    group=[{'label':'person','confidence':.9,'bbox_normalized':[x,.4,x+w,.95]}
           for x,w in [(.02,.1),(.09,.1),(.16,.1),(.23,.1),
                       (.46,.2),(.53,.2),(.60,.2),(.67,.2)]]
    classes=np.full((120,160),12,np.uint8)
    classes[:,np.arange(160)%10<2]=1
    sem={'class_map':classes}
    joined=crowd_evidence(group,DEFAULTS,sem)
    assert joined['candidate'] and joined['cluster_count']==8
    assert not crowd_evidence(group[:-1],DEFAULTS,sem)['candidate']
    distant=[{**d,'bbox_normalized':[v+(.2 if i%2==0 else 0) for i,v in enumerate(d['bbox_normalized'])]}
             if j>=4 else d for j,d in enumerate(group)]
    assert not crowd_evidence(distant,DEFAULTS,sem)['candidate']


def test_partly_occluded_people_still_need_eight_and_ground_support():
    group=[{**d,'confidence':.22} for d in dense()]
    assert crowd_evidence(group,DEFAULTS,semantic(1))['candidate']
    assert not crowd_evidence(group[:7],DEFAULTS,semantic(1))['candidate']
    assert not crowd_evidence(group,DEFAULTS,semantic(1,True))['candidate']


def test_broad_close_group_allows_some_floor_but_not_large_ground_gaps():
    classes=np.full((120,160),12,np.uint8)
    classes[:,np.arange(160)%9<2]=3
    evidence=crowd_evidence(dense(),DEFAULTS,{'class_map':classes})
    assert evidence['large_group_geometry'] and evidence['candidate']
    assert .20 < evidence['visible_ground_fraction'] < .25
    classes[:,np.arange(160)%9<3]=3
    rejected=crowd_evidence(dense(),DEFAULTS,{'class_map':classes})
    assert not rejected['candidate']
    assert 'substantial_ground_visible_between_people' in rejected['reasons']


def test_walking_group_can_include_background_but_still_needs_broad_coverage():
    # Explicit semantic QA masks: this tests the policy, not model accuracy.
    classes=np.full((120,160),12,np.uint8)
    classes[30:120:2,:]=1  # background inside the group, not visible pavement
    result=crowd_evidence(dense(),DEFAULTS,{'class_map':classes})
    assert .40 < result['semantic_person_fraction'] < .75
    assert result['candidate']
    narrow=[{**d,'bbox_normalized':[d['bbox_normalized'][0]*.6,.25,
                                    d['bbox_normalized'][2]*.6,.95]} for d in dense()]
    rejected=crowd_evidence(narrow,DEFAULTS,semantic(1))
    # A narrow group still passes with solid semantic support; with substantial
    # background it cannot use the relaxed rule intended for broad large groups.
    assert rejected['candidate'] and not rejected['large_group_geometry']
    rejected=crowd_evidence(narrow,DEFAULTS,{'class_map':classes})
    assert not rejected['candidate']
    assert 'visible_people_not_tightly_packed' in rejected['reasons']


def test_large_foreground_people_cannot_relax_a_mostly_smaller_queue():
    classes=np.full((120,160),12,np.uint8)
    classes[::2,:]=1
    moderate=[{**d,'bbox_normalized':[d['bbox_normalized'][0],.55,
                                      d['bbox_normalized'][2],.95]} for d in dense()]
    evidence=crowd_evidence(moderate,DEFAULTS,{'class_map':classes})
    assert not evidence['candidate'] and not evidence['large_group_geometry']
    assert evidence['semantic_person_minimum']==.70
    assert crowd_evidence(moderate,DEFAULTS,semantic(1))['candidate']
    large=crowd_evidence(dense(),DEFAULTS,{'class_map':classes})
    assert large['large_group_geometry'] and large['candidate']
    assert large['semantic_person_minimum']==.40


def test_background_and_large_ground_gaps_are_still_rejected():
    assert not crowd_evidence(dense(),DEFAULTS,{'class_map':np.full((120,160),1,np.uint8)})['candidate']
    classes=np.full((120,160),12,np.uint8)
    classes[60:100,:]=3
    evidence=crowd_evidence(dense(),DEFAULTS,{'class_map':classes})
    assert not evidence['candidate']
    assert 'substantial_ground_visible_between_people' in evidence['reasons']


def test_distinct_observations_confirm_and_box_churn_does_not_repeat():
    m=CrowdAwareness('qa')
    assert not observe(m,1,1)
    for _ in range(10):assert not observe(m,1,1)
    assert not observe(m,2,2)
    event=observe(m,3,3)[0]
    assert event['kind']=='CROWD' and event['priority']=='INFO' and event['distance_m'] is None
    assert event['severity']=='SAFETY'
    assert (event['phrase_id'],event['text'])==VOICE['CROWD']
    m.delivery(event['event_id'],'completed',3.1)
    for frame in range(4,25):
        assert not observe(m,frame,float(frame),dense(.01 if frame%2 else -.01))
    assert m.episode_id==event['episode_id']


def test_waiting_for_matching_semantics_does_not_erase_admitted_positive_history():
    m=CrowdAwareness('qa')
    assert not observe(m,1,1)
    assert not observe(m,2,2)
    pending={'frame_id':3,'captured_at':3,'model_id':'qa-person','detections':dense()}
    m.observe(pending,3,semantic(1.9))
    assert not m.step(3) and len(m.history)==2
    assert m.status(3)['state']=='UNKNOWN'
    m.observe(pending,3.1,semantic(3))
    events=m.step(3.1)
    assert len(events)==1 and events[0]['kind']=='CROWD'
    assert len(m.history)==3


def test_short_swing_quality_loss_does_not_create_a_new_crowd_episode():
    m=CrowdAwareness('qa')
    for frame in range(1,4):events=observe(m,frame,float(frame))
    event=events[0];m.delivery(event['event_id'],'completed',3)
    m.invalidate(4)
    assert not m.valid(event['event_id'],4)
    for frame in range(5,9):assert not observe(m,frame,float(frame))
    assert m.episode_id==event['episode_id']


def test_confirmed_queue_tolerates_one_short_negative_but_never_old_or_bad_camera_evidence():
    m=CrowdAwareness('qa')
    for frame in range(1,4):events=observe(m,frame,float(frame))
    event=events[0]
    observe(m,4,4,[])
    assert m.valid(event['event_id'],4.4)
    assert not m.valid(event['event_id'],5.1)
    m.invalidate(4.5)
    assert not m.valid(event['event_id'],4.5)


def test_old_inflight_results_after_outage_do_not_chase_the_invalidation_barrier():
    m=CrowdAwareness('qa')
    for frame in range(1,4):events=observe(m,frame,float(frame))
    m.delivery(events[0]['event_id'],'completed',3)
    m.invalidate(10)
    old={'frame_id':4,'captured_at':9.9,'detections':dense()}
    m.observe(old,10.1,semantic(9.9));m.observe(old,10.2,semantic(9.9))
    fresh={'frame_id':5,'captured_at':10.15,'detections':dense()}
    m.observe(fresh,10.3,semantic(10.15))
    assert m.invalid_before==10 and m.last_capture==10.15 and m.last_frame==5
    assert not m.step(10.3) # previous announcement identity survives


def test_reliable_clear_rearms_but_missing_frames_are_not_clear_votes():
    m=CrowdAwareness('qa')
    for frame in range(1,4):events=observe(m,frame,float(frame))
    first=events[0];m.delivery(first['event_id'],'completed',3)
    for frame in [4,5]:assert not observe(m,frame,float(frame),[],quality=False)
    assert m.episode_id==first['episode_id']
    for frame in range(6,11):assert not observe(m,frame,float(frame),[])
    assert not m.episode_id
    for frame in range(11,14):events=observe(m,frame,float(frame))
    assert events[0]['episode_id']!=first['episode_id']


def test_missing_gap_breaks_clear_sequence_and_timeout_is_explicit():
    records=[];m=CrowdAwareness('qa',record=lambda kind,**kw:records.append((kind,kw)))
    for frame in range(1,4):events=observe(m,frame,float(frame))
    m.delivery(events[0]['event_id'],'completed',3)
    for frame,at in [(4,4),(5,5),(6,9),(7,10),(8,11)]:observe(m,frame,at,[])
    assert m.episode_id
    m.step(25)
    assert not m.episode_id
    assert any('physical identity unknown' in f.get('reason','') for _,f in records)


@pytest.mark.parametrize('label,task,category',[
    ('pothole','pothole','DAMAGED_PAVEMENT'),('damaged_pavement','road','DAMAGED_PAVEMENT'),
    ('possible_puddle','puddle','PUDDLE'),('stairs','semantic','STAIRS'),('downstairs','stairs','STAIRS')])
def test_exact_approved_voice_retains_original_detection_and_risk(label,task,category):
    manager=HazardEpisodes('qa')
    for frame,at in [(1,1),(2,2)]:
        manager.observe(task,{'frame_id':frame,'captured_at':at,'model_id':'original-model',
                            'detections':[{'label':label,'confidence':.9,'raw_label':'original raw class',
                                           'bbox_normalized':[.2,.4,.8,.95]}]},at)
    event=manager.step(2)[0]
    assert (event['phrase_id'],event['text'])==VOICE[category]
    assert event['object_class']==label and event['raw_label']=='original raw class'
    assert event['warning_category']==category and event['source_model']=='original-model'
    assert event['priority']==('CRITICAL' if label in {'pothole','stairs','downstairs'} else 'SAFETY')


def test_two_distinct_damage_episodes_can_speak_again():
    m=HazardEpisodes('qa');events=[]
    def put(frame,at,label):
        m.observe('pothole',{'frame_id':frame,'captured_at':at,'detections':[] if label is None else [
            {'label':label,'confidence':.9,'bbox_normalized':[.2,.4,.8,.95]}]},at)
        return m.step(at)
    put(1,1,'pothole');first=put(2,2,'pothole')[0];m.delivery(first['event_id'],'completed',2)
    for frame,at in [(3,3),(4,4),(5,5)]:assert not put(frame,at,None)
    put(6,6,'pothole');second=put(7,7,'pothole')[0]
    assert first['episode_id']!=second['episode_id']
    assert first['phrase_id']==second['phrase_id']=='damaged_pavement'


def test_nearby_pothole_and_crack_one_warning_preserve_both_source_labels():
    m=HazardEpisodes('qa')
    for frame,at in [(1,1),(2,2)]:
        for task,label in [('road','damaged_pavement'),('pothole','pothole')]:
            m.observe(task,{'frame_id':frame,'captured_at':at,'model_id':task,'detections':[
                {'label':label,'confidence':.9,'bbox_normalized':[.2,.4,.8,.95]}]},at)
    events=m.step(2)
    assert len(events)==1 and events[0]['phrase_id']=='damaged_pavement'
    assert set(events[0]['evidence_labels'])=={'damaged_pavement','pothole'}
    assert events[0]['object_class']=='pothole' and events[0]['priority']=='CRITICAL'


@pytest.mark.parametrize('awareness,expected',[(True,'crowd_ahead'),(False,'chair_ahead')])
def test_voice_test_button_never_announces_individual_objects_in_awareness(awareness,expected):
    import threading,urllib.request
    from http.server import ThreadingHTTPServer
    from cane.vision.demo import handler_for
    sent=[]
    app=SimpleNamespace(awareness_mode=awareness,audio=SimpleNamespace(state='ready',submit=sent.append))
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(app))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{server.server_port}/test-audio',data=b'{}')) as response:
            assert response.status==200
        assert [e['phrase_id'] for e in sent]==[expected]
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


class ClockAudio:
    """Explicit synthetic playback durations; no hardware/process timing claim."""
    enabled=True;target='qa';state='ready';error='';last_played=None;playback_id='qa'
    def __init__(self,speech,clock,records):self.speech,self.clock,self.records=speech,clock,records;self.active=None
    def play(self,clips,text=None,context=None):
        self.stop();self.active=(clips[0],dict(context or {}),self.clock[0]+3)
        self.records.append((clips[0],self.clock[0]))
        self.speech._record_audio('audio_start',phrase_id=clips[0],**self.active[1])
    def busy(self):
        if self.active and self.clock[0]>=self.active[2]:
            phrase,context,_=self.active;self.active=None
            self.speech._record_audio('audio_done',phrase_id=phrase,**context)
        return self.active is not None
    def stop(self):
        if self.active:
            phrase,context,_=self.active;self.active=None
            self.speech._record_audio('audio_interrupted',phrase_id=phrase,**context)


@pytest.mark.parametrize('label,task',[('stairs','semantic'),('possible_puddle','puddle'),('damaged_pavement','road'),('pothole','pothole')])
def test_persistent_ground_and_crowd_both_play_with_real_queue(monkeypatch,tmp_path,label,task):
    from cane.vision.v2 import V2
    clock=[1.];records=[];crowd=CrowdAwareness('qa');ground=HazardEpisodes('qa')
    monkeypatch.setattr('cane.vision.speech.time.monotonic',lambda:clock[0])
    speech=Speech(tmp_path,False);speech.arbiter._clock=lambda:clock[0]
    speech.sink=ClockAudio(speech,clock,records)
    speech.on_delivery=lambda eid,action,now: (crowd if ':crowd-' in eid else ground).delivery(eid,action,now)
    speech.validator=lambda req: (crowd if ':crowd-' in req.event_id else ground).valid(req.event_id,clock[0])
    app=SimpleNamespace(awareness_mode=True,sample=None,crowd=crowd,audio=speech,
                        policy=SimpleNamespace(step=lambda _sample,now:ground.step(now)))
    # Deliberately observe both families before dispatch, then maintain fresh evidence.
    for tick in range(10,141):
        clock[0]=tick/10
        if tick%10==0:
            frame=tick//10
            crowd.observe({'frame_id':frame,'captured_at':clock[0],'detections':dense()},clock[0],semantic(clock[0]))
            ground.observe(task,{'frame_id':frame,'captured_at':clock[0],'detections':[
                {'label':label,'confidence':.9,'bbox_normalized':[.2,.4,.8,.95]}]},clock[0])
        if tick>=30:
            for event in V2._decision_events(app,clock[0]):speech.submit(event)
        speech.tick()
    assert [p for p,_ in records]==[VOICE[{'stairs':'STAIRS','possible_puddle':'PUDDLE','damaged_pavement':'DAMAGED_PAVEMENT','pothole':'DAMAGED_PAVEMENT'}[label]][0],'crowd_ahead']
    assert records[1][1]-records[0][1]>=5 # 3-second clip plus existing 2-second safety hold
    assert crowd.delivery_state=='completed'


def test_stale_crowd_is_not_played_after_ground_finishes(monkeypatch,tmp_path):
    clock=[3.];records=[];m=CrowdAwareness('qa')
    for frame in range(1,4):events=observe(m,frame,float(frame))
    monkeypatch.setattr('cane.vision.speech.time.monotonic',lambda:clock[0])
    speech=Speech(tmp_path,False);speech.arbiter._clock=lambda:clock[0]
    speech.sink=ClockAudio(speech,clock,records)
    speech.on_delivery=m.delivery
    speech.validator=lambda req: req.event_id=='fault:qa' or m.valid(req.event_id,clock[0])
    speech.submit({'phrase_id':'stairs_ahead','priority':'CRITICAL','event_id':'fault:qa','ttl_ms':5000})
    speech.submit(events[0]);clock[0]=6.1;speech.tick()
    assert [p for p,_ in records]==['stairs_ahead']
    assert not speech.arbiter.pending()


def test_awareness_never_uses_pico_distance_for_individual_object_speech():
    from cane.vision.v2 import V2
    calls=[]
    app=SimpleNamespace(awareness_mode=True,sample=object(),crowd=None,
                        policy=SimpleNamespace(step=lambda sample,now:calls.append(sample) or []))
    assert V2._decision_events(app,1)==[] and calls==[None]
