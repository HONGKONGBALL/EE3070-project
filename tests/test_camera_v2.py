"""Rule fixtures are explicitly simulated; they do not measure model accuracy."""

from dataclasses import replace
from pathlib import Path
import json
import time
import tomllib

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("ncnn")
pytest.importorskip("onnxruntime")

from cane.arbiter import OutputArbiter
from cane.model import OutputRequest, Priority, SensorFrame
from cane.vision.association import TrackBank, associate, in_region, ground_overlaps_region
from cane.vision.distance import DistanceObservation, MockDistanceProvider, PicoDistanceProvider, ProximityGate
from cane.vision.ground import RoadDetector, decode_road, semantic_regions
from cane.vision.puddle import PuddleDetector, context_pixels
from cane.vision.stairs import decode_stairs, confirm_stairs
from cane.vision.v2_policy import V2Policy

ROI = [[0.1,0.1],[0.9,0.1],[0.9,1.0],[0.1,1.0]]
CALIBRATION = {"verified":True,"id":"fixture-only","source":"mock",
               "beam_roi":[[0.45,0.4],[0.55,0.4],[0.55,0.8],[0.45,0.8]]}


def sample(seq, now, m=0.9, **kwargs):
    return DistanceObservation(str(seq),"mock",m,True,now,**kwargs)


def observed(frame_id, now, label="chair", bbox=None):
    return {"frame_id":frame_id,"captured_at":now,
            "detections":[{"label":label,"confidence":0.9,
                           "bbox_normalized":bbox or [0.3,0.3,0.7,0.9]}]}


def test_mock_distances_are_scripted_explicit_and_expire():
    mock = MockDistanceProvider([{"seconds":1,"cm":250},{"seconds":1,"cm":90}],clock=lambda:10)
    assert mock.latest(10) is None
    mock.start()
    assert mock.latest(10.1).distance_m == 2.5
    assert mock.latest(11.1).source == "mock"
    assert mock.latest(11.1).distance_m == 0.9
    assert mock.latest(12.1) is None
    assert mock.latest(11.1).sample_id != mock.latest(11.2).sample_id


def test_replay_mock_uses_media_time_and_preserves_sample_freshness():
    mock=MockDistanceProvider([{'seconds':1,'cm':250},{'seconds':1,'cm':90}],clock=lambda:10)
    mock.start()
    observation=mock.latest(100,elapsed=1.14)
    assert observation.distance_m==.9 and observation.sample_id=='mock:11'
    assert observation.fresh(100) and observation.received_monotonic==pytest.approx(99.96)
    assert mock.latest(100,elapsed=2.1) is None


def test_replay_cannot_consume_real_pico_or_attach_timeline_to_live_camera():
    from cane.vision.v2 import V2
    with pytest.raises(ValueError,match='live Pico'):
        V2({'source':{'video':'fixture.avi'},'distance':{'mode':'pico'}})
    with pytest.raises(ValueError,match='explicit replay'):
        V2({'simulation':{'timeline':'fixture.json'},'distance':{'mode':'none'}})


def test_scene_cut_expires_old_result_track_and_queued_speech():
    from types import SimpleNamespace
    from cane.vision.v2 import V2
    sink=Sink()
    arbiter=OutputArbiter(sink)
    arbiter.submit(OutputRequest('speaking',Priority.SAFETY))
    arbiter.submit(OutputRequest('pending',Priority.SAFETY))
    old=V2Policy('old',ROI,{})
    old.observe_ground('road',observed(1,1,'pothole'),1)
    old.observe_ground('road',observed(2,1.5,'pothole'),1.5)
    event=old.step(None,1.5)[0]
    app=SimpleNamespace(timeline=[{'id':'second','start_frame':11,'end_frame':20,'source_file':'second.jpg'}],
        scene=None,audio=SimpleNamespace(sink=sink,arbiter=arbiter),policy=old,run_id='fixture',
        config={'warning':{'roi':ROI,'ground_roi':ROI},'distance':{'enter_m':1,'leave_m':1.2}},log=lambda *_a,**_k:None)
    results=V2._scene_results(app,11,{'road':{'frame_id':10},'semantic':{'frame_id':11}},2.0)
    assert app.scene_first_observed_at==2.0
    assert list(results)==['semantic'] and app.policy is not old
    assert sink.stops==1 and not arbiter.pending()
    assert not app.policy.request_valid(OutputRequest('pothole',Priority.SAFETY,event_id=event['event_id']),1.6)
    assert app.policy.step(None,1.6)==[]


