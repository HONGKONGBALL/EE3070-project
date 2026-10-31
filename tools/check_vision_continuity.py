"""Real models, no scene resets: timed photographs, HTTP load and an explicit camera fault.

The camera scheduler is a QA fixture in this process only. It is not a USB or Pi
power/recovery test. Audio is disabled; events and queue records are inspected.
"""
import argparse
from collections import Counter
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi'))
from cane.vision import v2
from cane.vision.camera import Camera
from cane.vision.demo import ROOT, handler_for
from build_scene_replay import fit_frame


def working_set_mb():
    if os.name!='nt': return None
    from ctypes import wintypes
    class Memory(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in
            ('peak','working','paged_peak','paged','nonpaged_peak','nonpaged','pagefile','pagefile_peak')]
    m=Memory();m.cb=ctypes.sizeof(m)
    ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
    if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(m),m.cb):
        return round(m.working/1024**2,2)
    return None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phases',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--models-root',type=Path,required=True);p.add_argument('--mode',choices=['ground','balanced'],default='ground')
    p.add_argument('--clients',type=int,default=3);a=p.parse_args()
    if not 1<=a.clients<=4: p.error('Use one to four HTTP clients')
    fixture=json.loads(a.phases.read_text(encoding='utf8'));phases=fixture['phases'];cursor=0
    for phase in phases:
        phase['start_s']=cursor;cursor+=phase['seconds'];phase['end_s']=cursor
        if phase.get('asset'):
            path=Path(phase['asset']);actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if actual!=phase['sha256']:raise ValueError('Fixture asset checksum mismatch')
            phase['_frame']=fit_frame(cv2.imread(str(path)))
        else:phase['_frame']=np.zeros((480,640,3),np.uint8)
    if not 5<=cursor<=300:raise ValueError('Fixture must be 5–300 seconds')

    class FixtureCamera(Camera):
        def __init__(self,*args):
            super().__init__(*args);self.phase=None;self.phase_started_at=None;self.elapsed=0
        def _run(self):
            started=time.monotonic();self.fixture_started=started;self.media_fps=15
            while not self.stopped.is_set():
                now=time.monotonic();elapsed=now-started;self.elapsed=elapsed
                phase=next((s for s in phases if s['start_s']<=elapsed<s['end_s']),None)
                if phase is None:self.error='Replay finished';break
                if self.phase is not phase:self.phase_started_at=now
                self.phase=phase
                with self.condition:
                    if phase.get('camera_fault'):
                        self.frame=None;self.error='Injected QA camera outage'
                    else:
                        self.frame=phase['_frame'];self.sequence+=1;self.captured_at=now
                        self.delivered.append(now);self.error=''
                    self.condition.notify_all()
                self.stopped.wait(max(0,1/15-(time.monotonic()-now)))

    original=v2.ReplayCamera;v2.ReplayCamera=FixtureCamera
    config=v2.load_config(ROOT/'config/camera.v2.toml',mode=a.mode,models_root=a.models_root)
    config['source']={'video':'explicit-in-memory-QA-photo-schedule'}
    config['distance']['mode']='none';config['audio']['enabled']=False;config.pop('simulation',None)
    app=v2.V2(config)
    assert not app.timeline, 'Continuity must not reset the policy for each phase'
    server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(app));server.daemon_threads=True
    base=f'http://127.0.0.1:{server.server_port}'
    stop=threading.Event();lock=threading.Lock();responses=[];errors=[];samples=[];events={};phase_rows={}
    service=threading.Thread(target=server.serve_forever,daemon=True)
    def load(client):
        i=0
        while not stop.is_set():
            route='/status' if i%2==0 else '/frame.jpg';i+=1;began=time.perf_counter()
            try:
                try:
                    with urllib.request.urlopen(base+route,timeout=2) as response:
                        code=response.status;body=response.read()
                except urllib.error.HTTPError as exc:code=exc.code;body=exc.read()
                valid=(code==200 and (isinstance(json.loads(body),dict) if route=='/status' else body.startswith(b'\xff\xd8')))
                with lock:
                    responses.append({'phase':app.camera.phase['name'] if app.camera.phase else None,
                        'fixture_elapsed_s':app.camera.elapsed,'client':client,'route':route,'code':code,
                        'ms':(time.perf_counter()-began)*1000,'bytes':len(body),'valid':valid})
            except Exception as exc:
                with lock:errors.append(str(exc))
            stop.wait(.12)
    workers=[threading.Thread(target=load,args=(i,),daemon=True) for i in range(a.clients)]
    began=time.monotonic();cpu=time.process_time();report={'type':'local_real_model_continuity_and_http_stress',
        'not_live_camera':True,'not_pi_performance':True,'audio_playback_tested':False,'no_policy_scene_resets':True,
        'mode':a.mode,'clients':a.clients,'schedule_sha256':hashlib.sha256(a.phases.read_bytes()).hexdigest(),
        'config':config,'phase_schedule':[{k:v for k,v in s.items() if k!='_frame'} for s in phases]}
    try:
        app.start();service.start()
        for t in workers:t.start()
        while time.monotonic()-began<cursor+12:
            status=app.status();phase=app.camera.phase
            if phase:
                name=phase['name'];row=phase_rows.setdefault(name,{'statuses':0,'events':[],'object_labels':set(),'task_frames':{},'quality_unusable':0,'ready':0,'not_ready':0,'fps':[]})
                row['statuses']+=1;row['ready' if status['ready'] else 'not_ready']+=1
                if not status['visual_quality'].get('usable'):row['quality_unusable']+=1
                if status['ready']:row['fps'].append(status['fps'])
                for d in status['detections']:
                    if d['source_model']=='yolo11n-coco-ncnn':row['object_labels'].add(d['label'])
                for task,state in status['tasks'].items():
                    if state.get('frame_id'):row['task_frames'].setdefault(task,set()).add(state['frame_id'])
                for e in status['recent_events']:
                    if e['event_id'] not in events:
                        events[e['event_id']]={**e,'observed_phase':name};row['events'].append(events[e['event_id']])
                samples.append({'t':round(time.monotonic()-began,3),'phase':name,'ready':status['ready'],
                    'camera':status['camera'],'visual_quality':status['visual_quality'],'tasks':status['tasks'],
                    'fps':status['fps'],'detections_count':len(status['detections']),'memory_mb':working_set_mb()})
            if status['camera']['error']=='Replay finished':report['completed']=True;break
            time.sleep(.2)
    except Exception as exc:report['error']=str(exc)
    finally:
        stop.set()
        for t in workers:
            if t.ident:t.join(timeout=3)
        server.shutdown();server.server_close();app.close();v2.ReplayCamera=original
    report['elapsed_s']=round(time.monotonic()-began,2);report['cpu_process_seconds']=round(time.process_time()-cpu,2)
    report['events']=list(events.values());report['run_id']=app.run_id;report['runtime_log']=str(app.log_path)
    report['http_errors']=errors;report['http_request_count']=len(responses)
    report['task_errors']={name:state['error'] for s in samples for name,state in s['tasks'].items() if state['state']=='error'}
    report['http_metrics']={route:{'requests':len(rows),'p95_ms':round(float(np.percentile([x['ms'] for x in rows],95)),2),
        'max_ms':round(max(x['ms'] for x in rows),2),'codes':dict(Counter(x['code'] for x in rows))} for route in ('/status','/frame.jpg') if (rows:=[x for x in responses if x['route']==route])}
    report['phases']={}
    for name,row in phase_rows.items():
        row['object_labels']=sorted(row['object_labels']);row['task_frames']={k:len(v) for k,v in row['task_frames'].items()}
        row['preview_fps_median']=round(float(np.median(row.pop('fps'))),2) if row['ready'] else 0
        report['phases'][name]=row
    stable=[s['memory_mb'] for s in samples if s['t']>=15 and s['memory_mb'] is not None]
    report['memory']={'first_after_15s_mb':stable[0] if stable else None,'last_mb':stable[-1] if stable else None,
        'growth_mb':round(stable[-1]-stable[0],2) if stable else None,'peak_mb':max(stable) if stable else None}
    fault_names={s['name'] for s in phases if s.get('camera_fault')}
    fault_starts={s['name']:s['start_s'] for s in phases if s.get('camera_fault')}
    # Ignore the brief request straddling a phase boundary, not a settled outage.
    fault_responses=[r for r in responses if r['phase'] in fault_names and r['route']=='/frame.jpg'
                    and r['fixture_elapsed_s']-fault_starts[r['phase']]>.4]
    report['checks']={'no_runtime_model_errors':not report['task_errors'], 'no_http_exceptions':not errors,
        'successful_http_responses_well_formed':all(r['valid'] for r in responses if r['code']==200),
        'no_object_distance_bypass':not any(e['kind']=='OBJECT_NEAR' for e in events.values()),
        'no_stale_ground_event':all(e['created_at']-e['captured_at']<=1.5 for e in events.values() if e['kind']=='GROUND_HAZARD'),
        'no_directional_stair_speech':not any(e['object_class'] in ('upstairs','downstairs') for e in events.values()),
        'camera_fault_returns_503':all(r['code']==503 for r in fault_responses) if fault_names and fault_responses else None}
    for s in phases:
        row=report['phases'].get(s['name'],{})
        if 'expected_event' in s:
            count=sum(e['object_class']==s['expected_event'] for e in row.get('events',[]))
            report['checks'][s['name']+'_event_count']=count==s.get('expected_count',1)
        if s.get('object_expected_any'):
            report['checks'][s['name']+'_object_detected']=bool(set(s['object_expected_any'])&set(row.get('object_labels',[])))
        if s.get('quality_unusable_expected'):
            report['checks'][s['name']+'_quality_gate']=row.get('quality_unusable',0)>0
        if s.get('recovery_expected'):
            report['checks'][s['name']+'_fresh_preview_recovered']=row.get('ready',0)>0
    records=[json.loads(line) for line in app.log_path.read_text(encoding='utf8').splitlines()]
    report['audio_and_queue_records']={'record_counts':dict(Counter(r['record_type'] for r in records)),
        'arbiter_actions':dict(Counter(r['action'] for r in records if r['record_type']=='arbiter')),
        'note':'Audio disabled; audio_disabled/arbiter records do not prove physical playback.'}
    report['engineering_passed']=bool(report.get('completed')) and all(v is not False for v in report['checks'].values())
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    a.out.with_suffix('.samples.json').write_text(json.dumps({'samples':samples,'responses':responses},indent=2),encoding='utf8')
    print(json.dumps({k:report[k] for k in ('completed','elapsed_s','engineering_passed','checks','http_metrics','memory') if k in report},indent=2))

if __name__=='__main__':main()
