"""Ground evidence contracts. Fixtures do not establish model accuracy."""
import pytest
pytest.importorskip('cv2')
from cane.vision.v2_policy import task_hazards, support_potholes


def result(label, confidence=.8, captured=10):
    return {'frame_id':1,'captured_at':captured,'detections':[{'label':label,'confidence':confidence}]}


def test_semantic_water_and_duplicate_road_potholes_cannot_bypass_dedicated_models():
    assert task_hazards('semantic',result('possible_puddle'))['detections']==[]
    assert task_hazards('road',result('pothole'))['detections']==[]
    assert task_hazards('road',result('damaged_pavement'))['detections']
    assert task_hazards('pothole',result('pothole'))['detections']
    assert task_hazards('puddle',result('possible_puddle'))['detections']
    assert task_hazards('semantic',result('stairs'))['detections']


def test_strong_pothole_survives_uncertain_semantics_but_not_an_indoor_floor():
    observed=result('pothole')
    sem={'captured_at':9.5}
    assert support_potholes(observed,sem,False,False)['detections']
    assert support_potholes(observed,sem,False,True) is None
    assert support_potholes(observed,sem,True,True) is None
    assert support_potholes(result('pothole',.69),sem,False,False)['detections']==[]
    assert support_potholes(observed,None,False,False) is None
    assert support_potholes(observed,{'captured_at':9},False,False) is None
    assert support_potholes(observed,sem,True,False) is observed


def test_unknown_task_does_not_silently_acquire_hazard_ownership():
    with pytest.raises(KeyError):task_hazards('unknown',result('pothole'))
