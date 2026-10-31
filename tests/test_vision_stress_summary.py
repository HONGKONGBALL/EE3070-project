import pytest
from summarize_vision_stress import summarize


def test_each_hazard_is_scored_only_against_its_own_boolean_label():
    catalog={"items":[{"file":"floor.jpg","sha256":"abc","ground_truth":{
        "visible_puddle":False,"visible_pothole":None,"visible_pavement_damage":True}}]}
    case={"source_file":"floor.jpg","source_sha256":"abc","model_observations":{},
          "warnings":[{"object_class":"pothole"},{"object_class":"damaged_pavement"}]}
    report={"completed":True,"cases":{"case-01":case}}
    result=summarize(catalog,report)
    assert result['categories']['possible_puddle']['tn']==1
    assert result['categories']['pothole']['unscored']==1
    assert result['categories']['stairs']['unscored']==1
    assert result['categories']['damaged_pavement']['tp']==1
    assert result['unscored_events']==[{"file":"floor.jpg","category":"pothole"}]
    case['warnings']=[]
    assert summarize(catalog,report)['categories']['damaged_pavement']['fn']==1
    case['source_sha256']='changed'
    with pytest.raises(ValueError,match='provenance'):summarize(catalog,report)


def test_incomplete_or_duplicate_case_evidence_is_not_scored():
    with pytest.raises(ValueError,match='completed'):
        summarize({'items':[]},{'completed':False})
    case={'source_file':'x.jpg'}
    with pytest.raises(ValueError,match='Exactly one'):
        summarize({'items':[{'file':'x.jpg'}]},{'completed':True,'cases':{'1':case,'2':case}})
