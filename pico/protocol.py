# Line protocol with the Pi (USB CDC). Must match pi/cane/pico_link.py.
import json


def frame(front_cm, up_cm, tf_cm, tf_strength, ms):
    return json.dumps({"t": "d", "f": front_cm, "u": up_cm, "g": tf_cm, "gs": tf_strength, "ms": ms})


def button(name, kind):
    return json.dumps({"t": "b", "id": name, "ev": kind})


def power(vsys):
    return json.dumps({"t": "p", "vsys": round(vsys, 2)})


def warning(msg):
    return json.dumps({"t": "w", "msg": msg})


def parse_cmd(line):
    """Pi -> Pico command dict, or None if the line is not a valid command."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        m = json.loads(line)
    except ValueError:
        return None
    if not isinstance(m, dict) or m.get("t") not in ("h", "v", "l", "z"):
        return None
    return m


class LineReader:
    """Accumulate characters into lines (stdin is read char by char)."""

    def __init__(self, maxlen=256):
        self.buf = ""
        self.maxlen = maxlen

    def feed(self, ch):
        if ch in ("\n", "\r"):
            line, self.buf = self.buf, ""
            return line if line else None
        self.buf += ch
        if len(self.buf) > self.maxlen:
            self.buf = ""
        return None
