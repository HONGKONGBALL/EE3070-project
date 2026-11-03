"""Same advancing-road frames at 0.5/1/1.5 media rate, not calibrated walking speeds."""
import argparse
import hashlib
import json
from pathlib import Path
import cv2
from check_real_videos import run_clip


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--models-root',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--audio-dir',type=Path,required=True)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    catalog=json.loads(a.catalog.read_text(encoding='utf8'))
    source=next(c for c in catalog['clips'] if c['id']=='dashcam-potholes')
    assert hashlib.sha256(Path(source['input']).read_bytes()).hexdigest()==source['input_sha256']
    cap=cv2.VideoCapture(source['input']);frames=[]
    for _ in range(180):
        ok,frame=cap.read()
        if not ok:break
        frames.append(frame)
    cap.release()
    rows=[]
    for rate in [.5,1,1.5]:
        fps=source['input_fps']*rate;path=a.out/f'rate-{rate}.avi'
        writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),fps,(640,480))
        assert writer.isOpened()
        for frame in frames:writer.write(frame)
        writer.release()
        clip={**source,'id':f'rate-{rate}','input':str(path),'input_fps':fps,
              'input_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'duration_s':len(frames)/fps,
              'intervals':[],'review_note':'Media-rate stress; moving dashcam, no m/s or distance ground truth'}
        result=run_clip(clip,Path(catalog['config_file']),a.models_root,a.out,a.audio_dir,encode_review=False)
        result['media_rate']=rate;rows.append(result)
    (a.out/'summary.json').write_text(json.dumps({'basis':'Same 180 source frames retimed; no walking-speed or physical-distance claim',
        'source_sha256':source['input_sha256'],'runs':rows},indent=2),encoding='utf8')


if __name__=='__main__':main()