@pytest.mark.parametrize("reading",[float("nan"),float("inf"),-1,0,None])
def test_invalid_distance_is_not_proximity(reading):
    assert not sample(1,1,reading).fresh(1.1)


def test_gate_requires_distinct_samples_and_hysteresis_and_does_not_rearm_on_dropout():
    gate = ProximityGate()
    assert not gate.update(sample(1,1),1)
    assert not gate.update(sample(1,1),1.05)
    assert gate.update(sample(2,1.1),1.1)
    for i,m in enumerate([1.1,0.95,1.05,0.9]):
        assert not gate.update(sample(i+3,1.2+i/10,m),1.2+i/10)
    assert not gate.update(None,3)
    assert not gate.update(sample(8,3.1),3.1)
    assert not gate.update(sample(9,3.2,1.3),3.2)
    assert not gate.update(sample(10,3.3,1.3),3.3)
    assert not gate.near
    assert not gate.update(sample(11,3.4),3.4)
    assert gate.update(sample(12,3.5),3.5)


def test_track_streak_breaks_on_missing_frame_and_same_frame_never_counts_twice():
    bank = TrackBank("test")
    d = observed(1,1)["detections"]
    bank.update(d,1,1,1)
    bank.update(d,1,1,1.1)
    assert bank.tracks[0].hits == 1
    bank.update([],2,1.2,1.2)
    bank.update(d,3,1.3,1.3)
    assert bank.tracks[0].hits == 1


def stable_tracks():
    bank = TrackBank("test")
    for i,now in enumerate([1.0,1.1,1.2]):
        bank.update(observed(i,now)["detections"],i,now,now)
    return bank.tracks


def test_no_largest_or_only_object_association_without_calibration():
    tracks = stable_tracks()
    s = sample(1,1.3,direction="front",calibration_id="fixture-only")
    assert associate(s,tracks,{},1.3) is None
    assert associate(replace(s,direction="unknown"),tracks,CALIBRATION,1.3) is None
    assert associate(s,tracks,CALIBRATION,1.3) is tracks[0]
    assert associate(s,tracks+[replace(tracks[0],track_id="another")],CALIBRATION,1.3) is None
    assert associate(s,tracks,CALIBRATION,2) is None
    assert associate(replace(s,source="pico"),tracks,CALIBRATION,1.3) is None


def test_visible_objects_stay_silent_without_distance_and_unknown_near_is_generic():
    policy = V2Policy("run",ROI,{})
    for i in range(5):
        now = 1+i/10
        policy.observe_objects(observed(i,now),now)
        assert policy.step(None,now) == []
    assert policy.step(sample(1,1.4),1.4) == []
    events = policy.step(sample(2,1.5),1.5)
    assert events[0]["phrase_id"] == "obstacle_ahead"
    assert events[0]["object_class"] is None
    assert events[0]["association"] == "unmatched"


def test_named_mock_near_event_requires_matching_fixture_and_expires_on_visual_loss():
    policy = V2Policy("run",ROI,CALIBRATION)
    for i,now in enumerate([1,1.1,1.2]):
        policy.observe_objects(observed(i,now),now)
    opts = {"direction":"front","calibration_id":"fixture-only"}
    assert not policy.step(sample(1,1.2,**opts),1.2)
    event = policy.step(sample(2,1.3,**opts),1.3)[0]
    assert event["object_class"] == "chair"
    assert event["association"] == "matched_simulation"
    assert event['first_visible_captured_at']==1
    assert event['distance_decision_latency_ms']==0
    req = OutputRequest("chair_ahead",Priority.SAFETY,event_id=event["event_id"])
    assert policy.request_valid(req,1.3)
    policy.observe_objects(None,1.4)
    assert not policy.request_valid(req,1.4)


