"""Score actual warning events, separately for each explicitly labelled hazard."""
import argparse
import hashlib
import json
from pathlib import Path

HAZARDS = {"possible_puddle": "visible_puddle", "pothole": "visible_pothole",
           "damaged_pavement": "visible_pavement_damage", "stairs": "stairs_visible"}


def summarize(catalog, report):
    if not report.get("completed") or report.get("task_errors") or report.get("error"):
        raise ValueError("A completed error-free replay is required for accuracy scoring")
    cases = {c["source_file"]: c for c in report["cases"].values()}
    if len(cases) != len(report["cases"]) or set(cases) != {x["file"] for x in catalog["items"]}:
        raise ValueError("Exactly one case per catalog image is required")
    result = {"basis": "Actual warning events, not detector boxes; four-second photo exposures. Previously used regression material, not untouched holdout or field accuracy.",
              "categories": {}, "cases": [], "unscored_events": []}
    for label, key in HAZARDS.items():
        row = {"tp": 0, "fn": 0, "fp": 0, "tn": 0, "unscored": 0, "misses": [], "false_alerts": []}
        for item in catalog["items"]:
            case = cases[item["file"]]
            if case["source_sha256"] != item["sha256"]:
                raise ValueError("Photo provenance mismatch")
            truth = item["ground_truth"].get(key)
            emitted = any(e["object_class"] == label for e in case["warnings"])
            if not isinstance(truth, bool):
                row["unscored"] += 1
                if emitted: result["unscored_events"].append({"file": item["file"], "category": label})
                continue
            outcome = ("tp" if emitted else "fn") if truth else ("fp" if emitted else "tn")
            row[outcome] += 1
            if outcome == "fn": row["misses"].append(item["file"])
            if outcome == "fp": row["false_alerts"].append(item["file"])
        row["recall"] = round(row["tp"] / (row["tp"] + row["fn"]), 4) if row["tp"] + row["fn"] else None
        row["precision"] = round(row["tp"] / (row["tp"] + row["fp"]), 4) if row["tp"] + row["fp"] else None
        result["categories"][label] = row
    for item in catalog["items"]:
        case = cases[item["file"]]
        warnings = sorted({e["object_class"] for e in case["warnings"]})
        false = [c for c,k in HAZARDS.items() if item["ground_truth"].get(k) is False and c in warnings]
        missed = [c for c,k in HAZARDS.items() if item["ground_truth"].get(k) is True and c not in warnings]
        result["cases"].append({"file":item["file"], "original_file":item.get("original_file",item["file"]),
            "groups":item.get("coverage_groups",[]), "synthetic":item.get("synthetic",False),
            "ground_truth":item["ground_truth"],"warnings":warnings,"false_alerts":false,"misses":missed,
            "event_count":len(case["warnings"]), "model_observations":case["model_observations"]})
    result["case_count"] = len(result["cases"])
    result["cases_with_false_alert"] = sum(bool(c["false_alerts"]) for c in result["cases"])
    result["cases_with_miss"] = sum(bool(c["misses"]) for c in result["cases"])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalog',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=summarize(json.loads(a.catalog.read_text(encoding='utf8')),json.loads(a.report.read_text(encoding='utf8')))
    result['input_sha256']={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in (a.catalog,a.report)}
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('cases','unscored_events')},ensure_ascii=False))

if __name__=='__main__':main()
