"""Evidence decisions and warning ownership; fixtures are not model accuracy."""
import pytest

pytest.importorskip("cv2")
pytest.importorskip("ncnn")
pytest.importorskip("onnxruntime")
pytest.importorskip("PIL")

from cane.vision.stair_context import StairContextRefiner, LABELS
from cane.vision.v2_policy import V2Policy, task_hazards


ROI = [[.35,.4],[.65,.4],[.95,1],[.05,1]]


def refiner():
    value = StairContextRefiner.__new__(StairContextRefiner)
    value.ground_roi = ROI
    value.manifest = {"repository": "fixture", "revision": "fixture",
                      "sha256": {"stair-context-prototypes.npy": "fixture"}}
    return value


def mask(label="stairs"):
    return {"label": label, "bbox": [200,250,440,460], "confidence": .8, "context_supported": True}


def scores():
    return dict(zip(LABELS, [.3,.2,.1,.1,.2,.2]))


def test_scene_can_recover_missing_mask_without_fabricating_object_localization_or_direction():
    value = refiner().select([],640,480,scores(),.02)
    d, = value["detections"]
    assert d["label"] == "stairs" and d["localization"] == "classification_roi"
    assert d["score_kind"] == "cosine_similarity"
    assert d["evidence"]["direction_speech"] is False
    assert d["bbox"] == [32,120,608,480]


def test_negative_context_rejects_false_stair_mask_but_preserves_water_context():
    water = mask("possible_puddle")
    value = refiner().select([mask(),water],640,480,scores(),-.07)
    assert value["detections"] == [water]
    assert value["candidate_detections"][0]["evidence"]["verification_accepted"] is False
    assert task_hazards("semantic",value)["detections"] == []


def test_uncertain_context_preserves_an_existing_stair_mask_but_cannot_create_one():
    assert refiner().select([],640,480,scores(),-.01)["detections"] == []
    value = refiner().select([mask()],640,480,scores(),-.01)
    assert value["detections"][0]["localization"] == "segmentation_region"


def test_real_confirmation_policy_still_requires_two_observations_and_emits_only_generic_stairs():
    d, = refiner().select([],640,480,scores(),.02)["detections"]
    d["bbox_normalized"] = [v/(640 if i%2==0 else 480) for i,v in enumerate(d["bbox"])]
    policy = V2Policy("fixture",ROI,{},ground_roi=ROI)
    policy.observe_ground("semantic",{"frame_id":1,"captured_at":10,"detections":[d]},10)
    assert policy.step(None,10) == []
    policy.observe_ground("semantic",{"frame_id":2,"captured_at":10.5,"detections":[d]},10.5)
    events = policy.step(None,10.5)
    assert len(events) == 1 and events[0]["phrase_id"] == "stairs_ahead"
    assert events[0]["evidence"]["direction_speech"] is False


def test_loading_rejects_tampered_stair_recipe_before_starting_inference(tmp_path):
    import json
    (tmp_path/"manifest.json").write_text(json.dumps({"labels":list(LABELS),"crop":[0,0,1,1]}))
    import pytest
    with pytest.raises(ValueError,match="crop"):
        StairContextRefiner(tmp_path,ROI)


def test_weak_absolute_scene_match_is_not_stairs_even_with_winning_margin():
    weak=dict(zip(LABELS,[.12,.08,.09,.08,.13,.14]))
    value=refiner().select([],640,480,weak,.05)
    assert not value['detections']
    candidate,=value['candidate_detections']
    assert not candidate['evidence']['verification_accepted']
    assert candidate['evidence']['verification_reason']=='insufficient_absolute_stair_match'
    assert candidate['evidence']['score_kind']=='cosine_similarity'


def test_scene_evidence_floor_does_not_discard_independent_spatial_stairs():
    weak=dict(zip(LABELS,[.12,.08,.09,.08,.13,.14]))
    value=refiner().select([mask()],640,480,weak,.05)
    assert len(value['detections'])==1
    assert value['detections'][0]['localization']=='segmentation_region'


def test_weak_scene_rejection_preserves_other_ground_labels():
    weak=dict(zip(LABELS,[.12,.08,.09,.08,.13,.14]))
    water=mask('possible_puddle')
    assert refiner().select([water],640,480,weak,.05)['detections']==[water]
