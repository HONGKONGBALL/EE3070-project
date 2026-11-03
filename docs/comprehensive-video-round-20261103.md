# Four new real-video tests — 3 November 2026

This round evaluates the current awareness version without changing production
code, weights, thresholds, confirmation or voice scheduling. The eight-person
crowd gate and 0.75-second person sampling remain. All ground models, person
processing and the actual voice queue run together. Audio is timed WAV
simulation, not physical speaker or AirPods playback.

Review: <http://127.0.0.1:8858/comprehensive-4/>.
Evidence: `D:\SmartCaneRuntime\comprehensive-new-videos-20261103`.

## New source results

The four selected input hashes/source URLs were checked against older catalogs
before inference. Previously used staircase footage, overhead mall footage and
time-lapse candidates were excluded before evaluation. Labels were frozen from
the unannotated source contact sheet before running the models. No results were
used to change labels, select thresholds or reset state within a video.

| New video | Duration | Expected / actual result |
| --- | ---: | --- |
| [Street pedestrians, Mixkit 9261](https://mixkit.co/free-stock-video/popular-street-with-pedestrians-taking-a-walk-9261/) | 16.65s | No visible ground hazards; no ground warning events. Crowd density was labelled ambiguous before inference: no CROWD event, not scored as a hit or miss. |
| [Clothing store, Mixkit 9060](https://mixkit.co/free-stock-video/women-walking-through-fashion-mall-shopping-9060/) | 7.00s | Two shoppers and mannequins; no crowd or ground warning events. Much of the floor is out of view, so silence does not establish a clear path. |
| [Indoor staircase, Mixkit 22148](https://mixkit.co/free-stock-video/climbing-the-stairs-without-assistance-22148/) | 40.00s | One stair event: evidence at 1.21s, decision observed at 1.58s. Simulated stair sentence completed. No periodic repeat in this standalone clip. |
| [Puddle/reflections, Mixkit 1324](https://mixkit.co/free-stock-video/two-men-reflected-in-a-puddle-1324/) | 7.625s | Visible water from the beginning, but event evidence at 5.75s and decision at 5.875s. Simulated playback started, then was interrupted when the finite replay ended; not counted as complete playback. |

Street and puddle clips have mostly fixed cameras with real continuous motion;
store and stair clips have moving cameras. None is a cane-mounted outdoor field
recording. The supplied clip cadence was retained rather than extending them
with repeated stills. Some sources have a personal-use-only Mixkit license;
media and derivatives stay in the local private review, outside Git.

There are two visible ground-hazard positive categories, both generating an
event, and no false ground events in the four standalone clips. This is not
"100% accuracy": there is an ambiguous crowd scene, no new unequivocal crowd
positive, no real pothole/damaged-road positive, and a significant late puddle
warning with incomplete standalone playback.

## Why the puddle warning was late

The recorded puddle diagnostics show:

- 0.38–0.96s: no puddle candidate.
- 1.54s: a candidate was rejected by the scene verifier.
- 2.13s: one candidate was outside the path attention region.
- 2.71s, 3.33s and 4.63s: candidates were rejected by the scene verifier.
- 5.17s: a candidate was accepted, waiting for independent confirmation.
- 5.75s: the second accepted observation supported the warning event.

The event's final-frame capture-to-alert counter was 296ms on this computer.
That counter omits earlier missed/rejected water observations and is not the
time from the user's first seeing water, nor a Pi/headphone measurement. The
observed weakness is conservative recognition/verification on this reflection
scene, rather than five seconds spent waiting in the audio queue.

## Scene-switch coexistence check

A separate 74.87-second engineering montage starts with 3.6 seconds of the
previous ABU crowd baseline, then the new stairs, puddle, street and shop.
It is not a fifth new source or a natural uninterrupted walk. Frames were
resized through the existing 640×480 replay geometry and sampled at 15 FPS,
with MJPEG encoding. There is no text burned into model inputs and no policy
reset at the hard cuts. `scene-switch-segments.json` preserves the cuts,
source hashes and frozen expectations.

Observed decision-source times:

| Time | Generated event | Timed audio outcome |
| --- | --- | --- |
| 5.13s | Stairs, CRITICAL | Completed |
| 34.60s | Stairs again, CRITICAL | Completed |
| 49.40s | Puddle, SAFETY | Completed |
| 49.73s | **False pothole**, mapped to damaged-pavement speech, SAFETY | `dropped_invalid`; never played |

The second stair event follows a cleared/reconfirmed episode during the same
source stair clip. Detection continuity and repeat suppression therefore still
need moving-camera work; this is not an eight-second periodic repeat.

The false pothole appears on the water/cobblestone scene, not a labelled real
hole. Although its speech request was dropped, the false accepted event remains
a recognition problem. "Not spoken" must not be counted as correct detection.
It did not appear in the standalone water run. Compression, frame sampling,
startup/phase and preceding episode state differ between these runs, so this
test does not isolate which difference caused it or prove worker interference.

The short initial crowd baseline did not reach continuous confirmation. No
CROWD event was emitted in this montage, so it did not actually exercise crowd
speech being interrupted by critical stairs. Preserve that limitation rather
than claiming every priority combination was covered by these new videos.
The previous full crowd-video and queue-test evidence remains separate.

## Engineering and integrity

All four standalone runs and the montage reached replay completion with no
model-task errors or unwanted individual-object speech events. These are
engineering coexistence results, not a recognition accuracy certificate.
Runtime Python files and `config/camera.v2.toml` match the saved pre-round
hashes. Production code was not tuned on these four videos. No new automated
suite was run for this evaluation-only round; earlier 374-test results are
retained, not reported as a new run.

Raw candidates, final events, audio starts/completions/interruptions and dropped
requests remain separate in the JSON evidence and review page. Source-clock
audio intervals in the player are interpolated from capture timestamps and
are for review, not measured physical sound onset.

No Pi deployment, Git push, public media hosting, microphone or Pico changes
were performed.
