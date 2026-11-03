"""Compare the old/new stair admission on identical real model features.

Labels are evaluation-only. One semantic/scene forward per photo, unchanged
encoders, and each checkout's actual select method. This is not field accuracy.
"""
import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0,str(Path(__file__).parents[1]/'pi'))
from cane.vision.association import ground_overlaps_region
from cane.vision.ground import SemanticDetector
from cane.vision.puddle import context_pixels
from cane.vision.stair_context import StairContextRefiner, LABELS, CROP
from build_scene_replay import fit_frame


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--before-source',type=Path,required=True)
    p.add_argument('--models-root',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();cv2.setNumThreads(1)
    spec=importlib.util.spec_from_file_location('cane.vision._old_stair_context',
        a.before_source/'pi/cane/vision/stair_context.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    roi=[[.35,.4],[.65,.4],[.95,1],[.05,1]]
    semantic=SemanticDetector(a.models_root/'v2/segformer',384,.55,1)
    context=StairContextRefiner(a.models_root/'v2/stairs-context',roi,1)
    catalog=json.loads(a.catalog.read_text(encoding='utf8'));cases=[]
    for i,item in enumerate(catalog['items']):
        path=Path(item['asset']);assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
        frame=fit_frame(cv2.imread(str(path)));h,w=frame.shape[:2]
        raw=semantic.predict(frame)
        x1,y1,x2,y2=[round(v*(w if j%2==0 else h)) for j,v in enumerate(CROP)]
        vector=context.net.run(['image_embeddings'],{'pixel_values':context_pixels(frame[y1:y2,x1:x2])})[0][0]
        vector/=np.linalg.norm(vector)
        scores=dict(zip(LABELS,map(float,vector@context.prototypes.T)))
        margin=max(scores[k] for k in ('stairs','upstairs','downstairs'))-max(scores[k] for k in ('flat_ground','floor_objects','other'))
        before=old.StairContextRefiner.select(context,raw['detections'],w,h,scores,margin)
        after=context.select(raw['detections'],w,h,scores,margin)
        eligible=lambda result:[d for d in result['detections'] if d['label']=='stairs' and
            ground_overlaps_region([v/(w if j%2==0 else h) for j,v in enumerate(d['bbox'])],roi)]
        nonstairs=lambda result:[d for d in result['detections'] if d['label']!='stairs']
        assert nonstairs(before)==nonstairs(after)
        truth=None if item.get('score_excluded') else item.get('ground_truth',{}).get('stairs_visible')
        cases.append({**item,'scored_stairs_truth':truth,'before':bool(eligible(before)),
                      'after':bool(eligible(after)),'scores':scores,'margin':margin,
                      'before_detections':eligible(before),'after_detections':eligible(after),
                      'candidates':after['candidate_detections']})
        if i%20==0:print(i+1,'/',len(catalog['items']),flush=True)
    def metrics(rows):
        positives=[c for c in rows if c['scored_stairs_truth'] is True]
        negatives=[c for c in rows if c['scored_stairs_truth'] is False]
        return {'positive_photos':len(positives),'negative_photos':len(negatives),
                'before_true_positives':sum(c['before'] for c in positives),
                'after_true_positives':sum(c['after'] for c in positives),
                'before_false_positives':sum(c['before'] for c in negatives),
                'after_false_positives':sum(c['after'] for c in negatives),
                'new_misses':[c['file'] for c in positives if c['before'] and not c['after']],
                'remaining_misses':[c['file'] for c in positives if not c['after']]}
    groups={}
    for env in ('indoor','outdoor'):
        for direction in ('upstairs','downstairs'):
            groups[env+'/'+direction]=metrics([c for c in cases if c.get('environment')==env and
                c.get('ground_truth',{}).get(direction+'_visible') is True])
    report={'kind':'same_feature_before_after_stair_admission','not_pi_or_field_acceptance':True,
            'cases':cases,'metrics':metrics(cases),'groups':groups,
            'other_semantic_labels_unchanged':True,
            'catalog_sha256':hashlib.sha256(a.catalog.read_bytes()).hexdigest()}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report['metrics'],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
