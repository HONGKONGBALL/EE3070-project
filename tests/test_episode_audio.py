"""Use the real Speech/arbiter and a controlled sink to verify queue outcomes."""
from cane.vision.speech import Speech
from cane.vision.episodes import HazardEpisodes


def test_stale_queued_audio_drops_and_only_new_evidence_can_retry(monkeypatch, tmp_path):
    clock=[100.0]
    monkeypatch.setattr("cane.vision.speech.time.monotonic",lambda:clock[0])
    records=[];m=HazardEpisodes("audio")
    speech=Speech(tmp_path,False,record=lambda kind,**f:records.append((kind,f)))
    speech.arbiter._clock=lambda:clock[0]
    speech.on_delivery=m.delivery
    speech.validator=lambda req:m.valid(req.event_id,clock[0])
    class Sink:
        state="ready";error="";last_played=None;playback_id="fixture"
        playing=False
        def play(self,clips,text=None,context=None):
            self.playing=True
            speech._record_audio("audio_start",**context,phrase_id=clips[0])
        def busy(self):return self.playing
        def stop(self):self.playing=False
    speech.sink=Sink()
    def observe(task,label,frame,at):
        clock[0]=at
        m.observe(task,{"frame_id":frame,"captured_at":at,"detections":[] if label is None else [
            {"label":label,"confidence":.9,"bbox_normalized":[.2,.4,.8,1]}]},at)
    for frame,at in [(1,100),(2,100.7)]:
        observe("semantic","stairs",frame,at);observe("puddle","possible_puddle",frame,at)
    stairs,wet=m.step(100.7)
    speech.submit(stairs);speech.submit(wet)
    assert m.current["stairs"].delivery=="started"
    assert m.current["wet_surface"].delivery=="queued"
    # The wet evidence disappears while stairs occupies the speaker.
    observe("puddle",None,3,101.4)
    speech.sink.playing=False;clock[0]=102.4;speech.tick()
    assert not speech.arbiter.pending()
    assert m.current["wet_surface"].delivery=="invalid"
    assert not any(k=="audio_start" and f["event_id"]==wet["event_id"] for k,f in records)
    observe("puddle","possible_puddle",4,103)
    observe("puddle","possible_puddle",5,103.7)
    retry=m.step(103.7)[0];speech.submit(retry)
    assert retry["event_id"]!=wet["event_id"] and retry["episode_id"]==wet["episode_id"]
    assert m.current["wet_surface"].delivery=="started"
    assert all(f["queue_to_process_start_ms"]>=0 for k,f in records if k=="audio_start")


def test_disabled_audio_is_explicit_and_not_retried_forever(tmp_path):
    import time
    m=HazardEpisodes("disabled");speech=Speech(tmp_path,False)
    speech.on_delivery=m.delivery
    now=time.monotonic()
    for frame,at in [(1,now-.6),(2,now)]:
        m.observe("semantic",{"frame_id":frame,"captured_at":at,"detections":[
            {"label":"stairs","confidence":.9,"bbox_normalized":[.2,.4,.8,1]}]},at)
    event=m.step(now)[0];speech.submit(event)
    assert m.current["stairs"].delivery=="disabled" and speech.last_played is None
    assert not m.step(now+.1)
