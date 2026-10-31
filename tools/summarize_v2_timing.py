"""Join actual event and playback identities; software timings are not acoustic/browser latency."""

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np


def distribution(values):
    return {'samples':len(values),'p50_ms':round(float(np.percentile(values,50)),1),
            'p95_ms':round(float(np.percentile(values,95)),1),'max_ms':round(max(values),1)} if values else {'samples':0}


def summarize(rows):
    warnings={r['event_id']:r for r in rows if r['record_type']=='warning'}
    starts={r['playback_id']:r for r in rows if r['record_type']=='audio_start' and r.get('playback_id')}
    stopped={r['playback_id']:r for r in rows if r['record_type']=='audio_stopped' and r.get('playback_id')}
    outcomes={r['playback_id']:r['record_type'] for r in rows if r['record_type'] in ('audio_done','audio_error','audio_interrupted') and r.get('playback_id')}
    bound={}
    preemptions=[]
    for r in rows:
        if r['record_type']!='arbiter':continue
        if r['action']=='played' and r.get('playback_id') in starts:
            bound.setdefault(r['event_id'],[]).append(r['playback_id'])
        if r['action']=='preempted' and r.get('playback_id') in stopped:
            preemptions.append((stopped[r['playback_id']]['stopped_at']-r['monotonic'])*1000)
    events=[]
    for event_id,warning in warnings.items():
        ids=bound.get(event_id,[])
        events.append({'event_id':event_id,'phrase_id':warning['phrase_id'],'scene':warning.get('simulation_case'),
            'confirmation_latency_ms':warning.get('confirmation_latency_ms'),
            'distance_decision_latency_ms':warning.get('distance_decision_latency_ms'),
            'playbacks':[{'playback_id':p,'outcome':outcomes.get(p,'unfinished'),
                          'confirmation_to_process_ms':round((starts[p]['process_started_at']-warning['confirmed_at'])*1000,1)
                          if warning.get('confirmed_at') is not None else None} for p in ids]})
    confirmation=[e['confirmation_latency_ms'] for e in events if e['confirmation_latency_ms'] is not None]
    distance=[e['distance_decision_latency_ms'] for e in events if e['distance_decision_latency_ms'] is not None]
    process=[p['confirmation_to_process_ms'] for e in events for p in e['playbacks'] if p['confirmation_to_process_ms'] is not None]
    return {'type':'software_event_playback_timing','not_acoustic_or_browser_measurement':True,
        'warning_events':len(events),'audio_records':dict(Counter(r['record_type'] for r in rows if r['record_type'].startswith('audio_'))),
        'timings':{'first_visible_capture_to_confirmation':distribution(confirmation),
                   'distance_receive_to_decision':distribution(distance),'confirmation_to_player_process':distribution(process),
                   'preempting_submit_to_old_process_stopped':distribution(preemptions)},
        'legacy_audio_starts_without_identity':sum(r['record_type']=='audio_start' and not r.get('playback_id') for r in rows),
        'events':events}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    report=summarize([json.loads(line) for line in args.log.read_text(encoding='utf-8').splitlines()])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='events'},indent=2))


if __name__=='__main__':main()