def test_new_near_episode_waits_for_cooldown_without_losing_its_warning():
    policy=V2Policy("run",ROI,{})
    assert not policy.step(sample(1,1),1)
    assert policy.step(sample(2,1.1),1.1)
    policy.step(sample(3,2,1.4),2)
    policy.step(sample(4,2.1,1.4),2.1)
    policy.step(sample(5,3),3)
    assert not policy.step(sample(6,3.1),3.1)
    assert policy.step(sample(7,9.2),9.2)[0]['kind']=='OBJECT_NEAR'
    assert not policy.step(sample(8,9.3),9.3)


def test_verified_projection_can_wait_briefly_for_inflight_vision_but_never_guesses():
    opts={'direction':'front','calibration_id':'fixture-only'}
    policy=V2Policy('run',ROI,CALIBRATION)
    assert policy.step(sample(1,1,**opts),1)==[]
    assert policy.step(sample(2,1.1,**opts),1.1)==[]
    for frame,now in enumerate([1.12,1.15,1.18]):policy.observe_objects(observed(frame,now),now)
    event=policy.step(sample(3,1.18,**opts),1.18)[0]
    assert event['object_class']=='chair' and event['distance_decision_latency_ms']==80
    # Without visual evidence the bounded grace ends in a generic warning, not a fabricated name.
    policy=V2Policy('run',ROI,CALIBRATION)
    policy.step(sample(1,1,**opts),1);policy.step(sample(2,1.1,**opts),1.1)
    event=policy.step(sample(3,1.23,**opts),1.23)[0]
    assert event['object_class'] is None and event['distance_decision_latency_ms']==130


def test_ground_warns_independently_once_and_uses_its_own_region():
    policy = V2Policy("run",ROI,{},ground_roi=[[0.3,0.5],[0.7,0.5],[0.9,1],[0.1,1]])
    policy.observe_ground("road",observed(1,1,"pothole"),1)
    assert not policy.step(None,1)
    policy.observe_ground("road",observed(2,1.7,"pothole"),1.7)
    event = policy.step(None,1.7)[0]
    assert event["kind"] == "GROUND_HAZARD" and event["distance_m"] is None
    assert event['first_visible_captured_at']==1 and event['first_observed_at']==1
    assert event['confirmed_at']==1.7 and event['confirmation_latency_ms']==700
    policy.observe_ground("road",observed(3,2.3,"pothole"),2.3)
    assert not policy.step(None,2.3)
    policy.observe_ground("road",observed(4,3,"stairs",[0.1,0.1,0.3,0.2]),3)
    assert not policy.step(None,3)


def test_flat_hazard_intrusion_uses_footprint_instead_of_vertical_object_anchor():
    polygon=[[.3,.4],[.7,.4],[.7,1],[.3,1]]
    assert not in_region([.1,.5,.45,.8],polygon)
    assert ground_overlaps_region([.1,.5,.45,.8],polygon)
    assert not ground_overlaps_region([0,.5,.31,.8],polygon)
    assert not ground_overlaps_region([.4,.1,.6,.3],polygon)


