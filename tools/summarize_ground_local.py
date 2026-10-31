"""Score final replay events against a separately frozen, visually reviewed catalog."""

import argparse
import hashlib
import json
from pathlib import Path


def metrics(cases, category):
    truth="visible_"+category
    clear="obvious_"+category
    positive=[c for c in cases if c["truth"].get(truth) is True]
    obvious=[c for c in positive if c.get(clear,False)]
    negative=[c for c in cases if c["truth"].get(truth) is False]
    def hit(c):return bool(c["events"][category])
    def timely(c):return any(v is not None and v<=3000 for v in c["latencies_ms"][category])
    return {"positive_cases":len(positive),"positive_hits":sum(hit(c) for c in positive),
            "clear_positive_cases":len(obvious),"clear_positive_hits":sum(hit(c) for c in obvious),
            "clear_timely_hits":sum(timely(c) for c in obvious),
            "negative_cases":len(negative),"false_warning_cases":sum(hit(c) for c in negative),
            "clear_misses":[c["file"] for c in obvious if not hit(c)],
            "false_warnings":[c["file"] for c in negative if hit(c)]}


def summarize(report,catalog):
    if not report.get("completed"):
        raise ValueError("An incomplete replay cannot pass validation")
    by_file={c["source_file"]:c for c in report["cases"].values()}
    cases=[]
    for item in catalog["items"]:
        case=by_file[item["file"]]
        if case["source_sha256"]!=item["sha256"]:
            raise ValueError("Replay photo hash differs from frozen catalog: "+item["file"])
        events={category:[e for e in case["warnings"] if e.get("object_class")==label]
                for category,label in (("puddle","possible_puddle"),("pothole","pothole"))}
        entered=case.get("first_scene_frame_captured_at")
        latencies={k:[round((e["created_at"]-entered)*1000,1) if entered is not None else None for e in es]
                   for k,es in events.items()}
        cases.append({"file":item["file"],"split":item["evaluation_split"],
            "scene_group":item.get("scene_group",item["file"]),"truth":item["ground_truth"],
            "obvious_puddle":item.get("obvious_puddle",False),"obvious_pothole":item.get("obvious_pothole",False),
            "blue_ground":item.get("blue_ground",False),"events":events,"latencies_ms":latencies,
            "reason_counts":case["reason_counts"],"review_notes":item.get("review_notes","")})
    splits={}
    for split in dict.fromkeys(c["split"] for c in cases):
        subset=[c for c in cases if c["split"]==split]
        representatives={}
        for c in subset:representatives.setdefault(c["scene_group"],c)
        first=list(representatives.values())
        blue=[c for c in subset if c["blue_ground"]]
        splits[split]={"photos":{k:metrics(subset,k) for k in ("puddle","pothole")},
                       "first_photo_per_source_group":{k:metrics(first,k) for k in ("puddle","pothole")},
                       "blue_ground":{"cases":len(blue),"false_puddle_warnings":sum(bool(c["events"]["puddle"]) for c in blue)}}
    return {"run_id":report["run_id"],"not_outdoor_validation":True,"audio_playback_tested":False,
            "model_training_overlap":"unknown","split_counts":{k:sum(c["split"]==k for c in cases) for k in splits},
            "timing_basis":"First replay frame seen by renderer to warning decision, not physical exposure or audible output",
            "group_basis":catalog.get("grouping"),"splits":splits,"task_errors":report["task_errors"],"cases":cases}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",type=Path,required=True)
    parser.add_argument("--catalog",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    result=summarize(json.loads(args.report.read_text(encoding="utf-8")),
                     json.loads(args.catalog.read_text(encoding="utf-8")))
    result["catalog_sha256"]=hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="cases"},indent=2))


if __name__=="__main__":main()
