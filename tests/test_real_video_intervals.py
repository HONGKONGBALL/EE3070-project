from check_real_videos import evaluate_intervals


def test_evidence_clock_does_not_relabel_a_delayed_positive_as_a_flat_floor_false_alert():
    clip={'intervals':[{'start':0,'end':3,'truth':{'stairs':True,'pothole':None}},
                       {'start':4,'end':7,'truth':{'stairs':False}}]}
    event={'object_class':'stairs','evidence_source_s':2.9,'first_seen_decision_source_s':4.1}
    rows=evaluate_intervals(clip,[event])
    assert [r['result'] for r in rows]==['triggered','quiet']
    assert len(rows)==2  # An unknown pothole label is not a negative.


def test_boundary_gaps_and_endpoints_remain_unscored_and_true_misses_remain_visible():
    clip={'intervals':[{'start':0,'end':2,'truth':{'stairs':True}},
                       {'start':3,'end':5,'truth':{'stairs':False}}]}
    assert evaluate_intervals(clip,[])[0]['result']=='not_triggered'
    event={'object_class':'stairs','evidence_source_s':2}
    assert [r['result'] for r in evaluate_intervals(clip,[event])]==['not_triggered','quiet']
    event['evidence_source_s']=3
    assert evaluate_intervals(clip,[event])[1]['result']=='false_event'


def test_continuous_hazard_does_not_require_a_repeat_when_another_label_changes():
    clip={'intervals':[{'start':0,'end':9,'truth':{'pothole':True,'possible_puddle':True}},
                       {'start':9,'end':20,'truth':{'pothole':True,'possible_puddle':None}}]}
    rows=evaluate_intervals(clip,[{'object_class':'pothole','evidence_source_s':2}])
    pit=next(r for r in rows if r['category']=='pothole')
    water=next(r for r in rows if r['category']=='possible_puddle')
    assert (pit['start'],pit['end'],pit['result'])==(0,20,'triggered')
    assert (water['start'],water['end'],water['result'])==(0,9,'not_triggered')
