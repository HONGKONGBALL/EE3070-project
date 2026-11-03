"""Score frozen scenes by split and first catalog representative, without choosing the best image per group."""

import argparse
from collections import Counter
import json
from pathlib import Path


def counts(cases):
    positive=[c for c in cases if c['visible_puddle'] is True]
    negative=[c for c in cases if c['visible_puddle'] is False]
    return {'positive_cases':len(positive),'positive_warning_cases':sum(c['warned'] for c in positive),
            'positive_timely_warning_cases':sum(c['timely'] for c in positive),
            'negative_cases':len(negative),'false_warning_cases':sum(c['warned'] for c in negative),
            'ambiguous_cases':len(cases)-len(positive)-len(negative),
            'positive_misses':[c['file'] for c in positive if not c['warned']],
            'false_warnings':[c['file'] for c in negative if c['warned']]}


def summarize(report,catalog):
    if not report.get('completed'):raise ValueError('Cannot score an incomplete replay as completed')
    case_by_file={case['source_file']:case for case in report['cases'].values()}
    cases=[]
    for item in catalog['items']:
        case=case_by_file.get(item['file'])
        if case is None:raise ValueError('Missing replay case: '+item['file'])
        warnings=[e for e in case['warnings'] if e['phrase_id']=='puddle_ahead']
        cases.append({'file':item['file'],'scene_group':item.get('scene_group',item['file']),
            'split':item['evaluation_split'],'lighting':item.get('lighting','not_separately_measured'),
            'visible_puddle':item['ground_truth']['visible_puddle'],'warned':bool(warnings),
            'timely':any(e.get('confirmation_latency_ms',float('inf'))<=3000 for e in warnings),
            'confirmation_latency_ms':[e.get('confirmation_latency_ms') for e in warnings],
            'raw_detector_candidate':any(d['label']=='possible_puddle' for d in case['candidate_detections']),
            'accepted_candidate':any(d['label']=='possible_puddle' for d in case['raw_detections']),
            'warning_event_ids':[e['event_id'] for e in warnings],
            'task_errors':case['task_errors']})
    splits={}
    for split in dict.fromkeys(c['split'] for c in cases):
        subset=[c for c in cases if c['split']==split]
        representatives={}
        for case in subset:
            representatives.setdefault(case['scene_group'],case)
        first=list(representatives.values())
        splits[split]={'photos':counts(subset),'first_photo_per_metadata_group':counts(first),
            'representatives':[c['file'] for c in first],
            'night_photos':counts([c for c in subset if c['lighting']=='night'])}
    return {'run_id':report['run_id'],'not_outdoor_validation':True,'model_training_overlap':'unknown',
            'group_selection':'First catalog entry per metadata scene group; no best-result selection',
            'timely_basis':'First valid model evidence capture to confirmation, not first exposure or acoustic output',
            'splits':splits,'cases':cases,'performance':report['performance'],'elapsed_s':report['elapsed_s'],
            'audio_outcomes':dict(Counter(r['record_type'] for r in report['playback_records'])),
            'timing':report['timing']['timings'],
            'restored_live_camera':report['restored_live_camera']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=summarize(json.loads(args.report.read_text(encoding='utf-8')),
                     json.loads(args.catalog.read_text(encoding='utf-8')))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},indent=2))


if __name__=='__main__':main()