@pytest.mark.parametrize('label,expected',[('puddle',2),('wet_pavement',1),('shadow_or_art',0),('dry_ground',0),('indoor_floor',0)])
def test_puddle_uses_one_same_frame_verifier_and_restores_crop_coordinates(label,expected):
    from types import SimpleNamespace
    labels=['puddle','wet_pavement','shadow_or_art','dry_ground','indoor_floor']
    calls=[]
    def detect(crop):
        assert crop.shape==(50,100,3)
        return {'detections':[{'label':'possible_puddle','confidence':.49,'bbox':[10,5,40,25]},
                              {'label':'possible_puddle','confidence':.95,'bbox':[50,10,80,30]}]}
    def verify(names,inputs):
        calls.append(inputs['pixel_values'])
        assert inputs['pixel_values'].shape==(1,3,256,256)
        return [np.eye(len(labels),512,dtype=np.float32)[labels.index(label)][None]]
    detector=PuddleDetector.__new__(PuddleDetector)
    detector.boxes=SimpleNamespace(predict=detect)
    detector.ground_roi=[[0,.5],[1,.5],[1,1],[0,1]]
    detector.labels=labels
    detector.prototypes=np.eye(len(labels),512,dtype=np.float32)
    detector.visual=SimpleNamespace(run=verify)
    detector.manifest={'verifier':{'repository':'fixture','revision':'fixture'}}
    result=detector.predict(np.zeros((100,100,3),np.uint8))
    json.dumps(result)  # The same evidence is sent through /status and JSONL logs.
    assert len(calls)==1 and len(result['detections'])==expected
    assert result['candidate_detections'][0]['bbox']==[10,55,40,75]
    assert result['candidate_detections'][1]['bbox']==[50,60,80,80]
    assert result['candidate_detections'][0]['evidence']['verifier_label']==label


def test_puddle_without_candidates_skips_expensive_visual_encoder():
    from types import SimpleNamespace
    detector=PuddleDetector.__new__(PuddleDetector)
    detector.boxes=SimpleNamespace(predict=lambda _: {'detections':[]})
    detector.ground_roi=ROI
    detector.visual=SimpleNamespace(run=lambda *_:pytest.fail('No crop should be encoded'))
    assert detector.predict(np.zeros((100,100,3),np.uint8))=={'detections':[],'candidate_detections':[]}


def test_context_encoder_retains_upper_scene_instead_of_only_the_detection_box():
    image=np.zeros((100,100,3),np.uint8)
    image[:50,:,2]=255  # BGR red above the ground detector crop.
    image[50:,:,0]=255
    pixels=context_pixels(image)
    assert pixels.shape==(1,3,256,256) and pixels.flags.c_contiguous
    assert pixels[0,0,20,120]==1 and pixels[0,2,230,120]==1
    assert pixels[0,0,230,120]==0 and pixels.min()==0 and pixels.max()==1


def test_single_class_puddle_export_preserves_real_class_and_does_not_alias_generic_water():
    predictions=np.zeros((1,5,1),np.float32)
    predictions[0,:4,0]=[320,320,200,200];predictions[0,4,0]=.8
    output=decode_road(predictions,640,1,0,0,640,640,.4,names=['puddle'],features=5)
    assert output[0]['label']=='possible_puddle' and output[0]['raw_label']=='puddle'
    assert decode_road(predictions,640,1,0,0,640,640,.4,names=['water'],features=5)==[]


def test_raised_bump_is_only_the_actual_speedbump_class_not_generic_damage():
    predictions=np.zeros((1,6,2),np.float32)
    predictions[0,:4,:]=np.array([[208,100],[250,180],[200,80],[50,60]])
    predictions[0,5,0]=.9;predictions[0,4,1]=.8
    detections=decode_road(predictions,416,1,0,0,416,416,.4,names=['Pothole','SpeedBump'],features=6)
    assert {(d['raw_label'],d['label']) for d in detections}=={('Pothole','pothole'),('SpeedBump','raised_bump')}
    # Normalizing a known trained class does not make crack/step labels into measured unevenness.
    assert decode_road(predictions,416,1,0,0,416,416,.4,names=['Other','step'],features=6)==[]


def test_native_pixel_preprocessing_is_identical_for_a_crop_view_and_the_same_copied_pixels(monkeypatch):
    from cane.vision import ground
    class Extractor:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def input(self,name,mat):self.mat=mat
        def extract(self,name):return 0,self.mat
    class Net:
        def create_extractor(self):return Extractor()
    detector=RoadDetector.__new__(RoadDetector)
    detector.size=32;detector.confidence=.4;detector.names=['puddle'];detector.features=5;detector.net=Net()
    monkeypatch.setattr(ground,'decode_road',lambda values,*_a,**_k:values)
    image=np.arange(20*32*3,dtype=np.uint8).reshape(20,32,3)
    crop=image[2:18,4:28]
    assert not crop.flags.c_contiguous
    assert np.array_equal(detector.predict(crop)['detections'],detector.predict(crop.copy())['detections'])


