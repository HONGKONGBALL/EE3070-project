"""Evaluate the exact runtime adapter on attributed photos; truth is used only for reporting."""

import argparse
import json
import hashlib
from pathlib import Path
import time

import cv2

from build_scene_replay import fit_frame
from cane.vision.puddle import PuddleDetector


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--assets',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--held-out',action='store_true',help='Declare scenes frozen before this engineering pipeline was evaluated; training overlap remains unknown')
    args=parser.parse_args()
    cv2.setNumThreads(1)
    detector=PuddleDetector(args.model,ground_roi=[[.35,.4],[.65,.4],[.95,1],[.05,1]])
    catalog=json.loads(args.catalog.read_text(encoding='utf-8'))
    cases=[]
    started=time.monotonic()
    for item in catalog['items']:
        if time.monotonic()-started>540:raise RuntimeError('Photo evaluation exceeded its bounded budget')
        image=cv2.imread(str(args.assets/item['file']))
        if image is None:raise ValueError('Cannot read '+item['file'])
        began=time.perf_counter()
        result=detector.predict(fit_frame(image))
        cases.append({**item,**result,'inference_ms':round((time.perf_counter()-began)*1000,1),
                      'puddle_candidate':bool(result['detections'])})
    positives=[c for c in cases if c.get('ground_truth',{}).get('visible_puddle') is True]
    negatives=[c for c in cases if c.get('ground_truth',{}).get('visible_puddle') is False]
    metrics={'positive_photos':len(positives),'positive_candidates':sum(c['puddle_candidate'] for c in positives),
             'negative_photos':len(negatives),'false_candidates':sum(c['puddle_candidate'] for c in negatives),
             'ambiguous_photos':len(cases)-len(positives)-len(negatives)}
    report={'type':'exact_runtime_adapter_photo_evaluation','not_outdoor_validation':True,
            'development_data':not args.held_out,'held_out_from_engineering':args.held_out,
            'model_training_overlap':'unknown','catalog_sha256':hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
            'adapter_sha256':hashlib.sha256((Path(__file__).resolve().parents[1]/'pi/cane/vision/puddle.py').read_bytes()).hexdigest(),
            'training_performed':False,'model_manifest':detector.manifest,
            'metrics':metrics,'elapsed_s':round(time.monotonic()-started,2),'cases':cases}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(metrics))


if __name__=='__main__':main()
