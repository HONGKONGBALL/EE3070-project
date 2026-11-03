"""YOLO11n NCNN adapter: existing weights, no PyTorch or training on the Pi.

Output decoding follows Tencent's BSD-3-Clause examples/yolo11.cpp:
https://github.com/Tencent/ncnn/blob/master/examples/yolo11.cpp
Copyright 2025 Tencent. Retain upstream attribution when redistributing.
The YOLO weights retain their own Ultralytics license.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import ncnn
import numpy as np

NAMES = (
    "person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,"
    "fire hydrant,stop sign,parking meter,bench,bird,cat,dog,horse,sheep,cow,"
    "elephant,bear,zebra,giraffe,backpack,umbrella,handbag,tie,suitcase,frisbee,"
    "skis,snowboard,sports ball,kite,baseball bat,baseball glove,skateboard,"
    "surfboard,tennis racket,bottle,wine glass,cup,fork,knife,spoon,bowl,banana,"
    "apple,sandwich,orange,broccoli,carrot,hot dog,pizza,donut,cake,chair,couch,"
    "potted plant,bed,dining table,toilet,tv,laptop,mouse,remote,keyboard,"
    "cell phone,microwave,oven,toaster,sink,refrigerator,book,clock,vase,"
    "scissors,teddy bear,hair drier,toothbrush"
).split(",")


def decode(pred, padded_w, padded_h, scale, pad_x, pad_y, width, height, confidence,
           classes=None, max_detections=20):
    """Standard DFL decoding and class-aware OpenCV NMS for this exported model."""
    grids, strides = [], []
    for stride in (8, 16, 32):
        y, x = np.mgrid[: padded_h // stride, : padded_w // stride]
        grids.append(np.column_stack((x.ravel() + 0.5, y.ravel() + 0.5)) * stride)
        strides.append(np.full(x.size, stride, dtype=np.float32))
    centers, stride_values = np.concatenate(grids), np.concatenate(strides)
    if pred.shape != (len(centers), 144):
        raise ValueError(f"Unexpected YOLO11 output shape: {pred.shape}")
    labels = pred[:, 64:].argmax(axis=1)
    logits = pred[np.arange(len(pred)), labels + 64]
    keep = logits >= np.log(confidence / (1 - confidence))
    if classes is not None:
        keep &= np.isin(labels, classes)
    if not keep.any():
        return []
    labels, logits = labels[keep], logits[keep]
    reg = pred[keep, :64].reshape(-1, 4, 16)
    prob = np.exp(reg - reg.max(axis=2, keepdims=True))
    distances = (prob / prob.sum(axis=2, keepdims=True)) @ np.arange(16, dtype=np.float32)
    distances *= stride_values[keep, None]
    boxes = np.column_stack((centers[keep] - distances[:, :2], centers[keep] + distances[:, 2:]))
    boxes -= np.array([pad_x, pad_y, pad_x, pad_y])
    boxes /= scale
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width - 1)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height - 1)
    scores = 1 / (1 + np.exp(-logits))
    result = []
    for label in np.unique(labels):
        indices = np.flatnonzero(labels == label)
        xywh = boxes[indices].copy()
        xywh[:, 2:] -= xywh[:, :2]
        chosen = cv2.dnn.NMSBoxes(xywh.tolist(), scores[indices].tolist(), confidence, 0.45)
        for k in np.asarray(chosen).ravel():
            i = indices[k]
            result.append({"label": NAMES[label], "confidence": round(float(scores[i]), 3),
                           "bbox": boxes[i].round(1).tolist()})
    return sorted(result, key=lambda d: d["confidence"], reverse=True)[:max_detections]


class Detector:
    model_id = "yolo11n-coco-ncnn"

    def __init__(self, directory: Path, size=416, confidence=0.45, threads=2,
                 *, classes=None, max_detections=20):
        self.size, self.confidence = size, confidence
        self.classes, self.max_detections = classes, max_detections
        self.net = ncnn.Net()
        self.net.opt.use_vulkan_compute = False
        self.net.opt.num_threads = threads
        if self.net.load_param(str(directory / "yolo11n.ncnn.param")) != 0:
            raise RuntimeError("Unable to load YOLO11n parameter file")
        if self.net.load_model(str(directory / "yolo11n.ncnn.bin")) != 0:
            raise RuntimeError("Unable to load YOLO11n weights")

    def predict(self, image):
        h, w = image.shape[:2]
        scale = self.size / max(w, h)
        rw, rh = int(w * scale), int(h * scale)
        px, py = (-rw) % 32, (-rh) % 32
        mat = ncnn.Mat.from_pixels_resize(image, ncnn.Mat.PixelType.PIXEL_BGR2RGB,
                                         w, h, rw, rh)
        mat = ncnn.copy_make_border(mat, py // 2, py - py // 2, px // 2, px - px // 2,
                                    ncnn.BorderType.BORDER_CONSTANT, 114)
        mat.substract_mean_normalize([], [1 / 255.0] * 3)
        with self.net.create_extractor() as extractor:
            extractor.input("in0", mat)
            code, output = extractor.extract("out0")
            if code != 0:
                raise RuntimeError(f"YOLO11 inference failed ({code})")
            prediction = np.array(output)
        return decode(prediction, rw + px, rh + py, scale, px // 2, py // 2,
                      w, h, self.confidence, self.classes, self.max_detections)
