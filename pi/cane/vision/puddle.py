"""Ground crop detection with one whole-frame contextual verification per frame."""

import hashlib
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image

from .association import ground_overlaps_region
from .ground import RoadDetector, session

PREPROCESS={'size':256,'scope':'full_frame','resize_mode':'shortest','crop':'center',
            'interpolation':'bilinear','mean':[0.,0.,0.],'std':[1.,1.,1.]}
DECISION={'puddle_top1':True,'wet_pavement_min_confidence':.7,'reject_other_contexts':True}


def context_pixels(image):
    height,width=image.shape[:2]
    scale=256/min(width,height)
    rgb=Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB))
    rgb=rgb.resize((int(width*scale),int(height*scale)),Image.Resampling.BILINEAR)
    left,top=(rgb.width-256)//2,(rgb.height-256)//2
    rgb=rgb.crop((left,top,left+256,top+256))
    return np.ascontiguousarray(np.asarray(rgb,np.float32).transpose(2,0,1)[None])/255


class PuddleDetector:
    model_id='puddle-ncnn+mobileclip2-s0'

    def __init__(self,directory:Path,size=640,confidence=.25,threads=1,*,ground_roi):
        self.boxes=RoadDetector(directory,size,confidence,threads)
        self.manifest=self.boxes.manifest
        config=self.manifest['verifier']
        if config.get('preprocess')!=PREPROCESS:raise ValueError('Puddle verifier preprocessing does not match the fixed runtime')
        if config.get('decision')!=DECISION:raise ValueError('Puddle verifier decision does not match the fixed runtime')
        folder=directory/'verifier'
        for name,spec in config['files'].items():
            if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=spec['sha256']:
                raise ValueError('Puddle verifier checksum mismatch: '+name)
        self.labels=config['labels']
        self.prototypes=np.load(folder/'puddle-text-prototypes.npy',allow_pickle=False)
        if self.boxes.names!=['puddle'] or self.prototypes.shape!=(len(self.labels),512):
            raise ValueError('Wrong puddle checkpoint or verifier prototype layout')
        self.visual=session(folder/'visual.onnx',threads)
        self.ground_roi=ground_roi
        self.model_id=self.boxes.model_id+'+mobileclip2-s0-global'

    def predict(self,image):
        height,width=image.shape[:2]
        points=np.asarray(self.ground_roi)*[width,height]
        left,top=np.rint(points.min(axis=0)).astype(int)
        right,bottom=np.rint(points.max(axis=0)).astype(int)
        raw=self.boxes.predict(image[top:bottom,left:right])['detections']
        candidates_at=time.monotonic()
        for detection in raw:
            detection['bbox']=[float(v)+(left if i%2==0 else top) for i,v in enumerate(detection['bbox'])]
            detection['path_relevant']=bool(ground_overlaps_region(
                [v/(width if i%2==0 else height) for i,v in enumerate(detection['bbox'])],self.ground_roi))
        eligible=[d for d in raw if d['path_relevant']]
        if not eligible:return {'detections':[],'candidate_detections':raw,'candidates_observed_at':candidates_at,
                                'verification_completed_at':time.monotonic()}
        pixels=context_pixels(image)
        embedding=self.visual.run(['image_embeddings'],{'pixel_values':pixels})[0][0]
        norm=float(np.linalg.norm(embedding))
        if not np.isfinite(norm) or norm<1e-9:raise ValueError('Invalid visual verifier embedding')
        scores=(embedding/norm)@self.prototypes.T
        label=self.labels[int(scores.argmax())]
        evidence={'verifier_model':self.manifest['verifier']['repository'],
                  'verifier_revision':self.manifest['verifier']['revision'],'verifier_label':label,
                  'score_kind':'cosine_similarity','verifier_cosines':dict(zip(self.labels,map(float,scores))),
                  'verification_preprocess':PREPROCESS,'verification_frame_size':[width,height],
                  'verification_decision':DECISION,
                  'detector_crop':[int(left),int(top),int(right),int(bottom)]}
        for detection in eligible:
            accepted=label=='puddle' or (label=='wet_pavement' and
                detection['confidence']>=DECISION['wet_pavement_min_confidence'])
            detection['evidence']={**evidence,'verification_accepted':accepted,
                'verification_reason':('puddle_context' if label=='puddle' else 'strong_detector_wet_context')
                    if accepted else 'context_rejected'}
        return {'detections':[d for d in eligible if d['evidence']['verification_accepted']],
                'candidate_detections':raw,'candidates_observed_at':candidates_at,
                'verification_completed_at':time.monotonic()}
