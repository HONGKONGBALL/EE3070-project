# Local awareness delivery — 2 November 2026

Current V1 voice policy supersedes the older equal-priority/dual-immediate-playback descriptions below: see `docs/v1-voice-alerts.md`. In awareness mode only four approved voices are allowed; crowd audio is LOW and requires fresh reassessment after ground playback and its safety hold. Historical test records below are retained.

## Latest: walking-crowd correction

The later correction and measured results are documented in `docs/pi-crowd-calibration.md` and `D:/SmartCaneRuntime/crowd-calibration-20261102/final/REPORT.md`. Current local review: `http://127.0.0.1:8858/crowd-fixed/`. It preserves the original packed-group check while allowing large, broad person groups to use a less restrictive semantic cross-check, and fixes confirmation history being erased while awaiting a matching semantic result. The walking clip now generates one crowd event at about source time 2.4 s and completes one simulated announcement; the ordinary queue false positive from an intermediate attempt is rejected again. Eleven full-runtime videos have been replayed, including six crowd negatives with zero CROWD events. The overhead small-person case remains a miss. The historical reports below describe earlier builds and should not be mistaken for the latest results.

## Subsequent four-video validation

Additional unchanged-runtime tests are saved at `D:/SmartCaneRuntime/crowd-videos-round2-20261102/` and the local review at `http://127.0.0.1:8858/crowd-round2/`. These supersede any interpretation of the first delivery as adequate crowd-recognition accuracy: **both new clip-level crowd positives were missed**. The local engineering pipeline is integrated, but crowd recognition is not yet sufficiently validated for the intended walking use case.

- Mixkit 4437, close-view walking shopping-street crowd, 37.48 s: no CROWD candidate/event. Maximum 13 detected/clustered people; 42 of 43 independent admitted observations fail the 75% semantic-person support condition, with additional ground/cluster rejections on some frames.
- Pexels 35972076, crowded overhead market, 13.47 s: no CROWD candidate/event. Maximum five person detections and zero size-qualified cluster people; retained small-person/viewpoint miss.
- Pexels 3303323, ordinary shopping street, 30.32 s: correctly quiet for dense crowd. Maximum 22 detected people illustrates why raw count alone cannot be the trigger.
- Pexels 29152591, moderate changing pedestrian flow, 12.37 s: quiet; density is ambiguous and unscored.

All four completed through ground plus crowd tasks without model errors or individual-object events. The overhead market separately emitted pothole/stairs events; these have no validated ground truth in this round and are not credited as correct or as crowd warnings. No threshold, model, ground rule or Pi service was changed. `catalog.json` freezes human clip-level expectations before inference, not precise physical density or route-blockage truth. `summary.json` and the diagnostic videos separate person boxes, appearance candidates, confirmation, CROWD events and simulated audio completions. The next improvement to investigate is the overly conservative crowd decision layer; the four-video test itself does not tune it or establish broad accuracy.

## Scope and status

Local implementation is ready for review. Added dense visible crowd awareness alongside all existing ground tasks. **Nothing from this change has been deployed to the Pi or pushed to GitHub.** The default mode remains `ground`. The starting snapshot includes the previously working, uncommitted USB/Bluetooth output controls; those files are preserved.

Evidence root: `D:/SmartCaneRuntime/crowd-awareness-20261102/`. Open the attributed video/image/audio review at `http://127.0.0.1:8858/` while its local server is running, or `review/index.html` inside that directory. Baseline source and audio archives, failed development runs, input checksums, final JSON results and test XML are retained there.

## Behavior

- `ground`: original ground-only task selection. `balanced`: original object/distance behavior. `awareness`: ground tasks plus one person-only YOLO session sharing the same camera. No chair, bag, bicycle, vehicle or individual-person speech in awareness, including the audio-test button. `crowd.enabled = false` removes the person worker while keeping awareness ground tasks.
- Reuses the existing YOLO11n NCNN weights. Awareness alone uses 640-pixel input, confidence 0.25, one thread, about one inference per second; the original balanced detector settings remain 416/0.45. No new visual weights or training. Ground models, input preprocessing, verifiers, ROI, confirmation and risk thresholds are unchanged.
- A candidate requires at least eight usable clustered people **and** sufficient union occupancy, horizontal coverage, person size and concentration. It also reads the existing, time-matched semantic result: at least 75% person pixels and at most 15% visible flat-ground pixels inside the group bounds. This support adds no second runtime semantic inference. Boxes are capped at 80; clustering and 160×120 occupancy are bounded.
- Confirmation uses three positive independent observations among four within 4.5 seconds, spanning at least 1.2 seconds. At the default 1 Hz rate this normally needs roughly two seconds of repeated evidence, plus scheduling and any speech already playing. This is not a measured Pi reaction-time guarantee.
- One completed notice per crowd episode. Box changes, a short miss or camera swing do not start a new episode. Five reliable non-crowded observations spanning four seconds clear it. Twenty seconds without positive evidence ends an unobserved episode with identity explicitly unknown; later reappearance may warn again. There is no automatic eight-second repeating crowd announcement.
- A queued crowd request requires evidence no older than two seconds. Camera/quality invalidation cancels it immediately. A short isolated miss does not discard an otherwise fresh confirmed request while the current ground sentence finishes. Interrupted/failed speech can retry only after new fresh evidence; stale queued notices are dropped.
- Your latest direct instruction takes precedence over the pasted prompt's lower crowd priority: CROWD and ordinary ground are both `SAFETY`. Existing `CRITICAL` stairs/strong pothole priority stays higher. Persistent crowd plus ground produces both messages; critical warnings can interrupt a crowd sentence, which can subsequently retry. A partial start is not counted as a second completed announcement.
- Ground freshness has precedence over person scheduling: person inference defers if a ground task has no fresh result (1.3-second limit). The person thread has lower OS priority where supported. Missing person weights or disabling crowd does not stop the ground workers.

