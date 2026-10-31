"""Author-trained up/down Darknet model through the Pi's existing OpenCV DNN."""

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np


def confirm_stairs(result,semantic):
    """A direction box alone is insufficient; require fresh, spatial stair semantics."""
    if result is None or semantic is None or abs(result['captured_at']-semantic['captured_at'])>.75:
        return None
    mask=np.isin(semantic['class_map'],[53,59,121]) & semantic['ground_mask'].astype(bool)
    height,width=mask.shape
    detections=[]
    for detection in result['detections']:
        if detection['label']!='downstairs':continue
        x1,y1,x2,y2=map(int,detection['bbox'])
        region=mask[max(0,y1):min(height,y2),max(0,x1):min(width,x2)]
        fraction=float(region.mean()) if region.size else 0
        if fraction>=.1:
            detections.append({**detection,'evidence':{
                'semantic_model':semantic['model_id'],'semantic_revision':semantic.get('model_revision'),
                'semantic_frame_id':semantic['frame_id'],'semantic_captured_at':semantic['captured_at'],
                'stair_fraction':fraction,'max_frame_skew_s':.75}})
    return {**result,'detections':detections}


def decode_stairs(outputs,width,height,confidence=.5):
    # OpenCV Region outputs class scores already multiplied by objectness.
    rows=np.concatenate(outputs)
    if rows.ndim!=2 or rows.shape[1]!=8:
        raise ValueError('Expected Darknet output [N, 5+3], classes None/up/down')
    detections=[]
    for class_id,label in ((1,'upstairs'),(2,'downstairs')):
        selected=rows[(rows[:,5:].argmax(axis=1)==class_id)&(rows[:,5+class_id]>=confidence)]
        if not len(selected):continue
        xywh=selected[:,:4]*[width,height,width,height]
        boxes=np.column_stack((xywh[:,:2]-xywh[:,2:]/2,xywh[:,2:]))
        scores=selected[:,5+class_id]
        for index in np.asarray(cv2.dnn.NMSBoxes(boxes.tolist(),scores.tolist(),confidence,.45)).flatten():
            x,y,w,h=boxes[index]
            bbox=[float(np.clip(x,0,width-1)),float(np.clip(y,0,height-1)),
                  float(np.clip(x+w,0,width-1)),float(np.clip(y+h,0,height-1))]
            detections.append({'label':label,'raw_label':'up' if class_id==1 else 'down',
                               'confidence':float(scores[index]),'bbox':bbox})
    return detections[:20]


class StairDirectionDetector:
    model_id='bossenti/stairs-up-down:opencv-darknet'

    def __init__(self,directory:Path,size=416,confidence=.5,threads=1):
        self.manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        if self.manifest['classes']!=['None','up','down'] or size!=416:
            raise ValueError('Stair direction requires the verified 416px, None/up/down model')
        for name,expected in self.manifest['sha256'].items():
            if hashlib.sha256((directory/name).read_bytes()).hexdigest()!=expected:
                raise ValueError('Stair model checksum mismatch: '+name)
        if not hasattr(cv2.dnn,'readNetFromDarknet'):
            raise RuntimeError('This model needs OpenCV with Darknet support (Pi system OpenCV 4.10)')
        self.net=cv2.dnn.readNetFromDarknet(str(directory/'model.cfg'),str(directory/'model.weights'))
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.outputs=self.net.getUnconnectedOutLayersNames()
        self.size,self.confidence=size,confidence

    def predict(self,image):
        height,width=image.shape[:2]
        self.net.setInput(cv2.dnn.blobFromImage(image,1/255,(self.size,self.size),swapRB=True,crop=False))
        return {'detections':decode_stairs(self.net.forward(self.outputs),width,height,self.confidence)}
