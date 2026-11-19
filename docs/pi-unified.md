# Raspberry Pi vision and navigation

The V2 camera service is the integrated entry point. In awareness mode it serves one camera preview, Pico range data, crowd detection, ground-hazard warnings and the English voice assistant. It uses the Pi 5 code from the team's `codex/pi5-iphone-nearby` branch for iPhone location, place selection and walking navigation.

## Configure the Pi

1. Prepare the existing [local Ollama speech runtime](hey-apt.md#pi-setup-and-hardware-checks) and the team's [iPhone pairing](pi5-iphone-navigation.md#配对并联调). Provide the existing `AMAP_KEY` through the shell or service environment. Add and verify any named destination presets in `config/config.toml`.
2. Edit a copy of `config/camera.v2.toml`. Set `[assistant]` to `enabled = true` and `mode = "ollama"`. Set `ptt_gpio` to the BCM input for the push-to-talk switch. Leave `wake_enabled = false` for button use, or set it to `true` for the optional “Hey apateu” wake phrase.
3. Set `[distance].mode = "pico"` and point its `port` at the Pico serial link. In the navigation config, set the TFmini role to match the physical mount. Set `[navigation].enabled = true` in the camera config, with `config = "config/config.toml"` and `phone_pair = "certs/pi-phone"`. The integrated process owns the serial link. Use it as the only running cane service.
4. Set `[audio].dir = "audio/navigation"` in `config/config.toml` and generate the English navigation catalog with `python tools/gen_prompts.py --backend espeak --lang en --config config/config.toml --out audio/navigation`. In the camera config, select the AirPods or USB receiver as the output. The existing vision clips use the camera config's audio directory.

Launch from the repository root after adding the speech runtime modules to `PYTHONPATH`.

```bash
python -m cane.vision.v2 --config config/camera.unified.toml --mode awareness
```

Open `/assistant/` on the local camera server. The camera and distance dashboard shows route availability and activity. Use the phone page from the generated pairing link to share location.

## Use the voice assistant

- Hold the button, speak in English and release. Visual questions use the latest shared camera frame with local Ollama.
- Say “Navigate to CityU Library” and answer “Yes” at the confirmation prompt. An exact configured destination or alias can start route planning directly. For other names, choose a matching place on the iPhone and confirm it using Pico K1.
- Ask for the next turn or navigation status, or say “Stop navigation”. These commands read or change the existing route state locally. The navigator holds directional instructions when alignment is unverified and stops the route if location expires.
- Navigation prompts, assistant answers and hazard alerts share the same output queue. Hazard alerts take priority over navigation speech. The integrated controller uses the existing Pico command protocol for vibration.

## Local flow test

With Ollama serving locally, run `tools/check_pi_unified.py` from the repository root with paths to the vision models, speech models, a corridor photo and a replay video. Use `--help` for the arguments. It starts the normal V2 service, serves the actual dashboard over loopback, runs the existing vision and speech models, and exercises the teammate's route parser and navigator against an HTTP map fixture. Recorded sensor frames, simulated GPIO, a timed audio sink and the in-process phone API exercise the hardware boundaries on Windows. Its `flow-results.json` records the responses, route requests, warning delivery, model rates and serial-link ownership.

Live acceptance covers the real AMap credentials and route geometry, Safari location fixes, physical switch and sensor behavior, and AirPods or USB audio timing on the Pi.
