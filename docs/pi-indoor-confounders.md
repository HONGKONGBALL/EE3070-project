# Indoor dark-object / round-shape stress check — 2026-10-30

This round tests the reported lab failure mode: removed shoes, dark objects and
round shapes being interpreted as a pothole. The local calibrated runtime and
thresholds were kept unchanged. No training or Pi deployment was performed.

## Material and method

Labels and image hashes were frozen before inference in
`config/indoor-confounder-scenes.json`.

- Four new real photos: black sandals on carpet; black shoes and a briefcase
  on carpet; a mat covered with shoes; running shoes on a wood floor.
  These include close-ups and are not all cane-mounted camera viewpoints.
- Six explicitly synthetic scenes generated using built-in `image_gen`:
  shoes/backpack on a blue-grey lab floor; a black round mat with jacket and
  chair wheels; cables/reel/stool shadow/slippers; black shoes and cloth bag on
  a darker floor; flush black circular floor inlays; a black rubbish bag with
  umbrella and wheel. They are AI images, not captured lab data.
- Four existing controls: two pothole photos (one also has visible water),
  a real staircase, and the blue-carpet regression case from the previous round.
- An extra downloaded backpack/pineapple picture was excluded **before**
  inference because an indoor setting was not established.

All 14 scenes were replayed without annotations in a lossless 640×480 video,
6 seconds per image at 4 FPS. Run `931028e78a` took 86.58 seconds; every scene
had observations from all active ground tasks, with no model/runtime errors.
Audio was disabled. This is a static-image check, not a live Pi walking test.
Model pretraining overlap for public real photos is unknown.

## Results

| Material | Images | False puddle warnings | False pothole warnings |
|---|---:|---:|---:|
| New real object photos | 4 | 0 | 0 |
| New AI simulations | 6 | 0 | 0 |

Controls: both real pothole images generated pothole events; the one water
positive generated a puddle event; the real stairs image generated a stairs
event; the blue-carpet control produced no hazard warning.

Zero final warnings does not mean zero incorrect candidates:

- `synthetic-floor-circles.png`: road model's pothole candidate 0.482 was blocked
  by existing category ownership. A puddle candidate 0.910 was rejected by the
  existing image-context verifier. The dedicated pothole model had no candidate.
- `95549323.jpg` (black shoes / briefcase): puddle candidate 0.263 was rejected
  by the verifier.
- `70591504.jpg` (running shoes): puddle candidates 0.547 and 0.424 were rejected
  by the verifier.
- `122433233.jpg` (existing blue carpet): road model's pothole candidate 0.511
  was blocked by category ownership, as in the preceding round.

The current local version did **not** reproduce a final shoe-as-pothole warning.
The user's observed live lab failure remains unresolved: these few images do
not cover its exact lighting, viewpoint, motion or deployed model revision.
No rule was added to suppress all dark objects or all indoor scenes.
Objects such as shoes, cables and mats may still be physical trip hazards;
their negative labels here mean only “not a pothole / not standing water.”

## Review and provenance

Directory: `D:\SmartCaneRuntime\indoor-confounders-20261030`.

- `test-review.html`: actual candidate boxes, rejection reasons and final
  events. The selector separates six AI scenes, four real photos and four
  controls. Each AI scene also has a visible per-card synthetic label.
- `confounders-report.json`: actual replay output; `confounders-summary.json`:
  final event scores by material type.
- `observed-candidates.json`: representative per-task candidate observations.
- `evidence-manifest.json`: checksums and runtime provenance.
- `generation-prompts.json`: exact prompt set, mapped to each AI image;
  also copied to `docs/indoor-confounder-generation-prompts.json`.
- Source photos and AI outputs are under `photos/`; real photo source URLs,
  authors and licences are preserved in the frozen catalog and metadata.

The only shared tool change adds an explicit AI-material label to the offline
review page. No extra inference, network request or UI workload was added to
the live camera application. Review provenance tests and the generated
JavaScript syntax check passed.
