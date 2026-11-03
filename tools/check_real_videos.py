"""Replay actual moving videos through the current V2 pipeline and save timed evidence.

Labels are read only after inference; they never reset tracking or generate predictions.
Rendered WebM reviews resample actual runtime JPEGs onto the source media clock.
"""
import argparse
import bisect
import hashlib
import html
import json
import re
from pathlib import Path
import sys
import time

import cv2
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi'))
from cane.vision.v2 import V2,load_config


def evaluate_intervals(clip,events):
    # A label change for another category must not demand a repeated warning
    # for one continuously visible hazard (e.g. pothole=true before/after 9s).
    by_category={}
    for interval in clip['intervals']:
        for category,truth in interval['truth'].items():
            if not isinstance(truth,bool):continue
            segments=by_category.setdefault(category,[])
            if segments and segments[-1]['truth']==truth and abs(segments[-1]['end']-interval['start'])<1e-6:
                segments[-1]['end']=interval['end']
            else:segments.append({'start':interval['start'],'end':interval['end'],'truth':truth})
    rows=[]
    for category,segments in by_category.items():
        for interval in segments:
            truth=interval['truth']
            selected=[e for e in events if e['object_class']==category
                and interval['start']<=e['evidence_source_s']<interval['end']]
            rows.append({'start':interval['start'],'end':interval['end'],'category':category,
                'expected_visible':truth,'events':len(selected),
                'result':('triggered' if selected else 'not_triggered') if truth else ('false_event' if selected else 'quiet'),
                'evidence_clock_note':'Event evidence frame time, not when an actual speaker made sound.'})
    return rows


