# Indoor negative-case check — 2026-10-30

The current ground pipeline was replayed against 18 visually reviewed indoor
photos, without changing model weights, thresholds, indoor suppression rules,
or the Pi runtime. No training or Pi deployment was performed.

- Seven new photos: two Hong Kong PMQ corridors and five Sarasota Mall at UTC
  views. These are only two locations; Sarasota also appears in the older set.
- Eleven existing regression photos: mall/hospital/library corridors, blue
  carpets, indoor courts, coloured reflections, and one real staircase.
- Labels were frozen before this replay. Both visible-puddle and visible-pothole
  labels are negative for all 18 images. These labels do not imply that a scene
  is free of people, stairs, or other obstacles.
- Actual four-second, 4 FPS lossless photo replay; 74.44 seconds wall time.
  All ground tasks observed every case; no runtime/model errors.
- Final puddle false warnings: **0/7 new, 0/11 regression**.
- Final pothole false warnings: **0/7 new, 0/11 regression**.
- The staircase image still generated a stairs warning. This was not a test
  of a blanket indoor mute.

One blue-carpet image (`122433233.jpg`) produced a road-model pothole candidate
at confidence 0.5107. The existing category ownership rule blocked it from
generating speech. The separate dedicated pothole model produced no candidate
on that image. Therefore zero final warnings does **not** mean every individual
model candidate was correct. The review page preserves that false candidate.

## Review files

Local output directory: `D:\SmartCaneRuntime\indoor-validation-20261030`.

- `test-review.html`: offline review, defaulting to the 18 indoor examples.
  It also includes the original 129-case group, with explicit overlap noted.
  Select “有检测框” and the original group to inspect positive hazard examples.
- `frozen-indoor-scenes.json` and `.sha256`: pre-inference labels and source
  hashes, copied to the repository as `config/indoor-validation-scenes.json`.
- `indoor-report.json`: raw model observations, rejected candidates, and final
  warning events. Run ID: `8caba2c372`.
- `indoor-summary.json`: scored results by new/regression split.
- `indoor-replay.avi` and `.json`: unannotated model input and scene timeline.

Each displayed frame shows only one model observation, with its original frame
ID. The original photo is fitted to exactly the replay's 640×480 coordinates.
Boxes are actual candidates, not ground-truth annotations or proof of speech.
Semantic rectangles are enclosing boxes of segmentation regions, not YOLO
object detections. Historical records retain only partial first detections,
which the page identifies explicitly. Normal person/chair detection remains
disabled in ground mode.

`tools/check_ground_local.py` now records one bounded representative observation
per task/case for this offline review. `tools/build_ground_review.py` builds the
gallery, verifying photo hashes against both frozen catalog and replay report.
Neither tool adds inference or UI work to the live camera application.

This is a small static-photo false-warning check, not evidence that indoor
misclassification is impossible. Real indoor spills and camera movement were
not tested, and no audio output or live Pi performance was measured.
