# Smart Cane V1 voice alerts — final local policy

This policy applies to the existing `--mode awareness` runtime. It supersedes the previous crowd/ground equal-audio-priority policy. No Pi deployment or Git push was performed. The existing default mode remains ground; ground/balanced modes retain their prior voice behavior.

## Changes and exact scope

Only three production source files changed:

| File | Change |
|---|---|
| `pi/cane/vision/speech.py` | Optional allowlist before the existing arbiter; awareness permits exactly four phrase IDs. Manual crowd voice tests also use the existing lowest priority. Rejected diagnostic speech is recorded as `audio_filtered`. Other modes default to no allowlist. |
| `pi/cane/vision/v2.py` | Supplies the awareness allowlist and checks the existing ground event/current/pending/safety-hold state before considering crowd speech. Ground detection and render/admission logic are unchanged. |
| `pi/cane/vision/crowd.py` | Low audio priority with original safety severity retained separately; a voice-only capture barrier prevents old crowd requests being replayed after ground speech. Detection, confirmation, clearing and deduplication stay intact. Adds deferred-voice diagnostics. |

Test/report changes: `tests/test_v1_voice.py`, `tests/test_crowd_awareness.py`, `tools/check_awareness_queue.py`, this report, and a current-policy link in `docs/pi-awareness-report.md`.

No configuration values, model files, image processing, detection/verifier rules, confidence or temporal-confirmation thresholds, Pico sensing/vibration, USB/Bluetooth output control, or existing arbiter implementation were changed. No monitoring feature, new voice phrase, visual model or AI agent was added.

## Final priority and phrases

| Voice level | Existing arbiter value | Warning |
|---|---|---|
| HIGH | CRITICAL | Stairs; strong pothole risk as already identified by the existing logic |
| NORMAL | SAFETY | Puddles; other damaged pavement |
| LOW | INFO | Crowd awareness |

Crowd's raw warning retains `severity = SAFETY`; its **audio** priority is `INFO`, the existing lowest level. Ground severity/risk calculations are unchanged. All confirmed ground alerts can take precedence over crowd using the existing interruption mechanism. Ground recognition workers continue independently of audio.

| Category | Exact approved English phrase |
|---|---|
| STAIRS | Careful, there might be stairs ahead. |
| DAMAGED_PAVEMENT | The pavement ahead looks damaged. Watch your step! |
| PUDDLE | Looks like there's a puddle ahead. Take care! |
| CROWD | Looks a bit crowded ahead. Take care! |

Potholes and cracks retain their separate original labels, models, evidence and severity information, while sharing the DAMAGED_PAVEMENT voice. No WAV was regenerated or replaced.

## Ground plus crowd behavior

1. Ground events are considered first. If ground is newly confirmed, playing, pending, or in the arbiter's existing safety-hold interval, crowd speech is deferred.
2. No crowd speech request is generated from that deferred observation. Existing older crowd requests become invalid, even if later camera results are fresh; they cannot be mechanically backfilled.
3. After ground audio finishes, the existing two-second safety hold remains. Crowd can be reconsidered only using a capture newer than the voice barrier and still-valid existing crowd confirmation. There is no new detector or confirmation threshold.
4. If crowd disappears during ground playback, no crowd voice follows. If it persists later, a fresh request can speak; if an earlier crowd episode was already completed, its existing deduplication remains. Genuinely new episodes can still warn.
5. If crowd is already speaking when stairs or another confirmed ground warning arrives, the existing arbiter can interrupt it. A later retry requires fresh evidence and starts the short approved phrase again; it does not resume an old audio fragment.

The V1 allowlist filters camera failure, low-light/blur/status phrases, hardware/module/battery/startup/system narration and individual objects before they can reach playback. Existing diagnosis, fault handling, freshness invalidation, status UI and logs remain. Excluded WAVs remain on disk for other modes. The manual output-test button may still play the approved crowd sample at LOW priority; it is an explicit audio test, not a detector warning.

## Verification

Evidence root: `D:/SmartCaneRuntime/v1-voice-final-20261102/`.

