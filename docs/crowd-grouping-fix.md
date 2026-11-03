# Crowd grouping correction — 3 November 2026

This change keeps **eight qualifying people in one group** as the minimum.
It does not count everybody anywhere in the image as a crowd.

## Why obvious crowd footage could remain silent

There were several gates after person detection. In a recorded ABU frame,
12 people had already been detected, but the neighbour-link rule split them
into two groups of six. Neither group passed the eight-person gate. Averaging
the two neighbouring box widths made the tolerated gap too small when
perspective or occlusion produced differently sized boxes.

The short clip also lost useful partly occluded detections at the person-score
cutoff. A group's bounding rectangle includes background between people; small
changes around its semantic-person threshold could break the required sequence
of independent observations. In the walking clip, broad close groups were often
rejected when a little more than 20% of their rectangle was visible ground.

Thus the visible total, qualifying count, grouped count, crowd candidate,
continuous confirmation and spoken event are different quantities. Person boxes
alone do not prove that a crowd event has been emitted.

## Final bounded change

| Setting | Before | Now |
| --- | --- | --- |
| Minimum qualifying clustered people | 8 | **8, unchanged** |
| Horizontal neighbour gap | 0.8 × mean of two box widths | 0.95 × wider box width |
| Person confidence cutoff | 0.25 | 0.20 |
| Ordinary-group semantic-person support | 75% | 70% |
| Visible ground allowed for already broad, close groups | 20% | 25% |
| Person observation interval | 1.0 s | 0.75 s |

The broad/close geometry requirements and its 40% semantic-person support
remain unchanged. Other groups retain the original 15% ground limit. Minimum
person size, union occupancy, packing, horizontal coverage, three-of-four
confirmation, clear/re-entry rules, freshness and the 80-person cap remain.

No neural weights or input sizes changed, no additional model/camera reader
was added. Person sampling increases modestly from 1 Hz to about 1.33 Hz to
reduce missed confirmation opportunities in short, partly occluded scenes.
Ground inference rates remain unchanged and the existing ground-freshness
guard still defers person work when ground results fall behind. It is the same bounded group
computation with corrected neighbour scaling and modest calibration. The
`CrowdAwareness` temporal/episode/voice class itself is unchanged. Ground
recognition, the stair false-positive fix, Pico/distance, Bluetooth/USB output
and ground-first voice scheduling remain unchanged. Default mode remains ground;
crowd recognition is used in awareness mode.

## Rejected approaches

An initial lower-count experiment was cancelled after the user clarified that
eight people is appropriate. It is not included in the final code. Broadly
lowering ordinary-group semantic support to 60% caused a crowd false event in
the prelabelled ordinary queue video and was rejected. Merely raising every
neighbour gap also merged the existing spaced-person negative fixture, so that
approach was rejected. No source/video ID is consulted by runtime detection.

Diagnostic folders are retained, including failed attempts; they must not be
confused with final results. The final configuration is described by
`release75-*` and `sampling-repeat/` in
`D:\SmartCaneRuntime\crowd-relax-20261103`. Earlier `release-*` folders used
the superseded 1 Hz sampling profile. One interrupted long-gap replay was
incomplete and excluded, not treated as a valid performance measurement.

## Verification and review

The final collection contains the two required forward-view crowd videos,
six prelabelled crowd negatives (including queue, mall, promenade and ground
walking videos), two density-ambiguous clips, and the existing 12-photo crowd
collection. Elevated overhead crowd footage remains outside the required
cane-forward acceptance scope. Labels are retained; missed or false events
are not relabelled as successes. These are development/regression samples,
not a statistically independent real-world accuracy estimate.

The final measured results are recorded in `release75-summary.json`,
`release75-videos/all-results.json`, `release75-photos/results.json` and
`release75-pytest.xml`. `sampling-repeat/all-results.json` records three
additional complete cold-start runs of the same ABU clip; all three generated
a CROWD event, with evidence at 2.53s, 2.00s and 2.33s. These are repeatability
checks on one clip, not three independent videos. Full-runtime runs use the actual ground/person workers,
confirmation and voice queue without ground-truth-driven resets. WAV playback
is timed simulation, not physical AirPods/USB listening. Source-clock timings
are not Pi response-time measurements.

The two highlighted videos are rendered from final recorded person boxes and
states, with event times and raw records, at
<http://127.0.0.1:8858/crowd-grouping/>. Earlier pages retain their historical
results and link to this new review. The preview-only renderer has also been
corrected to display the configured count rather than hard-coded text.

This is a local change, without Pi deployment or Git push. Existing distant/
small-person misses remain, and a non-crowd-model warning in these videos must
not be counted as a successful crowd result. Physical cane placement, Pi load
and listening tests are separate hardware acceptance work.

## Rollback

Restore only `pi/cane/vision/crowd.py` and the `[crowd]` table in
`config/camera.v2.toml` from this task's `before-source.zip`. Do not reset the
whole repository: it contains earlier voice, Bluetooth and stair work.

## Final measured results

- Both required crowd clips generated a CROWD event and simulated complete
  playback. Three additional complete cold-start ABU replays also triggered.
- All six labelled negative videos remained free of CROWD events; two
  density-ambiguous videos are retained without accuracy scoring.
- The twelve-photo collection retained 4/5 positive appearance hits, zero
  false positives among five negatives, and two unscored cases. The distant/
  small-person positive miss remains. Stills do not generate invented votes.
- 374 automated tests passed. All ten full-runtime videos completed without
  model errors or unwanted individual-object events.
- Forest and rainy walking stair events remain. The forest video's puddle
  event at approximately 41 seconds remains an unrelated false positive;
  this is not counted as successful ground recognition or fixed by this task.
- Source/config integrity confirms unchanged non-crowd configuration, stair
  admission, voice scheduling and the crowd episode/confirmation class.

See the JSON summary for exact final-run event times. Sampling-phase timings
can vary; these development replays do not establish field accuracy or Pi
performance. The interrupted earlier replay is excluded.
