#!/usr/bin/env python3
"""A local page for reading sample sentences aloud and seeing what the cane would make of them.

Each recording goes through the same code the cane runs: Silero VAD cuts out the utterance,
SenseVoice transcribes it, and spoken_wake looks for "嘿，阿帕次" at its start. The page shows the
transcript and the verdict at once. The recordings and the results stay on this computer, in
voice-samples/, and are used to tune the wake phrase matching to the speaker.

    python tools/cane_voice_samples.py --models DIR [--port 8765]

DIR holds model.int8.onnx, tokens.txt and silero_vad.onnx. Needs sherpa-onnx and numpy.
Open http://localhost:8765 in a browser that may use the microphone.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
RATE = 16000

# (id, what to read, should it wake the cane, how many takes are useful)
SAMPLES = [
    ("wake", "嘿，阿帕次", True, 5),
    ("metro", "嘿，阿帕次，我想去最近的地铁站", True, 2),
    ("front", "嘿，阿帕次，前面有什么", True, 2),
    ("mcd", "嘿，阿帕次，带我去麦当劳", True, 1),
    ("first", "嘿，阿帕次，第一个", True, 2),
    ("walk", "嘿，阿帕次，前面可以走吗", True, 1),
    ("cancel", "嘿，阿帕次，取消导航", True, 1),
    ("plain-metro", "我想去最近的地铁站", False, 1),
    ("plain-intro", "各位老师同学大家好，今天我们介绍的是智能盲杖项目", False, 1),
    ("plain-front", "前面有什么东西，可以走吗", False, 1),
]

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>语音样本采集</title>
<style>
  :root { --bg:#f6f7f8; --card:#fff; --ink:#15202b; --dim:#5d6b78; --line:#dfe4e8; --accent:#19546c;
          --good:#1a7f4b; --bad:#b3261e; --rec:#d93025; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f1519; --card:#182127; --ink:#e8eef2; --dim:#95a4b1; --line:#2a363f; --accent:#6fc3df;
            --good:#5ecb8f; --bad:#ff8a80; --rec:#ff6b5e; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 -apple-system, "PingFang SC", "Helvetica Neue", sans-serif; }
  main { max-width: 760px; margin: 0 auto; padding: 28px 16px 80px; }
  h1 { font-size: 24px; margin: 0 0 6px; letter-spacing: .01em; }
  p.lead { color: var(--dim); margin: 0 0 18px; }
  .tally { display:flex; gap:10px; flex-wrap:wrap; margin: 0 0 22px; }
  .tally div { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; min-width: 150px; }
  .tally b { display:block; font-size: 22px; font-variant-numeric: tabular-nums; }
  .tally span { color: var(--dim); font-size: 13px; }
  h2 { font-size: 14px; color: var(--dim); font-weight: 600; margin: 26px 0 8px; }
  .row { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:10px; }
  .top { display:flex; gap:12px; align-items:center; }
  .say { flex:1; font-size: 19px; font-weight: 600; }
  .count { color: var(--dim); font-size: 13px; white-space: nowrap; font-variant-numeric: tabular-nums; }
  button { font: inherit; border: 0; border-radius: 999px; padding: 10px 18px; min-width: 96px; cursor: pointer;
           background: var(--accent); color: var(--bg); font-weight: 600; }
  button.on { background: var(--rec); color: #fff; }
  button:disabled { opacity: .45; cursor: default; }
  button:focus-visible, input:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
  .takes { margin: 10px 0 0; padding: 0; list-style: none; }
  .takes li { border-top: 1px solid var(--line); padding: 8px 0 0; margin-top: 8px; font-size: 15px; }
  .ok { color: var(--good); font-weight: 600; } .no { color: var(--bad); font-weight: 600; }
  .heard { color: var(--dim); }
  .meter { height: 4px; background: var(--line); border-radius: 2px; margin-top: 10px; overflow: hidden; display:none; }
  .meter i { display:block; height:100%; width:0; background: var(--rec); transition: width .08s linear; }
  .row.live .meter { display:block; }
  input[type=text] { flex:1; font: inherit; font-size: 17px; padding: 9px 12px; border-radius: 10px; border:1px solid var(--line);
                     background: var(--bg); color: var(--ink); min-width: 0; }
  #problem { display:none; background:var(--card); border:1px solid var(--bad); color:var(--bad); border-radius:12px; padding:12px 14px; margin-bottom:18px; }
  footer { color: var(--dim); font-size: 13px; margin-top: 26px; }
</style>
</head>
<body>
<main>
  <h1>嘿，阿帕次 · 语音样本</h1>
  <p class="lead">点“录音”，照着读，读完再点一次（或停顿一秒自动结束）。每条读完会显示手杖把它听成了什么、会不会被唤醒。平时怎么说就怎么读，不用刻意放慢。</p>
  <div id="problem"></div>
  <div class="tally">
    <div><b id="hit">0 / 0</b><span>该唤醒的，唤醒了</span></div>
    <div><b id="false">0 / 0</b><span>不该唤醒的，误唤醒</span></div>
  </div>
  <h2>只说唤醒词</h2><div id="g-wake"></div>
  <h2>唤醒词 + 要求，一口气说完</h2><div id="g-request"></div>
  <h2>平常说话（不应该唤醒）</h2><div id="g-plain"></div>
  <h2>想到什么说什么</h2>
  <div class="row" id="free">
    <div class="top">
      <input type="text" id="free-text" aria-label="准备说的话" placeholder="先写下准备说的话（可留空），再录音">
      <button type="button" data-id="free">录音</button>
    </div>
    <div class="meter"><i></i></div><ul class="takes"></ul>
  </div>
  <footer>录音只保存在这台电脑的 voice-samples 文件夹里，不会上传。</footer>
</main>
<script>
const SAMPLES = __SAMPLES__;
const tally = {hit:[0,0], miss:[0,0]};
let active = null;

function row(sample) {
  const el = document.createElement('div');
  el.className = 'row'; el.id = 'row-' + sample.id;
  el.innerHTML = `<div class="top"><div class="say"></div><span class="count">0 / ${sample.takes}</span>
    <button type="button" data-id="${sample.id}">录音</button></div><div class="meter"><i></i></div><ul class="takes"></ul>`;
  el.querySelector('.say').textContent = sample.text;
  return el;
}
for (const s of SAMPLES) {
  const group = !s.wake ? 'g-plain' : (s.id === 'wake' ? 'g-wake' : 'g-request');
  document.getElementById(group).appendChild(row(s));
}
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-id]');
  if (b) toggle(b);
});

function problem(text) {
  const box = document.getElementById('problem');
  box.textContent = text; box.style.display = 'block';
}

async function toggle(button) {
  if (active) { if (active.button === button) active.stop(); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    problem('这个窗口不能使用麦克风。请在 Safari 或 Chrome 里打开 ' + location.href); return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: true}});
  } catch (err) {
    problem('没有拿到麦克风权限（' + err.name + '）。请允许使用麦克风，或在 Safari / Chrome 里打开 ' + location.href); return;
  }
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  const source = ctx.createMediaStreamSource(stream);
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = []; let spoke = 0, quiet = 0; const started = performance.now();
  const rowEl = button.closest('.row'); const bar = rowEl.querySelector('.meter i');
  rowEl.classList.add('live'); button.classList.add('on'); button.textContent = '停止';
  const stop = () => {
    if (!active) return; active = null;
    node.disconnect(); source.disconnect(); stream.getTracks().forEach(t => t.stop()); ctx.close();
    rowEl.classList.remove('live'); button.classList.remove('on'); button.textContent = '识别中'; button.disabled = true;
    send(button, rowEl, chunks, ctx.sampleRate);
  };
  node.onaudioprocess = e => {
    const data = new Float32Array(e.inputBuffer.getChannelData(0)); chunks.push(data);
    let sum = 0; for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
    const level = Math.sqrt(sum / data.length);
    bar.style.width = Math.min(100, level * 600) + '%';
    const seconds = data.length / ctx.sampleRate;
    if (level > 0.02) { spoke += seconds; quiet = 0; } else if (spoke > 0.3) { quiet += seconds; }
    if (quiet > 1.2 || performance.now() - started > 12000) stop();
  };
  source.connect(node); node.connect(ctx.destination);
  active = {button, stop};
}

function wav(chunks, rate) {
  let length = 0; for (const c of chunks) length += c.length;
  const all = new Float32Array(length); let at = 0; for (const c of chunks) { all.set(c, at); at += c.length; }
  const count = Math.round(all.length * 16000 / rate);
  const out = new DataView(new ArrayBuffer(44 + count * 2));
  const text = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
  text(0, 'RIFF'); out.setUint32(4, 36 + count * 2, true); text(8, 'WAVEfmt '); out.setUint32(16, 16, true);
  out.setUint16(20, 1, true); out.setUint16(22, 1, true); out.setUint32(24, 16000, true); out.setUint32(28, 32000, true);
  out.setUint16(32, 2, true); out.setUint16(34, 16, true); text(36, 'data'); out.setUint32(40, count * 2, true);
  for (let i = 0; i < count; i++) {
    const p = i * rate / 16000, a = Math.floor(p), b = Math.min(a + 1, all.length - 1);
    const v = Math.max(-1, Math.min(1, all[a] + (all[b] - all[a]) * (p - a)));
    out.setInt16(44 + i * 2, v * 32767, true);
  }
  return out.buffer;
}

async function send(button, rowEl, chunks, rate) {
  const id = button.dataset.id;
  const sample = SAMPLES.find(s => s.id === id);
  const said = sample ? sample.text : document.getElementById('free-text').value.trim();
  const item = document.createElement('li');
  try {
    const response = await fetch('/api/clip?id=' + encodeURIComponent(id) + '&text=' + encodeURIComponent(said),
                                 {method: 'POST', headers: {'Content-Type': 'audio/wav'}, body: wav(chunks, rate)});
    const r = await response.json();
    if (!response.ok) throw new Error(r.error || response.status);
    const verdict = document.createElement('span');
    const expected = sample ? sample.wake : null;
    verdict.className = (expected === null || r.wake === expected) ? 'ok' : 'no';
    verdict.textContent = r.wake ? ('唤醒 ✓' + (r.request ? '　要求：「' + r.request + '」' : '　（只有唤醒词）')) : '没有唤醒';
    const heard = document.createElement('div'); heard.className = 'heard';
    heard.textContent = '听成：「' + (r.heard || '（没听到说话）') + '」';
    item.append(verdict, heard);
    if (expected === true) { tally.hit[1]++; if (r.wake) tally.hit[0]++; }
    if (expected === false) { tally.miss[1]++; if (r.wake) tally.miss[0]++; }
    document.getElementById('hit').textContent = tally.hit.join(' / ');
    document.getElementById('false').textContent = tally.miss.join(' / ');
    const count = rowEl.querySelector('.count');
    if (count) count.textContent = (rowEl.querySelectorAll('.takes li').length + 1) + ' / ' + sample.takes;
  } catch (err) {
    item.className = 'no'; item.textContent = '没有识别成功：' + err.message;
  }
  rowEl.querySelector('.takes').prepend(item);
  button.disabled = false; button.textContent = '录音';
}
</script>
</body>
</html>
"""


