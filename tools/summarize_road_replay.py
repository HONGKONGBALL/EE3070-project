"""Score literal road labels and paired surface observations; no detection is never proof of normal ground."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

KINDS={'pothole':('visible_pothole','pothole_ahead'),
       'visible_damage':('visible_pavement_damage','damaged_pavement'),
       'road_path_semantics':('road_path_semantics',None)}


def metrics(cases):
    positives=[case for case in cases if case['truth'] is True]
    negatives=[case for case in cases if case['truth'] is False]
    evidence=sum(case['timely_by_evidence'] for case in positives)
    scene=sum(case['timely_by_scene'] for case in positives)
    false=sum(case['detected'] for case in negatives)
    enough=len(positives)>=10 and len(negatives)>=10
    return {'positive_cases':len(positives),'positive_detected':sum(case['detected'] for case in positives),
            'positive_timely_by_evidence':evidence,'positive_timely_by_scene':scene,
            'negative_cases':len(negatives),'false_positive_cases':false,
            'ambiguous_cases':len(cases)-len(positives)-len(negatives),
            'passes_by_evidence':bool(enough and evidence/len(positives)>=.8 and false/len(negatives)<=.1),
            'passes_by_scene':bool(enough and scene/len(positives)>=.8 and false/len(negatives)<=.1),
            'misses':[case['file'] for case in positives if not case['detected']],
            'false_positives':[case['file'] for case in negatives if case['detected']]}


def summarize(report,catalog,label_amendments=None):
    if not report.get('completed'):raise ValueError('Cannot score an incomplete replay')
    edits=label_amendments or []
    overrides={}
    items={item['file']:item for item in catalog['items']}
    for edit in edits:
        key=(edit['file'],edit['truth_key'])
        if key in overrides:raise ValueError('Duplicate label amendment')
        if (edit['file'] not in items or edit['truth_key'] not in {pair[0] for pair in KINDS.values()}
                or not edit.get('reason') or edit.get('reviewed_after_predictions') is not True):
            raise ValueError('Label review must be explicit and target a known scene/category')
        if items[edit['file']]['ground_truth'][edit['truth_key']]!=edit['before']:
            raise ValueError('Amendment does not match frozen original label')
        if edit['after'] is not None and type(edit['after']) is not bool:raise ValueError('Truth must be boolean or null')
        overrides[key]=edit['after']
    source={case['source_file']:case for case in report['cases'].values()}
    by_kind={kind:[] for kind in KINDS}
    for item in catalog['items']:
        if item['file'] not in source:raise ValueError('Missing scene: '+item['file'])
        case=source[item['file']]
        if case['ground_truth']!=item['ground_truth']:raise ValueError('Truth differs from frozen replay: '+item['file'])
        onset=case.get('first_scene_frame_captured_at')
        for kind,(truth_key,phrase) in KINDS.items():
            if phrase:
                observations=[event for event in case['warnings'] if event.get('kind')=='GROUND_HAZARD' and event['phrase_id']==phrase]
                evidence=[event['confirmation_latency_ms'] for event in observations if event.get('confirmation_latency_ms') is not None]
                since_scene=[(event['confirmed_at']-onset)*1000 for event in observations
                             if onset is not None and event.get('confirmed_at') is not None]
            else:
                observations=[surface for surface in case.get('surface_observations',[]) if surface['pavement_detected']
                    and case['start_frame']<=surface['frame_id']<=case['end_frame']]
                evidence=[(surface['observed_at']-surface['captured_at'])*1000 for surface in observations]
                since_scene=[(surface['observed_at']-onset)*1000 for surface in observations if onset is not None]
            by_kind[kind].append({'file':item['file'],'scene_group':item.get('scene_group',item['file']),
                'truth':overrides.get((item['file'],truth_key),item['ground_truth'].get(truth_key)),'detected':bool(observations),
                'timely_by_evidence':any(0<=latency<=3000 for latency in evidence),
                'timely_by_scene':any(0<=latency<=3000 for latency in since_scene),
                'earliest_evidence_latency_ms':round(min(evidence),1) if evidence else None,
                'earliest_scene_latency_ms':round(min(since_scene),1) if since_scene else None,
                'observation_count':len(observations)})
    scores={}
    for kind,cases in by_kind.items():
        first={}
        for case in cases:first.setdefault(case['scene_group'],case)
        scores[kind]={'photos':metrics(cases),'first_photo_per_group':metrics(list(first.values()))}
    return {'run_id':report['run_id'],'not_outdoor_validation':True,'not_physical_height_validation':True,
        'model_training_overlap':'unknown','no_training':True,
        'group_selection':'First frozen catalog entry per metadata group, never best-result selection',
        'normal_semantics_basis':'Explicit road/sidewalk/path classification; may coexist with damage; not absence of risk or proof of safety',
        'evidence_time_basis':'Ground event: first valid evidence capture to confirmation; surface: its capture to observation',
        'scene_time_basis':'First scene frame observed by renderer to result/event; decode delivery, not exposure or sound',
        'post_prediction_label_review':bool(edits),'label_amendments':edits,
        'metrics':scores,'cases':by_kind,'performance':report['performance'],
        'audio_outcomes':dict(Counter(record['record_type'] for record in report['playback_records'])),
        'event_phrase_counts':dict(Counter(event['phrase_id'] for case in report['cases'].values() for event in case['warnings'])),
        'timing':report['timing']['timings'],'elapsed_s':report['elapsed_s'],
        'task_errors':{case['source_file']:case['task_errors'] for case in report['cases'].values() if case['task_errors']},
        'restored_live_camera':report['restored_live_camera']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--label-amendments',type=Path,help='Explicit post-prediction human/agent review, originals stay frozen')
    args=parser.parse_args()
    edits=[]
    if args.label_amendments:
        review=json.loads(args.label_amendments.read_text(encoding='utf-8'))
        if (review['catalog_sha256']!=hashlib.sha256(args.catalog.read_bytes()).hexdigest() or
                review['report_sha256']!=hashlib.sha256(args.report.read_bytes()).hexdigest()):
            raise ValueError('Label review hashes do not match these original inputs')
        edits=review['edits']
    result=summarize(json.loads(args.report.read_text(encoding='utf-8')),
                     json.loads(args.catalog.read_text(encoding='utf-8')),edits)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in result.items() if key!='cases'},indent=2))


if __name__=='__main__':main()
