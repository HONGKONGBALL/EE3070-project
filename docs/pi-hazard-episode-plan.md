# Hazard episode upgrade — local acceptance plan

Baseline: `de1d3a38db4e95ebbc2074f021b2c5d6275f008a` on the existing project. A detached baseline worktree and all new evidence live under `D:/SmartCaneRuntime/episode-upgrade-20261031/`. Do not deploy or connect to the Pi in this task. User will review local results first.

Source requirements: the user optimization prompt in attachment `23f2c506-3be0-463c-89a1-65d449fd8463/pasted-text-1.txt`, read before implementation. No training, new model weights, microphone/agent/navigation feature, or changes to Pico sensing/vibration.

## Design selected for evaluation

- Use one bounded time-aware K-of-N accumulator per admitted model label. Missing, unusable or stale observations do not count as negative evidence. Require multiple independent, time-separated observations, not a single weak candidate. Preserve the existing verifier, path and indoor-floor gates.
- Use three simultaneous front-path episodes: wet surface, damaged pavement, stairs. Keep original label/model/frame evidence. Pothole and crack speech share the damage family; a confirmed higher-risk pothole can upgrade a generic damage alert. This is temporal grouping inside the configured image path, not a map or physical object identity.
- Keep episode identity over brief visual loss. Re-arm after multiple reliable clear observations, or a bounded unobserved timeout explicitly labelled as uncertainty. Do not use box IoU as stair episode identity or impose the old global eight-second delay on a genuinely re-armed episode.
- Separate event creation, queueing, dispatch, disabled audio, playback process start, completion, interruption and failure. Retry an undelivered alert only from new, fresh still-relevant evidence. Do not replay obsolete events. Ground and balanced modes share these rules; object-distance association remains unchanged.
- Keep work bounded: no additional model calls or background inference, small rolling deques, one episode per family, at most the existing bounded audio queue. Retain the same preview/status endpoints and update only the existing collapsed diagnostics.

## Requirement/evidence checklist

| Requirement | Authoritative completion evidence |
|---|---|
| A: mobile confirmation | Deterministic tests for t1-positive/t2-missing/t3-positive, same-frame duplication, weak singleton, low quality, time-window expiry; moving-road replay vs saved baseline |
| B: episode dedup/re-arm | Changing stair boxes/ROI once, short frame loss retained, reliable exit/new stairs re-announced, bounded missing reset, independent urgent family tests; normal walking before/after videos |
| C: summary families | Crack+pothole same-area/same-family tests, original labels retained, one initial damage warning, independently confirmed escalation, short configurable English WAV/text consistency |
| D: delivery | Real arbiter and timed-sink tests for priority, simultaneous stairs/wet surface, expired/invalid pending alert, fresh retry, no late alert after exit, no repeat after successful start; explicit audio-disabled behavior |
| E: timing | Per-frame capture/candidate/verification timestamps, confirmation and queue timestamps, event/playback identities, process-start distinction; exported latency/FPS/drop/CPU/RAM results and HTTP load checks |
| E: speed/distance/Pi | Local media-rate stress is labelled as media rate, not walking m/s; no monocular distance claims. Real approach distances/speeds, ground vs balanced Pi CPU/temperature and physical audio remain a documented hardware checklist, intentionally not executed at user's instruction |
| F: regression | All existing tests plus new episode/delivery tests; existing 125-photo collection, black-shoe/blue-floor/reflection/circle negatives, old moving-road clips and normal walking clips. Report per-label and voice-family results separately, negatives and all tradeoffs |
| F: fault/balanced | Actual local model pipeline interruption/recovery/slow-result checks; ordinary people/chairs/bags still gated by distance; actual ground and balanced local load comparison |
| G: discipline | Baseline commit and worktree, unchanged model hashes, reviewed diff, configurable thresholds/phrases, no Pi mutations, no forced Git publication, bounded response payload and preview checks |
| User review artifact | Playable side-by-side before/after clips, event/episode/delivery timeline, optional clearly-labelled reconstructed English preview, final report with changed files, root causes, results, limitations and exact local/deployment commands |

Do not mark the goal complete based only on unit tests. Inspect rendered media and real-model outputs, document uncovered physical tests, and show the local comparison. Do not silently relabel frozen failures or count merged voice categories as improved detector accuracy.

## Local completion audit

Completed: final 125-case regression unchanged; 279 automated tests; eight before/after videos plus targeted retests; final ground/balanced three-client load and fault/recovery checks; three media rates; model checksums; local playable review and report. Known detector failures and episode timeout tradeoffs are retained. Real Pi/physical distance/audio and browser full-interaction checks remain explicitly unverified, outside the authorized local-only execution. No SSH, deployment or publication performed. See `docs/pi-hazard-episodes-20261031.md`.
