import pytest
from summarize_road_replay import summarize


def fixture():
    truth={'visible_pothole':True,'visible_pavement_damage':True,'road_path_semantics':True}
    item={'file':'fixture.jpg','scene_group':'fixture','ground_truth':truth}
    case={'source_file':'fixture.jpg','ground_truth':dict(truth),'start_frame':1,'end_frame':40,
          'first_scene_frame_captured_at':10,'normal_pavement_observed':True,
          'surface_observations':[],
          'warnings':[{'kind':'GROUND_HAZARD','phrase_id':'pothole_ahead','confirmed_at':13.5,'confirmation_latency_ms':900}],
          'task_errors':{}}
    report={'completed':True,'run_id':'fixture','cases':{'case':case},'performance':{},'playback_records':[],
            'timing':{'timings':{}},'elapsed_s':5,'restored_live_camera':True}
    return report,{'items':[item]},case


def test_road_summary_keeps_label_specificity_and_both_delay_measures():
    report,catalog,case=fixture()
    result=summarize(report,catalog)
    pothole=result['metrics']['pothole']['photos']
    assert pothole['positive_detected']==1 and pothole['positive_timely_by_evidence']==1
    assert pothole['positive_timely_by_scene']==0  # Early model evidence must not hide a late scene response.
    assert result['metrics']['visible_damage']['photos']['positive_detected']==0
    assert not pothole['passes_by_evidence']  # One image is never sufficient for the ten-scene gate.


def test_no_hazard_or_legacy_normal_flag_does_not_count_as_normal_semantics():
    report,catalog,case=fixture()
    assert summarize(report,catalog)['metrics']['road_path_semantics']['photos']['positive_detected']==0
    case['surface_observations']=[{'frame_id':41,'pavement_detected':True,'observed_at':11,'captured_at':10.5}]
    assert summarize(report,catalog)['metrics']['road_path_semantics']['photos']['positive_detected']==0
    case['surface_observations'][0]['frame_id']=2
    score=summarize(report,catalog)['metrics']['road_path_semantics']['photos']
    assert score['positive_detected']==1 and score['positive_timely_by_scene']==1
    case['ground_truth']['visible_pothole']=False
    with pytest.raises(ValueError,match='frozen'):summarize(report,catalog)


def test_label_amendments_are_explicit_non_destructive_and_cannot_silently_change_truth():
    report,catalog,case=fixture()
    review={'file':'fixture.jpg','truth_key':'visible_pothole','before':True,'after':False,
            'reason':'Audited example correction','reviewed_after_predictions':True}
    result=summarize(report,catalog,[review])
    assert result['metrics']['pothole']['photos']['false_positive_cases']==1
    assert result['post_prediction_label_review'] and result['label_amendments']==[review]
    assert case['ground_truth']['visible_pothole'] is True and catalog['items'][0]['ground_truth']['visible_pothole'] is True
    review['before']=False
    with pytest.raises(ValueError,match='frozen'):summarize(report,catalog,[review])
