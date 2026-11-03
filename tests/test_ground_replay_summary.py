import pytest
from summarize_ground_local import summarize


def fixture():
    items=[{'file':name,'sha256':name,'scene_group':'same-location','evaluation_split':'held_out_round2',
            'ground_truth':{'visible_puddle':truth,'visible_pothole':None},'obvious_puddle':truth is True}
           for name,truth in [('miss.jpg',True),('hit.jpg',True),('negative.jpg',False),('unknown.jpg',None)]]
    cases={}
    for item in items:
        hit=item['file'] in ('hit.jpg','negative.jpg','unknown.jpg')
        cases[item['file']]={'source_file':item['file'],'source_sha256':item['sha256'],
            'warnings':[{'object_class':'possible_puddle','created_at':11.5}] if hit else [],
            'first_scene_frame_captured_at':10,'reason_counts':{}}
    return {'completed':True,'run_id':'fixture','cases':cases,'task_errors':{}},{'items':items}


def test_score_uses_final_events_keeps_misses_and_does_not_choose_best_group_photo():
    report,catalog=fixture();result=summarize(report,catalog)['splits']['held_out_round2']
    photos=result['photos']['puddle']
    assert photos['clear_positive_cases']==2 and photos['clear_positive_hits']==1
    assert photos['clear_timely_hits']==1 and photos['false_warning_cases']==1
    assert photos['clear_misses']==['miss.jpg']
    assert result['first_photo_per_source_group']['puddle']['positive_hits']==0


def test_incomplete_or_wrong_source_replay_cannot_be_scored_as_valid():
    report,catalog=fixture();report['completed']=False
    with pytest.raises(ValueError,match='incomplete'):summarize(report,catalog)
    report['completed']=True;report['cases']['hit.jpg']['source_sha256']='different'
    with pytest.raises(ValueError,match='hash'):summarize(report,catalog)
