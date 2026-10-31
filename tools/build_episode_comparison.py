"""Build a small local before/after review; media and raw results stay outside Git."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil


PAGE = r'''<!doctype html><html lang="zh-Hans"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Smart Cane 本地优化对比</title><style>
body{font:16px/1.6 system-ui;background:#f3f5f8;color:#182739;max-width:1450px;margin:auto;padding:24px}h1{font-size:28px}h2{font-size:19px}button,select{font:inherit;padding:7px 12px;margin:5px}section{background:white;border:1px solid #cbd6e3;border-radius:10px;padding:18px;margin:16px 0}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}video{width:100%;background:#111}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}small,.note{color:#526276}a{color:#1455a2}.metrics{font-weight:600}.scroll{overflow:auto}@media(max-width:800px){.pair{grid-template-columns:1fr}body{padding:12px}}</style>
<h1>Smart Cane：本地危险片段与声音队列优化</h1>
<p>相同预训练权重，修改确认、片段去重与过期提醒处理。未连接或部署到 Pi。视频保留真实模型框；虚线是分类观察区域。</p>
<section><b>已验证：</b>125 张照片逐类结果与基线一致；鞋子／黑色图案 10 张没有危险提醒。裂纹仍是 2/12，尚未解决模型能力不足。<br>
<b>声音说明：</b>左侧基线关闭声音；右侧用英语 WAV 时长模拟真实队列，未测扬声器或 Pi 延迟。“开始／完成”均指模拟播放。点击表格试听仅用于了解句子。<br>
<a href="photos.html">125 张照片与实际模型框</a> · <a href="comparison.json">原始对比记录</a> · <a href="report.md">完整报告与待验证项目</a></section>
<label>场景 <select id="choice"></select></label><button id="together">从头同时播放</button><button id="pause">全部暂停</button>
<p id="note" class="note"></p><section class="pair"><div><h2>修改前：框跟踪＋8 秒冷却</h2><video id="before" controls muted preload="metadata"></video><p id="beforeCount" class="metrics"></p></div><div><h2>修改后：时间证据＋危险片段</h2><video id="after" controls muted preload="metadata"></video><p id="afterCount" class="metrics"></p></div></section>
<section><h2>修改后事件与模拟声音记录</h2><p>请求数包含排队过期后的重新判断；只有“模拟开始”计入播放。回看时间对应源视频；各阶段延迟使用运行时单调时钟，不是实测接近距离。</p><div class="scroll"><table><thead><tr><th>回看约秒</th><th>原始类别／片段</th><th>提醒句子</th><th>声音处理</th><th>本帧→事件</th><th>排队→模拟开始</th></tr></thead><tbody id="events"></tbody></table></div><details><summary>基线实际事件</summary><pre id="oldEvents"></pre></details><details><summary>片段退出／重开原因</summary><pre id="exits"></pre></details></section>
<script>
const cases=__DATA__,choice=document.getElementById('choice'),before=document.getElementById('before'),after=document.getElementById('after');
let joined=false,voice=null;
function cell(row,text){const c=document.createElement('td');c.textContent=text;row.append(c);return c}
for(const [i,c] of cases.entries()){const o=document.createElement('option');o.value=i;o.textContent=c.id;choice.append(o)}
function load(){joined=false;before.pause();after.pause();if(voice)voice.pause();const c=cases[Number(choice.value)];before.src=c.before_video;after.src=c.after_video;document.getElementById('note').textContent=c.note;
document.getElementById('beforeCount').textContent=`原始提醒事件 ${c.before_events.length} 次（基线未验证声音）`;
document.getElementById('afterCount').textContent=`请求 ${c.events.length} 次 · 模拟开始 ${c.starts} 次 · 有请求的危险片段 ${c.episodes} 个`;
const rows=[];for(const e of c.events){const r=document.createElement('tr');cell(r,e.source_s.toFixed(2));cell(r,e.label+' / '+e.episode);const message=cell(r,e.text);const b=document.createElement('button');b.textContent='试听';b.onclick=()=>{if(voice)voice.pause();voice=new Audio('audio/'+e.phrase+'.wav');voice.play()};message.append(b);cell(r,e.delivery);cell(r,e.latency+' ms');cell(r,e.queue_ms==null?'未开始':e.queue_ms+' ms');rows.push(r)}document.getElementById('events').replaceChildren(...rows);
document.getElementById('oldEvents').textContent=c.before_events.map(e=>`${e.time.toFixed(2)}s ${e.label}`).join('\n');document.getElementById('exits').textContent=c.exits.join('\n')||'片段没有重新开始';}
document.getElementById('together').onclick=()=>{before.currentTime=0;after.currentTime=0;joined=true;before.play();after.play()};document.getElementById('pause').onclick=()=>{joined=false;before.pause();after.pause();if(voice)voice.pause()};before.ontimeupdate=()=>{if(joined&&!before.paused&&Math.abs(before.currentTime-after.currentTime)>.35)after.currentTime=before.currentTime};choice.onchange=load;load();
</script></html>'''


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    root=a.root;out=root/'review';media=out/'media';media.mkdir(parents=True,exist_ok=True)
    catalog=json.loads((root/'final-catalog.json').read_text(encoding='utf8'))
    after=json.loads((root/'final-videos/all-results.json').read_text())
    assert after['completed']
    before={}
    for folder in ['walking-video-validation-20261031','video-validation-20261031']:
        base=root.parent/folder/'results'
        for r in json.loads((base/'all-results.json').read_text())['clips']:before[r['id']]=(base,r)
    cards=[]
    for clip,r in zip(catalog['clips'],after['clips']):
        base,old=before[r['id']];name=r['id']
        for version,path in [('before',base/old['annotated_video']),('after',root/'final-videos'/r['annotated_video'])]:
            shutil.copy2(path,media/f'{name}-{version}.webm')
        rows=[]
        for e in r['events']:
            aid=e['event_id'];audio=[x for x in r['audio_records'] if x.get('event_id')==aid]
            starts=[x for x in audio if x['record_type']=='audio_simulated_start']
            actions=[x['action'] for x in r['arbiter_records'] if x.get('event_id')==aid and x['action']!='played']
            stages=[x['record_type'].replace('audio_simulated_','模拟').replace('start','开始').replace('done','完成').replace('interrupted','中断') for x in audio if x['record_type'].startswith('audio_simulated_')]
            rows.append({'source_s':e['first_seen_decision_source_s'],'label':e['object_class'],
                'episode':e['episode_id'].split(':')[-1],'text':e['text'],'phrase':e['phrase_id'],
                'latency':e['capture_to_alert_ms'],'queue_ms':round((starts[0]['simulation_started_at']-starts[0]['submitted_at'])*1000,1) if starts else None,
                'delivery':' → '.join(stages+actions) or '只有请求，未开始播放'})
        cards.append({'id':name,'note':clip['review_note'],'before_video':f'media/{name}-before.webm',
            'after_video':f'media/{name}-after.webm','before_events':[{'label':e['object_class'],'time':e['first_seen_decision_source_s']} for e in old['events']],
            'events':rows,'starts':sum(x['record_type']=='audio_simulated_start' for x in r['audio_records']),
            'episodes':len({e['episode_id'] for e in r['events']}),
            'exits':[x['warning_family']+': '+x['reset_reason'] for x in r['episode_records'] if x['record_type']=='hazard_episode_end']})
    (out/'index.html').write_text(PAGE.replace('__DATA__',json.dumps(cards,ensure_ascii=False).replace('<','\\u003c')),encoding='utf8')
    (out/'comparison.json').write_text(json.dumps(cards,ensure_ascii=False,indent=2),encoding='utf8')
    photo_review=root/'photos-final/review.html'
    shutil.copy2(photo_review if photo_review.exists() else root/'photos/review.html',out/'photos.html')
    shutil.copytree(root/'audio/en',out/'audio',dirs_exist_ok=True)
    print(json.dumps({'review':str(out/'index.html'),'clips':len(cards)},ensure_ascii=False))


if __name__=='__main__':main()
