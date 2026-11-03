"""Short real-model research check, independent of the running camera service."""

import argparse
import json
from pathlib import Path
import time

import cv2
import numpy as np

from build_scene_replay import fit_frame
from cane.vision.stairs import StairDirectionDetector, confirm_stairs
from cane.vision.ground import SemanticDetector


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,default=Path('testdata/stairs-research/test_raw1.jpg'))
    parser.add_argument('--out',type=Path,default=Path('logs/v2-stair-candidate-pi.json'))
    args=parser.parse_args()
    cv2.setNumThreads(1)
    detector=StairDirectionDetector(Path('models/v2/stairs-direction-research'))
    semantic=SemanticDetector(Path('models/v2/segformer'))
    image=cv2.imread(str(args.image))
    if image is None:raise ValueError('Research image cannot be decoded')
    image=fit_frame(image)
    timings=[]
    for _ in range(3):
        started=time.monotonic()
        output=detector.predict(image)
        timings.append((time.monotonic()-started)*1000)
    segments=semantic.predict(image)
    output.update(captured_at=10)
    segments.update(captured_at=10,model_id=semantic.model_id,model_revision=semantic.manifest['revision'],frame_id=1)
    # Same still image: identical evidence time is a pairing fixture, not physical latency.
    report={'type':'pi_still_image_model_check','image':str(args.image),'not_live_camera':True,
            'direction_inference_ms':timings,'raw':output['detections'],
            'confirmed':confirm_stairs(output,segments)['detections'],
            'license':detector.manifest['license'],'revision':detector.manifest['revision']}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


if __name__=='__main__':main()
