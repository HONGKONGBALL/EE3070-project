"""Temporal policy and delivery contracts; not recognition or physical safety claims."""
import pytest
from cane.vision.episodes import HazardEpisodes


def observation(frame, at, label="stairs", box=(.25,.4,.8,.95), confidence=.9):
    return {"frame_id":frame,"captured_at":at,"detections":[] if label is None else [
        {"label":label,"confidence":confidence,"bbox_normalized":list(box)}]}


def admitted(manager, task, frame, at, label="stairs", **kwargs):
    manager.observe(task,observation(frame,at,label,**kwargs),at)
    return manager.step(at)


def test_missing_or_one_negative_between_independent_positives_can_confirm():
    for missing in [True,False]:
        m=HazardEpisodes("test")
        assert not admitted(m,"puddle",1,1,"possible_puddle")
        m.observe("puddle",None if missing else observation(2,1.6,None),1.6)
        events=admitted(m,"puddle",3,2.3,"possible_puddle")
        assert len(events)==1 and events[0]["confirmation"]["positive_observations"]==2


def test_singleton_duplicates_out_of_order_and_expired_evidence_never_confirm():
    m=HazardEpisodes("test")
    for _ in range(30): assert not admitted(m,"puddle",1,1,"possible_puddle")
    assert not admitted(m,"puddle",0,.8,"possible_puddle")
    assert not admitted(m,"puddle",2,5,"possible_puddle")
    assert m.counters["events"]==0


def test_pothole_keeps_stricter_negative_protection_but_missing_capture_is_not_negative():
    m=HazardEpisodes("pit-negative")
    admitted(m,"pothole",1,1,"pothole")
    admitted(m,"pothole",2,1.7,None)
    assert not admitted(m,"pothole",3,2.4,"pothole")
    m.observe("pothole",None,2.8)
    assert admitted(m,"pothole",4,3.1,"pothole")


def test_wet_surface_does_not_rearm_after_four_seconds_of_intermittent_absence():
    m=HazardEpisodes("wet-gap");admitted(m,"puddle",1,1,"possible_puddle")
    first=admitted(m,"puddle",2,1.7,"possible_puddle")[0]
    m.delivery(first["event_id"],"completed",1.8)
    for frame,at in enumerate([2.4,3.1,3.8,4.5,5.2],3):admitted(m,"puddle",frame,at,None)
    assert not admitted(m,"puddle",8,5.9,"possible_puddle")
    assert not admitted(m,"puddle",9,6.6,"possible_puddle")
    assert m.current["wet_surface"].episode_id==first["episode_id"]


def test_box_and_localization_changes_and_short_quality_loss_keep_announced_episode():
    m=HazardEpisodes("test")
    admitted(m,"semantic",1,1)
    event=admitted(m,"semantic",2,1.7,box=(.4,.5,.55,.7))[0]
    m.delivery(event["event_id"],"disabled",1.7)
    m.suspend(2)
    assert not m.valid(event["event_id"],2)
    for frame,at,box in [(3,2.4,(.2,.4,.9,1)),(4,3.1,(.45,.6,.6,.9)),(5,3.8,(.2,.4,.9,1))]:
        assert not admitted(m,"semantic",frame,at,box=box)
    assert m.current["stairs"].episode_id==event["episode_id"]
    assert m.counters["events"]==1


def test_reliable_clear_then_new_stairs_rearm_before_old_eight_second_cooldown():
    m=HazardEpisodes("test")
    admitted(m,"semantic",1,1)
    first=admitted(m,"semantic",2,1.6)[0];m.delivery(first["event_id"],"completed",1.6)
    for i,t in enumerate([2,3,4,5],3):assert not admitted(m,"semantic",i,t,None)
    assert m.current["stairs"].state=="CLEAR"
    admitted(m,"semantic",7,5.2)
    second=admitted(m,"semantic",8,5.9)[0]
    assert first["episode_id"]!=second["episode_id"]
    assert second["created_at"]-first["created_at"]<8


def test_unreliable_negatives_never_establish_exit_and_timeout_is_explicit():
    m=HazardEpisodes("test")
    admitted(m,"semantic",1,1)
    first=admitted(m,"semantic",2,1.6)[0];m.delivery(first["event_id"],"disabled",1.6)
    for i,t in enumerate([2,2.8,3.6],3):
        m.observe("semantic",observation(i,t,None),t,negative_reliable=False)
    assert m.current["stairs"].state!="CLEAR"
    m.step(8)
    assert "physical exit unknown" in m.current["stairs"].clear_reason


def test_damage_shares_one_phrase_and_strong_pothole_does_not_repeat_spoken_family():
    m=HazardEpisodes("test")
    admitted(m,"road",1,1,"damaged_pavement")
    generic=admitted(m,"road",2,1.7,"damaged_pavement")[0]
    m.delivery(generic["event_id"],"completed",1.7)
    admitted(m,"pothole",3,2,"pothole",confidence=.65)
    assert not admitted(m,"pothole",4,2.7,"pothole",confidence=.65)
    assert not admitted(m,"pothole",5,3.4,"pothole",confidence=.9)
    assert not admitted(m,"pothole",6,4.1,"pothole",confidence=.9)
    assert m.current['damaged_pavement'].announced_rank==4
    assert m.current['damaged_pavement'].signals['pothole'].latest['label']=='pothole'
    assert generic['warning_category']=='DAMAGED_PAVEMENT'
    assert generic['text']=='The pavement ahead looks damaged. Watch your step!'
    assert not admitted(m,"road",7,4.3,"damaged_pavement")


@pytest.mark.parametrize("action",["expired","invalid","rejected","replaced","interrupted","failed"])
def test_undelivered_events_require_new_fresh_evidence_for_retry(action):
    m=HazardEpisodes("test");admitted(m,"semantic",1,1)
    first=admitted(m,"semantic",2,1.7)[0]
    m.delivery(first["event_id"],action,1.8)
    assert not m.valid(first["event_id"],1.8) and not m.step(2.7)
    retry=admitted(m,"semantic",3,2.8)[0]
    assert retry["episode_id"]==first["episode_id"] and retry["event_id"]!=first["event_id"]
    m.delivery(first["event_id"],"completed",2.9) # late callback cannot complete replacement
    assert m.current["stairs"].announced_rank==0


def test_independent_urgent_family_is_not_blocked_and_does_not_replay_completed_family():
    m=HazardEpisodes("test")
    for frame,t in [(1,1),(2,1.7)]:
        m.observe("semantic",observation(frame,t,"stairs"),t)
        m.observe("puddle",observation(frame,t,"possible_puddle"),t)
    events=m.step(1.7)
    assert [e["object_class"] for e in events]==["stairs","possible_puddle"]
    for e in events:m.delivery(e["event_id"],"completed",1.8)
    for frame,t in [(3,2.4),(4,3.1)]:assert not admitted(m,"semantic",frame,t)


def test_timing_configuration_and_bounded_storage():
    m=HazardEpisodes("test",{"confirmation":{"stairs":{"hits":3,"observations":4}}})
    assert not admitted(m,"semantic",1,1)
    assert not admitted(m,"semantic",2,1.7)
    event=admitted(m,"semantic",3,2.4)[0]
    assert event["first_positive_candidate_at"]==1
    m.delivery(event["event_id"],"completed",2.4)
    for i in range(4,400):admitted(m,"semantic",i,2.4+i*.1)
    assert len(m.current["stairs"].signals["stairs"].history)<=4
    with pytest.raises(ValueError):HazardEpisodes("bad",{"confirmation":{"stairs":{"hits":1}}})
