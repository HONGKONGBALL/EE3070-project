"""Build a local, attributed review of recorded awareness experiments.

Consumes measured JSON results; does not run models or manufacture alerts.
"""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import re
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    root = args.results
    out = root / 'review'
    out.mkdir(exist_ok=True)
    esc = lambda value: html.escape(str(value), quote=True)
    read = lambda name: json.loads((root / name).read_text(encoding='utf8'))

    def copy(source, destination):
        target = out / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return destination

    def attribution(item):
        author = re.sub('<[^>]+>', '', item.get('author_html', item.get('artist_html', '')))
        url = item.get('source_page', item.get('source', ''))
        return (f'<a href="{esc(url)}" target="_blank" rel="noopener">原始来源</a> · '
                f'{esc(html.unescape(author))} · {esc(item.get("license", "见来源"))}')

    videos = read('full-videos-final/all-results.json')
    catalog = {c['id']: c for c in read('full-video-catalog.json')['clips']}
    cards = []
    video_rows = []
    for result in videos['clips']:
        item = catalog[result['id']]
        video = copy(root / 'full-videos-final' / result['annotated_video'],
                     'videos/' + result['annotated_video'])
        record = copy(root / 'full-videos-final' / (result['id'] + '-report.json'),
                      'records/' + result['id'] + '.json')
        starts = Counter(r['phrase_id'] for r in result['audio_records']
                         if r['record_type'] == 'audio_simulated_start')
        done = Counter(r['phrase_id'] for r in result['audio_records']
                       if r['record_type'] == 'audio_simulated_done')
        events = '; '.join(f"{e['first_seen_decision_source_s']:.2f}s {e['object_class']}"
                           for e in result['events']) or '无'
        note = item.get('label_notes', result.get('source_notes', ''))
        if result['id'] == 'video-201112989':
            note += ' 同时出现楼梯事件；这段游行视频的楼梯真值未确认，不能当作正确楼梯识别。'
        if result['id'] in ('forest-stairs', 'rain-walk'):
            note += (' 这是连续行走视频。窗口统计按证据帧时间计数：提醒可能在窗口前已发出；'
                     '同一事件持续存在时不为每个时间窗口重新播报，不把每个窗口都算识别成功。')
        cards.append(f'''<article><h3>{esc(result['id'])}</h3>
<video controls preload="metadata" muted src="{video}"></video>
<p>{esc(note)}</p><p><b>提醒事件：</b>{esc(events)}</p>
<p>模拟开始：{esc(dict(starts))}<br>模拟完整播完：{esc(dict(done))}</p>
<p>{attribution(item)} · <a href="{record}">完整记录（含窗口漏提醒）</a></p></article>''')
        video_rows.append({'id': result['id'], 'events': events, 'simulated_started': dict(starts),
                           'simulated_completed': dict(done), 'task_errors': result['task_errors']})

    stills = read('media-final/results.json')
    photos = []
    appearance = Counter()
    for item in stills['cases']:
        row = item['rows'][0]
        evidence = row['evidence']
        expected = item.get('expected_crowd')
        actual = evidence['candidate']
        key = ('positive_hit' if actual else 'positive_miss') if expected is True else (
              'negative_false_positive' if actual else 'negative_quiet') if expected is False else 'ambiguous'
        appearance[key] += 1
        picture = copy(root / 'media-final' / row['image'], 'images/' + item['id'] + '.jpg')
        photos.append(f'''<article><h3>{esc(item['id'])} · {esc(key)}</h3>
<img loading="lazy" src="{picture}" alt="真实图片推理结果">
<p>预期密集人群：{esc(expected)}；实际候选：{esc(actual)}；人框：{evidence['person_count']}</p>
<p>原因：{esc(', '.join(evidence['reasons']) or '满足外观条件')}</p>
<details><summary>原始证据与场景说明</summary><pre>{esc(json.dumps(evidence, ensure_ascii=False, indent=2))}</pre>{esc(item.get('label_notes', ''))}</details>
<p>{attribution(item)}</p></article>''')

    queue = read('queue-review/results.json')
    audio = []
    for case in queue['cases']:
        path = copy(root / 'queue-review' / case['review_audio'], 'audio/' + case['review_audio'])
        audio.append(f'''<article><h3>{esc(case['name'])}</h3><audio controls preload="metadata" src="{path}"></audio>
<p>开始（含可能中断）：{esc(case['spoken'])}<br>完整播完：{esc(case['completed'])}</p></article>''')
    manifest = read('voice-manifest.json')
    phrases = {
        'puddle_ahead': "Looks like there's a puddle ahead. Take care!",
        'damaged_pavement': 'The pavement ahead looks damaged. Watch your step!',
        'stairs_ahead': 'Careful, there might be stairs ahead.',
        'crowd_ahead': 'Looks a bit crowded ahead. Take care!',
    }
    single = []
    for phrase, text in phrases.items():
        path = copy(root / 'audio/en' / (phrase + '.wav'), 'audio/' + phrase + '.wav')
        single.append(f'<article><p>{esc(text)}</p><audio controls src="{path}"></audio></article>')
    pressure = read('mixed-pressure-final.json')
    copy(root / 'mixed-pressure-final.json', 'records/mixed-pressure.json')
    copy(root / 'ground-full-comparison.json', 'records/ground-comparison.json')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Smart Cane — 人群与路况共存测试</title><style>
