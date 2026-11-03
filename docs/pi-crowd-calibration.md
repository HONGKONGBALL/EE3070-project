# Crowd calibration after the walking-video misses

2 November 2026. Local implementation and evaluation only. No Pi deployment or Git push.

Scope clarification after user review: elevated bird's-eye crowd footage is outside the required cane-mounted crowd acceptance scope. Retain its missed detections as diagnostic records, but do not spend further development on that viewpoint or count it as a required delivery failure. Forward walking crowd awareness remains required. Normal downward camera tilt for ground-surface recognition remains in scope; it is different from an elevated overhead crowd view. Historical sample totals below remain unchanged rather than relabelling old results after the fact.

## What changed

The earlier rule demanded that 75% of a whole group bounding rectangle be segmented as person, with at most 15% visible ground. A walking group can include shop fronts, gaps and pavement inside that rectangle. It could therefore contain many detected people while never becoming a crowd candidate.

The original packed-group rule is retained. A less restrictive semantic check is allowed only when **all** of the following image-space geometry conditions hold: median person height at least 45% of frame height, person-box union occupancy at least 50% of the image, and group horizontal coverage at least 75%. Such a group may have semantic-person support of 40% and up to 20% visible ground. All original clustered-count, per-person confidence/size, packing and temporal conditions still apply, including at least eight qualifying clustered detections. These dimensions are not a distance estimate.

This is intentionally different from lowering the semantic threshold for every group. An intermediate broad relaxation caused a false crowd alert on an ordinary queue video. That attempt was rejected and its records retained. A couple of large foreground people cannot relax the rule for mostly smaller background pedestrians: the geometry condition uses **median** size and whole-group occupancy/coverage.

Also fixed a synchronization bug: when a person result arrived before its matching semantic result, the manager erased previously admitted confirmation history. A short wait is unknown evidence, not a negative observation. Prior history now survives that wait within its existing time window. It still cannot confirm without a fresh matching semantic result; camera/quality faults, expired history and stale results keep their original invalidation behavior. A dedicated regression test covers this.

No new model, camera reader, model inference or unbounded history was added. The added work is a few comparisons on already computed geometry. The visual models, weights, ground preprocessing/thresholds/confirmation, distance/Pico and audio-output logic are unchanged. Default mode remains ground. Existing crowd episode clearing and speech priorities remain unchanged.

## Evidence locations

`D:/SmartCaneRuntime/crowd-calibration-20261102/before/` and `before-hashes.json` preserve the pre-change crowd source/config/tests. The parent folder retains the rejected first attempt; it must not be confused with the final run.

`D:/SmartCaneRuntime/crowd-calibration-20261102/final/` contains the final version's fixed input catalog, real full-runtime video results, independent observation summaries, static-image regression, fresh negative-video test, fault/coexistence pressure run, queue fixtures, test XML and integrity receipt. `REPORT.md` there gives measured outcomes. Final review URL: `http://127.0.0.1:8858/crowd-fixed/`.

The four development videos are the same inputs that exposed the misses. They are calibration data, not an independent accuracy test. The six earlier real videos are replayed for regression, and a further promenade video is labelled before inference and kept out of threshold probing. The 12 previous real photos are checked again, including ordinary groups, spectators, crossings, distant people and retained overhead-view misses.

The runtime records separate person detections, appearance candidates, confirmed CROWD events, simulated audio starts and simulated completions. Videos are diagnostic resamplings of the source clock, with person-box source time shown. Queue tests use actual WAV durations without opening a physical sound device; there is no Pi listening claim.

## Remaining boundaries

Final measured run: 323 automatic tests passed; all 11 full-runtime videos completed without model errors or individual-object events. Both forward-view crowd positives generated one CROWD event and one simulated complete announcement each: the walking clip at source time 2.4 s, the ABU clip at 2.17 s. Six prelabelled negative videos had zero CROWD events; two density-ambiguous clips stayed unscored. The overhead positive was still missed. The 12-photo regression retained its original four of five positive appearance hits, five quiet negatives and two ambiguous cases. These are development-sample outcomes, not a claimed real-world accuracy percentage.

Final four-client fault/coexistence stress completed in 120.58 s with all 20 engineering checks passing: no model/HTTP exceptions, no stale ground events, and correct short-outage/clear/re-entry behavior. Status P95 was 18.80 ms across 1,833 requests; preview P95 was 17.62 ms across 1,832 requests, including 34 expected 503 responses during the injected camera outage. Seven timed-WAV queue cases passed; critical ground start delay versus the no-crowd fixture was zero in those deterministic cases. This does not supersede the earlier aggregate-P95 investigation or constitute a new paired Pi performance benchmark.

The overhead market still has tiny distorted person instances in a portrait source resized through the existing camera pipeline. It remains a viewpoint/small-person miss. The cane's intended forward walking view is the target of this change. Lowering the person-count requirement does not solve that detector limitation. No claim is made that overhead crowds, physical route blockage, distance or all outdoor crowds are now supported.

The short ABU crowd clip is sensitive to which frames a 1 Hz asynchronous worker samples and whether enough independent evidence is available before it ends. Its actual repeated result is retained even if not confirmed; no extra artificial frames or temporal votes are inserted to force a pass.

Original ground models can still emit incorrect/unverified warnings on new footage. Those events are listed separately and are not counted as crowd success. Source/config/weight integrity and engineering coexistence do not constitute ground or crowd field-accuracy certification. Pi load, temperature, moving-cane placement and physical AirPods/USB playback remain hardware acceptance work after local review.
