"""Raw frozen-model photo diagnostics; no temporal, ground-context, or audio pass."""

import argparse
import hashlib
import json
from pathlib import Path
import time

import cv2

from build_scene_replay import fit_frame
from cane.vision.yolox import YoloXPotholeDetector, NcnnYoloXPotholeDetector


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--backend', choices=['onnx','ncnn'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    cv2.setNumThreads(1)
    factory = YoloXPotholeDetector if args.backend == 'onnx' else NcnnYoloXPotholeDetector
    detector = factory(args.model, 640, .4, 1)
    catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
    began = time.monotonic()
    cases = []
    for item in catalog['items']:
        if time.monotonic()-began > 540:
            raise RuntimeError('Photo diagnostic exceeds nine-minute bound')
        path = args.assets/item['file']
        checksum = digest(path)
        if checksum != item['sha256']:
            raise ValueError('Original photo checksum differs: '+item['file'])
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError('Cannot decode original photo: '+item['file'])
        started = time.monotonic()
        output = detector.predict(fit_frame(image))
        cases.append({'file':item['file'], 'source_sha256':checksum,
            'ground_truth':item.get('ground_truth',{}), 'detections':output['detections'],
            'elapsed_ms':round((time.monotonic()-started)*1000,3)})
    positives = [c for c in cases if c['ground_truth'].get('visible_pothole') is True]
    negatives = [c for c in cases if c['ground_truth'].get('visible_pothole') is False]
    report = {'type':'raw_yolox_photo_development_diagnostic', 'training_performed':False,
        'not_temporal_ground_audio_outdoor_acceptance':True, 'confidence':.4, 'nms_iou':.45,
        'backend':args.backend, 'model_id':detector.model_id, 'model_manifest':detector.manifest,
        'catalog_sha256':digest(args.catalog), 'manifest_sha256':digest(args.model/'manifest.json'),
        'elapsed_s':round(time.monotonic()-began,3), 'cases':cases,
        'metrics':{'positives':len(positives),
            'positive_candidates':sum(bool(c['detections']) for c in positives),
            'negatives':len(negatives),
            'false_candidates':sum(bool(c['detections']) for c in negatives)}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report['metrics']))


if __name__ == '__main__':
    main()