body{font:16px/1.65 system-ui,sans-serif;color:#192c3c;background:#f3f6fa;max-width:1400px;margin:auto;padding:24px}
h1,h2,h3{line-height:1.3}h2{margin-top:44px}nav{display:flex;gap:20px;flex-wrap:wrap}a{color:#155b9b}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}article,.notice{background:white;border:1px solid #ced8e4;border-radius:12px;padding:18px;overflow-wrap:anywhere}
video,img{width:100%;background:#17202a;border-radius:6px}video{aspect-ratio:4/3}audio{width:100%}pre{white-space:pre-wrap;font-size:13px}summary{cursor:pointer} .notice{border-left:5px solid #297a65}
</style><h1>人群与路况：本地测试结果</h1>
<div class="notice">本地代码和模型回放；没有更新树莓派。6 条真实连续视频先测试，再测试多功能、断流恢复与 4 个并发浏览客户端。视频框、提醒事件与音频队列分开记录。音频是用真实 WAV 时长运行队列的模拟，没有在 Pi 耳机上测试。</div>
<nav><a href="#videos">真实视频</a><a href="#stills">真实图片与漏检</a><a href="#queue">共存播报试听</a><a href="#stress">压力测试</a></nav>
<p>不是人数达到阈值就提醒：同时检查人物聚集、画面占比、人物大小、已有语义模型中的人物与地面分布，以及连续帧。它不能判断身体朝向、实际每平方米人数、距离或道路是否可通行。</p>
<h2 id="videos">6 条真实连续视频</h2><p>视频按实际运行预览重采样回看，不是每个原始帧都做推理；下方记录给出生成事件与模拟播完次数。只有两段森林/雨天样本是连续行走视频，其余为真实场景动态记录。</p><div class="grid">'''
    page += ''.join(cards) + '</div><h2 id="stills">12 张真实图片：含已知漏检</h2>'
    page += f'<p>{esc(dict(appearance))}。这些是开发验证样本，部分用于调整阈值，不能当独立准确率测试集。单张照片只验外观候选，不伪造连续帧或播报。俯拍拥挤楼梯仍漏检。</p><div class="grid">'
    page += ''.join(photos) + '</div><h2>4 条新提示音</h2><div class="grid">' + ''.join(single) + '</div>'
    page += '<h2 id="queue">共存播报：明确构造的程序测试</h2><p>以下不是视频识别结果：用人工观察数据测试真实提醒管理器和音频队列。持续路况与人群均能完整播报；CRITICAL 可以中断人群句子，条件仍成立才重试。一次中断开始不等于一次完整播报。</p><div class="grid">'
    page += ''.join(audio) + '</div><h2 id="stress">综合压力测试</h2>'
    page += f'<p>最终 {pressure["elapsed_s"]:.2f} 秒，4 个并发客户端，全部工程检查通过：{esc(pressure["engineering_passed"])}。短断流恢复不重复提醒；可靠清空后重入能重新提醒。首次测试暴露了旧结果阻碍恢复的问题，已修复并保留失败记录。</p>'
    page += f'<pre>{esc(json.dumps(pressure["http_metrics"], indent=2))}</pre>'
    page += '<p>125 张原路况图片的 500 份原始模型输出和筛选结果无差异，75 个事件数量一致；这证明前后回归一致，不证明这些模型已具备实际出行准确率。</p><p><a href="records/mixed-pressure.json">压力测试原始记录</a> · <a href="records/ground-comparison.json">路况前后对比</a></p>'
    copy(root / 'benchmarks/final-comparison.json', 'records/final-performance.json')
    page += ('<p><b>性能保留项：</b>最终配对预览 11.2 → 11.0 FPS，路况推理仍约 1.48 Hz。'
             '包含队列重试的事件延迟 P95 为 365.2 → 527.25 ms，超出预设检查线；'
             '首次提醒 P95 为 365.2 → 362.6 ms。多出的一次积水队列重试单独保留，不能称全部性能通过。'
             '<a href="records/final-performance.json">原始比较与诊断</a></p>')
    page += '<p>边界：仅短时 Windows 结果；Pi 帧率、热稳定性、拐杖移动视角与 AirPods 实际播放需要后续实机验收。远处小人、俯拍和遮挡仍可漏检；模型已有路况误报没有被当作本轮成功。</p></html>'
    (out / 'index.html').write_text(page, encoding='utf8')
    (root / 'review-summary.json').write_text(json.dumps({'videos': video_rows, 'appearance_counts': dict(appearance),
        'pressure_passed': pressure['engineering_passed'], 'queue_passed': queue['passed'],
        'physical_audio_tested': False}, ensure_ascii=False, indent=2), encoding='utf8')
    print(out / 'index.html')


if __name__ == '__main__':
    main()
