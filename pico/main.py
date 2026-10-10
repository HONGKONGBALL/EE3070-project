# EE3070 smart cane - Pico H main loop (MicroPython).
#
# - 20 Hz sensor frames to the Pi (front/up HC-SR04, TFmini-S)
# - buttons -> events
# - executes vibration / light / buzzer commands from the Pi
# - heartbeat watchdog: no heartbeat for 3 s -> buzzer alarm + vibration, and a
#   local fallback that still vibrates for very close obstacles
# - VSYS monitor: 5 V rail lost (running on the supercap) -> power-loss tone
import sys
import time

import select
from machine import ADC, WDT, Pin

import config as C
import protocol
from buttons import Buttons
from hcsr04 import HCSR04
from outputs import BUZZER, VIBRATION, DigitalOut, PatternPlayer, PwmOut
from tfmini import TFmini


def send(line):
    sys.stdout.write(line + "\n")


def read_vsys(adc):
    # Pico (non-W): GPIO29 = VSYS / 3
    return adc.read_u16() * 3 * 3.3 / 65535


def main():
    led = Pin(C.LED_PIN, Pin.OUT)
    front = HCSR04(C.SONAR_FRONT_TRIG, C.SONAR_FRONT_ECHO, C.SONAR_TIMEOUT_US)
    up = HCSR04(C.SONAR_UP_TRIG, C.SONAR_UP_ECHO, C.SONAR_TIMEOUT_US)
    tf = TFmini(C.TFMINI_UART, C.TFMINI_TX, C.TFMINI_RX, C.TFMINI_BAUD)
    buttons = Buttons(C.BUTTONS, C.LONG_PRESS_MS, C.DEBOUNCE_MS)
    vib = PwmOut(C.VIBRATION_PIN, C.PWM_FREQ)
    light = PwmOut(C.LIGHT_PIN, C.PWM_FREQ)
    buzzer = DigitalOut(C.BUZZER_PIN, C.BUZZER_ACTIVE_HIGH)
    vsys_adc = ADC(29)

    vib_p = PatternPlayer()
    buz_p = PatternPlayer()
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    reader = protocol.LineReader()

    now = time.ticks_ms()
    last_hb = now
    last_frame = now
    last_power = now
    hb_lost = False
    power_alarm = False
    front_cm = up_cm = -1
    use_front = True
    wdt = WDT(timeout=C.WDT_MS)

    while True:
        wdt.feed()
        now = time.ticks_ms()

        # ---- commands from the Pi
        while poll.poll(0):
            ch = sys.stdin.read(1)
            if not ch:
                break
            line = reader.feed(ch)
            if line is None:
                continue
            cmd = protocol.parse_cmd(line)
            if cmd is None:
                continue
            t = cmd["t"]
            if t == "h":
                last_hb = now
                if hb_lost:
                    hb_lost = False
                    buz_p.play([], 0, now)
            elif t == "v" and cmd.get("p") in VIBRATION:
                pulses, duty = VIBRATION[cmd["p"]]
                vib_p.play(pulses, duty, now)
            elif t == "l":
                light.set(float(cmd.get("v", 0)))
            elif t == "z" and cmd.get("p") in BUZZER:
                buz_p.play(BUZZER[cmd["p"]], 1.0, now)

        # ---- sensors: alternate the two sonars so they don't hear each other
        tf_cm, tf_str = tf.poll()
        if time.ticks_diff(now, last_frame) >= C.FRAME_MS:
            last_frame = now
            if use_front:
                front_cm = front.distance_cm()
            else:
                up_cm = up.distance_cm()
            use_front = not use_front
            send(protocol.frame(front_cm, up_cm, tf_cm, tf_str, now))
            led.toggle()

        for name, kind in buttons.poll(now):
            send(protocol.button(name, kind))

        # ---- heartbeat watchdog
        if time.ticks_diff(now, last_hb) > C.HEARTBEAT_TIMEOUT_MS:
            if not hb_lost:
                hb_lost = True
                send(protocol.warning("heartbeat lost"))
            if not buz_p.active(now):
                buz_p.play(BUZZER["alarm"], 1.0, now)
            # local fallback: the Pi cannot speak, so at least vibrate for very close obstacles
            if 0 < front_cm <= C.LOCAL_STOP_CM and not vib_p.active(now):
                pulses, duty = VIBRATION["stop"]
                vib_p.play(pulses, duty, now)

        # ---- power monitor
        if time.ticks_diff(now, last_power) >= C.POWER_REPORT_MS:
            last_power = now
            v = read_vsys(vsys_adc)
            send(protocol.power(v))
            if v < C.VSYS_ALARM_V and not power_alarm:
                power_alarm = True
                buz_p.play(BUZZER["power"], 1.0, now)
            elif v >= C.VSYS_ALARM_V + 0.2:
                power_alarm = False

        vib.set(vib_p.level(now))
        buzzer.set(buz_p.level(now))
        time.sleep_ms(5)


main()
