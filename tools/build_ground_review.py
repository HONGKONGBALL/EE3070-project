"""Offline photo-review gallery with real model boxes, separate from warning events.

Input manifest: {"runs": [{"catalog": ..., "report": ..., "assets": ...,
"group": ...}]}. Boxes are valid only for a repeated still-photo replay.
No inference, ground-truth boxes, or annotations are added to model input.
"""

import argparse
import base64
import hashlib
import html
import io
import json
from pathlib import Path

import cv2
from PIL import Image

from build_scene_replay import fit_frame


def observations(case):
    """Keep each model/frame observation separate, including historical records."""
    if case.get("box_observations"):
        return list(case["box_observations"].values())
    groups = {}
    for d in case["raw_detections"]:
        if d["label"] not in {"possible_puddle", "pothole", "damaged_pavement", "stairs"}:
            continue
        key = (d["source_model"], d["frame_id"])
        group = groups.setdefault(key, {"task": d["source_model"], "frame_id": d["frame_id"],
                                      "candidates": [], "historical_partial": True})
        group["candidates"].append({"label": d["label"], "confidence": d["confidence"],
                                  "bbox_normalized": d["bbox_normalized"],
                                  "reason": "historical_model_output"})
    return list(groups.values())


def build(manifest):
    cards = []
    for run in manifest["runs"]:
        catalog = json.loads(Path(run["catalog"]).read_text(encoding="utf-8"))
        report = json.loads(Path(run["report"]).read_text(encoding="utf-8"))
        if not report.get("completed") or report.get("task_errors") or report.get("error"):
            raise ValueError("Only completed, error-free photo replays can be reviewed")
        by_file = {c["source_file"]: c for c in report["cases"].values()}
        research = {}
        if run.get("direction_report"):
            audit = json.loads(Path(run["direction_report"]).read_text(encoding="utf-8"))
            if not audit.get("completed") or not audit.get("not_audio_or_event_validation"):
                raise ValueError("Direction observations must be explicitly offline research")
            research = {c["source_file"]: c for c in audit["cases"]}
        for item in catalog["items"]:
            case = by_file[item["file"]]
            asset = Path(run["assets"])/item["file"]
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            if digest != item["sha256"] or digest != case["source_sha256"]:
                raise ValueError("Photo provenance mismatch: " + item["file"])
            frame = fit_frame(cv2.imread(str(asset)))
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            buf = io.BytesIO(); image.save(buf, format="JPEG", quality=84)
            warnings = sorted({e["object_class"] for e in case["warnings"]})
            views = observations(case)
            direction_note = ""
            if item["file"] in research:
                observation = research[item["file"]]
                if observation["source_sha256"] != digest:
                    raise ValueError("Direction audit photo mismatch")
                views.append({"task": "方向模型（单图研究，未启用播报）", "frame_id": "单图",
                              "candidates": [{"label": d["label"], "confidence": d["confidence"],
                                  "bbox_normalized": [v/(640 if i % 2 == 0 else 480)
                                                      for i, v in enumerate(d["bbox"])],
                                  "reason": "离线方向候选，未接入当前播报流程"}
                                  for d in observation["raw_direction_detections"]]})
                direction_note = "MobileCLIP2 单图研究排名："+observation["clip_prediction"]+"；不是播报事件，也不是正确率。"
            false = [label for label, truth in (("possible_puddle", "visible_puddle"),
                       ("pothole", "visible_pothole"), ("damaged_pavement", "visible_pavement_damage"))
                     if item["ground_truth"].get(truth) is False and label in warnings]
            stair_warning = any(w in {"stairs", "upstairs", "downstairs"} for w in warnings)
            if item["ground_truth"].get("stairs_visible") is False and stair_warning:
                false.append("stairs")
            misses = [label for label, truth in (("possible_puddle", "visible_puddle"),
                      ("pothole", "visible_pothole"), ("damaged_pavement", "visible_pavement_damage"))
                      if item["ground_truth"].get(truth) is True and label not in warnings]
            if item["ground_truth"].get("stairs_visible") is True and not stair_warning:
                misses.append("stairs")
            cards.append({"file": item["file"], "group": run["group"],
                          "synthetic": item.get("synthetic", False),
                          "truth": item["ground_truth"], "warnings": warnings, "false": false,
                          "misses": misses,
                          "views": sorted(views, key=lambda v: -len(v["candidates"])),
                          "expected_description": item.get("review_expected", ""),
                          "direction_note": direction_note,
                          "source": item["source_page"], "license": item.get("license", ""),
                          "notes": item.get("review_notes", ""),
                          "scene_group": item.get("scene_group", item["file"]),
                          "image": "data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode()})
    return cards


PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>路况识别：室内误报与模型框复核</title><style>
body{font:16px/1.5 system-ui,sans-serif;background:#f4f6f8;color:#17202a;margin:0}main{max-width:1250px;margin:auto;padding:22px}h1{font-size:25px}
.controls{position:sticky;top:0;background:#f4f6f8;padding:12px 0;display:flex;gap:10px;flex-wrap:wrap;z-index:2}button,select{font:inherit;padding:6px;max-width:100%}
#grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:16px}article{padding:12px;background:white;border:1px solid #d6dde5;border-radius:6px}article.bad{border-left:5px solid #c33}
canvas{display:block;width:100%;height:auto;background:#111}.result{font-weight:650}small{overflow-wrap:anywhere}details{font-size:13px}.legend{color:#536273}
</style><main><h1>路况识别：室内误报与模型框复核</h1>
<p>实线框来自实际模型／分割输出，不是人工标注；虚线是固定分类观察区域。橙框是候选区域，是否提醒看下方事件；未检出时不画框。虚线框表示图像分类观察区域，不是物体定位框。每次只显示一个模型的一次观测，不把不同帧的框混在一起。semantic／segformer 的矩形表示分割区域的外接框，并非 YOLO 检测框。</p>
<p>当前地面模式未运行人、椅子等普通物体检测，因此不会显示这些物件的框。这些图片仅用于检查指定路况，不等于认定整张场景安全。AI 模拟素材会逐张标明，不能当成真实场地验证。静态照片回放，未测试树莓派实拍或耳机发声。</p><p>__CONTEXT__</p>
<div class="controls"><select id="group"></select><select id="issue"><option value="all">全部</option><option value="false">有误报提醒</option><option value="miss">有漏报</option><option value="boxes">有检测框</option></select><button id="prev">上一页</button><button id="next">下一页</button><span id="count"></span></div><div id="grid"></div></main><script>
const cases=__DATA__,names={possible_puddle:'积水',pothole:'坑洞',damaged_pavement:'破损路面',stairs:'楼梯',upstairs:'上行楼梯',downstairs:'下行楼梯'},reasons={other_task_owns_category:'该模型不负责此类提醒，已拦截',context_only:'仅作环境参考',verifier_rejected:'复核拒绝',outside_path:'不在前方关注区域',awaiting_confirmation:'等待连续确认',already_handled:'已处理此目标',cooldown:'提示冷却中',historical_model_output:'历史实际检测',eligible:'满足提示条件',indoor_floor_context:'室内地面条件拒绝',strong_evidence_required:'证据不足'},group=document.querySelector('#group'),issue=document.querySelector('#issue'),grid=document.querySelector('#grid');let page=0;
function el(tag,value,parent){const e=document.createElement(tag);if(value!==null)e.textContent=value;parent.appendChild(e);return e}
for(const v of [...new Set(cases.map(c=>c.group)),'全部']){const o=el('option',v,group);o.value=v}
group.value='全部';
function yes(v){return v===true?'有':v===false?'无':'未定'}
function render(){const selected=cases.filter(c=>(group.value==='全部'||c.group===group.value)&&(issue.value==='all'||(issue.value==='false'?c.false.length:issue.value==='miss'?c.misses.length:c.views.some(v=>v.candidates.length))));const max=Math.max(0,Math.ceil(selected.length/16)-1);page=Math.min(page,max);grid.replaceChildren();
for(const c of selected.slice(page*16,page*16+16)){const card=el('article',null,grid);if(c.false.length||c.misses.length)card.className='bad';const canvas=el('canvas',null,card);canvas.width=640;canvas.height=480;const ctx=canvas.getContext('2d'),image=new Image(),choice=el('select',null,card);c.views.forEach((v,i)=>{const o=el('option',v.task+' / frame '+v.frame_id+' / '+v.candidates.length+' 个候选',choice);o.value=i});el('option','原图（不画框）',choice).value='raw';const explanation=el('small','',card);
function draw(){if(!image.complete)return;ctx.drawImage(image,0,0,640,480);const v=choice.value==='raw'?null:c.views[Number(choice.value)];if(!v){explanation.textContent='原始照片按回放尺寸等比例缩放';return}ctx.lineWidth=3;ctx.font='16px sans-serif';for(const d of v.candidates){ctx.setLineDash(d.localization==='classification_roi'?[8,6]:[]);const b=d.bbox_normalized.map((n,i)=>n*(i%2?480:640));ctx.strokeStyle='#ffad32';ctx.strokeRect(b[0],b[1],b[2]-b[0],b[3]-b[1]);const t=d.localization==='classification_roi'?'楼梯：观察区域（非物体边界）':(names[d.label]||d.label)+' '+d.confidence.toFixed(2),w=ctx.measureText(t).width+8,y=Math.max(0,b[1]-22),x=Math.max(0,Math.min(b[0],640-w));ctx.fillStyle='#ffad32';ctx.fillRect(x,y,w,22);ctx.fillStyle='#151515';ctx.fillText(t,x+4,y+17)}ctx.setLineDash([]);explanation.textContent=v.candidates.length?v.candidates.map(d=>(names[d.label]||d.label)+': '+(reasons[d.reason]||d.reason)).join('；'):'这次观测未检出目标';if(v.historical_partial)explanation.textContent+='（历史记录仅保留部分检测，不是该帧全部输出）'}
image.onload=draw;image.src=c.image;choice.onchange=draw;el('p',c.synthetic?'AI 模拟图片（非实拍）':'真实照片回放',card).className='legend';
el('p',c.expected_description?'预期：'+c.expected_description:`预期：积水 ${yes(c.truth.visible_puddle)}；坑洞 ${yes(c.truth.visible_pothole)}`,card);el('p','实际提醒事件：'+(c.warnings.map(w=>names[w]||w).join('、')||'没有'),card).className='result';if(c.false.length)el('p','误报：'+c.false.map(w=>names[w]).join('、'),card);if(c.misses.length)el('p','未触发应有提醒：'+c.misses.map(w=>names[w]).join('、'),card);const details=el('details',null,card);el('summary','素材与说明',details);el('small',c.file+' | '+c.scene_group,details);el('p',c.notes,details);if(c.direction_note)el('p',c.direction_note,details);el('p',c.license,details);const a=el('a','原始来源',details);a.href=c.source;a.target='_blank';a.rel='noopener'}
document.querySelector('#count').textContent=`${selected.length} 张，第 ${page+1}/${max+1} 页`;document.querySelector('#prev').disabled=page===0;document.querySelector('#next').disabled=page===max}
group.onchange=issue.onchange=()=>{page=0;render()};document.querySelector('#prev').onclick=()=>{page--;render()};document.querySelector('#next').onclick=()=>{page++;render()};render();
</script></html>'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cards = build(manifest)
    page = PAGE.replace("__DATA__", json.dumps(cards, ensure_ascii=False).replace("<", "\\u003c"))
    page = page.replace("路况识别：室内误报与模型框复核", html.escape(manifest.get(
        "title", "路况识别：室内误报与模型框复核"))).replace("__CONTEXT__", html.escape(manifest.get("context", "")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(json.dumps({"gallery": str(args.out), "cards": len(cards),
                      "cases_with_boxes": sum(any(v["candidates"] for v in c["views"]) for c in cards)}))


if __name__ == "__main__":
    main()
