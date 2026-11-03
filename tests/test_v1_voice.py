"""V1 voice contracts through the existing runtime coordinator and real arbiter.

Observations and three-second playback are explicit QA fixtures, not recognition
accuracy or measured physical sound timing.
"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from cane.model import Priority
from cane.vision import v2
from cane.vision.crowd import CrowdAwareness
from cane.vision.speech import Speech
from cane.vision.v2_policy import V2Policy
from cane.vision.voice import VOICE
from test_crowd_awareness import ClockAudio, dense, semantic


GROUND = {'stairs':'semantic', 'pothole':'pothole', 'damaged_pavement':'road', 'possible_puddle':'puddle'}
PHRASES = {'stairs':'stairs_ahead', 'pothole':'damaged_pavement',
           'damaged_pavement':'damaged_pavement', 'possible_puddle':'puddle_ahead'}


class VoiceScenario:
    def __init__(self, monkeypatch, tmp_path):
        self.clock=[100.];self.starts=[];self.logs=[];self.events=[];self.frame=0
        monkeypatch.setattr('cane.vision.speech.time.monotonic',lambda:self.clock[0])
        config=v2.load_config(Path(__file__).parents[1]/'config/camera.v2.toml',mode='awareness')
        self.speech=Speech(tmp_path,False,allowed_phrases={v[0] for v in VOICE.values()},
                           record=lambda kind,**fields:self.logs.append((kind,fields)))
        self.speech.arbiter._clock=lambda:self.clock[0]
        self.speech.sink=ClockAudio(self.speech,self.clock,self.starts)
        self.crowd=CrowdAwareness('v1',config['crowd'])
        self.policy=V2Policy('v1',config['warning']['roi'],{},
                             ground_roi=config['warning']['ground_roi'],warning=config['warning'])
        self.app=SimpleNamespace(awareness_mode=True,sample=None,crowd=self.crowd,
                                 audio=self.speech,policy=self.policy)
        self.speech.on_delivery=lambda eid,action,now:v2.V2._on_delivery(self.app,eid,action,now)
        self.speech.validator=lambda req:v2.V2._request_valid(self.app,req)

    def tick(self, seconds, *, observations=False, label=None, people=False):
        now=self.clock[0]=100+seconds
        if observations:
            self.frame+=1
            self.crowd.observe({'frame_id':self.frame,'captured_at':now,'detections':dense() if people else []},now,semantic(now))
            for task in ('road','pothole','puddle','semantic'):
                detections=[] if GROUND.get(label)!=task else [
                    {'label':label,'raw_label':label,'confidence':.9,'bbox_normalized':[.2,.4,.8,.95]}]
                self.policy.observe_ground(task,{'frame_id':self.frame,'captured_at':now,
                                                'model_id':'QA-admitted-ground','detections':detections},now)
        for event in v2.V2._decision_events(self.app,now):
            self.events.append(event);self.speech.submit(event)
        self.speech.tick()

    def run(self, end, scene):
        for tick in range(round(end*10)+1):
            label,people=scene(tick/10)
            self.tick(tick/10,observations=tick%10==0,label=label,people=people)


@pytest.mark.parametrize('label,phrase',list(PHRASES.items())+[(None,'crowd_ahead')])
def test_each_approved_hazard_alone_plays_once(monkeypatch,tmp_path,label,phrase):
    s=VoiceScenario(monkeypatch,tmp_path)
    s.run(20,lambda t:(label,label is None))
    assert [p for p,_ in s.starts]==[phrase]
    event=s.events[0]
    assert event['text'] in {v[1] for v in VOICE.values()}
    if label is not None:
        assert event['object_class']==label and event['raw_label']==label
        assert event['priority']==('CRITICAL' if label in {'stairs','pothole'} else 'SAFETY')
    else:
        assert event['priority']=='INFO' and event['severity']=='SAFETY'


@pytest.mark.parametrize('label',list(PHRASES))
def test_simultaneous_ground_first_then_fresh_reassessment_not_immediate_crowd(monkeypatch,tmp_path,label):
    s=VoiceScenario(monkeypatch,tmp_path)
    s.run(20,lambda t:(label,True))
    assert [p for p,_ in s.starts]==[PHRASES[label],'crowd_ahead']
    ground_start,crowd_start=s.starts[0][1],s.starts[1][1]
    assert crowd_start>=ground_start+3+2
    crowd_event=next(e for e in s.events if e['kind']=='CROWD')
    assert crowd_event['captured_at']>=ground_start+3+2
    assert crowd_event['created_at']>=ground_start+3+2
    assert len({e['episode_id'] for e in s.events if e['kind']=='CROWD'})==1


@pytest.mark.parametrize('label',list(PHRASES))
def test_crowd_disappearing_during_ground_is_never_backfilled(monkeypatch,tmp_path,label):
    s=VoiceScenario(monkeypatch,tmp_path)
    s.run(16,lambda t:(label,t<3))
    assert [p for p,_ in s.starts]==[PHRASES[label]]


@pytest.mark.parametrize('label',list(PHRASES))
def test_new_ground_interrupts_crowd_using_existing_priority_mechanism(monkeypatch,tmp_path,label):
    s=VoiceScenario(monkeypatch,tmp_path)
    s.run(18,lambda t:(label if t>=3 else None,True))
    assert [p for p,_ in s.starts][:2]==['crowd_ahead',PHRASES[label]]
    interruptions=[fields for kind,fields in s.logs if kind=='audio_interrupted']
    assert any(r['phrase_id']=='crowd_ahead' for r in interruptions)
    assert s.starts[1][1]<s.starts[0][1]+3
    assert [p for p,_ in s.starts].count(PHRASES[label])==1


def test_new_crowd_episode_is_not_permanently_suppressed(monkeypatch,tmp_path):
    s=VoiceScenario(monkeypatch,tmp_path)
    s.run(28,lambda t:('possible_puddle' if t<5 else None,t<4 or t>=16))
    assert [p for p,_ in s.starts]==['puddle_ahead','crowd_ahead']
    assert s.starts[1][1]>=118
    assert len({e['episode_id'] for e in s.events if e['kind']=='CROWD'})==1


def test_queued_crowd_request_cannot_be_freshened_after_deferral(monkeypatch,tmp_path):
    s=VoiceScenario(monkeypatch,tmp_path)
    s.run(2,lambda t:(None,True))
    old=next(e for e in s.events if e['kind']=='CROWD')
    s.crowd.defer_speech(103)
    s.crowd.observe({'frame_id':4,'captured_at':104,'detections':dense()},104,semantic(104))
    assert s.crowd.confirmed(104)
    assert not s.crowd.valid(old['event_id'],104)


def test_completed_crowd_then_ground_then_genuinely_new_crowd_can_speak(monkeypatch,tmp_path):
    s=VoiceScenario(monkeypatch,tmp_path)
    s.run(30,lambda t:('possible_puddle' if 7<=t<12 else None,t<8 or t>=18))
    assert [p for p,_ in s.starts]==['crowd_ahead','puddle_ahead','crowd_ahead']
    crowd_events=[e for e in s.events if e['kind']=='CROWD']
    assert len({e['episode_id'] for e in crowd_events})==2


@pytest.mark.parametrize('phrase',['stairs_ahead','damaged_pavement','puddle_ahead','crowd_ahead'])
def test_expired_approved_request_is_not_played(monkeypatch,tmp_path,phrase):
    s=VoiceScenario(monkeypatch,tmp_path)
    s.speech.submit({'phrase_id':phrase,'priority':'CRITICAL','created_at':90,'ttl_ms':1000})
    assert not s.starts
    assert any(kind=='arbiter' and fields['action']=='expired' for kind,fields in s.logs)


@pytest.mark.parametrize('phrase',[
    'camera_unavailable','visual_unavailable','ground_unavailable','object_unavailable','visual_ready',
    'low_light','blurry_image','hardware_disconnected','battery_low','startup','network_status',
    'chair_ahead','backpack_ahead','bicycle_ahead','person_ahead','pothole_ahead','downstairs_ahead'])
def test_v1_filters_diagnostic_and_individual_object_audio_but_logs_it(monkeypatch,tmp_path,phrase):
    s=VoiceScenario(monkeypatch,tmp_path)
    assert not s.speech.submit({'phrase_id':phrase,'priority':'CRITICAL','event_id':'fault:qa'})
    assert not s.starts and not s.speech.arbiter.pending()
    assert any(kind=='audio_filtered' and fields['phrase_id']==phrase for kind,fields in s.logs)


def test_v1_manual_crowd_test_is_also_low_priority(monkeypatch,tmp_path):
    s=VoiceScenario(monkeypatch,tmp_path)
    s.speech.submit({'phrase_id':'crowd_ahead','priority':'CRITICAL'})
    assert s.speech.arbiter.current.priority is Priority.INFO


@pytest.mark.parametrize('mode',['ground','balanced','awareness'])
def test_voice_filter_is_wired_only_in_awareness_mode(monkeypatch,tmp_path,mode):
    config=v2.load_config(Path(__file__).parents[1]/'config/camera.v2.toml',mode=mode)
    config['audio']['enabled']=False
    monkeypatch.setattr(v2,'ROOT',tmp_path)
    app=v2.V2(config)
    try:
        assert app.audio.allowed_phrases==({v[0] for v in VOICE.values()} if mode=='awareness' else None)
        if mode!='awareness':
            app.audio.validator=lambda req:True
            assert app.audio.submit({'phrase_id':'camera_unavailable','priority':'CRITICAL'})
    finally:
        app.audio.close();app.logfile.close()