These are visible-scene heuristics. They do not measure physical people/m², walking direction, personal trajectory, clearance, obstacle distance or whether a route is safe to pass.

## Voice dictionary

| Category | Internal labels retained | Phrase |
|---|---|---|
| PUDDLE | possible_puddle | Looks like there's a puddle ahead. Take care! |
| DAMAGED_PAVEMENT | pothole, damaged_pavement | The pavement ahead looks damaged. Watch your step! |
| STAIRS | stairs, optional downstairs | Careful, there might be stairs ahead. |
| CROWD | crowd | Looks a bit crowded ahead. Take care! |

`voice.py` is the mapping source. Raw labels, source model/revision, evidence and serious risk remain in ground events. Internal pothole/crack signals stay separate; an already handled damage-family sentence is not repeated merely because internal risk increases. The higher risk is still recorded. A strong initial pothole remains CRITICAL. The optional downstairs label uses the generic stairs phrase, not an unverified direction instruction.

`config/awareness-voice-artifacts.json` records exact text, SHA-256, sizes and WAV durations. The four files are prerecorded Microsoft David English WAVs generated offline by `tools/gen_camera_prompts.ps1 -AwarenessOnly`; no network TTS at runtime. Original fault/status files and legacy WAVs are unchanged. Voice assets are in the local delivery bundle, not committed binary files. USB/Bluetooth selection and fallback logic are unchanged. This task did not test sound on a physical Pi/headset.

## Recognition and regression evidence

### Existing ground behavior

`ground-full-before.json`, `ground-full-after.json`, `ground-full-comparison.json`: 125 original image sources through the four real ground models. All 500 raw outputs and all admitted results are identical; 75 ground events before and after. The comparison executes each revision's actual render-loop admission block with explicit still-image observation timestamps. Timing metadata and the intended voice mapping are excluded from comparison. This is a deterministic regression check, not a new accuracy measurement or a real walking trial.

`integrity-final.json`: all 114 model-file hashes match the snapshot. Ground adapters, verifiers, scene/quality logic, distance handling, audio output selection and Speech source match the starting snapshot. Configuration is identical except the intended phrases and the added crowd section. No Pico or road-boundary implementation changes.

The deliberate event-count exception is an already spoken damage-family warning followed by a stronger pothole in the same episode: the duplicate voice escalation is suppressed while internal risk updates. Separate new hazard episodes and categories still warn. A dedicated before/after fixture is saved as `damage-upgrade-comparison.json`.

### Real media

Six real continuous videos were run through the actual V2 runtime, shared camera, all ground tasks, person task, confirmation, arbiter and timed WAV sink. No photo timeline/scene reset was used. Results: `full-videos-final/all-results.json`. All six completed with no task errors or individual-object events.

| Video | Crowd result | Other recorded events |
|---|---|---|
| ABU Cultural Carnival, 7.17 s | One crowd event; one simulated complete crowd announcement | Also a stairs event; stairs truth is unverified here, so this is **not** credited as correct ground recognition |
| Hong Kong passage entrance, 17.42 s | No crowd event; severity labelled ambiguous before inference | One stairs event; not used as labelled ground accuracy evidence |
| Sparse Hong Kong mall walkway, 10.78 s | Quiet, as expected | None |
| Pexels street queue, 11.52 s | Quiet, as expected for this strict density rule | None |
| Forest stair walking video, 47.93 s | Quiet, as expected | One stairs event |
| Rainy walking video, 60 s | Quiet, as expected | One stairs event |

