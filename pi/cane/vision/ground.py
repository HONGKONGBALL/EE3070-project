"""Fixed ONNX adapters: RDD2022 damage and ADE20K semantic context, no training."""

from pathlib import Path
import time
import json
import cv2
import numpy as np
import onnxruntime as ort
import ncnn

ROAD_NAMES = ("longitudinal_crack", "transverse_crack", "alligator_crack", "pothole")
# Outdoor scope: an indoor floor label alone must not enable road-hazard speech.
GROUND_LABELS = {6, 11, 13, 46, 52, 53, 59, 91, 94, 121}


def semantic_regions(classes,probability,width,height,confidence,labels):
    """Water may replace most of the path; inspect its boundary, not a global road quota."""
    context=np.isin(classes,list(GROUND_LABELS))
    detections=[]
    for label,ids in (("possible_puddle",[21]),("stairs",[53,59,121])):
        selected=(np.isin(classes,ids)&(probability>=confidence)).astype(np.uint8)
        count,regions,stats,_=cv2.connectedComponentsWithStats(selected)
        for i in range(1,count):
            x,y,rw,rh,area=stats[i]
            if area<selected.size*.005: continue
            mask=regions==i
            border=cv2.dilate(mask.astype(np.uint8),np.ones((5,5),np.uint8)).astype(bool)&~mask
            adjacent=float(np.count_nonzero(context&border)/max(1,np.count_nonzero(border)))
            raw=", ".join(labels[str(int(v))] for v in np.unique(classes[mask]))
            detections.append({"label":label,"raw_label":raw,
                               "confidence":float(probability[mask].mean()),
                               "context_supported":label=="stairs" or adjacent>=.1,
                               "adjacent_ground_fraction":round(adjacent,3),
                               "bbox":[x/selected.shape[1]*width,y/selected.shape[0]*height,
                                       (x+rw)/selected.shape[1]*width,(y+rh)/selected.shape[0]*height]})
    return detections


def session(path, threads=1):
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def decode_road(prediction, size, scale, pad_x, pad_y, width, height, confidence,
                names=ROAD_NAMES, features=None):
    pred = prediction[0]
    if pred.ndim != 2 or pred.shape[0] != (features or 4+len(names)):
        raise ValueError(f"Wrong RDD2022 export layout: {prediction.shape}")
    scores = pred[4:4+len(names)].max(axis=0)
    kept = np.flatnonzero(scores >= confidence)
    labels = pred[4:4+len(names), kept].argmax(axis=0)
    xywh = pred[:4, kept].T
    boxes = np.column_stack((xywh[:, :2]-xywh[:, 2:]/2, xywh[:, :2]+xywh[:, 2:]/2))
    boxes -= [pad_x, pad_y, pad_x, pad_y]
    boxes /= scale
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width-1)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height-1)
    results = []
    for class_id in np.unique(labels):
        raw_label = names[class_id]
        normalized = raw_label.lower().replace(" ","_")
        label={'pothole':'pothole','puddle':'possible_puddle','speedbump':'raised_bump'}.get(normalized,
                'damaged_pavement' if 'crack' in normalized else None)
        if label is None:
            continue
        indices = np.flatnonzero(labels == class_id)
        rects = boxes[indices].copy()
        rects[:, 2:] -= rects[:, :2]
        chosen = cv2.dnn.NMSBoxes(rects.tolist(), scores[kept[indices]].tolist(), confidence, 0.45)
        for j in np.asarray(chosen).ravel():
            i = indices[j]
            results.append({"label": label,
                            "raw_label": raw_label, "confidence": float(scores[kept[i]]),
                            "bbox": boxes[i].tolist()})
    return results[:20]


class OnnxRoadDetector:
    """Reference adapter used to verify conversion; production YOLO uses NCNN below."""
    model_id = "rdd2022-yolov8n-onnx"

    def __init__(self, directory: Path, size=416, confidence=0.4, threads=1):
        self.size, self.confidence = size, confidence
        self.net = session(directory / "best.onnx", threads)
        self.input = self.net.get_inputs()[0].name
        self.manifest = json.loads((directory / "manifest.json").read_text())
        self.names = [v for k,v in sorted(self.manifest["export"]["classes"].items(),key=lambda kv:int(kv[0]))]
        self.features = self.net.get_outputs()[0].shape[1]
        self.output = self.net.get_outputs()[0].name
        self.model_id = self.manifest["repository"]+":onnx"

    def predict(self, image):
        h, w = image.shape[:2]
        scale = self.size / max(h, w)
        rw, rh = round(w*scale), round(h*scale)
        px, py = (self.size-rw)//2, (self.size-rh)//2
        resized = cv2.resize(image, (rw, rh), interpolation=cv2.INTER_LINEAR)
        frame = np.full((self.size, self.size, 3), 114, np.uint8)
        frame[py:py+rh, px:px+rw] = resized
        tensor = np.ascontiguousarray(frame[:, :, ::-1].transpose(2,0,1)[None], dtype=np.float32)/255
        output = self.net.run([self.output], {self.input: tensor})[0]
        return {"detections": decode_road(output, self.size, scale, px, py, w, h, self.confidence,self.names,self.features)}


