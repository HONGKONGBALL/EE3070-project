"""One lightweight scene check for stairs; never announces an up/down direction."""

import hashlib
import json
from pathlib import Path

import numpy as np

from .association import ground_overlaps_region
from .ground import session
from .puddle import context_pixels


LABELS = ("stairs", "flat_ground", "floor_objects", "other", "upstairs", "downstairs")
CROP = (.05, .25, .95, 1.0)
DECISION = {"strong_margin": .01, "semantic_veto_margin": -.03, "direction_speech": False}


class StairContextRefiner:
    """Classify a fixed camera region, keeping localization evidence explicit.

    A positive scene classification can recover a missed stair mask. Existing
    masks survive uncertain classification but are rejected by strong negative
    context. Cosine scores are never interpreted as calibrated probabilities.
    """

    def __init__(self, directory: Path, ground_roi, threads=1):
        self.manifest = json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
        if tuple(self.manifest["labels"]) != LABELS or tuple(self.manifest["crop"]) != CROP:
            raise ValueError("Stair context labels/crop do not match the frozen recipe")
        if self.manifest["decision"] != DECISION:
            raise ValueError("Stair context decision does not match the frozen recipe")
        for name, expected in self.manifest["sha256"].items():
            with (directory/name).open("rb") as file:
                actual = hashlib.file_digest(file, "sha256").hexdigest()
            if actual != expected:
                raise ValueError("Stair context checksum mismatch: "+name)
        self.prototypes = np.load(directory/"stair-context-prototypes.npy", allow_pickle=False)
        if self.prototypes.shape != (6, 512):
            raise ValueError("Unexpected stair context prototype dimensions")
        self.net = session(directory/"visual.onnx", threads)
        self.ground_roi = ground_roi

    def refine(self, image, semantic_detections):
        height, width = image.shape[:2]
        x1, y1, x2, y2 = [round(v*(width if i % 2 == 0 else height)) for i, v in enumerate(CROP)]
        vector = self.net.run(["image_embeddings"], {
            "pixel_values": context_pixels(image[y1:y2, x1:x2])})[0][0]
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm < 1e-9:
            raise ValueError("Invalid stair scene embedding")
        scores = dict(zip(LABELS, map(float, (vector/norm) @ self.prototypes.T)))
        positive = max(scores[k] for k in ("stairs", "upstairs", "downstairs"))
        negative = max(scores[k] for k in ("flat_ground", "floor_objects", "other"))
        margin = positive-negative
        return self.select(semantic_detections, width, height, scores, margin)

    def select(self, semantic_detections, width, height, scores, margin):
        """Apply the same bounded decision to real outputs or explicit rule fixtures."""
        retained = [d for d in semantic_detections if d["label"] != "stairs"]
        stairs = [d for d in semantic_detections if d["label"] == "stairs"]
        supported = margin >= DECISION["semantic_veto_margin"]
        strong = margin >= DECISION["strong_margin"]
        evidence = {"stair_context_model": self.manifest["repository"],
                    "stair_context_revision": self.manifest["revision"],
                    "prototype_sha256": self.manifest["sha256"]["stair-context-prototypes.npy"],
                    "score_kind": "cosine_similarity", "context_cosines": scores,
                    "stair_margin": margin, "direction_speech": False,
                    "classification_crop": list(CROP), "decision": DECISION}
        candidates = []
        relevant_mask = False
        for d in stairs:
            relevant_mask |= ground_overlaps_region(
                [v/(width if i % 2 == 0 else height) for i, v in enumerate(d["bbox"])], self.ground_roi)
            candidate = {**d, "localization": "segmentation_region", "evidence": {
                **evidence, "verification_accepted": supported,
                "verification_reason": "semantic_stairs_supported" if supported else "negative_stair_context"}}
            candidates.append(candidate)
            if supported:
                retained.append(candidate)
        if strong and not relevant_mask:
            # This is the observed classification region, NOT an object bounding box.
            candidate = {"label": "stairs", "raw_label": "stairs_scene_context",
                "confidence": max(scores[k] for k in ("stairs", "upstairs", "downstairs")),
                "score_kind": "cosine_similarity", "localization": "classification_roi",
                "context_supported": True,
                "bbox": [v*(width if i % 2 == 0 else height) for i, v in enumerate(CROP)],
                "evidence": {**evidence, "verification_accepted": True,
                             "verification_reason": "strong_stair_scene_without_path_mask"}}
            candidates.append(candidate)
            retained.append(candidate)
        return {"detections": retained, "candidate_detections": candidates,
                "stair_context": {"margin": margin, "cosines": scores,
                                  "direction_speech": False, "localization_is_not_depth": True}}