The last two are moving walking recordings; the others are genuine dynamic scenes, not cane-mounted walking captures. Videos show the actual runtime preview resampled for review; not every source frame is inferred. Source/licence/author/checksums are in `full-video-catalog.json` and the review cards. Per-window missed-event records remain visible: an episode can be announced just before a labelled window and stay active without a new event inside each later window. We do not count every window as a detection success or claim all stair segments were newly announced.

An additional ground-only ABU replay also emits stairs (`ground-parade-reference/video-201112989-report.json`), so that unverified ground event is reproducible without crowd enabled. It is not evidence that crowd improved stair recognition.

The 12 real-image appearance cases give four hits among five labelled dense positives, five quiet ordinary-scene negatives, and two unscored ambiguous cases. A crowded overhead staircase is a retained miss: too few sufficiently large persons are found. Later examples were used to harden the semantic threshold, so these are development validation, not an independent statistical test set. Single photos have no fabricated temporal alert/audio claim. No mAP or overall real-world accuracy is asserted.

The offline still tool letterboxes frames, while the existing runtime video reader resizes to 640×480. Earlier offline ABU sampling did not achieve temporal confirmation; actual runtime did. Both runs are retained, and they must not be treated as interchangeable preprocessing experiments. The latest camera-outage fix was after the six-video run and is covered by its dedicated regression test and the final integrated fault run.

## Coexistence and stress evidence

`queue-review/results.json`: seven deterministic program-level cases with real episode managers, Speech/arbiter and actual WAV durations. Crowd completes alongside stairs, puddle, pavement damage and strong pothole; stale crowd is not spoken. In the all-family case a crowd start is interrupted by a critical warning and retried; each category completes once. First critical-ground start times have zero added delay against the no-crowd fixture. Rendered audio is available to listen to; these are constructed policy observations, not model recognition or hardware playback.

`mixed-pressure-final.json`: 120.77 seconds, four concurrent HTTP clients. Includes a persistent crowd, short camera outage/return without duplicate crowd notice, reliable clear/new crowd, existing stairs, puddle/pothole, black frames, recovery, synthetic ±6° image rotation, clear interval and stair re-entry. All 20 engineering checks pass. 1,839 status requests: P95 18.56 ms. 1,836 preview requests: P95 17.14 ms; 36 expected 503 responses during injected missing-camera periods. No model/HTTP exceptions, unwanted labels, OBJECT_NEAR bypasses or stale ground events. Peak working set 1,048.38 MB. This short run does not prove absence of a long-term leak.

First pressure run failed: repeated old in-flight results kept moving the crowd invalidation barrier forward, preventing recovery. Fixed by consuming rejected frames once without advancing that barrier; added a regression test. A final stair re-entry expectation also needed an actually stair-free clearing fixture, because the prior crowd image already triggered the unchanged stair scene classifier. Both original failed records and final passing records are retained. Ground thresholds were not weakened to pass.

`benchmarks/summary.json` records initial paired Windows measurements: baseline ground ~1.48 Hz per task and preview 11.1 FPS; awareness ground ~1.48–1.49 Hz, preview 11.2 FPS, persons 0.99 Hz with median 63 ms inference. Awareness added about 0.10 busy CPU core and 85 MB peak working set versus the starting snapshot. Ground request P95 changed from 371 to 410.25 ms, within the predeclared 10% + 20 ms investigation gate. Ground result age changes were within that gate. Balanced mode also passed its actual-model continuity check. A final ground/awareness paired check after the recovery fix is recorded separately in `benchmarks/final-comparison.json`; use its final metrics when reporting this build.

The final paired run keeps ground inference at 1.48–1.49 Hz; preview changes 11.2 → 11.0 FPS, CPU 1.119 → 1.184 busy cores and peak RSS 968.9 → 1,061.32 MB. **The original all-event request P95 gate failed: 365.2 → 527.25 ms**, exceeding 421.72 ms. Its diagnostic shows five first attempts in each run, with first-attempt P95 365.2 → 362.6 ms. Awareness additionally recorded one same-episode puddle retry (578 ms evidence age) after critical arbitration in the audio-disabled fixture. All ground rates/ages and preview gates passed; the aggregate failure remains in the record. This investigation does not justify claiming all performance gates passed or zero timing impact. No detector/ground threshold was changed in response; queue coexistence is separately exercised with actual WAV durations. Pi timing remains unverified.

Final automatic suite: **319 passed**, one existing Starlette/httpx deprecation warning. Baseline before changes: 291 passed. `pytest-final.xml` contains the run. These Windows numbers do not guarantee Pi FPS, Pi thermal behavior, Bluetooth latency or real moving-cane reliability. Pi deployment, camera motion and physical listening remain the next hardware acceptance step after user review.

## Changed source and review tools

