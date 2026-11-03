"""Bounded explanations of the existing ground policy; no extra inference."""

from collections import Counter

from .association import ground_overlaps_region
from .v2_policy import PHRASES, GROUND_TASK_LABELS


def ground_diagnostic(name, raw, filtered, policy, now, *, blocked_by=""):
    """Inspect actual candidates/tracks without changing what may generate speech.

    Counts describe this model observation, not accuracy or independent trials.
    A handled track means the policy handled it, not that audio was heard.
    """
    if raw is None or now-raw["captured_at"] > 1.5:
        return {"state": "unavailable", "reason": "no_fresh_result", "candidates": []}
    if not raw.get("visual_quality", {}).get("usable", True):
        return {"state": "limited", "reason": "image_quality", "candidates": [],
                "frame_id": raw["frame_id"], "captured_at": raw["captured_at"]}

    def key(d): return d["label"], tuple(d["bbox_normalized"])

    detected = {key(d) for d in raw["detections"]}
    retained = {key(d) for d in filtered["detections"]} if filtered is not None else set()
    unique = {key(d): d for d in raw.get("candidate_detections", []) + raw["detections"]}
    rows = []
    for identity, d in list(unique.items())[:20]:
        if d["label"] not in PHRASES:
            reason = "unsupported_label"
        elif d["label"] not in GROUND_TASK_LABELS[name]:
            reason = "context_only" if name=="semantic" else "other_task_owns_category"
        elif not ground_overlaps_region(d["bbox_normalized"], policy.ground_roi):
            reason = "outside_path"
        elif identity not in detected:
            reason = "verifier_rejected" if d.get("evidence", {}).get("verification_accepted") is False else "model_filtered"
        elif identity not in retained:
            reason = blocked_by or "context_rejected"
        else:
            reason = policy.episodes.reason(name, d["label"], now)
        rows.append({"label": d["label"], "confidence": d["confidence"],
                     "localization": d.get("localization", "bbox"),
                     "bbox_normalized": d["bbox_normalized"], "reason": reason})
    return {"state": "observed", "frame_id": raw["frame_id"], "captured_at": raw["captured_at"],
            "model_id": raw.get("model_id", name), "raw_count": len(unique),
            "candidate_observed_at":raw.get("candidates_observed_at"),
            "verification_completed_at":raw.get("verification_completed_at"),
            "model_accepted_count": len(detected), "context_accepted_count": len(retained),
            "reason": "no_candidates" if not unique else "candidates_observed",
            "reason_counts": dict(Counter(d["reason"] for d in rows)), "candidates": rows}
