"""Program-level coexistence test with real queue and generated WAV durations.

All observations below are explicit QA fixtures, not model recognition results.
No sound device is opened. Optional WAV review renders this simulated timeline.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import wave
from types import SimpleNamespace

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi'))
from cane.vision.crowd import CrowdAwareness
from cane.vision.episodes import HazardEpisodes
from cane.vision.speech import Speech
from cane.vision.v2 import V2
from cane.vision.voice import VOICE
from timed_review_audio import TimedReviewAudio


def combine(starts,directory,destination):
    if not starts:return
    content=bytearray();origin=starts[0]['simulation_started_at'];rate=None;params=None
    for record in starts:
        with wave.open(str(directory/(record['phrase_id']+'.wav')),'rb') as clip:
            if params is None:params=clip.getparams();rate=clip.getframerate()
            assert (clip.getframerate(),clip.getnchannels(),clip.getsampwidth())==(rate,params.nchannels,params.sampwidth)
            frame_bytes=params.nchannels*params.sampwidth
            offset=round((record['simulation_started_at']-origin)*rate)*frame_bytes
            if offset<len(content):del content[offset:]
            content.extend(b'\x00'*max(0,offset-len(content)));content.extend(clip.readframes(clip.getnframes()))
    with wave.open(str(destination),'wb') as output:
        output.setparams(params);output.writeframes(content)


def run_case(name,labels,directory,out,stale=False,crowd_enabled=True,ground_from=0):
    clock=[100.];original=time.monotonic;time.monotonic=lambda:clock[0]
    records=[];events=[];ground=HazardEpisodes(name);crowd=CrowdAwareness(name)
    speech=Speech(directory,False,allowed_phrases={v[0] for v in VOICE.values()},
                  record=lambda kind,**fields:records.append({'kind':kind,**fields}))
    speech.arbiter._clock=lambda:clock[0]
    speech.sink=TimedReviewAudio(speech,directory)
    speech.validator=lambda req:(crowd if ':crowd-' in req.event_id else ground).valid(req.event_id,clock[0])
    speech.on_delivery=lambda eid,action,now:(crowd if ':crowd-' in eid else ground).delivery(eid,action,now)
    app=SimpleNamespace(awareness_mode=True,sample=None,crowd=crowd if crowd_enabled else None,
                        audio=speech,policy=SimpleNamespace(step=lambda _sample,now:ground.step(now)))
    tasks={'stairs':'semantic','possible_puddle':'puddle','damaged_pavement':'road','pothole':'pothole'}
    try:
        for tick in range(301):
            clock[0]=100+tick/10
            if tick%10==0:
                frame=tick//10+1;at=clock[0]
                people=[] if stale and tick>=30 else [
                    {'label':'person','confidence':.9,'bbox_normalized':[.02+i*.095,.25,.24+i*.095,.95]} for i in range(8)]
                crowd.observe({'frame_id':frame,'captured_at':at,'model_id':'QA-box-fixture','detections':people},at,
                              {'captured_at':at,'class_map':np.full((120,160),12,np.uint8)})
                for label in labels:
                    ground.observe(tasks[label],{'frame_id':frame,'captured_at':at,'model_id':'QA-ground-fixture',
                        'detections':[] if tick/10<ground_from else [
                            {'label':label,'raw_label':label,'confidence':.9,'bbox_normalized':[.2,.4,.8,.95]}]},at)
            if tick>=20:
                for event in V2._decision_events(app,clock[0]):
                    events.append(event);speech.submit(event)
            speech.tick()
        starts=[r for r in records if r['kind']=='audio_simulated_start']
        completions=[r for r in records if r['kind']=='audio_simulated_done']
        spoken=[r['phrase_id'] for r in starts]
        completed=[r['phrase_id'] for r in completions]
        expected={('stairs_ahead' if l=='stairs' else 'puddle_ahead' if l=='possible_puddle' else 'damaged_pavement') for l in labels}
        if not stale and crowd_enabled:expected.add('crowd_ahead')
        assert set(spoken)==expected,(name,spoken,expected)
        assert all(completed.count(phrase)==1 for phrase in expected),(name,spoken,completed)
        if labels and not ground_from:
            assert spoken[0]!='crowd_ahead','Simultaneous ground must not wait for crowd'
        if 'crowd_ahead' in completed and labels:
            crowd_starts=[x['simulation_started_at'] for x in starts if x['phrase_id']=='crowd_ahead']
            ground_done=[x['finished_at'] for x in completions if x['phrase_id']!='crowd_ahead']
            assert max(crowd_starts)>=max(ground_done)+2-1e-6,'Fresh crowd reassessment must follow the safety hold'
        combine(starts,directory,out/(name+'.wav'))
        return {'name':name,'passed':True,'qa_observations_not_images':True,'physical_audio_tested':False,
                'events':events,'spoken':spoken,'completed':completed,'starts':starts,'records':records,
                'note':'Interrupted starts may retry with fresh evidence; only completed announcements count as delivered.',
                'review_audio':name+'.wav','crowd_delivery':crowd.delivery_state}
    finally:
        speech.close();time.monotonic=original


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--audio-dir',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(exist_ok=True,parents=True)
    reports=[run_case('crowd-alone',[],a.audio_dir,a.out)]
    for label in ['stairs','possible_puddle','damaged_pavement','pothole']:
        reports.append(run_case(label+'-alone',[label],a.audio_dir,a.out,crowd_enabled=False))
    for name,labels in [('stairs-and-crowd',['stairs']),('puddle-and-crowd',['possible_puddle']),
                        ('damage-and-crowd',['damaged_pavement']),('pothole-and-crowd',['pothole']),
                        ('all-families',['stairs','possible_puddle','pothole','damaged_pavement'])]:
        reports.append(run_case(name,labels,a.audio_dir,a.out))
    reports.append(run_case('stale-crowd',['stairs'],a.audio_dir,a.out,stale=True))
    reports.append(run_case('stairs-interrupt-crowd',['stairs'],a.audio_dir,a.out,ground_from=3))
    reference=run_case('ground-only-reference',['stairs','possible_puddle','pothole','damaged_pavement'],a.audio_dir,a.out,crowd_enabled=False)
    combined=next(r for r in reports if r['name']=='all-families')
    times=lambda r:{phrase:min(x['simulation_started_at'] for x in r['starts'] if x['phrase_id']==phrase) for phrase in ['stairs_ahead','damaged_pavement']}
    baseline_times,combined_times=times(reference),times(combined)
    deltas={p:combined_times[p]-baseline_times[p] for p in baseline_times}
    assert all(delta<=.10001 for delta in deltas.values()),deltas
    reports.append(reference)
    (a.out/'results.json').write_text(json.dumps({'cases':reports,'passed':all(r['passed'] for r in reports),
        'critical_ground_start_delta_s':deltas,'critical_timing_basis':'deterministic queue fixture, not hardware latency'},indent=2))
    print(json.dumps({r['name']:r['spoken'] for r in reports},indent=2))


if __name__=='__main__':main()
