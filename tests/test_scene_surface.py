from check_v2_scene_replay import frame_in_case,scene_surface


def test_surface_scoring_rejects_previous_scene_and_expired_semantics_even_when_legacy_flag_true():
    case={'start_frame':41,'end_frame':80}
    status={'normal_pavement_detected':True,'ground_context_valid':True,'visual_quality':{'usable':True},
            'ground_surface_observation':{'frame_id':40,'valid_until':11.5,'pavement_detected':True}}
    assert scene_surface(status,case,11) is None
    status['ground_surface_observation']['frame_id']=41
    assert scene_surface(status,case,11)['pavement_detected']
    status['visual_quality']['usable']=False
    assert scene_surface(status,case,11) is None
    status['visual_quality']['usable']=True
    assert scene_surface(status,case,11.6) is None
    assert scene_surface({'normal_pavement_detected':True},case,11) is None


def test_frame_scope_accepts_both_boundaries_and_background_but_not_another_case():
    case={'start_frame':41,'end_frame':80}
    assert frame_in_case(41,case) and frame_in_case(80,case)
    assert not frame_in_case(40,case) and not frame_in_case(81,case)
    assert not frame_in_case(None,case)
    assert frame_in_case(900,{'id':'background'})
