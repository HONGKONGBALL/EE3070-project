"""Keep generic stair events, direction candidates and field acceptance separate."""
import argparse
import hashlib
import json
from pathlib import Path


def summarize(catalog, replay, audit):
    if not replay.get("completed") or replay.get("task_errors") or replay.get("error"):
        raise ValueError("An incomplete or failed replay cannot be scored")
    if not audit.get("completed") or not audit.get("not_audio_or_event_validation"):
        raise ValueError("Direction candidates must be explicitly identified as offline research")
    runtime = {c["source_file"]: c for c in replay["cases"].values()}
    candidates = {c["source_file"]: c for c in audit["cases"]}
    cases = []
    for item in catalog["items"]:
        case, candidate = runtime[item["file"]], candidates[item["file"]]
        if item["sha256"] != case["source_sha256"] or item["sha256"] != candidate["source_sha256"]:
            raise ValueError("Photo hash mismatch: "+item["file"])
        events = [e for e in case["warnings"] if e["object_class"] in {"stairs", "upstairs", "downstairs"}]
        truth = item["ground_truth"]
        label_conflict = truth.get("stairs_visible") is False and any(
            truth.get(k) is True for k in ("upstairs_visible", "downstairs_visible"))
        entered = case.get("first_scene_frame_captured_at")
        latencies = [(e["created_at"]-entered)*1000 for e in events] if entered is not None else []
        cases.append({"file": item["file"], "environment": item["environment"],
                      "truth": truth, "label_conflict_excluded": label_conflict,
                      "extreme_viewpoint": item.get("extreme_viewpoint", False),
                      "split": item["evaluation_split"], "generic_stair_events": events,
                      "timely_generic_stair_event": any(0 <= v <= 3000 for v in latencies),
                      "raw_direction_labels": sorted({d["label"] for d in candidate["raw_direction_detections"]}),
                      "clip_top1": candidate["clip_prediction"],
                      "existing_down_confirmation": bool(candidate["existing_down_confirmation"])})

    def group_metrics(rows, label):
        opposite = "downstairs" if label == "upstairs" else "upstairs"
        return {"photos": len(rows), "ordinary_view_photos": sum(not r["extreme_viewpoint"] for r in rows),
                "generic_stair_warning_cases": sum(bool(r["generic_stair_events"]) for r in rows),
                "generic_stair_warning_within_3s_cases": sum(r["timely_generic_stair_event"] for r in rows),
                "direction_model_raw_hit_cases_not_events": sum(label in r["raw_direction_labels"] for r in rows),
                "direction_model_opposite_candidates": sum(opposite in r["raw_direction_labels"] for r in rows),
                "clip_top1_correct_cases_not_events": sum(r["clip_top1"] == label for r in rows),
                "clip_top1_opposite_cases": sum(r["clip_top1"] == opposite for r in rows)}

    matrix = {}
    for environment in ("indoor", "outdoor"):
        for label in ("upstairs", "downstairs"):
            opposite = "downstairs" if label == "upstairs" else "upstairs"
            rows = [r for r in cases if not r["label_conflict_excluded"] and r["environment"] == environment
                    and r["truth"].get(label+"_visible") is True
                    and r["truth"].get(opposite+"_visible") is False]
            matrix[environment+"_"+label] = {"all_reviewed_views": group_metrics(rows, label),
                "ordinary_views": group_metrics([r for r in rows if not r["extreme_viewpoint"]], label),
                "new_photos": group_metrics([r for r in rows if r["split"] == "new_real_stair_photos"], label)}
    negatives = [r for r in cases if not r["label_conflict_excluded"] and r["truth"].get("stairs_visible") is False]
    return {"kind": "local_stair_readiness_audit", "run_id": replay["run_id"],
            "current_runtime_direction_enabled": "stairs" in replay["active_tasks"],
            "direction_acceptance_passed": False, "field_acceptance_passed": False,
            "audio_playback_tested": False,
            "label_conflicts_excluded_after_freeze": [r["file"] for r in cases if r["label_conflict_excluded"]],
            "decision_basis": "Current default emits generic stairs only. Direction candidates are not events. "
                              "Historical/extreme views cannot replace representative field trials.",
            "matrix": matrix, "nonstair_negative_photos": len(negatives),
            "nonstair_generic_stair_false_warning_cases": sum(bool(r["generic_stair_events"]) for r in negatives),
            "nonstair_direction_raw_false_candidate_cases": sum(bool(r["raw_direction_labels"]) for r in negatives),
            "nonstair_clip_direction_top1_cases": sum(r["clip_top1"] in {"upstairs", "downstairs"} for r in negatives),
            "cases": cases}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ("catalog", "replay", "audit", "out"):
        p.add_argument("--"+flag, type=Path, required=True)
    args = p.parse_args()
    result = summarize(*(json.loads(getattr(args, f).read_text(encoding="utf-8"))
                         for f in ("catalog", "replay", "audit")))
    result["catalog_sha256"] = hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, indent=2))


if __name__ == "__main__":
    main()
