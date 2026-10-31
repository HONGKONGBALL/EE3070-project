"""Compare frozen stair-direction candidates on reviewed photos; never enable speech.

Requires an explicitly selected OpenCV build supporting Darknet. Uses the
existing direction weights and fixed MobileCLIP2 stair prototypes, without
training, prompt tuning, or adding either model to the live camera service.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"pi"))
from cane.vision.stairs import StairDirectionDetector, confirm_stairs
from cane.vision.ground import SemanticDetector, session
from cane.vision.quality import assess_frame
from build_scene_replay import fit_frame


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def clip_pixels(frame):
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    scale = 256/min(image.size)
    image = image.resize(tuple(int(v*scale) for v in image.size), Image.Resampling.BILINEAR)
    left, top = (image.width-256)//2, (image.height-256)//2
    image = image.crop((left, top, left+256, top+256))
    return np.asarray(image, np.float32).transpose(2, 0, 1)[None]/255


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", type=Path, required=True)
    p.add_argument("--assets", type=Path, required=True)
    p.add_argument("--models", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    cv2.setNumThreads(1)
    direction = StairDirectionDetector(args.models/"stairs-direction-research")
    semantic = SemanticDetector(args.models/"segformer")
    clip_dir = args.models/"mobileclip2-research"
    clip = session(clip_dir/"visual.onnx")
    labels = list(json.loads((clip_dir/"stair-prompts.json").read_text()))
    prototypes = np.load(clip_dir/"stair-text-prototypes.npy")
    if labels != ["downstairs", "upstairs", "level_ground", "other"] or prototypes.shape != (4, 512):
        raise ValueError("Expected the frozen four-class stair prototypes")
    cases = []
    started = time.monotonic()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    for item in catalog["items"]:
        path = args.assets/item["file"]
        if sha(path) != item["sha256"]:
            raise ValueError("Frozen image hash mismatch: "+item["file"])
        frame = cv2.imread(str(path))
        if frame is None:
            raise ValueError("Cannot decode "+str(path))
        frame = fit_frame(frame)
        quality = assess_frame(frame)
        before = time.monotonic()
        raw = direction.predict(frame)
        direction_ms = (time.monotonic()-before)*1000
        segments = semantic.predict(frame)
        # Identical still-image inputs; fixture timestamps are not real exposure timing.
        raw.update(captured_at=1)
        segments.update(captured_at=1, frame_id=1, model_id=semantic.model_id,
                        model_revision=semantic.manifest["revision"])
        confirmed = confirm_stairs(raw, segments)["detections"]
        before = time.monotonic()
        vector = clip.run(["image_embeddings"], {"pixel_values": clip_pixels(frame)})[0][0]
        vector /= np.linalg.norm(vector)
        scores = vector @ prototypes.T
        clip_ms = (time.monotonic()-before)*1000
        cases.append({"source_file": item["file"], "source_sha256": item["sha256"],
                      "raw_direction_detections": raw["detections"],
                      "existing_down_confirmation": confirmed,
                      "semantic_detections": segments["detections"], "quality": quality,
                      "clip_prediction": labels[int(scores.argmax())],
                      "cosine_scores_not_probabilities": dict(zip(labels, map(float, scores))),
                      "inference_ms_windows": {"direction": round(direction_ms, 2),
                                               "clip_visual": round(clip_ms, 2)}})
        print(item["file"], [(d["label"], round(d["confidence"], 2)) for d in raw["detections"]],
              "clip="+cases[-1]["clip_prediction"], flush=True)
    report = {"kind": "offline_frozen_direction_candidate_audit", "completed": True,
              "not_live_camera": True, "not_audio_or_event_validation": True,
              "not_training": True, "no_live_config_changed": True,
              "opencv": cv2.__version__, "catalog_sha256": sha(args.catalog),
              "elapsed_s": round(time.monotonic()-started, 2),
              "direction_manifest": direction.manifest,
              "clip_evidence_sha256": {f: sha(clip_dir/f) for f in (
                  "visual.onnx", "visual.onnx.data", "stair-text-prototypes.npy", "stair-prompts.json")},
              "source_sha256": {f: sha(Path(__file__).resolve().parents[1]/"pi/cane/vision"/f)
                                for f in ("stairs.py", "ground.py", "v2_policy.py")},
              "thresholds": {"direction": .5, "semantic": .55, "stair_pixel_fraction": .1},
              "cases": cases}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Report:", args.out, "cases:", len(cases))


if __name__ == "__main__":
    main()
