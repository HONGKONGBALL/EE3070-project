"""Explicit research adapter for the frozen single-class YOLOX pothole ONNX.

Uses upstream YOLOX preprocessing; the publisher's fork-specific source has not
been verified. Never selected automatically or used to infer physical depth.
"""

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import ncnn

from .ground import session


def preprocess(image, size=640):
    height, width = image.shape[:2]
    ratio = min(size / height, size / width)
    padded = np.full((size, size, 3), 114, np.uint8)
    resized = cv2.resize(image, (int(width * ratio), int(height * ratio)),
                         interpolation=cv2.INTER_LINEAR)
    padded[:resized.shape[0], :resized.shape[1]] = resized
    # Upstream YOLOX expects BGR float pixels, without RGB reversal or /255.
    return np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32), ratio


def focus_pixels(tensor):
    """Original graph channel order: top-left, bottom-left, top-right, bottom-right."""
    return np.ascontiguousarray(np.concatenate((tensor[:, :, ::2, ::2], tensor[:, :, 1::2, ::2],
        tensor[:, :, ::2, 1::2], tensor[:, :, 1::2, 1::2]), axis=1))


def decode(raw, ratio, width, height, confidence=.4, size=640):
    count = sum((size // stride) ** 2 for stride in (8, 16, 32))
    if raw.shape != (1, count, 6) or not np.isfinite(raw).all():
        raise ValueError('Invalid single-class YOLOX output')
    prediction = raw[0]
    if np.any(prediction[:, 4:] < 0) or np.any(prediction[:, 4:] > 1):
        raise ValueError('YOLOX objectness/class scores must already be sigmoid probabilities')
    grids, strides = [], []
    for stride in (8, 16, 32):
        x, y = np.meshgrid(np.arange(size // stride), np.arange(size // stride))
        grid = np.stack((x, y), 2).reshape(-1, 2)
        grids.append(grid)
        strides.append(np.full((len(grid), 1), stride))
    grid, stride = np.concatenate(grids), np.concatenate(strides)
    scores = prediction[:, 4] * prediction[:, 5]
    selected = np.flatnonzero(scores >= confidence)
    if not len(selected):
        return []
    xy = (prediction[selected, :2] + grid[selected]) * stride[selected]
    wh = np.exp(prediction[selected, 2:4]) * stride[selected]
    if not np.isfinite(wh).all():
        raise ValueError('Nonfinite decoded YOLOX boxes')
    boxes = np.column_stack((xy - wh / 2, xy + wh / 2)) / ratio
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width - 1)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height - 1)
    rects = boxes.copy()
    rects[:, 2:] -= rects[:, :2]
    valid = np.flatnonzero((rects[:, 2:] > 0).all(axis=1))
    keep = cv2.dnn.NMSBoxes(rects[valid].tolist(), scores[selected[valid]].tolist(), confidence, .45)
    return [{'label':'pothole', 'raw_label':'pothole',
             'confidence':float(scores[selected[valid[j]]]), 'bbox':boxes[valid[j]].tolist()}
            for j in np.asarray(keep).ravel()][:20]


class YoloXPotholeDetector:
    """Opt-in candidate, compatible with the shared V2 task/output contract."""

    def __init__(self, directory: Path, size=640, confidence=.4, threads=1):
        self.manifest = json.loads((directory / 'manifest.json').read_text())
        if size != 640 or self.manifest['repository'] != 'AMD-PAVS-AI/pothole-detection':
            raise ValueError('YOLOX research adapter requires the frozen 640 single-pothole model')
        path = directory / 'pothole_yolox_s.onnx'
        with path.open('rb') as source:
            checksum = hashlib.file_digest(source, 'sha256').hexdigest()
        if checksum != self.manifest['sha256']:
            raise ValueError('YOLOX research weight checksum mismatch')
        self.net = session(path, threads)
        if (self.net.get_inputs()[0].shape != [1, 3, 640, 640] or
                self.net.get_outputs()[0].shape != [1, 8400, 6]):
            raise ValueError('Unexpected YOLOX ONNX layout')
        self.input = self.net.get_inputs()[0].name
        self.size, self.confidence = size, confidence
        self.model_id = self.manifest['repository'] + ':yolox-s-onnx-research'

    def predict(self, image):
        tensor, ratio = preprocess(image, self.size)
        raw = self.net.run(None, {self.input: tensor})[0]
        height, width = image.shape[:2]
        return {'detections':decode(raw, ratio, width, height, self.confidence, self.size)}


class NcnnYoloXPotholeDetector:
    """Explicit NCNN conversion; the ONNX backend remains a separate reference."""

    def __init__(self, directory: Path, size=640, confidence=.4, threads=1):
        self.manifest = json.loads((directory/'manifest.json').read_text())
        metadata = self.manifest['export_ncnn']
        if (size != 640 or metadata['input_shape'] != [1,12,320,320] or
                not metadata['focus_matches_original_onnx_exactly']):
            raise ValueError('Unexpected converted YOLOX input contract')
        for entry in metadata['files']:
            with (directory/entry['filename']).open('rb') as source:
                if hashlib.file_digest(source,'sha256').hexdigest() != entry['sha256']:
                    raise ValueError('Converted YOLOX checksum mismatch')
        self.net = ncnn.Net()
        self.net.opt.use_vulkan_compute = False
        self.net.opt.num_threads = threads
        if (self.net.load_param(str(directory/'pothole_yolox_focused.ncnn.param')) != 0 or
                self.net.load_model(str(directory/'pothole_yolox_focused.ncnn.bin')) != 0):
            raise RuntimeError('Cannot load converted YOLOX')
        self.size, self.confidence = size, confidence
        self.model_id = self.manifest['repository']+':yolox-s-ncnn-research'

    def predict(self, image):
        tensor, ratio = preprocess(image, self.size)
        focused = focus_pixels(tensor)[0]
        with self.net.create_extractor() as extractor:
            extractor.input('in0', ncnn.Mat(focused).clone())
            code, output = extractor.extract('out0')
            if code != 0:
                raise RuntimeError('Converted YOLOX extraction failed')
            raw = np.array(output)[None]
        height, width = image.shape[:2]
        return {'detections':decode(raw,ratio,width,height,self.confidence,self.size)}