- **368 automatic tests passed; 0 failed.** One pre-existing Starlette/httpx deprecation warning remains. The new V1 suite exercises the actual V2 decision coordinator, real warning managers and existing arbiter with explicit QA observations and controlled playback.
- Verified stairs/pothole/crack/puddle/crowd alone; simultaneous ground-first cases with no immediate crowd; fresh later reassessment; disappearance without backfill; ground interrupting crowd; continuous-episode deduplication; expired requests; genuinely new crowd episodes; diagnostic/object rejection; and awareness-only filtering with ground/balanced compatibility.
- **13 timed-WAV scenarios passed.** These use the actual approved WAV durations, actual Speech/arbiter and V2 coordinator. They cover all five single-hazard cases, four simultaneous combinations, all families, stale crowd, stairs interrupting crowd, and the no-crowd reference. Critical-ground start times have zero added delay relative to the no-crowd fixture in these deterministic cases. Partial interrupted starts are distinguished from completed announcements.
- **125-image before/after real-model regression:** all 500 model outputs and all admitted detections match; all 75 ground event contents match. Raw reports retain one equal-priority ground-event pair in a different list order between separately hash-randomized Python processes. The ground ranking/enumeration source is unchanged; comparison by event identity proves matching content rather than silently treating array order as identical. This is regression evidence, not a claim that all 125 scenes were correctly classified.
- **Integrity:** 114 model-file hashes and 16 audio-file hashes match the pre-edit snapshot. Configuration is byte-identical. AST comparisons prove crowd geometry/observation/confirmation/reset/invalidation and runtime model loops, freshness scheduling, preprocessing/admission/render methods unchanged. Pico/core source hashes match; the full suite includes Pico link/protocol and arbiter tests. No physical Pico retest is claimed.
- **Real-model runtime fault test:** 20.72 seconds with black frames, an injected camera outage, recovery and two HTTP clients. All ten engineering checks pass. `camera_error` remains in logs, while `camera_unavailable` is recorded as `audio_filtered`, not dispatched to the sink. Only approved stairs/crowd phrases were dispatched in that fixture, with audio disabled. This verifies routing, not physical hearing.

Relevant commands, from the repository root with the existing Python environment and weights:

```powershell
& 'D:\SmartCaneRuntime\.venv\Scripts\python.exe' -X utf8 -m pytest -q
& 'D:\SmartCaneRuntime\.venv\Scripts\python.exe' -X utf8 tools/check_awareness_queue.py --audio-dir D:\SmartCaneRuntime\crowd-awareness-20261102\audio\en --out D:\SmartCaneRuntime\v1-voice-final-20261102\timed-audio
```

Local listening review: `http://127.0.0.1:8858/v1-voice/`. Audio files there render the simulated queue timeline using the actual WAV files; they are **not recordings from the Pi or headphones**. They start at the first phrase and preserve later quiet gaps and interruptions.

## Remaining limitations

- This is a voice-policy change. Existing visual misses/false positives remain; it does not improve detection accuracy or certify route safety. Elevated bird's-eye crowds remain outside required cane-mounted acceptance. Normal downward ground viewing remains in scope.
- A brief crowd may be skipped while ground is being handled or may disappear before fresh reassessment; that is intended. Continuous new ground risks may keep deferring crowd.
- The existing queue capacity, warning TTLs, episode clear/missing rules and ordinary-warning start spacing remain. A genuinely new episode can warn again after the existing clear/unobserved criteria; there is no permanent suppression or fixed eight-second crowd repeat loop.
- Windows tests and timed WAV simulation do not establish physical AirPods/USB latency or Pi thermal/FPS behavior. Pi deployment/listening remains a later acceptance step. This does not erase previously documented performance/recognition limitations.

## Rollback

`before-source.zip` preserves the entire pre-change worktree source, including prior uncommitted Bluetooth and crowd work. Its extracted copy is `D:/SmartCaneRuntime/v1-voice-final-20261102/before-source/`. Models, configuration and audio are unchanged by this task.

To restore only this task's production behavior after stopping the local process, copy the three original runtime files back. This does not delete or reset other work:

```powershell
$v1ProjectRoot = 'C:\Users\szecy\.codex\.chatgpt-projects\g-p-6aa7bfe0096c8191a44a81a6c922f9fd\repos\EE3070-project'
$v1BackupRoot = 'D:\SmartCaneRuntime\v1-voice-final-20261102\before-source'
foreach ($v1Name in @('speech.py','v2.py','crowd.py')) {
    Copy-Item -LiteralPath (Join-Path $v1BackupRoot "pi\cane\vision\$v1Name") -Destination (Join-Path $v1ProjectRoot "pi\cane\vision\$v1Name")
}
```

The new V1 tests intentionally enforce the new policy. For a complete old-source review, use the separate extracted backup rather than deleting new tests or applying a hard reset. No Pi rollback is needed because the Pi was not modified.


## 2026-11-03 用户复核补充

用户复核 Mixkit 4437 和 ABU 201112989 两段真实视频没有楼梯；实际分别于源视频15.36秒和1.30秒生成的楼梯提示均记为误报。原始模型输出、预期标签和日志不改写，补充真值记录见 real-video-review/stairs-human-review-20261103.json。此前播报调度测试通过，仅证明给定事件的播放规则，不证明模型事件是真实危险。两个误报均来自已有 stairs_scene_context 场景判断，接纳原因均为 strong_stair_scene_without_path_mask。本次仅修正测试标注，未改模型、阈值或播报代码。