class Listener:
    """The cane's own wake-up path, fed one recording at a time."""

    def __init__(self, repo, models):
        sys.path.insert(0, str(Path(repo).resolve() / "pi"))
        from cane.vision.sherpa_speech import SenseVoiceRecognizer, SpokenWake
        from cane.vision.wake_word import spoken_wake

        models = Path(models).resolve()
        self.recognizer = SenseVoiceRecognizer(models, "zh", 2)
        self.recognizer.recognizer
        self.wake = SpokenWake(models / "silero_vad.onnx", self.recognizer, spoken_wake)
        self.lock = threading.Lock()

    def judge(self, pcm):
        with self.lock:
            # As on the cane: microphone blocks of 0.1 s, then enough quiet for the VAD to close the utterance.
            stream = bytes(RATE // 2) + pcm + bytes(RATE * 2 * 2)
            self.wake.reset()
            request = None
            for offset in range(0, len(stream), 3200):
                heard = self.wake.feed(stream[offset:offset + 3200])
                if heard is not None and request is None:
                    request = heard
            # heard: the whole recording. head: what the wake-up check itself transcribed (the VAD's cut).
            return {"heard": self.recognizer.transcribe(pcm), "head": self.wake.heard,
                    "wake": request is not None, "request": request or ""}


def serve(listener, directory, port):
    directory.mkdir(parents=True, exist_ok=True)
    samples = [{"id": name, "text": text, "wake": wake, "takes": takes} for name, text, wake, takes in SAMPLES]
    page = PAGE.replace("__SAMPLES__", json.dumps(samples, ensure_ascii=False)).encode("utf-8")
    expected = {name: (text, wake) for name, text, wake, _ in SAMPLES}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body, kind="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if urlsplit(self.path).path == "/":
                self.reply(200, page, "text/html")
            else:
                self.reply(404, b"{}")

        def do_POST(self):
            url = urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if url.path != "/api/clip" or not 44 < length <= 2_000_000:
                self.reply(400, json.dumps({"error": "bad request"}).encode())
                return
            query = parse_qs(url.query)
            name = re.sub(r"[^a-z0-9-]", "", query.get("id", ["free"])[0])[:24] or "free"
            said = query.get("text", [""])[0][:80]
            body = self.rfile.read(length)
            path = directory / f"{time.strftime('%H%M%S')}-{name}-{int(time.time() * 1000) % 1000:03d}.wav"
            try:
                path.write_bytes(body)
                with wave.open(str(path)) as stream:
                    if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (RATE, 1, 2):
                        raise ValueError("expected 16 kHz mono 16-bit audio")
                    pcm = stream.readframes(stream.getnframes())
                result = listener.judge(pcm)
            except Exception as error:  # the page shows the reason
                self.reply(500, json.dumps({"error": str(error)}, ensure_ascii=False).encode())
                return
            record = dict(result, file=path.name, id=name, said=said,
                          should_wake=expected[name][1] if name in expected else None,
                          seconds=round(len(pcm) / 2 / RATE, 2))
            with (directory / "results.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(json.dumps(record, ensure_ascii=False), flush=True)
            self.reply(200, json.dumps(result, ensure_ascii=False).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"voice samples: http://localhost:{port}  (recordings in {directory})", flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--models", required=True, help="folder with model.int8.onnx, tokens.txt and silero_vad.onnx")
    parser.add_argument("--repo", default=str(HERE.parent), help="checkout of the cane code")
    parser.add_argument("--out", type=Path, default=HERE.parent / "voice-samples")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(Listener(args.repo, args.models), args.out, args.port)


if __name__ == "__main__":
    main()