class RoadDetector:
    """The selected YOLO export, fixed NCNN runtime; no automatic backend fallback."""
    model_id = "road-damage-ncnn"

    def __init__(self,directory: Path,size=416,confidence=0.4,threads=1):
        self.size,self.confidence = size,confidence
        self.manifest = json.loads((directory/'manifest.json').read_text())
        metadata = self.manifest['export_ncnn']
        if metadata['input_size'] != size:
            raise ValueError('Configured input does not match the fixed NCNN export')
        self.names = [v for k,v in sorted(metadata['classes'].items(),key=lambda kv:int(kv[0]))]
        self.features = 4+len(self.names)+(32 if metadata['task']=='segment' else 0)
        self.model_id = self.manifest['repository']+':ncnn'
        self.net = ncnn.Net()
        self.net.opt.use_vulkan_compute = False
        self.net.opt.num_threads = threads
        folder = directory/'best_ncnn_model'
        if self.net.load_param(str(folder/'model.ncnn.param')) != 0:
            raise RuntimeError('Cannot load NCNN ground parameters')
        if self.net.load_model(str(folder/'model.ncnn.bin')) != 0:
            raise RuntimeError('Cannot load NCNN ground weights')

    def predict(self,image):
        image=np.ascontiguousarray(image)
        h,w = image.shape[:2]
        scale = self.size/max(h,w)
        rw,rh = round(w*scale),round(h*scale)
        px,py = (self.size-rw)//2,(self.size-rh)//2
        mat = ncnn.Mat.from_pixels_resize(image,ncnn.Mat.PixelType.PIXEL_BGR2RGB,w,h,rw,rh)
        mat = ncnn.copy_make_border(mat,py,self.size-rh-py,px,self.size-rw-px,
                                    ncnn.BorderType.BORDER_CONSTANT,114)
        mat.substract_mean_normalize([],[1/255]*3)
        with self.net.create_extractor() as extractor:
            extractor.input('in0',mat)
            code,output = extractor.extract('out0')
            if code != 0: raise RuntimeError(f'Ground NCNN extraction failed ({code})')
            prediction = np.array(output)[None]
        return {'detections':decode_road(prediction,self.size,scale,px,py,w,h,self.confidence,self.names,self.features)}


class SemanticDetector:
    model_id = "segformer-b0-ade20k-onnx"

    def __init__(self, directory: Path, size=384, confidence=0.55, threads=1,
                 *, stair_context_directory=None, ground_roi=None):
        self.size, self.confidence = size, confidence
        self.net = session(directory / "model.onnx", threads)
        self.input = self.net.get_inputs()[0].name
        self.manifest = json.loads((directory / "manifest.json").read_text())
        self.labels = json.loads((directory / "config.json").read_text())["id2label"]
        prep = json.loads((directory / "preprocessor_config.json").read_text())
        self.mean = np.asarray(prep["image_mean"], np.float32)[:,None,None]
        self.std = np.asarray(prep["image_std"], np.float32)[:,None,None]
        self.stair_context = None
        if stair_context_directory is not None:
            if ground_roi is None:
                raise ValueError("Stair context requires an explicit path region")
            from .stair_context import StairContextRefiner
            self.stair_context = StairContextRefiner(Path(stair_context_directory), ground_roi, threads)
            self.manifest = {**self.manifest, "stair_context": self.stair_context.manifest}
            self.model_id += "+stairs-context"

    def predict(self, image):
        h, w = image.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(image, (self.size,self.size)), cv2.COLOR_BGR2RGB)
        tensor = (rgb.astype(np.float32).transpose(2,0,1)/255-self.mean)/self.std
        logits = self.net.run(None, {self.input: tensor[None]})[0][0]
        classes = logits.argmax(axis=0).astype(np.uint8)
        maxima = logits.max(axis=0)
        probability = 1/np.exp(logits-maxima[None]).sum(axis=0)
        ground = np.isin(classes, list(GROUND_LABELS)) & (probability >= self.confidence)
        detections=semantic_regions(classes,probability,w,h,self.confidence,self.labels)
        result = {"detections": detections, "candidates_observed_at":time.monotonic(),
                "class_map": cv2.resize(classes, (w,h), interpolation=cv2.INTER_NEAREST),
                "ground_mask": cv2.resize(ground.astype(np.uint8), (w,h), interpolation=cv2.INTER_NEAREST)}
        for d in detections:d["candidate_observed_at"]=result["candidates_observed_at"]
        if self.stair_context is not None:
            result.update(self.stair_context.refine(image, detections))
        result["verification_completed_at"]=time.monotonic()
        for d in result["detections"]:
            d.setdefault("candidate_observed_at",result["verification_completed_at"])
        return result
