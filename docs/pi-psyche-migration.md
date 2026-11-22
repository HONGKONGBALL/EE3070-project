# Pi cloud vision migration

## Runtime and wiring

The V2 service owns the camera, microphone and audio queue. A K4 tap plays a local cue before recording. The next tap ends recording and sends a single fresh JPEG to Psyche with the question. YOLO continues during the cloud request and the local hazard recordings retain priority.

K2 uses the same microphone worker. Its transcription goes to the existing navigation destination parser. K1 confirms the destination, K2 long stops navigation, and K3 short replays the current instruction. In cloud mode the dashboard Hold to ask control and K4 ask visual questions. K4 cannot submit destinations or change a route. AMap remains the navigation provider and uses its existing AMAP_KEY.

GP13 is the existing light/test-mode button. Set VISION_BUTTON_PIN in pico/config.py only after confirming a spare GPIO on the actual wiring. The new switch is normally open with the other leg connected to GND. The firmware ignores attempts to reuse pins allocated to other functions.

Vosk and Piper remain active. Authenticated model listing, image verification, speech capability and audible playback have to be tested separately. Only migrate cloud speech once the corresponding evidence is available.

On 2026-11-20 Windows verified the gateway model ID grok-4.7 with a text-only request and two controlled JPEGs. The images produced different correct color answers. Use the model ID verified for the account being deployed. The gateway docs available during this check did not establish audio transcription or speech synthesis support.

## Prepare a Pi release

Copy the reviewed source into a separate directory at $HOME/.smart-cane-deploy/psyche/release. Keep the previous release, working model assets and service overrides.

~~~sh
cd "$HOME/.smart-cane-deploy/psyche/release"
core="$HOME/EE3070-project/.venv/bin/python"
"$core" -m pip install -e '.[camera-v2,assistant,iphone]'
install -d -m 700 "$HOME/.config/smart-cane"
test -f "$HOME/.config/smart-cane/psyche.env" ||
  install -m 600 .env.example "$HOME/.config/smart-cane/psyche.env"
nano "$HOME/.config/smart-cane/psyche.env"
~~~

Set PSYCHE_API_KEY and PSYCHE_VISION_MODEL in the private file after the model is verified. The gateway address in the example is https://psycheliu.icu/v1. Leave optional audio model variables unset until the gateway documents the candidate endpoints and supported models. Continue providing AMAP_KEY through the existing navigation environment.

Copy the working camera TOML to config/camera.unified.toml inside the new release. Keep the camera, detection, audio, Pico and phone pairing settings. Set these assistant values.

~~~toml
[assistant]
enabled = true
mode = "psyche_grok"
wake_enabled = false
dashboard_wake_test = false
demo_exclusive_vision = false
request_timeout_s = 8
max_scene_age_s = 12
# Optional local daily cap. Omit it when the gateway key already enforces limits.
# daily_request_budget = 50
# Acceptance only. Keep these values across capability probes and live tests.
request_budget_run_id = "pi-acceptance"
request_budget_run_limit = 25
request_budget_path = "logs/psyche-requests.sqlite"
~~~

Keep speech_models_dir pointed at the existing speech assets. Use pactl list short sources to identify the exact USB microphone endpoint if used. Connect the AirPods or USB playback device. Preserve the current iPhone pairing and Pico configuration when enabling navigation. [Pi navigation setup](pi-unified.md)

For the service, back up the previous overrides outside the drop-in directory. If the earlier 50-unified-b135b55.conf pulls in smart-cane-ollama.service, archive that drop-in and retain any audio-tool PATH it supplied. Set up 80-psyche-cloud.conf under the existing camera service's drop-in directory.

~~~ini
[Service]
WorkingDirectory=%h/.smart-cane-deploy/psyche/release
EnvironmentFile=%h/.config/smart-cane/psyche.env
Environment=PYTHONPATH=%h/.smart-cane-deploy/psyche/release/pi
ExecStart=
ExecStart=%h/EE3070-project/.venv/bin/python -m cane.vision.v2 --config config/camera.unified.toml --mode awareness
~~~

~~~sh
systemctl --user daemon-reload
systemctl --user restart smart-cane-camera.service
curl -fsS http://127.0.0.1:8000/assistant/status
systemctl --user show smart-cane-camera.service -p Wants
systemctl --user status smart-cane-ollama.service
~~~

Verify that the assistant reports psyche_grok. Stop the separate Ollama service for performance measurement only after checking that it belongs to this project and is idle. Keep the service files and model for rollback.

## Capability and bench checks

Use controlled frames and consenting participants. Keep the same acceptance run ID and database. Each admitted Psyche request consumes one slot before transmission. Acceptance mode applies the same counter to AMap requests from the integrated navigator. The database stores counts.

If verification starts on Windows, copy the existing acceptance SQLite file to logs/psyche-requests.sqlite on the Pi before starting its first acceptance request. Continue using the same run ID so the remaining allowance carries over.

~~~sh
set -a
. "$HOME/.config/smart-cane/psyche.env"
set +a
"$core" -m cane.vision.psyche_probe --live models
"$core" -m cane.vision.psyche_probe --live text
"$core" -m cane.vision.psyche_probe --live vision captures/chair.jpg captures/box.jpg --question "What is in front of me?" --expect chair --compare-expect box
~~~

The vision probe requires distinct observations for two JPEGs under the same question. Run the two audio probes only after confirming the gateway documents matching audio endpoints and supports the configured model and voice.

~~~sh
"$core" -m cane.vision.psyche_probe --live --audio-endpoints-verified asr captures/known.wav --expect "What is in front of me?"
"$core" -m cane.vision.psyche_probe --live --audio-endpoints-verified tts --voice "$VERIFIED_TTS_VOICE"
~~~

Verify audible TTS with the intended device in a supervised bench check. Keep images, recordings and any synthesized sample under the ignored captures directory.

| Hardware case | Evidence |
| --- | --- |
| K4 front view | Physical taps, transcript, scene and answer, ASR/API/TTS times and audible onset |
| K4 left and right | Chair on the left and box on the right. Compare the answer against the current image |
| K4 during navigation | Start a route with K2, iPhone and K1. Check GPS and route are preserved and K3 repeats without rerouting |
| Detector baseline and five K4 questions | System CPU, RAM, preview FPS, each detector frequency, temperature, throttling and outcomes |
| Warning interruption and failure cases | Inject an approved local test alert while speech plays. Verify that network failure leaves local warnings, Pico and the camera operational |

## Rollback

Stop the cloud camera service, archive its new override and restore the saved drop-ins. Reload systemd, start the existing Ollama service if the restored profile uses it and restart the camera service. Verify /assistant/status and /status. Keep the previous Pico firmware when configuring the optional vision button.
