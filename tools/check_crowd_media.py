"""Real person-model evaluation: still appearance and actual sampled video frames.

Stills do not invent independent observations or crowd warning events. Video
time is source media time, not Pi latency; muted deliveries are labelled so.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi'))
from cane.vision.detector import Detector
from cane.vision.crowd import CrowdAwareness, crowd_evidence, DEFAULTS
from cane.vision.quality import assess_frame
from cane.vision.ground import SemanticDetector
from build_scene_replay import fit_frame


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--models-root',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True);cv2.setNumThreads(1)
    catalog=json.loads(a.catalog.read_text(encoding='utf8'))
    cfg={**DEFAULTS,**catalog.get('crowd_settings',{})}
    detector=Detector(a.models_root/'yolo11n',cfg['person_size'],cfg['confidence'],1,classes=(0,),max_detections=80)
    semantic_model=SemanticDetector(a.models_root/'v2/segformer',384,.55,1)
    report={'kind':'real_person_model_and_crowd_appearance_video_evaluation','not_pi_test':True,
            'physical_audio_tested':False,'catalog_sha256':hashlib.sha256(a.catalog.read_bytes()).hexdigest(),
            'settings':cfg,'cases':[]}
    for case in catalog['items']:
        source=Path(case['file']);assert hashlib.sha256(source.read_bytes()).hexdigest()==case['sha256']
        folder=a.out/case['id'];folder.mkdir(exist_ok=True)
        video=case['kind']=='video';engine=CrowdAwareness(case['id'],cfg)
        cap=cv2.VideoCapture(str(source)) if video else None
        fps=cap.get(cv2.CAP_PROP_FPS) if video else 1
        step=max(1,round(fps*cfg['interval_s']));index=0;rows=[];events=[]
        writer=None
        try:
            while True:
                if video:ok,original=cap.read()
                elif index==0:original=cv2.imread(str(source));ok=original is not None
                else:break
                if not ok:break
                frame_id=index+1;at=index/fps;index+=1
                if (index-1)%step:continue
                frame=fit_frame(original);quality=assess_frame(frame)
                started=time.perf_counter()
                detections=detector.predict(frame) if quality['usable'] else []
                inference_ms=(time.perf_counter()-started)*1000
                semantic_started=time.perf_counter()
                semantic=semantic_model.predict(frame) if quality['usable'] else None
                semantic_ms=(time.perf_counter()-semantic_started)*1000
                if semantic is not None:semantic['captured_at']=at
                for d in detections:d['bbox_normalized']=[v/(640 if i%2==0 else 480) for i,v in enumerate(d['bbox'])]
                started=time.perf_counter()
                evidence=crowd_evidence(detections,cfg,semantic) if quality['usable'] else {'candidate':False,'reasons':['image_quality'],'person_count':0}
                processing_ms=(time.perf_counter()-started)*1000
                state=None
                if video:
                    engine.observe({'frame_id':frame_id,'captured_at':at,'model_id':detector.model_id,
                                    'detections':detections,'visual_quality':quality},at,semantic)
                    new=engine.step(at);events.extend(new)
                    for event in new:engine.delivery(event['event_id'],'disabled',at)
                    state=engine.status(at)
                for d in detections:
                    x1,y1,x2,y2=map(int,d['bbox'])
                    cv2.rectangle(frame,(x1,y1),(x2,y2),(60,210,80),1)
                text=f"{len(detections)} persons | {'DENSE CANDIDATE' if evidence['candidate'] else 'NO DENSE CANDIDATE'}"
                cv2.rectangle(frame,(0,0),(640,45),(15,15,15),-1)
                cv2.putText(frame,text,(8,18),cv2.FONT_HERSHEY_SIMPLEX,.47,(255,255,255),1)
                cv2.putText(frame,f"Source frame {frame_id} | {at:.1f}s" if video else 'Still image: appearance only, no temporal alert',(8,36),cv2.FONT_HERSHEY_SIMPLEX,.43,(255,255,255),1)
                image=folder/f'{frame_id:04d}.jpg';assert cv2.imwrite(str(image),frame)
                if video:
                    if writer is None:
                        writer=cv2.VideoWriter(str(folder/'sampled.webm'),cv2.VideoWriter_fourcc(*'VP80'),fps/step,(640,480))
                        assert writer.isOpened()
                    writer.write(frame)
                rows.append({'source_frame_id':frame_id,'source_time_s':at,'quality':quality,'evidence':evidence,
                             'detections':detections,'state':state,'inference_ms':inference_ms,
                             'analysis_ms':processing_ms,'offline_semantic_support_inference_ms':semantic_ms,
                             'image':image.relative_to(a.out).as_posix()})
        finally:
            if cap:cap.release()
            if writer:writer.release()
        result={**case,'rows':rows,'events':events,'not_real_audio':True,
                'temporal_evaluated':video,'dense_frames':sum(r['evidence']['candidate'] for r in rows)}
        report['cases'].append(result)
        print(json.dumps({'id':case['id'],'samples':len(rows),'people':[r['evidence']['person_count'] for r in rows],
                          'candidates':result['dense_frames'],'events':len(events)},ensure_ascii=False),flush=True)
    (a.out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
