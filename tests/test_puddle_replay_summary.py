import pytest
from summarize_puddle_replay import summarize


def test_group_score_keeps_first_failure_instead_of_cherry_picking_later_success():
    items=[{'file':file,'scene_group':group,'evaluation_split':split,
            'ground_truth':{'visible_puddle':truth}} for file,group,split,truth in [
        ('first.jpg','same','held_out_after_global_recipe',True),
        ('second.jpg','same','held_out_after_global_recipe',True),
        ('negative.jpg','negative','held_out_after_global_recipe',False),
        ('dev.jpg','dev','development',True)]]
    cases={item['file']:{'source_file':item['file'],'warnings':
        [{'phrase_id':'puddle_ahead','event_id':item['file'],'confirmation_latency_ms':1500}]
        if item['file']!='first.jpg' else [],'candidate_detections':[],
        'raw_detections':[],'task_errors':{}} for item in items}
    report={'completed':True,'run_id':'fixture','cases':cases,'performance':{},'elapsed_s':20,
            'playback_records':[],'timing':{'timings':{}},'restored_live_camera':True}
    result=summarize(report,{'items':items})
    held=result['splits']['held_out_after_global_recipe']
    assert held['photos']['positive_warning_cases']==1
    assert held['first_photo_per_metadata_group']['positive_warning_cases']==0
    assert held['photos']['false_warning_cases']==1
    assert held['representatives']==['first.jpg','negative.jpg']
    assert result['splits']['development']['photos']['positive_warning_cases']==1
    report['completed']=False
    with pytest.raises(ValueError,match='incomplete'):summarize(report,{'items':items})