def run_clip(clip,config,models_root,out,timed_audio_dir=None,encode_review=True,mode='ground'):
    source=Path(clip['input']);assert hashlib.sha256(source.read_bytes()).hexdigest()==clip['input_sha256']
    settings=load_config(config,mode=mode,models_root=models_root)
    settings['source']={'video':str(source)};settings['distance']['mode']='none';settings['audio']['enabled']=False
    settings.pop('simulation',None)
    app=V2(settings);assert not app.timeline
    if timed_audio_dir:
        from timed_review_audio import TimedReviewAudio
        app.audio.sink=TimedReviewAudio(app.audio,timed_audio_dir)
    started=time.monotonic();samples=[];previews=[];events={};errors={};seen_preview=-1
    report={'id':clip['id'],'input_sha256':clip['input_sha256'],'config':settings,
        'mode':mode,
        'no_policy_scene_resets':True,'audio_playback_tested':False,'not_pi_timing':True,'completed':False,
        'timed_wav_simulation':bool(timed_audio_dir),
        'runtime_source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__).parents[1]/'pi/cane/vision').glob('*.py')}}
    try:
        app.start();report['run_id']=app.run_id;report['runtime_log']=str(app.log_path)
        while time.monotonic()-started<clip['duration_s']*1.5+20:
            status=app.status();frame=status['frame_id'];media=(frame-1)/clip['input_fps'] if frame else 0
            samples.append({'observed_source_s':media,'status':status})
            for name,state in status['tasks'].items():
                if state['state']=='error':errors[name]=state['error']
            for event in status['recent_events']:
                if event['event_id'] not in events:
                    events[event['event_id']]={**event,'evidence_source_s':(event['frame_id']-1)/clip['input_fps'],
                        'first_seen_decision_source_s':media}
            if status['ready']:
                with app.condition:
                    jpeg=app.jpeg;preview_id=app.frame_id
                if jpeg and preview_id!=seen_preview:
                    previews.append(((preview_id-1)/clip['input_fps'],jpeg));seen_preview=preview_id
            if status['camera']['error']=='Replay finished':
                report['completed']=True;break
            time.sleep(.08)
    finally:app.close()
    report.update(elapsed_s=round(time.monotonic()-started,2),task_errors=errors,events=list(events.values()),
                  collected_preview_frames=len(previews),preview_fps_last=samples[-1]['status']['fps'] if samples else None)
    last_by_category={}
    for event in report['events']:
        previous=last_by_category.get(event['object_class'])
        event['seconds_since_previous_same_category']=round(event['created_at']-previous,3) if previous is not None else None
        last_by_category[event['object_class']]=event['created_at']
    report['interval_checks']=evaluate_intervals(clip,report['events'])
    log=[json.loads(line) for line in app.log_path.read_text(encoding='utf8').splitlines()]
    report['runtime_error_records']=[r for r in log if r['record_type'] in ('model_error','inference_error','camera_error')]
    report['arbiter_records']=[r for r in log if r['record_type']=='arbiter']
    report['episode_records']=[r for r in log if r['record_type'].startswith('hazard_episode_')]
    report['audio_records']=[r for r in log if r['record_type'].startswith('audio_')]
    video=out/(clip['id']+'-annotated.webm')
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'VP80'),10,(640,480)) if encode_review else None
    assert writer is None or writer.isOpened(),'WebM encoder unavailable'
    clocks=[x[0] for x in previews]
    try:
        for i in range(int(clip['duration_s']*10) if encode_review else 0):
            now=i/10;index=bisect.bisect_right(clocks,now)-1
            if index>=0 and now-clocks[index]<=1.5:
                image=cv2.imdecode(np.frombuffer(previews[index][1],np.uint8),cv2.IMREAD_COLOR)
            else:
                image=np.zeros((480,640,3),np.uint8)
                cv2.putText(image,'Waiting for fresh runtime preview',(20,210),cv2.FONT_HERSHEY_SIMPLEX,.7,(220,220,220),1)
            cv2.rectangle(image,(0,430),(640,480),(20,20,20),-1)
            audio_label='TIMED AUDIO SIMULATION' if timed_audio_dir else 'AUDIO OFF'
            cv2.putText(image,f'REAL VIDEO | source {now:.1f}s | {audio_label}',(7,449),cv2.FONT_HERSHEY_SIMPLEX,.43,(240,240,240),1)
            recent=[e['object_class'] for e in events.values() if 0<=now-e['first_seen_decision_source_s']<2]
            cv2.putText(image,'EVENT: '+(', '.join(recent) if recent else 'none newly emitted'),(7,470),cv2.FONT_HERSHEY_SIMPLEX,.47,(70,230,255),1)
            writer.write(image)
    finally:
        if writer:writer.release()
    report['annotated_video']=video.name if encode_review else None
    (out/(clip['id']+'-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (out/(clip['id']+'-samples.json')).write_text(json.dumps(samples,ensure_ascii=False),encoding='utf8')
    print(json.dumps({'id':clip['id'],'completed':report['completed'],'elapsed_s':report['elapsed_s'],
        'errors':errors,'events':[(e['object_class'],round(e['evidence_source_s'],2)) for e in events.values()]},ensure_ascii=False),flush=True)
    return report


def gallery(catalog,reports,out):
    names={'possible_puddle':'积水','pothole':'坑洞','damaged_pavement':'可见裂损','stairs':'楼梯'}
    cards=[]
    for clip,r in zip(catalog['clips'],reports):
        events='<br>'.join(f"{e['first_seen_decision_source_s']:.2f}s：{html.escape(names.get(e['object_class'],e['object_class']))}（证据帧 {e['evidence_source_s']:.2f}s）"+
            (f"；距同类上次实际 {e['seconds_since_previous_same_category']:.2f} 秒" if e.get('seconds_since_previous_same_category') is not None else '') for e in r['events']) or '没有生成提醒事件'
        checks='<br>'.join(f"{x['start']:.1f}–{x['end']:.1f}s {names[x['category']]}："+
            {'triggered':'出现提醒','not_triggered':'未触发（正例窗口）','false_event':'出现误报事件','quiet':'保持安静（负例窗口）'}[x['result']] for x in r['interval_checks'])
        cards.append(f'''<article><h2>{html.escape(clip['id'])}</h2><video controls preload="metadata" muted src="{r['annotated_video']}"></video>
<p>{html.escape(clip['review_note'])}</p><p><b>实际生成的提醒事件：</b><br>{events}</p>
<details><summary>冻结时间窗口与结果</summary>{checks}</details>
<p><a href="{html.escape(clip['source_page'],quote=True)}" target="_blank" rel="noopener">原始来源</a> · {html.escape(clip.get('license') or '许可详情见来源')} · <a href="{clip['id']}-report.json">原始运行记录</a></p>
<details><summary>作者与原视频说明</summary>{html.escape(re.sub(r'<[^>]+>', ' ', clip.get('artist_html','')))}<br>{html.escape(re.sub(r'<[^>]+>', ' ', clip.get('description_html','')))}</details></article>''')
    page='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>真实视频回放测试</title>
<style>body{font:16px/1.6 system-ui;background:#f3f5f8;color:#17202a;margin:24px auto;max-width:1300px;padding:12px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:20px}article{background:white;padding:16px;border:1px solid #ccd4df;border-radius:8px}video{width:100%;background:black}h2{font-size:20px}details{margin:12px 0}p{overflow-wrap:anywhere}</style>
<h1>真实视频回放：模型框与提醒事件</h1><p>__CLIP_COUNT__ 段真实连续视频，不是重复照片，不重置视频内部跟踪。使用指定配置和本地版本代码在电脑运行，模型权重保持不变，未更改 Pi 实时服务。无声音输出。</p>
<p>视频是实际运行预览按源视频时间重采样的回看，可能重复最近一帧，并非每一原始帧都完成了推理。框表示模型结果，虚线表示分类观察区域；黄色 EVENT 行仅表示生成了提醒事件，不代表耳机发声。首次模型加载期间可能显示等待画面。</p>
<p>真值在运行前按原视频人工抽帧检查冻结。未知类别和转换边界不计分；没有逐帧框标注，不报告 mAP 或总体准确率。模糊、车流遮挡及加速视频均明确保留，不能替代真实拐杖移动验收。</p><main>'''+''.join(cards)+'</main>'
    (out/'video-review.html').write_text(page.replace('__CLIP_COUNT__',str(len(reports))),encoding='utf8')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--catalog',type=Path,required=True)
    p.add_argument('--models-root',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--timed-audio-dir',type=Path,help='Exercise the real queue with WAV durations; no sound device')
    a=p.parse_args()
    if a.timed_audio_dir and not a.timed_audio_dir.is_dir():p.error('Timed audio directory must exist')
    catalog=json.loads(a.catalog.read_text(encoding='utf8'));config=Path(catalog['config_file'])
    assert hashlib.sha256(config.read_bytes()).hexdigest()==catalog['config_sha256']
    a.out.mkdir(parents=True,exist_ok=True);reports=[]
    for clip in catalog['clips']:
        reports.append(run_clip(clip,config,a.models_root,a.out,a.timed_audio_dir))
        (a.out/'all-results.json').write_text(json.dumps({'catalog_sha256':hashlib.sha256(a.catalog.read_bytes()).hexdigest(),
            'not_pi_performance':True,'audio_playback_tested':False,
            'completed':len(reports)==len(catalog['clips']) and all(r['completed'] for r in reports),'clips':reports},ensure_ascii=False,indent=2),encoding='utf8')
    gallery(catalog,reports,a.out)

if __name__=='__main__':main()
