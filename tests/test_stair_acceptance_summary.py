"""A correct box/ranking must never be counted as an audible direction event."""
import pytest
from summarize_stair_acceptance import summarize


def fixture():
    item = {"file": "up.jpg", "sha256": "same", "environment": "indoor",
            "evaluation_split": "new_real_stair_photos", "ground_truth": {
                "stairs_visible": True, "upstairs_visible": True, "downstairs_visible": False}}
    replay = {"completed": True, "task_errors": {}, "run_id": "fixture", "active_tasks": ["semantic"],
              "cases": {"case-01": {"source_file": "up.jpg", "source_sha256": "same",
                        "first_scene_frame_captured_at": 10,
                        "warnings": [{"object_class": "stairs", "created_at": 11}]}}}
    audit = {"completed": True, "not_audio_or_event_validation": True, "cases": [{
        "source_file": "up.jpg", "source_sha256": "same", "raw_direction_detections": [{"label": "upstairs"}],
        "existing_down_confirmation": [], "clip_prediction": "upstairs"}]}
    return {"items": [item]}, replay, audit


def test_correct_offline_direction_and_generic_event_do_not_pass_direction_acceptance():
    catalog, replay, audit = fixture()
    result = summarize(catalog, replay, audit)
    group = result["matrix"]["indoor_upstairs"]["all_reviewed_views"]
    assert group["generic_stair_warning_within_3s_cases"] == 1
    assert group["direction_model_raw_hit_cases_not_events"] == 1
    assert not result["direction_acceptance_passed"]
    assert not result["field_acceptance_passed"] and not result["current_runtime_direction_enabled"]


def test_summary_rejects_incomplete_run_or_different_photo():
    catalog, replay, audit = fixture()
    replay["completed"] = False
    with pytest.raises(ValueError, match="incomplete"):
        summarize(catalog, replay, audit)
    replay["completed"] = True
    audit["cases"][0]["source_sha256"] = "different"
    with pytest.raises(ValueError, match="hash"):
        summarize(catalog, replay, audit)


def test_conflicting_frozen_labels_are_retained_but_excluded_from_both_scores():
    catalog, replay, audit = fixture()
    catalog["items"][0]["ground_truth"]["stairs_visible"] = False
    result = summarize(catalog, replay, audit)
    assert result["label_conflicts_excluded_after_freeze"] == ["up.jpg"]
    assert result["nonstair_negative_photos"] == 0
    assert result["matrix"]["indoor_upstairs"]["all_reviewed_views"]["photos"] == 0
    assert result["cases"][0]["truth"]["upstairs_visible"] is True
