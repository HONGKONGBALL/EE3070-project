# ─── How to run ───
# Copy to the Pico as main.py using a MicroPython-capable USB editor.
# Set BUTTON_PIN to config/local.json pico.button_pin before copying.
# ──────────────────
"""Debounced physical confirmation messages over Pico USB CDC."""

import json
import machine
import select
import sys
import time

BUTTON_PIN = 14
DEBOUNCE_MS = 40
button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
boot_id = machine.unique_id().hex() + "-" + str(time.ticks_ms())
sequence = 0
last_raw = button.value()
stable_level = last_raw
changed_at = time.ticks_ms()
armed = stable_level == 1
context = None
context_deadline = 0
poller = select.poll()
poller.register(sys.stdin, select.POLLIN)
print(json.dumps({"type": "pico_ready", "boot_id": boot_id, "button_pin": BUTTON_PIN}))

while True:
    current = time.ticks_ms()
    if poller.poll(0):
        try:
            message = json.loads(sys.stdin.readline())
            if message.get("type") == "context" and 0 < message.get("ttl_ms", 0) <= 2000:
                context = message
                context_deadline = time.ticks_add(current, message["ttl_ms"])
        except (ValueError, TypeError):
            context = None
    raw = button.value()
    if raw != last_raw:
        last_raw = raw
        changed_at = current
    stable_ms = time.ticks_diff(current, changed_at)
    if raw != stable_level and stable_ms >= DEBOUNCE_MS:
        stable_level = raw
        if stable_level == 1:
            armed = True
        elif armed and context is not None and time.ticks_diff(context_deadline, current) > 0:
            armed = False
            sequence += 1
            print(json.dumps({"type": "button", "boot_id": boot_id, "sequence": sequence,
                              "pressed_at_ms": current, "stable_ms": stable_ms,
                              "session_id": context["session_id"], "generation": context["generation"],
                              "route_version": context["route_version"], "event_id": context["event_id"]}))
            sys.stdout.flush()
    time.sleep_ms(5)
