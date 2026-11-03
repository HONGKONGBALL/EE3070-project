# Pi awareness deployment — 3 November 2026

The current local runtime is deployed in `/home/gp7_3070/EE3070-project` on
`3070gp7`, now reachable at `192.168.0.160`. Its SSH host key was verified against
the previously trusted Pi identity. The existing `smart-cane-camera` user service
is enabled and running in **awareness** mode with a real Logitech C270 camera.

Computer preview: <http://127.0.0.1:8010/> through the hidden SSH forwarding
process. Pi-local preview remains <http://127.0.0.1:8000/>. The forwarding process
must remain running; a later IP/network change requires reconnecting it.

## Installed and preserved

Nine changed vision runtime files, four current English WAV files, the voice
artifact manifest and five reference documents were installed. All 19 payload
files were byte/hash checked. The active Pi configuration was merged separately:
awareness mode, current crowd settings and current four-category phrases were
updated; camera, server, existing ground thresholds/inference tuning, distance
configuration and audio target/enabled values were preserved.

The deployment includes the generic stair evidence correction, current crowd
grouping/occlusion settings and ground-first voice queue. The crowd minimum
remains eight qualifying grouped persons, with three-of-four confirmation.
Person sampling is capped at about 1.33 Hz (0.75-second interval), but actual
Pi sampling is lower when its existing ground-freshness guard defers person work.

No model binaries were replaced or downloaded. The six required model manifests
match the local versions, dependencies were already installed, and the shared
arbiter/model/Pico-link source files matched and were left untouched. No new
microphone, Pico, road-entry or navigation behavior was enabled.

The service retains PipeWire Bluetooth/USB output selection and the previously
saved AirPods target. Output inventory was empty during this check; no physical
headphone listening test was performed. The preview's audio controls can select
a connected AirPods or USB playback device when available.

## Live deployment check

Run ID: `b901c90126`. A 40.38-second live check collected 34 status/JPEG samples
over the computer's SSH tunnel. All were ready and valid JPEGs; frame numbers
advanced from 1872 to 2454 with no HTTP/model-task errors.

| Metric | Observed |
| --- | ---: |
| Camera capture at final audit | 14.9 FPS |
| Preview render median | 11.6 FPS |
| Road / pothole / puddle task medians | About 0.99 Hz each |
| Semantic/stair task median | 1.12 Hz |
| Person task median | 0.955 Hz; ready/deferred, no error |
| Status request P95, including Wi-Fi/SSH | 171 ms |
| JPEG request P95, including Wi-Fi/SSH | 266 ms |
| Service automatic restarts | 0 |
| Final temperature | 59.3°C |
| Voltage/throttling flags | `0x0` |

Ground tasks retained their Pi tuning: road/pothole/puddle caps of 1 Hz and
two-thread semantic/stair processing with a 1.5 Hz cap. This is a short health
and live-frame check, not a long-term thermal test, walking-ground recognition
acceptance, audio-onset measurement or proof that the user heard a warning.
The camera was close to indoor furniture/body occlusion during inspection.

Known algorithm limitations from the new-video round remain: reflection-puddle
admission can be late, a mixed replay produced a false pothole event whose
speech was dropped, and stair re-confirmation can produce another announcement.
Deployment does not claim those issues were fixed.

## Records and rollback

Windows evidence: `D:\SmartCaneRuntime\deploy\pi-awareness-20261103` contains
the audit, payload/manifest, install script, deployment result, live samples,
live validation, final audit, preview image and tunnel metadata.

Pi backup before replacement:
`/home/gp7_3070/.smart-cane-deploy/pi-awareness-20261103/before-awareness-20261103T052014Z.tgz`.
The same staging directory retains installed/before configuration, absent-file
list and deployment result. The installer restores that backup if activation
fails; this deployment passed activation and live checks.

Manual rollback uses the existing service stop, that archive's project-relative
files, and the recorded `absent-before.json` list for files newly introduced by
this deployment, then restarts the service. Do not reset the entire repository,
delete model directories or reflash the SD card.

No Git commit/push or public media upload was performed in this deployment.
