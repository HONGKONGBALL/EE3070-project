# Smart Cane local voice assistant

Hold the push-to-talk button while speaking in English, then release it to process the command. This is the default activation method when the assistant is enabled. The dashboard's **Hold to ask** control uses the same flow.

With wake listening enabled, say **“Hey ah-pa-teu”** (**헤이 아파트**). Wait for “Yes?”, then ask a visual question or give a navigation command. The wake detector uses a small Korean Vosk model with a constrained phrase and a second phonetic check on candidate clips.

Both inputs use one command worker, the existing Vosk speech recognition, and the same router. Visual questions take one recent camera frame and use local Ollama. Navigation commands call the adapter. For a new destination, wait for the confirmation prompt and say “Yes” or “No”. The microphone stream closes during the assistant's own playback. Piper generates spoken replies and the existing audio manager lets hazard warnings interrupt them.

## Local Windows acceptance

The unified fixture test starts the assistant with GPIO Zero's mock pin factory. It supplies PCM speech fixtures and a corridor photo to real Vosk, Piper and local Ollama. The test verifies that the button triggers processing after release, and exercises separate wake recordings followed by visual and navigation commands. The adapter is a mock, warnings are injected events, and a timed sink simulates speaker delivery. Each run writes the generated replies as WAV files and writes the transcription, routing calls and delivery events to `flow-results.json`.

The Windows run passed ten flow checks with loopback-only connections in the test process and Ollama's local-only mode. The left-side answer identified the directory sign. The front and surroundings answers identified the corridor and chair and missed the people at the far end. An injected stair warning interrupted scene speech, and a crowd warning interrupted the navigation confirmation before any route request. Hardware checks still cover the actual push-to-talk switch, microphone and speaker, and acoustic wake on the Pi. Route execution still needs the teammate's adapter.

The measured wake set used one synthetic voice. Five clean clips triggered at 1.6–1.9 seconds from clip start. Two positives at reduced volume or with added noise were missed. Thirteen negatives failed to trigger, including the spoken acronym, “Hey apartment,” “Hey up to you,” ordinary speech, and speech mentioning the acronym over a synthetic background tone. These are file-level results for this set. Real microphone wake recall and false activations per hour remain unmeasured.

The Windows comparison replayed the same footage and original detector configuration for about 59 seconds per run. The listening run supplied repeated negative speech at microphone cadence and preloaded both speech recognition models. Frame-to-warning time covers the supporting frame through the software warning event. The replay used software events and a separate timed sink for the interruption test.

| Measurement | Vision only | Vision with wake listening |
| --- | ---: | ---: |
| Median preview FPS | 10.8 | 10.8 |
| Median road inference FPS | 1.48 | 1.48 |
| Median whole-system CPU | 11.9% | 13.0% |
| Vision process CPU, one core = 100% | 101.6% | 110.1% |
| Vision process resident memory at the end | 1027.9 MiB | 1615.4 MiB |
| Stair warning | 328 ms | 328 ms |
| Puddle warning | 234 ms | 203 ms |
| Crowd warning | 172 ms | 156 ms |

The memory figure is a difference between two runs in one Windows process. Warning timings have a single event per category per run. These figures apply to the earlier wake-listening replay. ARM64 throughput and active AirPods audio remain hardware tests.

The Windows setup uses these paths from the repository root.

```powershell
$env:PYTHONPATH = 'D:\Ollama\voice\python-lib;D:\Ollama\python-lib;' + (Resolve-Path ..\..\ollama-pi).Path + ';' + (Resolve-Path .\pi).Path
$python = 'D:\SmartCaneRuntime\.venv\Scripts\python.exe'
$qa = 'D:\Ollama\voice\unified-qa'
# Use a WAV named ipa.wav containing the intended wake phrase in the fixture directory.
# The GPIO integration fixture uses the gpiozero package and its mock pin factory.
& $python -X utf8 -B .\tools\check_wake_local.py --models D:\Ollama\voice\models --fixtures $qa --image D:\Ollama\vlm-eval\images\T06.jpg
$samples = (Get-ChildItem -LiteralPath 'D:\Ollama\voice\wake-qa' -Filter '*.wav' | Where-Object { $_.BaseName -notmatch '-\d+$' }).FullName
& $python -X utf8 -B -m cane.vision.wake_word --models D:\Ollama\voice\models @samples
& $python -X utf8 -m pytest -q
```