def test_audio_spacing_does_not_delay_ground_confirmation_or_new_distance_decision():
    policy=V2Policy('run',ROI,{})
    policy.observe_ground('semantic',observed(1,1,'possible_puddle'),1)
    policy.observe_ground('semantic',observed(2,1.5,'possible_puddle'),1.5)
    assert policy.step(None,1.5)
    assert not policy.step(sample(1,1.6),1.6)
    event=policy.step(sample(2,1.7),1.7)[0]
    assert event['kind']=='OBJECT_NEAR'
    assert event['distance_decision_latency_ms']==pytest.approx(0)


def test_rdd_decode_preserves_classes_and_coordinates_and_rejects_wrong_export():
    pred = np.zeros((1,8,2),np.float32)
    pred[0,:4,0] = [208,208,100,100]
    pred[0,7,0] = .9
    pred[0,:4,1] = [50,100,40,30]
    pred[0,4,1] = .8
    result = decode_road(pred,416,1,0,0,416,416,.4)
    assert {d["raw_label"] for d in result} == {"pothole","longitudinal_crack"}
    assert next(d for d in result if d["label"]=="pothole")["bbox"] == [158,158,258,258]
    with pytest.raises(ValueError): decode_road(np.zeros((1,84,100)),416,1,0,0,416,416,.4)


def test_large_water_area_can_use_narrow_surrounding_land_context():
    classes=np.full((30,40),13,np.uint8)
    classes[1:-1,1:-1]=21
    result=semantic_regions(classes,np.full(classes.shape,.9),640,480,.55,{'13':'earth','21':'water'})
    assert len(result)==1 and result[0]['context_supported']
    assert result[0]['raw_label']=='water'


def test_stair_model_preserves_trained_direction_ignores_none_and_suppresses_duplicate_boxes():
    rows=np.array([[.5,.5,.4,.6,.9,.01,.02,.81],
                   [.51,.5,.4,.6,.8,.01,.02,.72],
                   [.1,.2,.2,.3,.9,.01,.85,.01],
                   [.8,.8,.1,.1,.99,.98,.01,.01]],np.float32)
    detections=decode_stairs([rows],640,480,.5)
    assert {d['raw_label'] for d in detections}=={'up','down'} and len(detections)==2
    down=next(d for d in detections if d['label']=='downstairs')
    assert down['confidence']==pytest.approx(.81)  # OpenCV already applied objectness.
    assert down['bbox']==pytest.approx([192,96,448,384])
    with pytest.raises(ValueError):decode_stairs([np.zeros((3,7))],640,480)


def test_downstairs_requires_temporal_confirmation_but_no_distance_and_can_preempt_ordinary_event():
    policy=V2Policy('fixture',ROI,{})
    policy.observe_ground('semantic',observed(1,1,'possible_puddle'),1)
    policy.observe_ground('semantic',observed(2,1.5,'possible_puddle'),1.5)
    assert policy.step(None,1.5)[0]['priority']=='SAFETY'
    policy.observe_ground('stairs',observed(3,1.6,'upstairs'),1.6)
    assert not policy.step(None,1.6)
    policy.observe_ground('stairs',observed(4,1.7,'downstairs'),1.7)
    assert not policy.step(None,1.7)
    policy.observe_ground('stairs',observed(5,2.7,'downstairs'),2.7)
    event=policy.step(None,2.7)[0]
    assert event['phrase_id']=='downstairs_ahead' and event['priority']=='CRITICAL'
    assert event['distance_m'] is None


