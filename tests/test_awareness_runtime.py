"""Existing runtime threads with explicit QA model/camera outputs, not recognition."""
import json
from pathlib import Path
import time

import numpy as np
import pytest

from cane.vision import v2
from cane.vision.camera import Camera
from test_crowd_awareness import dense


@pytest.mark.parametrize('enabled,person_failure',[(True,False),(False,False),(True,True)])
def test_awareness_one_shared_camera_person_only_no_object_speech(monkeypatch,tmp_path,enabled,person_failure):
    counts={'camera':0,'person_models':0,'ground_models':0}
    image=np.random.default_rng(3070).integers(20,230,(480,640,3),dtype=np.uint8)
    class FixtureCamera(Camera):
        def __init__(self,*args):super().__init__(*args);counts['camera']+=1
        def _run(self):
            self.media_fps=15
            while not self.stopped.is_set():
                now=time.monotonic()
                with self.condition:
                    self.frame=image;self.sequence+=1;self.captured_at=now;self.error='';self.delivered.append(now)
                    self.condition.notify_all()
                self.stopped.wait(1/15)
    class Persons:
        model_id='qa-person';manifest={}
        def __init__(self,*args,**kw):
            counts['person_models']+=1
            assert kw['classes']==(0,) and kw['max_detections']==80
            if person_failure:raise RuntimeError('Explicit QA person model failure')
        def predict(self,image):
            return [{**d,'bbox':[v*(640 if i%2==0 else 480) for i,v in enumerate(d['bbox_normalized'])]} for d in dense()]+[
                {'label':'chair','confidence':.99,'bbox':[100,100,400,400]},
                {'label':'backpack','confidence':.99,'bbox':[100,100,400,400]}]
    class Ground:
        model_id='qa-ground';manifest={}
        def __init__(self,*args,**kw):counts['ground_models']+=1
        def predict(self,image):return {'detections':[]}
    class Semantic(Ground):
        model_id='qa-semantic'
        def predict(self,image):
            return {'detections':[{'label':'stairs','confidence':.9,'bbox':[200,220,440,470],
                                   'context_supported':True}],
                    'class_map':np.full((480,640),12,np.uint8),
                    'ground_mask':np.ones((480,640),np.uint8)}
    monkeypatch.setattr(v2,'ReplayCamera',FixtureCamera)
    monkeypatch.setattr(v2,'Detector',Persons)
    for name in ['RoadDetector','NcnnYoloXPotholeDetector','PuddleDetector']:monkeypatch.setattr(v2,name,Ground)
    monkeypatch.setattr(v2,'SemanticDetector',Semantic)
    config=v2.load_config(Path(__file__).parents[1]/'config/camera.v2.toml',mode='awareness')
    config['source']={'video':'explicit QA fixture'};config['audio']['enabled']=False
    config['crowd'].update(enabled=enabled,interval_s=.5,min_span_s=.7)
    monkeypatch.setattr(v2,'ROOT',tmp_path)
    app=v2.V2(config);app.start()
    try:
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            status=app.status()
            kinds={e['kind'] for e in status['recent_events']}
            if 'GROUND_HAZARD' in kinds and (not enabled or person_failure or 'CROWD' in kinds):break
            time.sleep(.03)
        assert status['ready'] and 'GROUND_HAZARD' in kinds
        assert ('CROWD' in kinds) is (enabled and not person_failure)
        assert counts=={'camera':1,'person_models':1 if enabled else 0,'ground_models':4}
        assert status['tasks']['objects']['state']=='disabled'
        assert status['tasks']['persons']['state'] in ({'error'} if person_failure else {'ready','deferred'} if enabled else {'disabled'})
        assert not any(e['kind']=='OBJECT_NEAR' for e in status['recent_events'])
        assert all(d['label'] in {'person','stairs'} for d in status['detections'])
        assert not any(d['label'] in {'chair','backpack','bicycle','car'} for d in status['detections'])
        assert app.image().startswith(b'\xff\xd8')
        assert status['audio']['state']=='disabled'
    finally:app.close()


def test_person_scheduler_defers_to_stale_ground_without_changing_it():
    from types import SimpleNamespace
    from threading import Condition
    app=SimpleNamespace(condition=Condition(),ground_tasks={'puddle':None,'semantic':None},
        crowd=SimpleNamespace(cfg={'ground_max_age_s':1.3}),
        results={'puddle':{'captured_at':10},'semantic':{'captured_at':10}})
    assert v2.V2._person_ground_fresh(app,11)
    assert not v2.V2._person_ground_fresh(app,11.4)
    assert app.results['semantic']['captured_at']==10


def test_runtime_mode_default_and_ground_configuration_unchanged():
    root=Path(__file__).parents[1]
    config=v2.load_config(root/'config/camera.v2.toml')
    assert config['vision']['mode']=='ground'
    assert config['road']['confidence']==.4 and config['pothole']['confidence']==.6
    assert config['semantic']['confidence']==.55 and config['puddle']['confidence']==.25
    assert config['warning']['confirmation']['pothole']['observations']==2
    assert config['warning']['confirmation']['stairs']['observations']==3
    assert config['warning']['ground_roi']==[[.35,.4],[.65,.4],[.95,1.],[.05,1.]]
