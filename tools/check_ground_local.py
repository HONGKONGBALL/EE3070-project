"""Run real ground models on a local video; no Pi, service changes or audio needed."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"pi"))
from cane.vision.v2 import V2, ROOT, load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, help="Explicit photo-scene timeline; never used to generate predictions")
    parser.add_argument("--config", type=Path, default=ROOT/"config/camera.v2.toml")
    parser.add_argument("--models-root", type=Path)
    parser.add_argument("--seconds", type=float, default=60, help="Maximum run time, including model startup (5-600 seconds)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 600:
        parser.error("--seconds must be between 5 and 600")
    if not args.video.is_file():
        parser.error("--video must be an existing local file")
    timeline = json.loads(args.timeline.read_text(encoding="utf-8")) if args.timeline else None
    settings = load_config(args.config, mode="ground", models_root=args.models_root)
    settings["source"] = {"video": str(args.video.resolve())}
    settings["distance"]["mode"] = "none"
    settings["audio"]["enabled"] = False
    # A timeline from a different configured replay must never leak into this run.
    settings.pop("simulation", None)
    if timeline:
        settings["simulation"] = {"timeline": str(args.timeline.resolve())}
    with args.video.open("rb") as file:
        video_sha256 = hashlib.file_digest(file, "sha256").hexdigest()
    report = {"kind": "local_ground_replay", "not_live_camera": True,
              "not_outdoor_validation": True, "audio_playback_tested": False,
              "video": str(args.video.resolve()),
              "video_sha256": video_sha256,
              "runtime_source_sha256":{name:hashlib.sha256((ROOT/"pi/cane/vision"/name).read_bytes()).hexdigest()
                  for name in ("v2.py","v2_policy.py","episodes.py","speech.py","diagnostics.py","camera.py","puddle.py","ground.py","stair_context.py")},
              "config": settings, "completed": False, "cases": {}, "task_errors": {},
              "count_basis": "Distinct sampled task frames, including explicit image-quality skips; detections retain first observation per model/label. Not independent accuracy trials."}
    if args.timeline:
        report["timeline_sha256"] = hashlib.sha256(args.timeline.read_bytes()).hexdigest()
    expected = {case["id"]: case for case in timeline["cases"]} if timeline else {}
    app = None
    seen = set()
    started = time.monotonic()
    try:
        app = V2(settings)
        app.start()
        report["active_tasks"] = list(app.ground_tasks)
        report["run_id"], report["runtime_log"] = app.run_id, str(app.log_path)
        last_image = None
        while time.monotonic()-started < args.seconds:
            status = app.status()
            report["last_tasks"] = status["tasks"]
            for name, state in status["tasks"].items():
                if state["state"] == "error": report["task_errors"][name] = state["error"]
            scene = status.get("simulation_case")
            case_id = scene["id"] if scene else ("clip" if not timeline else None)
            if status["ready"] and case_id:
                case = report["cases"].setdefault(case_id, {**expected.get(case_id, {}),
                    "model_observations": {}, "reason_counts": {}, "raw_detections": {},
                    "box_observations": {}, "warnings": {}})
                if scene and scene.get("first_scene_frame_captured_at") is not None:
                    case["first_scene_frame_captured_at"]=scene["first_scene_frame_captured_at"]
                for name in app.ground_tasks:
                    state=status["tasks"][name]
                    key=(case_id,name,state.get("frame_id"))
                    current=expected.get(case_id)
                    if current and not current["start_frame"] <= state.get("frame_id",-1) <= current["end_frame"]:
                        continue
                    if state.get("prediction_skipped") and key not in seen:
                        seen.add(key)
                        case["model_observations"][name]=case["model_observations"].get(name,0)+1
                        counts=case["reason_counts"].setdefault(name,{})
                        counts["image_quality"]=counts.get("image_quality",0)+1
                for name, entry in status["ground_diagnostics"].items():
                    key = (case_id, name, entry.get("frame_id"))
                    expected_case = expected.get(case_id)
                    if expected_case and not expected_case["start_frame"] <= entry.get("frame_id", -1) <= expected_case["end_frame"]:
                        continue
                    if entry.get("state") != "observed" or key in seen: continue
                    seen.add(key)
                    case["model_observations"][name] = case["model_observations"].get(name, 0)+1
                    counts = case["reason_counts"].setdefault(name, {})
                    reasons = entry["reason_counts"] or {entry["reason"]: 1}
                    for reason, count in reasons.items(): counts[reason] = counts.get(reason, 0)+count
                    # One bounded, actual observation per task for photo review.
                    # Keep its frame ID and full candidate list together; never combine
                    # boxes from different frames into a purported single prediction.
                    previous = case["box_observations"].get(name)
                    if previous is None or len(entry["candidates"]) > len(previous["candidates"]):
                        case["box_observations"][name] = {
                            "task": name, "frame_id": entry["frame_id"],
                            "captured_at": entry["captured_at"], "model_id": entry["model_id"],
                            "candidates": entry["candidates"]}
                for detection in status["detections"]:
                    if expected.get(case_id) and not expected[case_id]["start_frame"] <= detection["frame_id"] <= expected[case_id]["end_frame"]:
                        continue
                    key = detection["source_model"]+":"+detection["label"]
                    case["raw_detections"].setdefault(key, detection)
                for event in status["recent_events"]:
                    if not timeline or event.get("simulation_case") == case_id:
                        case["warnings"][event["event_id"]] = event
                last_image = app.image() or last_image
                report["last_preview_fps"] = status["fps"]
            if status["camera"]["error"] == "Replay finished":
                report["completed"] = bool(report["cases"]) and (not timeline or set(report["cases"]) == set(expected))
                report["stop_reason"] = "end_of_video"
                break
            time.sleep(.2)
        else:
            report["stop_reason"] = "time_limit"
        if last_image:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            preview = args.out.with_suffix(".jpg")
            preview.write_bytes(last_image)
            report["last_annotated_preview"] = str(preview)
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        if app is not None:
            app.close()
        report["elapsed_s"] = round(time.monotonic()-started, 2)
        for case in report["cases"].values():
            case["raw_detections"] = list(case["raw_detections"].values())
            case["warnings"] = list(case["warnings"].values())
            case["all_ground_tasks_observed"] = all(name in case["model_observations"]
                for name in report.get("active_tasks", []))
        report["completed"] = report["completed"] and all(
            case["all_ground_tasks_observed"] for case in report["cases"].values())
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(args.out), "completed": report["completed"],
                      "stop_reason": report.get("stop_reason"), "cases": len(report["cases"]),
                      "task_errors": report["task_errors"], "error": report.get("error")}, indent=2))
    if not report["completed"] or report["task_errors"] or report.get("error"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