def test_direction_requires_confident_stair_pixels_in_its_box_and_a_nearby_capture_time():
    result={'captured_at':10,'detections':[{'label':'downstairs','bbox':[5,5,15,15]}]}
    semantic={'captured_at':9.5,'class_map':np.full((20,20),6,np.uint8),
              'ground_mask':np.ones((20,20),np.uint8),'model_id':'fixture','frame_id':7}
    assert not confirm_stairs(result,semantic)['detections']
    semantic['class_map'][5:15,5:15]=53
    confirmed=confirm_stairs(result,semantic)['detections'][0]
    assert confirmed['evidence']['semantic_frame_id']==7
    assert confirmed['evidence']['stair_fraction']==1
    semantic['ground_mask'][:]=0
    assert not confirm_stairs(result,semantic)['detections']
    semantic['ground_mask'][:]=1;semantic['captured_at']=9
    assert confirm_stairs(result,semantic) is None


def test_confirmed_downstairs_supersedes_generic_stairs_in_the_same_region():
    policy=V2Policy('fixture',ROI,{})
    for frame,now in [(1,1),(2,1.7)]:
        policy.observe_ground('semantic',observed(frame,now,'stairs'),now)
        policy.observe_ground('stairs',observed(frame,now,'downstairs'),now)
    events=policy.step(None,1.7)
    assert [e['phrase_id'] for e in events]==['downstairs_ahead']
    assert not policy.step(None,2)


def test_requested_pipewire_interruption_is_not_an_audio_failure_when_player_handles_sigterm(monkeypatch,tmp_path):
    import threading
    from cane.vision import speech
    records=[]
    class Process:
        def __init__(self,*_a,**_k):self.returncode=None;self.ended=threading.Event()
        def poll(self):return self.returncode
        def communicate(self,timeout):self.ended.wait(timeout);return b'',b''
        def terminate(self):self.returncode=1;self.ended.set()
        def wait(self,timeout):self.ended.wait(timeout);return self.returncode
    monkeypatch.setattr(speech.subprocess,'Popen',Process)
    sink=speech.PipeWireAudio(tmp_path,True,'',lambda kind,**fields:records.append((kind,fields)))
    sink.play(['fixture']);sink.stop()
    assert any(kind=='audio_interrupted' for kind,_ in records)
    assert not any(kind=='audio_error' for kind,_ in records)
    assert len({fields['playback_id'] for _,fields in records})==1
    assert any(kind=='audio_stopped' and fields['stop_latency_ms']>=0 for kind,fields in records)


def test_audio_provenance_keeps_preempted_and_new_event_playbacks_distinct(monkeypatch,tmp_path):
    import threading
    from cane.vision import speech
    records=[]
    class Process:
        def __init__(self,*_a,**_k):self.returncode=None;self.ended=threading.Event()
        def poll(self):return self.returncode
        def communicate(self,timeout):self.ended.wait(timeout);return b'',b''
        def terminate(self):self.returncode=1;self.ended.set()
        def wait(self,timeout):self.ended.wait(timeout);return self.returncode
    monkeypatch.setattr(speech.subprocess,'Popen',Process)
    audio=speech.Speech(tmp_path,True,record=lambda kind,**fields:records.append((kind,fields)))
    try:
        audio.submit({'phrase_id':'low','priority':'SAFETY','event_id':'scene:low'})
        audio.submit({'phrase_id':'high','priority':'CRITICAL','event_id':'scene:high'})
    finally:audio.close()
    starts={fields['phrase_id']:fields['playback_id'] for kind,fields in records if kind=='audio_start'}
    assert len(set(starts.values()))==2
    played={fields['event_id']:fields['playback_id'] for kind,fields in records if kind=='arbiter' and fields['action']=='played'}
    assert played=={'scene:low':starts['low'],'scene:high':starts['high']}
    preempted=[fields for kind,fields in records if kind=='arbiter' and fields['action']=='preempted']
    assert preempted[0]['playback_id']==starts['low']
    assert {fields['playback_id'] for kind,fields in records if kind=='audio_interrupted'}==set(starts.values())


def test_water_prediction_surrounded_only_by_indoor_floor_is_not_ground_hazard_evidence():
    classes=np.full((30,40),3,np.uint8);classes[10:20,10:30]=21
    result=semantic_regions(classes,np.full(classes.shape,.9),640,480,.55,{'3':'floor','21':'water'})
    assert result and not result[0]['context_supported']


