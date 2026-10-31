"""Offline real-model research evaluation. Truth labels never enter model or warning input."""

import argparse
import json
from pathlib import Path

import cv2

from build_scene_replay import fit_frame
from cane.vision.association import in_region
from cane.vision.ground import SemanticDetector
from cane.vision.stairs import StairDirectionDetector, confirm_stairs


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--assets',type=Path,required=True)
    parser.add_argument('--models',type=Path,default=Path('models/v2'))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    cv2.setNumThreads(1)
    direction=StairDirectionDetector(args.models/'stairs-direction-research')
    semantic=SemanticDetector(args.models/'segformer')
    catalog=json.loads(args.catalog.read_text(encoding='utf-8'))
    cases=[]
    for item in catalog['items']:
        image=cv2.imread(str(args.assets/item['file']))
        if image is None:raise ValueError('Photo cannot be read: '+item['file'])
        image=fit_frame(image)
        result=direction.predict(image);segments=semantic.predict(image)
        # Both models read the exact same still image; these are pairing fixtures, not exposure times.
        result.update(captured_at=1)
        segments.update(captured_at=1,frame_id=1,model_id=semantic.model_id,model_revision=semantic.manifest['revision'])
        confirmed=confirm_stairs(result,segments)['detections']
        eligible=[d for d in confirmed if in_region([v/(640 if i%2==0 else 480) for i,v in enumerate(d['bbox'])],
                    [[.35,.4],[.65,.4],[.95,1],[.05,1]])]
        cases.append({**item,'raw_detections':result['detections'],'confirmed_detections':confirmed,
                      'eligible_for_temporal_policy':eligible})
    positives=[c for c in cases if c.get('ground_truth',{}).get('downstairs_visible') is True]
    negatives=[c for c in cases if c.get('ground_truth',{}).get('downstairs_visible') is False]
    metrics={'positive_photos':len(positives),'negative_photos':len(negatives),
             'positive_photos_eligible':sum(bool(c['eligible_for_temporal_policy']) for c in positives),
             'negative_photos_eligible':sum(bool(c['eligible_for_temporal_policy']) for c in negatives)}
    report={'type':'offline_real_model_still_photo_evaluation','not_outdoor_validation':True,
            'not_temporal_or_audio_validation':True,'model_revision':direction.manifest['revision'],
            'thresholds':{'direction':.5,'semantic':.55,'stair_pixel_fraction':.1},
            'metrics':metrics,'cases':cases}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2))
    print(json.dumps(metrics))


if __name__=='__main__':main()
