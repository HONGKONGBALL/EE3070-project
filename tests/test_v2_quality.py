"""Pixel degradation and policy contracts, not outdoor recognition accuracy."""

from types import SimpleNamespace
import time

import numpy as np
import pytest

cv2=pytest.importorskip('cv2')
pytest.importorskip('ncnn')
pytest.importorskip('onnxruntime')

from cane.arbiter import OutputArbiter
from cane.model import OutputRequest, Priority
from cane.vision.quality import assess_frame
from cane.vision.distance import DistanceObservation
from cane.vision.v2 import V2
from cane.vision.v2_policy import V2Policy


def textured():
    rng=np.random.default_rng(37)
    return rng.integers(30,225,(480,640,3),dtype=np.uint8)


@pytest.mark.parametrize('level,reason',[(0,'too_dark'),(255,'overexposed'),(120,'insufficient_detail')])
def test_uniform_unusable_images_are_limited_without_guessing_occlusion(level,reason):
    result=assess_frame(np.full((480,640,3),level,np.uint8))
    assert not result['usable'] and reason in result['reasons']
    assert 'occluded' not in result['reasons']


def test_very_low_sharpness_does_not_mistake_a_crisp_low_texture_edge_for_blur():
    image=np.full((480,640,3),40,np.uint8)
    image[:,320:]=210
    assert assess_frame(image)['usable']
    blurred=cv2.GaussianBlur(image,(151,151),35)
    assert 'very_low_sharpness' in assess_frame(blurred)['reasons']
    assert assess_frame(textured())['usable']


def test_quality_loss_clears_visual_event_and_pending_audio_but_allows_generic_distance():
    now=time.monotonic()
    roi=[[0,0],[1,0],[1,1],[0,1]]
    policy=V2Policy('quality-test',roi,{})
    for frame,ts in [(1,now-.6),(2,now)]:
        policy.observe_ground('puddle',{'frame_id':frame,'captured_at':ts,'detections':[
            {'label':'possible_puddle','confidence':.9,'bbox_normalized':[.2,.5,.8,.9]}]},ts)
    event=policy.step(None,now)[0]
    class Sink:
        playing=False
        stops=0
        def play(self,*_):self.playing=True
        def stop(self):self.playing=False;self.stops+=1
        def busy(self):return self.playing
    sink=Sink()
    arbiter=OutputArbiter(sink,valid_request=lambda req:policy.request_valid(req,now))
    arbiter.submit(OutputRequest(event['phrase_id'],Priority.SAFETY,event_id=event['event_id']))
    arbiter.submit(OutputRequest('pending',Priority.SAFETY,event_id=event['event_id']))
    app=SimpleNamespace(policy=policy,audio=SimpleNamespace(sink=sink,arbiter=arbiter),
        visual_quality={'usable':True,'reasons':[]},log=lambda *_a,**_k:None,
        _request_valid=lambda req:policy.request_valid(req,now))
    assert V2._quality_results(app,np.zeros((480,640,3),np.uint8),3,{'puddle':{'frame_id':2}})=={}
    assert sink.stops==1 and not arbiter.pending() and not policy.ground['puddle'].tracks
    assert not policy.request_valid(OutputRequest('stale',Priority.SAFETY,event_id=event['event_id']),now)
    # The proximity provider is independent; no visual category is fabricated.
    policy.gate.update(DistanceObservation('a','mock',.9,True,now+4),now+4)
    events=policy.step(DistanceObservation('b','mock',.9,True,now+4.1),now+4.1)
    assert len(events)==1 and events[0]['phrase_id']=='obstacle_ahead'
    assert events[0]['object_class'] is None


def test_recovery_requires_usable_model_frame_not_old_rejected_result():
    app=SimpleNamespace(visual_quality={'usable':False,'reasons':['too_dark']},quality_after_frame=3,
                        log=lambda *_a,**_k:None)
    results={'old':{'frame_id':4,'visual_quality':{'usable':False}},
             'pre_loss':{'frame_id':2,'visual_quality':{'usable':True}},
             'fresh':{'frame_id':4,'visual_quality':{'usable':True}}}
    assert set(V2._quality_results(app,textured(),4,results))=={'fresh'}
    assert app.visual_quality['usable']