class Sink:
    def __init__(self): self.playing=False;self.stops=0;self.played=[]
    def play(self,clips,text=None): self.playing=True;self.played.append(clips[0])
    def busy(self): return self.playing
    def stop(self): self.playing=False;self.stops+=1


def test_existing_arbiter_preempts_bounds_and_discards_obsolete_events():
    now=[10.0];valid={"low","pending","high"};sink=Sink()
    arbiter=OutputArbiter(sink,clock=lambda:now[0],max_pending=1,
                          valid_request=lambda req:req.event_id in valid)
    assert arbiter.submit(OutputRequest("object",Priority.SAFETY,event_id="low"))
    assert arbiter.submit(OutputRequest("puddle",Priority.SAFETY,event_id="pending"))
    assert arbiter.submit(OutputRequest("pothole",Priority.CRITICAL,event_id="high"))
    assert sink.stops == 1 and sink.played[-1]=="pothole"
    assert len(arbiter.pending()) <= 1
    valid.clear();sink.playing=False
    assert arbiter.tick() is None
    assert not arbiter.pending()
    # Canceling queued events does not reset the safety hold.
    assert arbiter.safety_hold_active()


@pytest.mark.parametrize('task',['camera','road'])
def test_queued_fault_audio_is_not_played_after_its_component_recovers(task):
    from types import SimpleNamespace
    from cane.vision.v2 import V2
    ready={'value':False}
    app=SimpleNamespace(camera=SimpleNamespace(status=lambda:{'ready':ready['value']}),
                        task_states={'road':{'state':'error'}},
                        policy=SimpleNamespace(request_valid=lambda req,now:True))
    sink=Sink()
    arbiter=OutputArbiter(sink,valid_request=lambda req:V2._request_valid(app,req))
    arbiter.submit(OutputRequest('speaking',Priority.SAFETY))
    assert arbiter.submit(OutputRequest('fault',Priority.SYSTEM,event_id='fault:'+task))
    ready['value']=True;app.task_states['road']['state']='ready';sink.playing=False
    assert arbiter.tick() is None
    assert sink.played==['speaking']


def test_pico_adapter_reuses_legacy_units_and_rejects_duplicate_and_rebooted_timestamp(monkeypatch):
    from cane.vision import distance
    import queue
    class Link:
        def __init__(self,*_): self.inbox=queue.Queue()
        def start(self): pass
        def close(self): pass
        def heartbeat(self): pass
    monkeypatch.setattr(distance,"SerialPicoLink",Link)
    provider=PicoDistanceProvider("fixture")
    provider.link.inbox.put(SensorFrame(10,front_m=.9,pico_ms=100))
    first=provider.latest(10)
    assert first.fresh(10.1) and first.direction=="unknown"
    provider.link.inbox.put(SensorFrame(10.1,front_m=.5,pico_ms=100))
    assert provider.latest(10.1).sample_id==first.sample_id
    provider.link.inbox.put(SensorFrame(10.2,front_m=.5,pico_ms=2))
    assert provider.latest(10.2) is None


