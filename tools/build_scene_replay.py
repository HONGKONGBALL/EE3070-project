"""Build a clearly documented photo-replay simulation; labels are never burned into model input."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

import cv2
import numpy as np


def fit_frame(image,width=640,height=480):
    h,w=image.shape[:2]
    scale=min(width/w,height/h)
    resized=cv2.resize(image,(round(w*scale),round(h*scale)))
    frame=np.zeros((height,width,3),np.uint8)
    y,x=(height-resized.shape[0])//2,(width-resized.shape[1])//2
    frame[y:y+resized.shape[0],x:x+resized.shape[1]]=resized
    return frame


def build(catalog,asset_dir,output,seconds=6,fps=8,codec='FFV1'):
    items=catalog['items']
    if seconds*len(items)>600: raise ValueError('Replay must not exceed 10 minutes')
    output.parent.mkdir(parents=True,exist_ok=True)
    asset_dir.mkdir(parents=True,exist_ok=True)
    frames=round(seconds*fps)
    if codec not in {'FFV1','MJPG'}:raise ValueError('Unsupported replay codec')
    writer=cv2.VideoWriter(str(output),cv2.VideoWriter_fourcc(*codec),fps,(640,480))
    if not writer.isOpened(): raise RuntimeError('Replay video encoder unavailable')
    cases=[];first_frame=None
    try:
        for i,item in enumerate(items):
            path=asset_dir/item['file']
            if not path.exists():
                req=urllib.request.Request(urllib.parse.quote(item['download_url'],safe=':/?%=&'),
                    headers={'User-Agent':'EE3070-Camera-Evaluation/1.0 (educational local testing)'})
                # Respect rate limiting: stop instead of cycling hosts or repeatedly retrying.
                try:
                    with urllib.request.urlopen(req,timeout=30) as response:path.write_bytes(response.read())
                except urllib.error.HTTPError as exc:
                    if exc.code==429: raise RuntimeError('Asset host rate-limited requests; retry later') from exc
                    raise
                time.sleep(2)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
                raise RuntimeError('Photo checksum mismatch: '+item['file'])
            image=cv2.imread(str(path))
            if image is None: raise RuntimeError('Cannot decode photo: '+item['file'])
            frame=fit_frame(image)
            if first_frame is None:first_frame=frame.copy()
            for _ in range(frames):writer.write(frame)
            cases.append({'id':f'case-{i+1:02d}','start_frame':i*frames+1,'end_frame':(i+1)*frames,
                          'source_file':item['file'],'source_page':item.get('source_page'),
                          'source_sha256':item['sha256'],
                          'evaluation_split':item.get('evaluation_split','development'),
                          'ground_truth':item.get('ground_truth',{}),'scene_group':item.get('scene_group',item['file'])})
    finally:writer.release()
    lossless_verified=False
    if codec=='FFV1' and first_frame is not None:
        capture=cv2.VideoCapture(str(output))
        try:ok,decoded=capture.read()
        finally:capture.release()
        if not ok or not np.array_equal(decoded,first_frame):
            raise RuntimeError('FFV1 replay did not preserve the original input pixels')
        lossless_verified=True
    timeline={'input_type':'public_real_photos_replayed_as_video','not_live_camera':True,
              'fps':fps,'seconds_per_case':seconds,'duration_s':len(cases)*seconds,
              'encoding':{'codec':codec,'lossless_first_frame_verified':lossless_verified},'cases':cases}
    target=output.with_suffix('.json');target.write_text(json.dumps(timeline,indent=2),encoding='utf-8')
    return timeline


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,default=Path('config/simulation-scenes.json'))
    parser.add_argument('--assets',type=Path,default=Path('D:/SmartCaneRuntime/testdata/outdoor-simulation') if os.name=='nt' else Path('testdata/simulation'))
    parser.add_argument('--output',type=Path,default=Path('D:/SmartCaneRuntime/simulation/outdoor-scenes.avi') if os.name=='nt' else Path('testdata/simulation/outdoor-scenes.avi'))
    parser.add_argument('--seconds',type=float,default=6)
    parser.add_argument('--codec',choices=['FFV1','MJPG'],default='FFV1',help='FFV1 preserves photo pixels; MJPG is an explicit lossy replay condition')
    args=parser.parse_args()
    if args.seconds<3:parser.error('Each scene needs at least 3 seconds for temporal filtering')
    catalog=json.loads(args.catalog.read_text(encoding='utf-8'))
    result=build(catalog,args.assets,args.output,args.seconds,codec=args.codec)
    print(json.dumps({'video':str(args.output),'timeline':str(args.output.with_suffix('.json')),
                      'cases':len(result['cases']),'duration_s':result['duration_s']}))


if __name__=='__main__':main()
