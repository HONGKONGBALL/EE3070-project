"""Make visible, timed crowd diagnostics from recorded full-runtime video tests.

No model, threshold, observation or event is changed by this review renderer.
"""
import argparse
import bisect
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import re
import shutil

import cv2
import numpy as np


REASONS = {
    'too_few_concentrated_people': '合格的聚集人物未达到配置人数',
    'low_visible_occupancy': '人物框覆盖画面不足 18%',
    'people_too_spread_out': '聚集区域内部不够紧密',
    'cluster_covers_little_of_view': '聚集人群横向覆盖不足',
    'people_small_in_view': '人物在画面中太小',
    'semantic_context_unavailable': '语义结果不可用',
    'visible_people_not_tightly_packed': '聚集区域的人物语义支持不足',
    'substantial_ground_visible_between_people': '聚集区域内可见地面过多',
    'no_usable_people': '没有合格人物框',
    'awaiting_matching_semantic_observation': '等待时间匹配的语义结果',
    'no_person_observation': '尚未获得人物结果',
}
STATES = {'UNKNOWN': '未知／结果未就绪', 'NOT_CROWDED': '不满足密集条件',
          'CANDIDATE': '密集候选，尚未连续确认', 'CONFIRMED': '已确认密集人群',
          'DEFERRED': '路况优先，等待重新评估人群',
          'PENDING_AUDIO': '已生成提醒，等待播报', 'ANNOUNCED': '本次人群已处理'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    root = args.results
    repo = Path(__file__).parents[1]
    out = root / 'review'
    out.mkdir(exist_ok=True)
    read = lambda path: json.loads((root / path).read_text(encoding='utf8'))
    catalog = read('catalog.json')
    reports = {r['id']: r for r in read('runs/all-results.json')['clips']}
    esc = lambda value: html.escape(str(value), quote=True)
    cards, summaries = [], []
    for clip in catalog['clips']:
        report = reports[clip['id']]
        settings = report['config']['crowd']
        samples = read('runs/' + clip['id'] + '-samples.json')
        clocks = [s['observed_source_s'] for s in samples]
        observations = {}
        for sample in samples:
            crowd = sample['status']['crowd']
            if (crowd.get('frame_id', -1) > 0 and crowd.get('fresh')
                    and crowd['evidence'].get('context_available')):
                observations[crowd['frame_id']] = {
                    'source_s': round((crowd['frame_id'] - 1) / clip['input_fps'], 3),
                    'state': crowd['state'], 'evidence': crowd['evidence'],
                    'delivery': crowd['delivery'], 'confirmed': crowd['confirmed']}
        rows = list(observations.values())
        reasons = Counter(reason for row in rows for reason in row['evidence']['reasons'])
        crowd_events = [e for e in report['events'] if e['kind'] == 'CROWD']
        starts = [r for r in report['audio_records']
                  if r['record_type'] == 'audio_simulated_start' and r['phrase_id'] == 'crowd_ahead']
        completed = [r for r in report['audio_records']
                     if r['record_type'] == 'audio_simulated_done' and r['phrase_id'] == 'crowd_ahead']
        summary = {'id': clip['id'], 'label_zh': clip['label_zh'], 'expected_crowd': clip['expected_crowd'],
                   'independent_observations': len(rows),
                   'dense_candidate_observations': sum(row['evidence']['candidate'] for row in rows),
                   'crowd_events': len(crowd_events), 'simulated_crowd_starts': len(starts),
                   'simulated_crowd_completed': len(completed),
                   'person_count_max': max((row['evidence']['person_count'] for row in rows), default=0),
                   'cluster_count_max': max((row['evidence']['cluster_count'] for row in rows), default=0),
                   'rejection_counts': dict(reasons),
                   'other_events': [{'label': e['object_class'], 'source_s': e['first_seen_decision_source_s']}
                                    for e in report['events'] if e['kind'] != 'CROWD'],
                   'model_errors': report['task_errors'], 'physical_audio_tested': False}
        summaries.append(summary)
        cap = cv2.VideoCapture(clip['input'])
        target = out / (clip['id'] + '-diagnostic.webm')
        writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*'VP80'), 10, (960, 540))
        assert writer.isOpened()
        index, current = -1, None
        try:
            for tick in range(int(clip['duration_s'] * 10)):
                at = tick / 10
                wanted = int(at * clip['input_fps'])
                while index < wanted:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    index += 1
                    current = frame
                assert current is not None
                canvas = np.zeros((540, 960, 3), np.uint8)
                canvas[:480, :640] = cv2.resize(current, (640, 480))
                pos = max(0, bisect.bisect_right(clocks, at) - 1)
                status = samples[pos]['status']
                crowd = status['crowd']
                evidence = crowd['evidence']
                people = [d for d in status['detections'] if d['label'] == 'person']
                for d in people:
                    box = np.asarray(d['bbox_normalized']) * [640, 480, 640, 480]
                    x1, y1, x2, y2 = np.rint(box).astype(int)
                    cv2.rectangle(canvas, (x1, y1), (x2, y2), (240, 190, 60), 1)
                    cv2.putText(canvas, f"person {d['confidence']:.2f}", (x1, max(12, y1 - 3)),
                                cv2.FONT_HERSHEY_SIMPLEX, .35, (240, 220, 80), 1)
                def pct(key):
                    value = evidence.get(key)
                    return 'n/a' if value is None else f'{value * 100:.1f}%'
                person_min = evidence.get('semantic_person_minimum', settings['min_semantic_person_fraction'])
                ground_max = evidence.get('visible_ground_maximum', settings['max_visible_ground_fraction'])
                lines = [f'SOURCE {at:.1f}s',
                         'SUPPORT: ' + ('LARGE PERSON GROUP' if evidence.get('large_group_geometry') else 'PACKED CONTEXT'),
                         f"State: {crowd['state']}",
                         f"People: {evidence.get('person_count', 0)}",
                         f"Usable: {evidence.get('usable_person_count', 0)}",
                         f"Cluster: {evidence.get('cluster_count', 0)} / min {settings['min_people']}",
                         f"Box area: {pct('occupancy')} / min {settings['min_occupancy']:.0%}",
                         f"Width: {pct('horizontal_coverage')} / min {settings['min_horizontal_coverage']:.0%}",
                         f"Person height: {pct('median_box_height')} / min {settings['min_median_height']:.0%}",
                         f"Semantic people: {pct('semantic_person_fraction')}",
                         f"Semantic people required: >={person_min:.0%}",
                         f"Visible ground: {pct('visible_ground_fraction')}",
                         f"Visible ground required: <={ground_max:.0%}",
                         'Dense candidate: ' + ('YES' if evidence.get('candidate') else 'NO'),
                         f"Crowd events so far: {sum(e['first_seen_decision_source_s'] <= at for e in crowd_events)}",
                         'AUDIO: TIMED SIMULATION ONLY']
                for line_no, text in enumerate(lines):
                    cv2.putText(canvas, text, (650, 25 + line_no * 28), cv2.FONT_HERSHEY_SIMPLEX,
                                .43, (100, 235, 120) if 'YES' in text else (235, 235, 235), 1)
                person_frame = status.get('tasks', {}).get('persons', {}).get('frame_id')
                evidence_time = (person_frame - 1) / clip['input_fps'] if person_frame else None
                foot = 'Person boxes from latest detector result; not inferred on every source frame.'
                cv2.putText(canvas, foot, (8, 502), cv2.FONT_HERSHEY_SIMPLEX, .47, (230, 230, 230), 1)
                cv2.putText(canvas, f'Boxes source time: {evidence_time:.2f}s' if evidence_time is not None else 'Waiting for model result',
                            (8, 525), cv2.FONT_HERSHEY_SIMPLEX, .47, (230, 230, 230), 1)
                writer.write(canvas)
        finally:
            cap.release()
            writer.release()
        diagnosis = '; '.join(f'{REASONS.get(k, k)}：{v} 帧' for k, v in reasons.most_common())
        observer_rows = [{'t': r['source_s'], 'state': STATES.get(r['state'], r['state']),
                          'candidate': r['evidence']['candidate'], 'people': r['evidence']['person_count'],
                          'cluster': r['evidence']['cluster_count'],
                          'reasons': '；'.join(REASONS.get(x, x) for x in r['evidence']['reasons'])}
                         for r in rows]
        observations_file = clip['id'] + '-observations.json'
        (out / observations_file).write_text(json.dumps(observer_rows, ensure_ascii=False), encoding='utf8')
        shutil.copy2(root / 'runs' / (clip['id'] + '-report.json'), out / (clip['id'] + '-report.json'))
        expected = '应出现密集人群提醒（片段级预期）' if clip['expected_crowd'] is True else (
                   '应保持安静' if clip['expected_crowd'] is False else '边界样本，不计正确率')
        outcome = '已生成提醒' if crowd_events else '漏提醒' if clip['expected_crowd'] is True else '未提醒'
        other = '；'.join(f"{e['source_s']:.2f}s {e['label']}" for e in summary['other_events']) or '无'
        author = html.unescape(re.sub('<[^>]+>', '', clip.get('author_html', '')))
        voice_preview = ''
        if completed:
            shutil.copy2(repo / 'audio/en/crowd_ahead.wav', out / 'crowd_ahead.wav')
            voice_preview = ('<p>提示音样本（独立试听，视频没有合成音轨）：</p>'
                             '<audio controls preload="none" src="crowd_ahead.wav"></audio>')
        # A representative result is visible before playback, rather than only
        # showing the empty model-loading frame as the poster.
        representative = max(rows, key=lambda row: row['evidence'].get('cluster_count', 0)) if rows else None
        preview = cv2.VideoCapture(str(target))
        poster_s = (crowd_events[0]['first_seen_decision_source_s'] + .15 if crowd_events else
                    representative['source_s'] if representative else 3)
        preview.set(cv2.CAP_PROP_POS_MSEC, poster_s * 1000)
        ok, poster = preview.read()
        preview.release()
        assert ok and cv2.imwrite(str(out / (clip['id'] + '-poster.jpg')), poster)
        cards.append(f'''<article data-observations="{observations_file}"><h2>{esc(clip['label_zh'])} · {outcome}</h2>
<p>{expected}；{clip['duration_s']:.2f} 秒。使用本轮固定配置，未在视频途中改参。</p>
<video controls preload="metadata" poster="{clip['id']}-poster.jpg" src="{target.name}"></video>
<div class="live">播放视频即可查看当前时间的人群判定</div>
<p class="counts">人群候选 {summary['dense_candidate_observations']}/{len(rows)} 个独立观察帧 · 提醒事件 {len(crowd_events)} · 模拟完整播报 {len(completed)}</p>
{voice_preview}
<p>主要拦截原因：{esc(diagnosis or '无')}</p>
<details><summary>本视频的其他路况事件（不能算成人群成功）</summary>{esc(other)}<br>本轮没有逐帧路况真值，不将这些事件当作正确路况识别。</details>
<details><summary>全部独立人物观察时间线</summary><div class="timeline"></div></details>
<p><a href="{esc(clip['source'])}" target="_blank" rel="noopener">原视频来源</a> · {esc(author)} · {esc(clip['license'])}<br>
<a href="{clip['id']}-report.json">完整运行记录</a> · <a href="{observations_file}">人物观察记录</a></p></article>''')
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>人群视频测试与诊断</title><style>
body{font:16px/1.6 system-ui,sans-serif;background:#f2f5fa;color:#182c40;max-width:1400px;margin:auto;padding:24px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(500px,1fr));gap:24px}article,.notice{background:white;border:1px solid #cad5e4;border-radius:12px;padding:20px}
h1,h2{line-height:1.3}h2{font-size:23px}video{width:100%;background:#111;border-radius:6px}.live{background:#e9f0f8;padding:12px;min-height:70px}.counts{font-weight:700}.timeline{max-height:280px;overflow:auto}button{margin:3px;padding:6px;cursor:pointer}a{color:#125fa2}p{overflow-wrap:anywhere}summary{cursor:pointer} @media(max-width:600px){main{display:block}article{margin-top:18px}body{padding:10px}}
</style><h1>人群视频：人物框 → 人群判定 → 提醒</h1>
<div class="notice">真实连续视频，使用完整路况＋人群程序与本轮固定配置。蓝色框只代表检测到一个人；右侧 Dense candidate 才表示达到密集外观条件，连续确认后才产生 CROWD 提醒。页面明确列出漏提醒，不能把检测框当作播报成功。没有在树莓派上运行。</div>
<p><b>__OUTCOME__</b></p>
<p>__COVERAGE__。预期在推理前按原视频粗略人工检查冻结，没有逐帧密度或路线阻挡真值。人物框来自最近一次真实推理结果，画面中的时间标签显示其源帧时间，可能落后于当前帧。</p>
<p>视频为 10 FPS 诊断回看，保持完整原片段时间，无重复照片、人工生成检测结果或跟踪重置。原片声音未播放；“模拟播报”仅运行真实音频队列和 WAV 时长，不能证明耳机实际出声。</p><main>'''
    page += ''.join(cards) + '''</main><script>
for (const article of document.querySelectorAll('article')) {
  const video=article.querySelector('video'), live=article.querySelector('.live');
  fetch(article.dataset.observations).then(r=>r.json()).then(rows=>{
    video.addEventListener('timeupdate',()=>{
      const row=rows.findLast(r=>r.t<=video.currentTime);
      live.textContent=row ? `${video.currentTime.toFixed(1)}s：${row.state}；检测 ${row.people} 人，合格聚集 ${row.cluster} 人。${row.reasons || '外观条件已满足，仍需连续确认和队列处理。'}` : '等待首个独立人物结果';
    });
    const timeline=article.querySelector('.timeline');
    for (const row of rows) {
      const button=document.createElement('button');
      button.textContent=`${row.t.toFixed(1)}s ${row.candidate?'密集候选':'未达条件'} (${row.people}人)`;
      button.title=row.reasons; button.onclick=()=>{video.currentTime=row.t;video.pause();};timeline.append(button);
    }
  }).catch(()=>{live.textContent='人物观察记录加载失败，请查看完整运行记录。';});
}
</script></html>'''
    positives = [s for s in summaries if s['expected_crowd'] is True]
    hits = sum(s['crowd_events'] > 0 for s in positives)
    false_alerts = sum(s['crowd_events'] > 0 for s in summaries if s['expected_crowd'] is False)
    negatives = sum(s['expected_crowd'] is False for s in summaries)
    ambiguous = len(summaries)-len(positives)-negatives
    page = page.replace('__COVERAGE__', f'本页 {len(positives)} 段人群正例、{negatives} 段负例、{ambiguous} 段边界样本')
    page = page.replace('__OUTCOME__', f'本轮 {len(positives)} 段人工预期正例中 {hits} 段产生人群提醒；负例误提醒 {false_alerts} 段。漏提醒和误提醒按实际结果保留。其他路况事件单独列出，不算人群成功。这是开发验证，不是独立准确率评估。')
    (out / 'index.html').write_text(page, encoding='utf8')
    before = read('runtime-before.json')
    unchanged = all(hashlib.sha256((repo / name).read_bytes()).hexdigest() == digest
                    for name, digest in before.items())
    assert unchanged
    (root / 'summary.json').write_text(json.dumps({'cases': summaries, 'runtime_source_unchanged': unchanged,
        'fixed_configuration_during_run': True,
        'thresholds_changed_from_previous_round': catalog.get('thresholds_changed_from_previous_round', False),
        'physical_audio_tested': False}, ensure_ascii=False, indent=2), encoding='utf8')
    print(out / 'index.html')


if __name__ == '__main__':
    main()