Record positive examples with several speakers saying the same phrase, plus ordinary speech and near misses as negative examples. Replay WAV files through the probe before trying the feature live. Measure live false activations per hour and wake latency on the Pi before relying on hands-free use.

## Pi setup and hardware checks

Use a copy of the running camera configuration for a supervised trial. Install the dependencies in the standalone runtime's `requirements-voice.txt`. Put its Python modules on `PYTHONPATH` alongside the repository's `pi` directory. Copy the English and Korean Vosk directories, Piper voice and voice JSON from `D:\Ollama\voice\models` into the directory set by `speech_models_dir`. The local service uses the existing Ollama loopback API. Start the test as the user who owns the PipeWire audio session. The microphone uses PulseAudio-compatible capture tools.

In the trial copy of the configuration, use these assistant settings. Retain the camera, detector and audio settings from the working configuration. Use a smaller maximum age if a stale scene should be dropped earlier.

```toml
[assistant]
enabled = true
mode = "ollama"
microphone = "default"
speech_models_dir = "/home/your-user/smart-cane-ollama/voice/models"
ollama_port = 11434
wake_enabled = false
# Set ptt_gpio to the BCM number of the dedicated button when wired.
dashboard_wake_test = false
max_record_s = 8
max_scene_age_s = 12
request_timeout_s = 12
```

With the Pi powered off, connect a dedicated normally open button between a free GPIO input and GND. Set `ptt_gpio` to its BCM number and make `gpiozero` and `lgpio` available to the assistant's Python interpreter. The existing Pico protocol reports short and long gestures without a separate release event after a long press. The Pi GPIO button supplies the two edges used to start and stop recording. [GPIO Zero Button reference](https://gpiozero.readthedocs.io/en/stable/api_input.html#button), [Pi 5 pin factory](https://gpiozero.readthedocs.io/en/latest/api_pins.html#changing-the-pin-factory)

Replace the model path with the path on the Pi. In local Ollama mode, `microphone = "default"` selects an AirPods input by name. For a different microphone, set the input name or node ID from `pactl list short sources`. Verify the existing playback target with the headset connected. Open the loopback camera server's `/assistant/` dashboard to test **Hold to ask**. For a hands-free trial, set `wake_enabled = true` and test the spoken phrase. The separate `dashboard_wake_test` option exposes a simulated wake acknowledgment.

For live acceptance, record wake latency, misses, false activations per hour, and frame-to-warning delay with wake listening on and off. Test silence, repeated utterances and microphone unplug and reconnect. Test urgent and crowd warnings during an assistant answer. For an offline trial, use local hardware access or an isolated Linux network namespace that preserves loopback. Preserve an existing remote management connection.

## Navigation adapter contract

Follow the [unified Pi setup](pi-unified.md) to use the teammate's navigator. The standalone assistant uses a mock when integration is disabled. The adapter accepts a confirmed destination through `plan_route(destination)` and uses the current route state for `stop_navigation()`, `get_next_instruction()` and `get_navigation_status()`. The asynchronous navigator announces route activation after it accepts a walking route.

## Rollback and files

Remove `ptt_gpio` and restart the trial configuration to use the dashboard button. Set `wake_enabled = false` to switch off hands-free listening, or set the assistant's `enabled = false` for a vision-only session.

The integration uses `wake_word.py`, `wake_assistant.py`, `voice_commands.py` and `ptt_button.py`. `test_wake_assistant.py` covers routing and arbitration, and `tools/check_wake_local.py` exercises the combined flow.