@pytest.mark.parametrize('puddle_enabled',[False,True])
@pytest.mark.parametrize('source_usable',[False,True])
@pytest.mark.parametrize('pothole_backend',['ncnn','yolox_ncnn_research'])
@pytest.mark.parametrize('vision_mode,replay_fps',[('balanced',15),('ground',15),('ground',4)])
def test_v2_replay_preview_and_ground_event_without_distance(tmp_path,monkeypatch,puddle_enabled,source_usable,pothole_backend,vision_mode,replay_fps):
    from cane.vision import v2,demo
    # Deliberately synthetic detections: integration contract, not model accuracy.
    class Objects:
        model_id="fixture-objects"
        def __init__(self,*_):
            assert vision_mode=='balanced', 'Ground mode must not load an object model'
        def predict(self,image): return [{"label":"chair","confidence":.9,"bbox":[180,180,430,450]}]
    class Road:
        model_id="fixture-road"
        def __init__(self,*_): pass
        def predict(self,image): return {"detections":[{"label":"pothole","raw_label":"pothole","confidence":.9,"bbox":[180,240,430,450]}]}
    class Semantic:
        model_id="fixture-semantics"
        def __init__(self,*_,**_options): pass
        def predict(self,image):
            h,w=image.shape[:2]
            return {"detections":[],"class_map":np.full((h,w),6,np.uint8),"ground_mask":np.ones((h,w),np.uint8)}
    class Puddle:
        model_id='fixture-puddle'
        def __init__(self,*_a,**_k):pass
        def predict(self,image):return {'detections':[],'candidate_detections':[]}
    monkeypatch.setattr(v2,"Detector",Objects)
    monkeypatch.setattr(v2,"RoadDetector",Road)
    class ResearchPothole(Road):
        model_id='fixture-yolox-research'
    monkeypatch.setattr(v2,'NcnnYoloXPotholeDetector',ResearchPothole)
    monkeypatch.setattr(v2,"SemanticDetector",Semantic)
    monkeypatch.setattr(v2,"PuddleDetector",Puddle)
    monkeypatch.setattr(v2,"ROOT",tmp_path)
    monkeypatch.setattr(demo,"ROOT",tmp_path)
    video=tmp_path/'fixture.avi'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),replay_fps,(640,480))
    assert writer.isOpened()
    rng=np.random.default_rng(11)
    for i in range(60):
        writer.write(rng.integers(50,200,(480,640,3),dtype=np.uint8) if source_usable else
                     np.full((480,640,3),120,np.uint8))
    writer.release()
    config=tomllib.loads((Path(__file__).parents[1]/'config/camera.v2.toml').read_text())
    config['source']={'video':str(video)}
    config['vision']={'mode':vision_mode}
    config['pothole']['backend']=pothole_backend
    config['puddle']={'enabled':puddle_enabled,'directory':'fixture','size':640,'confidence':.25,'threads':1,'max_fps':1.5}
    config['audio']['enabled']=False
    app=v2.V2(config);app.start()
    try:
        assert app.ground_tasks['pothole'] is (ResearchPothole if pothole_backend=='yolox_ncnn_research' else Road)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            status=app.status()
            if status['recent_events'] and any(d.get('reason_counts',{}).get('already_handled')
                    for d in status['ground_diagnostics'].values()):break
            time.sleep(.02)
        status=app.status()
        assert status['ready'] and app.image().startswith(b'\xff\xd8')
        assert len(status['tasks'])==(5 if puddle_enabled else 4)
        assert all(task['state']=='ready' for name,task in status['tasks'].items() if name!='objects')
        assert status['tasks']['objects']['state']==('ready' if vision_mode=='balanced' else 'disabled')
        assert (app.detector is None) is (vision_mode=='ground')
        assert status['vision_mode']==vision_mode
        assert status['input_source']=='replay' and status['distance']['source']=='none'
        assert status['visual_quality']['usable'] is source_usable
        if source_usable:
            surface=status['ground_surface_observation']
            assert surface['frame_id']>0 and surface['captured_at']<=surface['observed_at']
            assert surface['model_id']=='fixture-semantics' and surface['pavement_detected']
            assert surface['valid_until']>surface['captured_at']
        else:
            assert status['ground_surface_observation'] is None
        assert all(task['prediction_skipped'] is not source_usable for name,task in status['tasks'].items() if task['state']!='disabled')
        assert any(e['kind']=='GROUND_HAZARD' for e in status['recent_events']) is source_usable
        assert not any(e['kind']=='OBJECT_NEAR' for e in status['recent_events'])
        assert status['capabilities']['sudden_drop']=='required_unverified'
        if source_usable:
            assert status['ground_diagnostics']['road']['raw_count']==1
            assert any(d.get('reason_counts',{}).get('already_handled')==1
                       for d in status['ground_diagnostics'].values())
    finally: app.close()
