"""Real moving videos through all existing ground tasks plus the one person task.

Preserves the real runtime, camera geometry, scheduler, confirmation and queue.
Output audio uses timed WAV simulation, not speakers. No still-image timeline.
"""
import argparse
import hashlib
import json
from pathlib import Path
from collections import Counter

from check_real_videos import run_clip


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--config',type=Path,default=Path(__file__).parents[1]/'config/camera.v2.toml')
    p.add_argument('--models-root',type=Path,required=True)
    p.add_argument('--audio-dir',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--no-review-video',action='store_true',
                   help='Keep model/status/audio records without re-encoding a review video')
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    catalog=json.loads(a.catalog.read_text(encoding='utf8'))
    result={'kind':'full_runtime_real_video_awareness','not_pi_benchmark':True,'physical_audio_tested':False,
            'catalog_sha256':hashlib.sha256(a.catalog.read_bytes()).hexdigest(),'clips':[]}
    for clip in catalog['clips']:
        report=run_clip(clip,a.config,a.models_root,a.out,a.audio_dir,
                        encode_review=not a.no_review_video,mode='awareness')
        report['source']=clip.get('source_page',clip.get('source'))
        report['source_notes']=clip.get('label_notes','')
        samples=json.loads((a.out/(clip['id']+'-samples.json')).read_text(encoding='utf8'))
        report['crowd_state_counts']=dict(Counter(s['status']['crowd']['state'] for s in samples))
        report['person_count_max']=max((s['status']['crowd'].get('evidence',{}).get('person_count',0) for s in samples),default=0)
        report['unwanted_labels']=sorted({d['label'] for s in samples for d in s['status']['detections']
            if d['label'] not in {'person','possible_puddle','damaged_pavement','pothole','stairs','downstairs'}})
        report['no_individual_object_events']=not any(e['kind']=='OBJECT_NEAR' for e in report['events'])
        report['simulated_spoken_categories']=dict(Counter(r.get('warning_family') for r in report['audio_records'] if r['record_type']=='audio_simulated_start'))
        result['clips'].append(report)
        (a.out/'all-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'id':clip['id'],'crowd_states':report['crowd_state_counts'],
                          'spoken':report['simulated_spoken_categories'],'unwanted':report['unwanted_labels']},ensure_ascii=False),flush=True)
    result['completed']=len(result['clips'])==len(catalog['clips']) and all(c['completed'] for c in result['clips'])
    result['runtime_checks_pass']=result['completed'] and all(not c['task_errors'] and not c['unwanted_labels'] and c['no_individual_object_events'] for c in result['clips'])
    (a.out/'all-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
