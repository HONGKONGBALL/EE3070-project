"""18-second real Pi / explicitly mock distance check; always restore none mode."""

import json
from pathlib import Path
import subprocess
import time
import urllib.request


def status():
    with urllib.request.urlopen('http://127.0.0.1:8000/status',timeout=2) as response:
        return json.load(response)


def main():
    config=Path('config/camera.v2.toml')
    original=config.read_bytes()
    report={'type':'real_pi_live_camera_with_mock_distance','started_at':time.time(),'events':{},'audio':[]}
    out=Path('logs/v2-mock-runtime.json')
    try:
        text=original.decode()
        if 'mode = "none"' not in text: raise RuntimeError('Expected original distance=none config')
        config.write_text(text.replace('mode = "none"','mode = "mock"',1))
        subprocess.run(['systemctl','--user','restart','smart-cane-camera'],check=True)
        deadline=time.monotonic()+18
        while time.monotonic()<deadline:
            time.sleep(.25)
            try: s=status()
            except OSError: continue
            if s.get('version')!=2: continue
            report['run_id']=s['run_id']
            if s['distance']['source']!='mock': raise RuntimeError('Wrong distance mode')
            for event in s['recent_events']: report['events'][event['event_id']]=event
            if s['audio']['last_played'] and s['audio']['last_played'] not in report['audio']:
                report['audio'].append(s['audio']['last_played'])
        near=[e for e in report['events'].values() if e['kind']=='OBJECT_NEAR']
        report['passed']=(len(near)==1 and near[0]['phrase_id']=='obstacle_ahead'
                          and near[0]['object_class'] is None and near[0]['association']=='unmatched'
                          and near[0]['source']=='vision+mock_distance'
                          and any(a['phrase_id']=='obstacle_ahead' for a in report['audio']))
        report['events']=list(report['events'].values())
    except Exception as exc:
        report['passed'],report['error']=False,str(exc)
    finally:
        config.write_bytes(original)
        subprocess.run(['systemctl','--user','restart','smart-cane-camera'],check=True)
        report['restored_distance_mode']='none'
        report['finished_at']=time.time()
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)
    if not report['passed']: raise SystemExit(1)


if __name__=='__main__': main()
