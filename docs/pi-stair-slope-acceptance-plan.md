# Indoor/outdoor stair acceptance and GY-521 slope scope

Updated 2026-10-30. This follows the user's new requirement to verify both
ascending and descending stairs, indoors and outdoors. It supersedes the old
downstairs-only scope for this verification work. It is not a passed report.

## GY-521: what can be measured

The MPU-6050 fitted to a typical GY-521 combines an accelerometer and gyro.
Gravity-based tilt estimation is valid mainly when additional acceleration is
small. Gyro integration also requires bias calibration and drift correction.
Sources: [TDK product specification](https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Datasheet.pdf),
[Analog Devices inclination application note](https://www.analog.com/en/resources/app-notes/an-1057.html).

Engineering implication for this cane: it measures the sensor mounting frame's
orientation. A sensor fixed to a handle follows handle pitch; handle pitch is
not uniquely determined by road slope. Filtering cannot supply that missing
mechanical relationship.

A conditional slope demo is possible when a calibrated sensing plate/base
follows the ground surface, with a known forward axis. First test the mechanical
relationship; do not write a handle-tilt threshold and call it a road-slope
detector. This detects the contacted surface's inclination, not a slope ahead
before contact, and does not determine whether visible stairs ascend or descend.

Hardware follow-up, once the Pi/sensor are available:

1. Record the actual chip/board, connection, axis orientation and mounting.
2. Calibrate stationary gyro bias and a flat-surface reference.
3. Compare repeated known positive/negative inclinations with an independent
   angle reference; save raw acceleration/gyro and estimates.
4. On a level floor, change the handle angle and push the cane. Road-slope output
   must not incorrectly follow those handle movements.
5. On real inclines, test stable contact and movement separately. Unstable
   contact/pose must remain unknown, not confidently uphill/downhill.

No Pico firmware, wiring or sensor code was changed in this local verification.

## Stair requirements

Score four groups independently: indoor up, indoor down, outdoor up, outdoor
down. Use visible geometry from the camera viewpoint; a photograph titled
“steps down” can show ascending stairs when photographed from below.

Keep three outcomes distinct:

- Generic stairs: `Possible stairs ahead.`
- Upward direction: a correct explicit upstairs event.
- Downward direction: a correct explicit downstairs event.

The current default only supports generic stairs. The optional older direction
adapter is disabled; its confirmation currently retains down only. It does not
provide an active upstairs event. Correct offline boxes or MobileCLIP rankings
must not be scored as direction announcements.

Use the existing PRD's initial gate: at least ten positives per category, at
least eight correct events within three seconds, and no more than one false
event per ten negatives. Report opposite-direction errors separately, even
when a generic stair warning also occurs. Collect representative approach
views; architectural vertical/spiral/side views remain a separate stress set
and cannot substitute for ten cane approach trials. Do not tune on held-out
results and continue calling those results independent acceptance.

Coverage includes straight and turning stairs, light/dark steps, stone/tile/
carpet, shadows, railings, and stairs near the camera path. Flat tiles, carpet
patterns, reflections and ramps are negatives for stair events. Escalators,
mixed directions and ambiguous geometry remain explicitly separate.

## Verification phases

1. Frozen public-photo audit: source/licence/hash, environment and direction
   reviewed before inference. Existing development and new material separated.
2. Actual default-runtime photo replay: final event log and decision timing,
   audio disabled. This cannot pass explicit direction capability that is not
   enabled, and cannot establish live-camera/audio performance.
3. Once a direction method passes, integrate a single chosen method behind
   existing scheduling and event arbitration, preserving preview responsiveness.
   Recheck indoor dark-object negatives and ordinary stair recall.
4. Pi field trials: fixed camera mounting, real approach clips, then cane
   movement. Verify the same four groups, end-to-end delay and English sound.
   Use a sighted tester/helper approaching and stopping before stair edges;
   do not require a blind user to rely on an unverified warning system.

All Pi field tests, actual GY mounting/measurement and directional speech remain
unverified until physically tested. The local photo round does not close them.
