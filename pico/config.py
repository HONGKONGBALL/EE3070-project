# Pico H pin map and timing. Copy all pico/*.py to the Pico root.
#
# Power: UPS 5 V -> Schottky diode -> VSYS (pin 39). USB is data only, so the
# Pico does not die with the Pi. The 5.5 V 1 F supercap sits on VSYS after the
# diode and keeps Pico + buzzer alive ~1-2 s after the 5 V rail drops.
#
# HC-SR04 at 3.3 V: check first that it ranges reliably and that Echo high
# stays <= 3.3 V. If it needs 5 V, power it from 5 V and put a divider
# (e.g. 1k/2k) on Echo.

SONAR_FRONT_TRIG = 2
SONAR_FRONT_ECHO = 3
SONAR_UP_TRIG = 4
SONAR_UP_ECHO = 5

# TFmini-S: 5 V supply, 3.3 V UART, default 115200 8N1
TFMINI_UART = 1
TFMINI_TX = 8   # Pico TX -> TFmini RX
TFMINI_RX = 9   # Pico RX <- TFmini TX
TFMINI_BAUD = 115200

# buttons to GND, internal pull-ups
BUTTONS = {"ok": 10, "dest": 11, "repeat": 12, "mode": 13}
LONG_PRESS_MS = 800
DEBOUNCE_MS = 30

# low-side MOSFETs, gate via ~100 R, 10-100 k pull-down; flyback diode on motor.
# If the MOSFET needs ~10 V gate drive (IRF520/IRFZ44N), add a transistor stage.
VIBRATION_PIN = 14
LIGHT_PIN = 15
PWM_FREQ = 1000

BUZZER_PIN = 16
BUZZER_ACTIVE_HIGH = True  # check the module: many are active-low

LED_PIN = 25  # on-board LED (Pico H)

# timing
FRAME_MS = 50                 # sensor frame to the Pi (20 Hz)
SONAR_TIMEOUT_US = 25000      # ~4 m
HEARTBEAT_TIMEOUT_MS = 3000   # no heartbeat -> local alarm
POWER_REPORT_MS = 2000
VSYS_ALARM_V = 4.2            # below this -> power-loss alarm
LOCAL_STOP_CM = 60            # Pi dead: still vibrate for very close obstacles
WDT_MS = 2000                 # Pico self-reset if the main loop hangs
