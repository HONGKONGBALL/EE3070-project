"""Deterministic real-model outputs on the frozen 125-image collection.

Run in separate processes against the saved source snapshot and new source.
Expected scene labels are not inputs to any model. No runtime/audio simulation.
"""
import argparse
import ast
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import cv2
import numpy as np


def stable(value):
    if isinstance(value, np.ndarray):
        return {'array_sha256': hashlib.sha256(value.tobytes()).hexdigest(),
                'shape':list(value.shape),'dtype':str(value.dtype)}
    if isinstance(value, dict):
        return {k:stable(v) for k,v in value.items() if not k.endswith('_at') and not k.endswith('_ms')}
    if isinstance(value, (list,tuple)):return [stable(v) for v in value]
    if isinstance(value, np.generic):return value.item()
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--models-root',type=Path,required=True)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    sys.path.insert(0,str(a.source_root/'pi'))
    from cane.vision.v2 import load_config
    import cane.vision.v2 as runtime
    from cane.vision.ground import RoadDetector, SemanticDetector
    from cane.vision.yolox import NcnnYoloXPotholeDetector
    from cane.vision.puddle import PuddleDetector
    from cane.vision.quality import assess_frame
    from build_scene_replay import fit_frame
    # Execute the actual source admission block from each checkout, not a second
    # handwritten implementation. Three QA observations per still are labelled
    # synthetic policy time; this is not a walking-video accuracy measurement.
    tree=ast.parse((a.source_root/'pi/cane/vision/v2.py').read_text(encoding='utf8'))
    loop=next(n for cls in tree.body if isinstance(cls,ast.ClassDef) and cls.name=='V2'
              for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_render_loop')
    while_body=next(n.body for n in loop.body if isinstance(n,ast.While))
    def assigns(node,name):
        return isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets)
    begin=next(i for i,n in enumerate(while_body) if assigns(n,'semantic'))
    end=next(i for i,n in enumerate(while_body[begin:],begin) if assigns(n,'events'))
    function=ast.parse('def qa_admit(self,snapshots,now,image):\n objects=None\n return None').body[0]
    function.body=function.body[:1]+while_body[begin:end]+[ast.Return(value=ast.Name(id='ground_inputs',ctx=ast.Load()))]
    module=ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
    scope=dict(vars(runtime));exec(compile(module,'source-ground-admission','exec'),scope)
    admit=scope['qa_admit']
    cv2.setNumThreads(1)
    config=load_config(a.source_root/'config/camera.v2.toml',mode='ground',models_root=a.models_root)
    models={}
    for name,factory in [('road',RoadDetector),('pothole',NcnnYoloXPotholeDetector),('puddle',PuddleDetector),('semantic',SemanticDetector)]:
        c=config[name];extra={}
        if name=='puddle':extra={'ground_roi':config['warning']['ground_roi']}
        if name=='semantic':extra={'ground_roi':config['warning']['ground_roi'],
                                  'stair_context_directory':Path(c['stair_context_directory'])}
        models[name]=factory(Path(c['directory']),c['size'],c['confidence'],c['threads'],**extra)
    catalog=json.loads(a.catalog.read_text(encoding='utf8'))
    report={'kind':'real_ground_model_regression_not_field_accuracy','source_root':str(a.source_root),
            'catalog_sha256':hashlib.sha256(a.catalog.read_bytes()).hexdigest(),'cases':[]}
    started=time.monotonic()
    for index,item in enumerate(catalog['items']):
        source=Path(item['original_asset'])
        assert hashlib.sha256(source.read_bytes()).hexdigest()==item['sha256'],str(source)
        frame=fit_frame(cv2.imread(str(source)));quality=assess_frame(frame)
        outputs={}
        raw={}
        for name,model in models.items():
            result=model.predict(frame) if quality['usable'] else {'detections':[],'quality_skip':True}
            outputs[name]=stable(result)
            raw[name]=result
        policy=runtime.V2Policy('qa125',config['warning']['roi'],{},ground_roi=config['warning']['ground_roi'],warning=config['warning'])
        app=SimpleNamespace(config=config,policy=policy,ground_tasks=models,crowd=None)
        admitted=[];events=[]
        for number,at in enumerate([100.,100.7,101.4],1):
            snapshots=copy.deepcopy(raw) if quality['usable'] else {}
            for name,result in snapshots.items():
                result.update(frame_id=number,captured_at=at,model_id=models[name].model_id,visual_quality=quality)
                result['candidates_observed_at']=at;result['verification_completed_at']=at
                for d in result['detections']+result.get('candidate_detections',[]):
                    d['bbox_normalized']=[float(v)/(640 if j%2==0 else 480) for j,v in enumerate(d['bbox'])]
                    d.update(frame_id=number,captured_at=at,candidate_observed_at=at,verification_completed_at=at)
            admitted.append(stable(admit(app,snapshots,at,frame)))
            new=policy.step(None,at);events.extend(new)
            for e in new:policy.episodes.delivery(e['event_id'],'disabled',at)
        report['cases'].append({'file':item['file'],'source_sha256':item['sha256'],'quality':quality,
                                'outputs':outputs,'output_sha256':hashlib.sha256(json.dumps(outputs,sort_keys=True).encode()).hexdigest(),
                                'admitted':admitted,'events':events,
                                'policy_time_basis':'Three synthetic QA observations of one fixed image, not independent real frames'})
        if index%20==0:print(index+1,'/',len(catalog['items']),flush=True)
    report['elapsed_s']=time.monotonic()-started
    a.out.parent.mkdir(exist_ok=True,parents=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'completed':len(report['cases']),'elapsed_s':report['elapsed_s']}),flush=True)


if __name__=='__main__':main()
