"""Explain actual policy decisions without bypassing the warning rules."""

import json
from pathlib import Path

import pytest

pytest.importorskip('cv2')
from cane.vision.diagnostics import ground_diagnostic
from cane.vision.v2_policy import V2Policy

ROI=[[.3,.4],[.7,.4],[.9,1],[.1,1]]


def observation(frame=1, captured=1, box=None):
    return {'frame_id':frame,'captured_at':captured,'model_id':'fixture',
            'detections':[{'label':'damaged_pavement','confidence':.8,'bbox_normalized':box or [.3,.5,.7,.8]}]}


def test_context_rejection_and_model_absence_are_distinct_and_do_not_emit_events():
    policy=V2Policy('fixture',ROI,{},ground_roi=ROI)
    raw=observation()
    blocked=ground_diagnostic('road',raw,None,policy,1,blocked_by='ground_context_unavailable')
    assert blocked['reason_counts']=={'ground_context_unavailable':1}
    assert ground_diagnostic('road',{**raw,'detections':[]},None,policy,1)['reason']=='no_candidates'
    assert policy.step(None,1)==[] and all(e.state=="UNSEEN" for e in policy.episodes.current.values())


def test_outside_path_and_verifier_rejections_are_visible_without_promoting_candidates():
    policy=V2Policy('fixture',ROI,{})
    raw=observation(box=[0,0,.1,.1])
    assert ground_diagnostic('road',raw,raw,policy,1)['reason_counts']=={'outside_path':1}
    rejected=observation()['detections'][0]
    rejected['label']='possible_puddle'
    rejected['evidence']={'verification_accepted':False}
    raw={**observation(),'detections':[],'candidate_detections':[rejected]}
    assert ground_diagnostic('puddle',raw,raw,policy,1)['reason_counts']=={'verifier_rejected':1}
    assert policy.step(None,1)==[]


def test_confirmation_and_handling_follow_distinct_policy_frames_without_claiming_audio():
    policy=V2Policy('fixture',ROI,{})
    first=observation()
    policy.observe_ground('road',first,1)
    assert ground_diagnostic('road',first,first,policy,1)['reason_counts']=={'awaiting_confirmation':1}
    policy.observe_ground('road',first,1.1)
    assert not policy.step(None,1.1)
    second=observation(2,1.5)
    policy.observe_ground('road',second,1.5)
    event=policy.step(None,1.5)[0]
    assert ground_diagnostic('road',second,second,policy,1.5)['reason_counts']=={'episode_pending_audio':1}
    policy.episodes.delivery(event['event_id'],'disabled',1.5)
    result=ground_diagnostic('road',second,second,policy,1.5)
    assert result['raw_count']==1 and result['reason_counts']=={'episode_announced':1}
    assert ground_diagnostic('road',second,second,policy,4)['reason']=='no_fresh_result'


def test_diagnostics_deduplicate_shared_candidate_and_bound_response_size():
    policy=V2Policy('fixture',ROI,{})
    raw=observation()
    raw['candidate_detections']=raw['detections']
    result=ground_diagnostic('road',raw,raw,policy,1)
    assert result['raw_count']==1 and len(result['candidates'])==1
    json.dumps(result)


def test_local_model_paths_and_mode_are_explicit_and_config_file_stays_unchanged(tmp_path):
    pytest.importorskip('ncnn');pytest.importorskip('onnxruntime')
    from cane.vision.v2 import V2, load_config
    source=Path(__file__).parents[1]/'config/camera.v2.toml'
    before=source.read_bytes()
    settings=load_config(source,mode='ground',models_root=tmp_path)
    assert settings['vision']['mode']=='ground'
    assert settings['pothole']['directory']==str(tmp_path/'v2/pothole-yolox-research')
    assert source.read_bytes()==before
    with pytest.raises(ValueError,match='Vision mode'):
        V2({'distance':{'mode':'none'},'vision':{'mode':'automatic'}})
