# Indoor/outdoor stair readiness audit — NOT PASSED

Date: 2026-10-30. No Pi was connected. No model training, live configuration
change, directional speech enablement, or Pico firmware change was performed.
The GY-521 mounting/slope conditions and remaining field checks are specified
in [the acceptance plan](pi-stair-slope-acceptance-plan.md).

## Actual current-runtime result

59 real photographs were replayed through the current ground pipeline at
640×480, 4 FPS, five seconds per photo. Each photo cut resets scene tracking;
labels are not supplied to the models. Lossless replay verified input pixels.
Wall time: 303.14 seconds, run `53dcfe89ff`. Every ground task was observed in
each case; no model/runtime errors. Audio was disabled.

| Photographed direction / setting | Scored photos | Generic stair event within 3 seconds |
|---|---:|---:|
| Indoor up | 11 | 6 |
| Indoor down | 10 | 4 |
| Outdoor up | 13 | 10 |
| Outdoor down | 9 | 1 |

These are **generic stairs events**, not correct up/down announcements.
The default active tasks remain road, pothole, semantic and puddle. Explicit
stair direction is disabled. An existing adapter currently confirms down only;
there is no enabled up-direction warning in this runtime.

Only 6/11 indoor-up and 2/10 indoor-down photos are non-extreme viewpoints.
Many interior images are architectural views down a stairwell, rather than cane
approaches. Counts cannot substitute for representative field trials. The
ordinary-view subset is retained separately in the JSON: generic events were
5/6 indoor up, 1/2 indoor down, 10/12 outdoor up, and 1/9 outdoor down.

Nine internally consistent non-stair negatives had **one generic-stair false
warning**, on `validation-2898.jpg` (a doorway/campus passage). It remains a
false warning under the frozen non-stair label; it was not relabelled after
prediction to improve the result. Physical threshold/step height was not
measured. Four escalator/mixed/uncertain scenes and two older uncertain
directions remain separate from the four direction groups.

## Offline direction research — not warning events

Two existing frozen candidates were audited on the same fitted input photos:
the author-trained Tiny-YOLO up/down weights and fixed MobileCLIP2 stair text
prototypes. No third model was added and no thresholds/prompts were tuned.
The local research process explicitly selected the previously installed
OpenCV 4.10 build for Darknet; the main environment was not downgraded.

| Setting / direction | Tiny-YOLO raw box hit | MobileCLIP2 top-1 correct | MobileCLIP2 opposite direction |
|---|---:|---:|---:|
| Indoor up | 2/11 | 8/11 | 3/11 |
| Indoor down | 4/10 | 6/10 | 3/10 |
| Outdoor up | 6/13 | 8/13 | 4/13 |
| Outdoor down | 2/9 | 6/9 | 0/9 |

A Tiny-YOLO raw box hit can coexist with an opposite-direction box and is not
an event or spatially confirmed success. MobileCLIP2 scores are cosine
rankings, not calibrated probabilities. It also ranks the mall negative as
downstairs. Neither candidate passed the existing PRD gate across the four
groups. Correct offline labels must not be presented as audible output.

Examples of wrong direction are retained: Tiny-YOLO labels the outdoor wooden
descending stairs `stairs-06.jpg` as up (~0.94); MobileCLIP2 labels the ascending
carpeted `stairs-17.jpg` as down. Generic-stair capability also has substantial
downstairs misses, so merely enabling the old direction model is not a fix.

## Material and annotation integrity

The 59 photos comprise 24 earlier Commons direction-development images, 25
newly collected real images, and ten intended non-stair regressions. Their
source/licence/hash, environment, direction and special-view flags were frozen
before this round's predictions. Repeated sites are grouped where known;
pretraining overlap with public photos is unknown. No AI images were used.
New and existing subsets are scored separately in the JSON.

One annotation merge error was discovered after inference:
`validation-1932.jpg` inherited `upstairs_visible=true`, while this round added
`stairs_visible=false`. The contradictory frozen record is preserved. It is
displayed in a separate unscored review group and excluded from **both** positive
and negative scores. No silently corrected independent-test claim is made.
It produced no runtime stair event. Consistency checks now detect this case.

The original raw catalog contains 14 outdoor-up entries; 13 remain after this
exclusion. Nine negatives remain. This further prevents a ten-negative or
ten-representative-trials acceptance claim.

## Files and reproducibility

Output: `D:\SmartCaneRuntime\stairs-validation-20261030`.

- `test-review.html`: four direction/setting groups, actual runtime candidate
  observations/events and visibly separate offline direction boxes. Filters
  show missed generic-stair events, false events and images with boxes.
- `frozen-stair-scenes.json` / `.sha256`: original frozen metadata, also copied
  to `config/stair-indoor-outdoor-scenes.json`.
- `direction-audit.json`: actual single-image candidate outputs and model hashes.
- `default-runtime-report.json`: full actual application replay observations
  and warning events; not an audio test.
- `acceptance-summary.json`: setting/direction, ordinary-view and new-photo
  subsets; also copied to `docs/stair-acceptance-summary.json`.
- `label-conflict-record.json`: explicit post-freeze exclusion record.
- `evidence-manifest.json`: report/model/config/source provenance and hashes.

CLI tools: `audit_stair_directions.py`, `check_ground_local.py`,
`summarize_stair_acceptance.py` and `build_ground_review.py`. Gallery provenance,
candidate-versus-event separation, conflicting-label exclusion and existing
scene/timing checks passed (nine targeted tests). Generated JavaScript syntax
was checked. Runtime source hashes/config match the preceding round.

Two additional source repositories were inspected without deploying anything:
[StairVision training repo](https://github.com/Yesh492/stair-detection-training)
at `1a84e65db3eb611e220c7cf5e4a92419adeefa17` documents a local trained weight path,
but no model checkpoint appears in the inspected Git tree;
[StairNet-DepthIn](https://github.com/MrChenWang/StairNet-DepthIn) at
`92a091e6eaa46081e4ee182f1cac1d2df8c7e451` requires depth input and is not a direct
replacement for the single C270 camera. Neither is reported as a ready solution.

## Required work remaining

- [ ] Improve and validate ordinary downstairs detection, especially outdoors.
- [ ] Obtain an up/down method that meets both recall and opposite-direction
      error requirements; add up-direction speech only after its evidence passes.
- [ ] Collect enough representative approach trials in all four groups and
      at least ten consistently labelled negatives.
- [ ] Verify the chosen version on the Pi, fixed camera then cane-mounted
      movement, with English audio and actual latency.
- [ ] Validate GY mounting/contact independently before any road-slope claim.

The stair work remains required and unverified. This audit completed its
measurements; it did not complete the feature or pass field acceptance.
