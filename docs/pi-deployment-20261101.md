# Pi deployment and warning behavior — 1 November 2026

The hazard-episode update is installed in `~/EE3070-project` on the Pi and running under the existing `smart-cane-camera` user service. Nine runtime Python files and 15 English WAV files were installed and SHA256-verified. Existing model binaries, camera settings, Pico distance behavior and the Pi's tuned inference rates were preserved. The cancelled sidewalk/road-entry experiment has been removed from the source and is absent from the Pi runtime/configuration.

## Current warning behavior

All ground alerts require admission to the configured front image region, class-specific scene checks and multiple independent valid model observations. Drawing a candidate box does not itself mean an alert was accepted or played.

| Evidence | Spoken phrase |
|---|---|
| Confirmed possible puddle/wet surface | `Possible wet pavement ahead.` |
| Confirmed pavement damage or weaker pothole evidence | `Possible damaged pavement ahead.` |
| Pothole with at least two confirming observations meeting confidence >=0.75 and box area >=1% of the image | `Possible pothole ahead.` |
| Confirmed generic stairs | `Possible stairs ahead.` |

Potholes and pavement damage retain separate detectors but share a bounded warning family. A stronger pothole observation sequence may upgrade a previously announced damage warning. Score and image area do not measure physical depth, distance or risk. Generic stairs remains the enabled default; merely shipping an optional steps-down clip does not enable up/down classification.

Puddle, damage and generic stairs confirmation use two positives among up to three valid observations in 3.2 seconds, spanning at least 0.3 seconds. Potholes use two of two. Missing or unusable images do not count as independent confirmation or reliable clear observations.

After a successful playback, a continuous episode is not announced again on a fixed eight-second timer. Rearming needs reliable negative observations: wet surface six observations spanning five seconds, damage three spanning 1.5 seconds, stairs four spanning three seconds. An unobserved timeout also resets identity: ten seconds for wet surface, six for damage/stairs. A later alert after such a gap is possible; this is temporal grouping, not proof that the physical hazard is new.

Normal safety prompt starts are spaced by at least 1.5 seconds. The speech queue holds at most two pending requests, urgent stairs/strong-pothole alerts have priority, and stale ground requests expire after 1.5 seconds. Failed/cancelled delivery can retry only with fresh evidence. Playback disabled is recorded as disabled rather than heard.

## Pi state and actual checks

- Mode: `ground`, live C270 camera, no Pico distance input. `balanced` remains available but is not enabled by this deployment.
- Camera target 15 FPS; preview target 12 FPS; road/pothole/puddle capped at 1 Hz; semantic/stair context capped at 1.5 Hz with two threads, preserving the existing Pi tuning.
- Live check: 32.16 seconds, 16 status/JPEG samples, source frame number progressed 430 to 878, preview median 11.8 FPS. Road/pothole/puddle approximately 1 Hz, semantic approximately 1.34 Hz. All active model tasks reported no errors.
- Service active/running, automatic restarts zero. End temperature 60.9°C, `get_throttled=0x0`. This short run does not establish long-term thermal performance.
- Local regression immediately before deployment: **279 passed**, one existing dependency deprecation warning, 61.32 seconds.
- Audio remains **disabled**, matching the previous user-selected video-only setup. PipeWire currently sees Dummy Output and no actual headphone sink. Correct WAV files and warning rules are installed, but physical audio and approach-to-sound latency were not tested.
- The camera is not aimed at walking ground during this check. It verifies deployment and the live capture/model/preview chain, not real hazard accuracy.

## Use and rollback

On the Pi:

```sh
systemctl --user restart smart-cane-camera
systemctl --user status smart-cane-camera --no-pager
```

Pi preview: `http://127.0.0.1:8000/`. On the development computer, while its SSH forwarding process is alive, preview is `http://127.0.0.1:8010/`.

Before installation the overwritten source tree, configuration and audio were archived under `~/.smart-cane-deploy/20261101/before-episodes-20261031T170553Z.tar.gz` (the filename timestamp is UTC). Deployment, config hash and live check records are retained in the same directory. Windows records are in `D:/SmartCaneRuntime/deploy/pi-20261101/`.

To restore the previous runtime, stop the service, extract that archive into `~/EE3070-project`, remove the newly added `pi/cane/vision/episodes.py` if absent from the old release, and restart the service. Model weights are not affected by this update.

The model and audio binaries remain outside Git. Models use the existing preparation workflow. Audio generation is in `tools/gen_camera_prompts.ps1`; current release hashes/text are in `config/hazard-episode-artifacts.json`. The older `config/vision-runtime-artifacts.json` retains the model baseline and original audio package provenance; the newer audio manifest takes precedence for the changed clips.
