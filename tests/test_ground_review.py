"""Review evidence must never mix frame provenance or replace actual alerts."""
import hashlib
import json

import cv2
import numpy as np
import pytest

from build_ground_review import build, observations


def test_historical_boxes_stay_separate_by_model_and_frame():
    def d(frame, label="pothole"):
        return {"source_model": "detector", "frame_id": frame, "label": label,
                "confidence": .8, "bbox_normalized": [.2, .4, .8, .9]}
    result = observations({"raw_detections": [d(10), d(11), d(10, "person")]})
    assert [r["frame_id"] for r in result] == [10, 11]
    assert all(len(r["candidates"]) == 1 and r["historical_partial"] for r in result)


def test_gallery_preserves_rejected_candidate_without_inventing_warning(tmp_path):
    source = tmp_path/"floor.png"
    cv2.imwrite(str(source), np.zeros((480, 640, 3), np.uint8))
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    item = {"file": source.name, "sha256": sha, "ground_truth": {
        "visible_puddle": False, "visible_pothole": False}, "source_page": "https://example.org/source"}
    candidate = {"label": "possible_puddle", "confidence": .8,
                 "bbox_normalized": [.2, .4, .8, .9], "reason": "verifier_rejected"}
    case = {"source_file": source.name, "source_sha256": sha, "raw_detections": [],
            "warnings": [], "box_observations": {"puddle": {
                "task": "puddle", "frame_id": 2, "candidates": [candidate]}}}
    catalog, report = tmp_path/"catalog.json", tmp_path/"report.json"
    catalog.write_text(json.dumps({"items": [item]}))
    report.write_text(json.dumps({"completed": True, "task_errors": {}, "cases": {"case-01": case}}))
    manifest = {"runs": [{"catalog": str(catalog), "report": str(report),
                           "assets": str(tmp_path), "group": "indoor"}]}
    card = build(manifest)[0]
    assert card["views"][0]["candidates"] == [candidate]
    assert card["warnings"] == [] and card["false"] == []
    item['ground_truth']['visible_pavement_damage']=True
    catalog.write_text(json.dumps({'items':[item]}))
    assert build(manifest)[0]['misses']==['damaged_pavement']
    item['ground_truth']['visible_pavement_damage']=False
    case['warnings']=[{'object_class':'damaged_pavement'}]
    catalog.write_text(json.dumps({'items':[item]}))
    report.write_text(json.dumps({'completed':True,'cases':{'case-01':case}}))
    assert build(manifest)[0]['false']==['damaged_pavement']
    source.write_bytes(b"changed photo")
    with pytest.raises(ValueError, match="provenance"):
        build(manifest)
