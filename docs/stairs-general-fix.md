# General stair false-positive correction — 2026-11-03

## Cause and change

The scene classifier compares six text prototypes. Its cosine scores are not
probabilities, and a relative winner need not be a good match. Previously, a
margin of 0.01 was sufficient to recover stairs even without a spatial stair
region. Ordinary walking/crowd scenes with absolute similarity around
0.10–0.16 could therefore become stair candidates and pass temporal confirmation.

`StairContextRefiner.select` now requires an absolute stair similarity of at
least **0.18**, in addition to the existing relative margin, for **maskless
scene recovery only**. Independent semantic stair regions keep the existing
verification. Weak matches remain visible as rejected diagnostic candidates
with reason `insufficient_absolute_stair_match`; they are not admitted to the
hazard policy. This is a generic admission rule, with no source IDs, scene
blacklist, crowd veto, or audio muting. It adds no inference calls.

The threshold is empirical for this frozen encoder and prototype recipe. It
is not 18% confidence and must be revalidated if that recipe changes. It was
selected using existing development material, so these results are regression
evidence rather than an estimate of unseen-world accuracy.

A pedestrian-scene negative prototype was also explored locally and rejected:
it removed false positives but introduced two misses on genuine outdoor
downstairs scenes. That prototype, its prompts, and its classifier changes are
**not integrated**. Neural weights, the original six prototypes, preprocessing,
voice priority/queue rules, person/crowd detection, other ground models and Pico
code remain unchanged.

## Measured results

Artifacts: `D:\SmartCaneRuntime\stairs-general-fix-20261103`.

| Photo comparison, identical model features | Before | After |
| --- | ---: | ---: |
| True stair detections / 60 positive photos | 54 | 54 |
| False stair detections / 27 negative photos | 2 | 0 |
| New misses | — | 0 |

91 unique photos were processed: 87 scored; three uncertain labels and one
previously documented conflicting label were retained but excluded. Coverage
includes indoor/outdoor and upstairs/downstairs, with crowded real stairs
preserved. The six remaining positive misses are listed in
`photo-comparison.json`; this change does not solve them. Direction groups
describe evaluation labels, not spoken up/down capability.

Five real continuous videos ran through the current awareness runtime, all
ground workers, person/crowd processing, and the actual queue with timed WAV
simulation. No ground-truth-driven scene resets were used.

| Video | New stair events | Notes |
| --- | ---: | --- |
| mixkit-4437 | 0 | User-reviewed stair-negative; previous run had a false stair event. Crowd still generated an event. |
| video-201112989 | 0 | User-reviewed stair-negative; previous run had a false stair event. Crowd did not confirm in this replay. |
| pexels-36961548 | 0 | Additional ordinary promenade negative; earlier run had two false stair events. |
| forest-stairs | 1 | Evidence at 1.20s. Later stair observations remain accepted; an ongoing episode does not require another announcement. |
| rain-walk | 1 | Evidence at 25.33s, in the previously unscored approach transition; later actual stair observations remain accepted. |

Keep the frozen interval evaluator's raw results: it says no *new event* in
the second forest positive window and the rain 26–37s window. Do not rewrite
those labels to count earlier events as in-window successes. Check accepted
observations and episode continuity separately. These are replay observations,
not measured Pi/headphone latency or physical audio tests.

There is also a **puddle false event at 40.73s in forest-stairs**. This unrelated
model error is retained in the review, not hidden or reported as fixed.

## Regression and verification

- 371 automated tests passed, including weak absolute matches, independent
  spatial stair evidence, and preservation of non-stair semantic labels.
- Replayed the frozen 125-image ground collection: all 375 raw road/pothole/
  puddle outputs were identical; all 125 semantic class maps, ground masks and
  non-stair detection lists were identical; no non-stair policy events changed.
- One extra maskless stair decision changed in that collection: the Sarasota
  mall corridor photo. Its original ground-truth labels cover puddle/pothole
  only, so it is not added retrospectively to the stair accuracy denominator.
- Stair model artifact hashes match the pre-change snapshot. Existing runtime
  source differs only in `pi/cane/vision/stair_context.py`; tests also changed.
  `tools/check_stair_generalization.py` is a new reproducible evaluation helper.

Local review: <http://127.0.0.1:8858/stairs-fixed/>. The old V1 voice page links
to it while retaining historical false-positive records. This change is local;
it has not been deployed to Pi or pushed to GitHub.

## Limits and rollback

This improves the measured false-positive behavior without additional misses
in this photo set. It cannot guarantee the next unfamiliar scene is correct.
Existing stair misses, true cane motion, lighting changes, timing on Pi and
outdoor use still require validation. No weight training was performed.

The exact pre-change source is saved in `before-source.zip`. To roll back the
runtime change, restore only `pi/cane/vision/stair_context.py` from that snapshot;
do not reset the repository and discard earlier voice/Bluetooth/crowd work.
