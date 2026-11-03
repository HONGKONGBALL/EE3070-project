"""Check attributed replay or an explicit live background interval, then restore the camera service."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from summarize_v2_timing import summarize


def status():
    with urllib.request.urlopen('http://127.0.0.1:8000/status',timeout=2) as response:
        return json.load(response)


def restart():
    subprocess.run(['systemctl','--user','daemon-reload'],check=True,timeout=10)
    subprocess.run(['systemctl','--user','restart','smart-cane-camera'],check=True,timeout=20)


def table_config(settings):
    """Camera configuration has flat tables of scalar/array values, all JSON-compatible TOML."""
    return '\n\n'.join('['+name+']\n'+'\n'.join(key+' = '+json.dumps(value)
        for key,value in values.items()) for name,values in settings.items())+'\n'


def frame_in_case(frame_id,case):
    return isinstance(frame_id,int) and ('start_frame' not in case or
        case['start_frame']<=frame_id<=case['end_frame'])


def scene_surface(status,case,now):
    observation=status.get('ground_surface_observation')
    if (not status.get('visual_quality',{}).get('usable') or not observation or now>observation['valid_until'] or
            not frame_in_case(observation['frame_id'],case)):return None
    return observation


def main():
    import fcntl  # Pi/Linux only; does not affect the portable video builder.
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group()
    source.add_argument('--video',type=Path)
    source.add_argument('--background',type=float,metavar='SECONDS',help='Measure real camera background with no simulated scene or distance')
    parser.add_argument('--out',type=Path,default=Path('logs/v2-scene-replay.json'))
    parser.add_argument('--stairs',action='store_true',help='Explicit research-only stair direction trial; never a live default')
    parser.add_argument('--puddle',action='store_true',help='Explicit specialized puddle research trial')
    parser.add_argument('--pothole-yolox',action='store_true',help='Explicit candidate YOLOX NCNN; restores the original config afterward')
    parser.add_argument('--fixture',type=Path,help='Explicit mock distance/association fixture; never real calibration')
    args=parser.parse_args()
    if args.background is not None:
        duration=args.background
        timeline={'cases':[{'id':'background','ground_truth':{},'source_file':'live_camera'}]}
        timeline_path=None
        if args.fixture:raise ValueError('Background camera check uses distance=none')
    else:
        args.video=args.video or Path('testdata/simulation/outdoor-scenes.avi')
        timeline_path=args.video.with_suffix('.json').resolve()
        timeline=json.loads(timeline_path.read_text())
        duration=float(timeline['duration_s'])
    if not 0<duration<=540: raise ValueError('Replay plus recovery must fit within 10 minutes')
    if args.video is not None and not args.video.is_file(): raise FileNotFoundError(args.video)
    lock=Path('logs/v2-scene-replay.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    config=Path('config/camera.v2.toml')
    original=config.read_bytes()
    settings=tomllib.loads(original.decode())
    if settings['distance']['mode']!='none' or settings.get('source',{}).get('video'):
        raise ValueError('Start from live camera and distance=none')
    if args.fixture:
        fixture=tomllib.loads(args.fixture.read_text())
        if set(fixture)-{'distance','association'} or fixture.get('distance',{}).get('mode')!='mock':
            raise ValueError('Fixture may only override mock distance and its simulated association')
        for table,values in fixture.items():settings[table].update(values)
    expected_distance=settings['distance']['mode']
    report={'type':'live_camera_background' if args.background is not None else 'public_photo_replay_real_pi_models','started_at':time.time(),
            'not_outdoor_validation':True,'not_independent_holdout':True,
            'distance_source':expected_distance,'cases':{},'completed':False,'performance':{},
            'base_config_sha256_before':hashlib.sha256(original).hexdigest(),
            'simulated_association_fixture':str(args.fixture) if args.fixture else None}
    report['runtime_source_sha256']={str(path):hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path('pi/cane/arbiter.py'),*(Path('pi/cane/vision')/(name+'.py')
                     for name in ('v2','ground','yolox','puddle','quality','v2_policy','speech'))) if path.is_file()}
    if args.video is not None:
        report['timeline_sha256']=hashlib.sha256(timeline_path.read_bytes()).hexdigest()
        with args.video.open('rb') as source:report['video_sha256']=hashlib.file_digest(source,'sha256').hexdigest()
        report['replay_encoding']=timeline.get('encoding',{'codec':'legacy_not_recorded'})
    if args.stairs:
        if 'stairs' in settings:raise ValueError('Research override requires a base config without a stairs table')
        settings['stairs']={'enabled':True,'directory':'models/v2/stairs-direction-research',
                            'size':416,'confidence':.5,'threads':1,'max_fps':1.0}
        report['research_stair_weights']=json.loads(Path('models/v2/stairs-direction-research/manifest.json').read_text())
    if args.puddle:
        settings['puddle']={'enabled':True,'directory':'models/v2/puddle-research',
                            'size':640,'confidence':.25,'threads':1,'max_fps':1.5}
        report['research_puddle_weights']=json.loads(Path('models/v2/puddle-research/manifest.json').read_text())
    if args.pothole_yolox:
        settings['pothole']={'backend':'yolox_ncnn_research','directory':'models/v2/pothole-yolox-research',
                             'size':640,'confidence':.4,'threads':1,'max_fps':1.5}
        report['research_pothole_weights']=json.loads(Path('models/v2/pothole-yolox-research/manifest.json').read_text())
    if args.video is not None:
        settings['source']={'video':str(args.video.resolve())}
        settings['simulation']={'timeline':str(timeline_path)}
    runtime=Path('/run/user')/str(os.getuid())
    dropin=runtime/'systemd/user/smart-cane-camera.service.d/90-smart-cane-replay.conf'
    if dropin.exists():raise RuntimeError('A replay runtime override already exists; inspect it before starting another')
    runtime_config=None
    override_created=False
    expected={c['id']:c for c in timeline['cases']}
    def interrupted(*_): raise RuntimeError('Replay interrupted; restoring live camera')
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    try:
        # Both files live in /run: a power cycle discards them and starts the unchanged live config.
        with tempfile.NamedTemporaryFile('w',dir=runtime,prefix='smart-cane-replay-',suffix='.toml',delete=False) as file:
            runtime_config=Path(file.name)
            file.write(table_config(settings))
        dropin.parent.mkdir(parents=True,exist_ok=True)
        with dropin.open('x') as file:
            override_created=True
            file.write('[Service]\nExecStart=\nExecStart='+json.dumps(sys.executable)+
                       ' -m cane.vision.v2 --config '+json.dumps(str(runtime_config))+'\n')
        report['runtime_config']=str(runtime_config)
        restart()
        deadline=time.monotonic()+duration+25
        first_replay_at=None
        while time.monotonic()<deadline:
            time.sleep(.25)
            try: s=status()
            except OSError: continue
            if s.get('input_source')!=('live_camera' if args.background is not None else 'replay'): continue
            report['run_id']=s['run_id']
            if first_replay_at is None:
                active=[v for v in s.get('tasks',{}).values() if v['state']!='disabled']
                if not s.get('ready') or not active or not all(v['state']=='ready' for v in active):continue
                first_replay_at=time.monotonic()
            performance=report['performance']
            if time.monotonic()-first_replay_at>=3 and not s['camera']['error']:
                if s['fps']<performance.get('preview_min_fps_after_3s',float('inf')):
                    performance['lowest_preview_sample']={key:s.get(key) for key in
                        ('run_id','frame_id','frame_age_ms','fps','latency_p95_ms','simulation_case','visual_quality')}
                performance['preview_min_fps_after_3s']=min(performance.get('preview_min_fps_after_3s',float('inf')),s['fps'])
            for name,state in s.get('tasks',{}).items():
                if state.get('state')=='ready' and state.get('inference_ms') is not None:
                    performance[name+'_max_inference_ms']=max(performance.get(name+'_max_inference_ms',0),state['inference_ms'])
                    if time.monotonic()-first_replay_at>=3 and not s['camera']['error']:
                        performance[name+'_min_fps_after_3s']=min(performance.get(name+'_min_fps_after_3s',float('inf')),state['fps'])
            if s['distance']['source']!=expected_distance: raise RuntimeError('Replay distance changed unexpectedly')
            scene={'id':'background'} if args.background is not None else s.get('simulation_case')
            if scene:
                case=report['cases'].setdefault(scene['id'],{
                    **expected[scene['id']],'sample_count':0,'raw_detections':{},'candidate_detections':{},'warnings':{},
                    'ground_context_observed':False,'normal_pavement_observed':False,'surface_observations':{},'task_errors':{}})
                case['sample_count']+=1
                if scene.get('first_scene_frame_captured_at') is not None:
                    case['first_scene_frame_captured_at']=scene['first_scene_frame_captured_at']
                surface=scene_surface(s,case,time.monotonic())
                if surface:
                    case['surface_observations'][surface['frame_id']]=surface
                    case['ground_context_observed'] |= surface['context_valid']
                    case['normal_pavement_observed'] |= surface['pavement_detected']
                for d in s['detections']:
                    if not frame_in_case(d['frame_id'],case):continue
                    # Preserve actual frames/models/confidence; never read truth labels for inference.
                    key=':'.join([str(d['frame_id']),d['source_model'],d['label'],str(d['bbox'])])
                    case['raw_detections'][key]=d
                for d in s.get('ground_candidates',[]):
                    if not frame_in_case(d['frame_id'],case):continue
                    key=':'.join([str(d['frame_id']),d['source_model'],d['label'],str(d['bbox'])])
                    case['candidate_detections'][key]=d
                for e in s['recent_events']:
                    if args.background is not None or e.get('simulation_case')==scene['id']:case['warnings'][e['event_id']]=e
                for task,state in s['tasks'].items():
                    if state['state']=='error':case['task_errors'][task]=state['error']
            if args.background is not None and time.monotonic()-first_replay_at>=duration:
                report['observed_background_s']=round(time.monotonic()-first_replay_at,2)
                report['completed']=True
                break
            if args.background is None and s['camera']['error']=='Replay finished':
                report['completed']=len(report['cases'])==len(expected)
                break
        if not report['completed']: report['error']='Replay did not complete all cases within its bound'
    except Exception as exc:
        report['error']=str(exc)
    finally:
        if override_created:dropin.unlink()
        restart()
        if runtime_config is not None:runtime_config.unlink(missing_ok=True)
        report['base_config_sha256_after']=hashlib.sha256(config.read_bytes()).hexdigest()
        report['base_config_unchanged']=report['base_config_sha256_before']==report['base_config_sha256_after']
        report['runtime_override_removed']=not dropin.exists()
        deadline=time.monotonic()+25
        report['restored_live_camera']=False
        while time.monotonic()<deadline:
            time.sleep(.25)
            try: s=status()
            except OSError: continue
            if (s.get('input_source')=='live_camera' and s.get('ready') and
                s.get('distance',{}).get('source')=='none' and
                all(t['state'] in ('ready','disabled') for t in s.get('tasks',{}).values()) and s.get('tasks')):
                report['restored_live_camera']=True
                report['restored_run_id']=s['run_id']
                break
        if report.get('run_id'):
            log=Path('logs')/('camera-'+report['run_id']+'.jsonl')
            report['runtime_log']=str(log)
            records=[json.loads(line) for line in log.read_text().splitlines()]
            report['playback_records']=[r for r in records if r['record_type'].startswith('audio_')]
            report['arbiter_records']=[r for r in records if r['record_type']=='arbiter']
            report['timing']=summarize(records)
        for case in report['cases'].values():
            case['raw_detections']=list(case['raw_detections'].values())
            case['candidate_detections']=list(case['candidate_detections'].values())
            case['warnings']=list(case['warnings'].values())
            case['surface_observations']=list(case['surface_observations'].values())
        report['finished_at']=time.time()
        report['elapsed_s']=round(report['finished_at']-report['started_at'],2)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','playback_records','arbiter_records')},indent=2))
    if not all(report.get(key) for key in ('completed','restored_live_camera','base_config_unchanged','runtime_override_removed')):
        raise SystemExit(1)


if __name__=='__main__':main()
