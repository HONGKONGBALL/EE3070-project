# Bluetooth and USB voice output

The existing Pi PipeWire playback supports both Bluetooth headphones such as AirPods and USB audio receivers such as HyperX. They are alternative output routes for the same English alerts; detection and alert confirmation rules do not change.

## Demo controls

Open the V2 preview (`http://127.0.0.1:8010/` on the computer while SSH forwarding is running, or port 8000 on the Pi). Expand **Headphones / audio output**:

1. For AirPods, first pair/connect them to the **Pi**, using the Pi desktop Bluetooth menu. For USB headphones, plug their receiver into the **Pi**.
2. Click **Refresh headphones**, select the connected device, enable **Voice alerts**, then **Save output**.
3. Click **Test voice** and confirm you hear the phrase in the selected headphones.
4. For another demo, connect the other device, refresh, choose it and save. To mute, uncheck **Voice alerts** and save.

Only connected PipeWire playback sinks are offered. Microphones and Dummy Output are excluded. Bluetooth pairing stays with the OS; the web page does not scan, pair or trust arbitrary Bluetooth devices. AirPods connected to the computer or phone do not automatically become the Pi's audio output.

The selected stable PipeWire node name and enabled setting are saved in the active configuration's `[audio] target` and `enabled`. Selection applies without restarting the camera and survives a service restart. Switching stops current playback and discards queued old requests; it does not replay historical alerts already handled during muted operation. The selected device must be connected again on future starts; the app does not claim automatic pairing/reconnection or physical delivery.

## Implementation and verification limits

- Device inventory uses `pw-dump` only when opening or refreshing the control panel or saving an enabled selection. Normal status refreshes and inference do not enumerate devices.
- `pw-play --target <node.name>` sends the same WAV files to the selected sink. USB and Bluetooth paths use the same playback/priority/expiry mechanism.
- Selecting a disconnected device is rejected without changing the running route or file. Configuration writes preserve detector, camera, Pico and warning settings.
- APIs: `GET /audio-outputs`; `POST /audio-output` with JSON `{ "target": "<connected node name>", "enabled": true }`; existing `POST /test-audio` requests a test phrase. These controls are served on the existing loopback-bound preview server.
- Tests cover discovery, exclusion of dummy/microphone nodes, Bluetooth-to-USB switching, configuration reload, invalid/disconnected selection, failed writes, target forwarding, queue cancellation and HTTP controls. Simulated sinks are software tests, not hardware compatibility evidence.
- The Pi has an enabled Bluetooth controller but no paired AirPods at implementation time. Physical AirPods/USB listening and Bluetooth latency still need confirmation after the devices are connected. A successful player process alone does not prove that the user heard sound.

References: [PipeWire target selection](https://pipewire.pages.freedesktop.org/pipewire/page_man_pw-cat_1.html), [Raspberry Pi audio configuration](https://www.raspberrypi.com/documentation/computers/configuration.html#change-audio-output).