| File / function | Purpose |
|---|---|
| `pi/cane/vision/crowd.py`: crowd_evidence, CrowdAwareness | Bounded person-cluster evidence, semantic support, confirmation/clear, freshness and delivery state |
| `pi/cane/vision/voice.py` | Four-category English dictionary and internal-label mapping |
| `pi/cane/vision/detector.py`: Detector, decode | Optional person-only decoding/cap; original default decoding retained |
| `pi/cane/vision/v2.py`: _run, _model_loop, _person_ground_fresh, _decision_events, _on_delivery, _request_valid, status/draw | Awareness worker scheduling and shared results, no individual speech, crowd lifecycle, diagnostics and preview |
| `pi/cane/vision/episodes.py` | Approved phrase mapping, original labels/risk preserved, suppress repeated damage-family speech |
| `pi/cane/vision/demo.py` | Crowd diagnostics and mode-appropriate audio test |
| `config/camera.v2.toml` | Crowd settings, intended phrase changes; original ground values intact |
| `tools/gen_camera_prompts.ps1` | Four-phrase-only offline regeneration option |
| `tests/test_crowd_awareness.py`, `tests/test_awareness_runtime.py` | Temporal/fault/queue/runtime/class-filter/coexistence and disabled/failed person worker coverage |
| `tools/check_awareness_ground_regression.py` | Real-model original/new ground comparison |
| `tools/check_crowd_media.py`, `tools/check_awareness_videos.py` | Real image and complete-runtime continuous video experiments |
| `tools/check_awareness_queue.py` | Deterministic actual-queue coexistence + listenable simulated timeline |
| `tools/check_vision_continuity.py`, `tools/check_real_videos.py`, `tools/timed_review_audio.py` | Awareness test support; existing default invocation behavior retained |
| `tools/build_awareness_review.py` | Attributed review page from measured JSON; no inference or invented events |

The existing Bluetooth-related source changes in Git status predate this task; the snapshot audit distinguishes them from this task's edits. No commit has been created for this local feature.

## Run, disable and rollback

Windows, from the repository root, with existing weights installed:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'pi')
& 'D:\SmartCaneRuntime\.venv\Scripts\python.exe' -m cane.vision.v2 --config config/camera.v2.toml --models-root D:\SmartCaneRuntime\models --mode awareness --distance none --no-audio
```

The existing preview uses port 8000 from config. Add `--replay "ABSOLUTE_VIDEO_PATH"` to review a recording without a USB camera. For original modes replace `--mode awareness` with `--mode ground` or `--mode balanced`. Stop the active process before starting another on that port. To disable only crowd inside awareness set `enabled = false` under `[crowd]` in a copied config, then pass that config with `--config`; no person model/session is started. Restore `true` to re-enable it. The default production config still selects ground.

Recreate the local review from existing recorded results:

```powershell
& 'D:\SmartCaneRuntime\.venv\Scripts\python.exe' tools/build_awareness_review.py --results D:\SmartCaneRuntime\crowd-awareness-20261102
& 'D:\SmartCaneRuntime\.venv\Scripts\python.exe' -m http.server 8858 --bind 127.0.0.1 --directory D:\SmartCaneRuntime\crowd-awareness-20261102\review
```

Non-destructive rollback inspection uses a **new empty directory**, preserving the current worktree and its existing Bluetooth work:

```powershell
Expand-Archive -LiteralPath D:\SmartCaneRuntime\crowd-awareness-20261102\baseline-source.zip -DestinationPath D:\SmartCaneRuntime\crowd-awareness-20261102\rollback-review
Expand-Archive -LiteralPath D:\SmartCaneRuntime\crowd-awareness-20261102\baseline-audio.zip -DestinationPath D:\SmartCaneRuntime\crowd-awareness-20261102\rollback-review\audio\en
Set-Location D:\SmartCaneRuntime\crowd-awareness-20261102\rollback-review
$env:PYTHONPATH = (Join-Path (Get-Location) 'pi')
& 'D:\SmartCaneRuntime\.venv\Scripts\python.exe' -m cane.vision.v2 --config config/camera.v2.toml --models-root D:\SmartCaneRuntime\models --mode ground --distance none --no-audio
```

No hard reset, deletion, forced checkout or remote changes are needed. The WAV archive contains original filenames at its root.

After user review, future Pi acceptance should use a temporary awareness config, stable Pi 5 power and the real C270i. Measure ground freshness/preview/CPU/temperature before and after enabling crowd. Test a labelled nearby dense group versus spread pedestrians, actual walking camera motion and crowd plus each hazard. Select AirPods and USB separately through the existing output controls, play the new four phrases, verify hearing and reconnect/fallback, then test queued/interrupted/stale speech with actual output. Do not enable this as a reliable mobility aid based only on the local replay evidence.
