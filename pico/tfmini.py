# Benewake TFmini-S, standard 9-byte UART frame:
# 0x59 0x59 DistL DistH StrL StrH TempL TempH Checksum(low byte of sum of first 8)
# Distance in cm. Per the TFmini-S manual, readings with strength < 100 or
# strength == 65535 (overexposure) are unreliable -> reported as -1.

HEADER = 0x59
MIN_STRENGTH = 100


class TFminiParser:
    def __init__(self):
        self.buf = bytearray()
        self.bad_checksum = 0

    def feed(self, data):
        """Feed raw bytes; returns a list of (dist_cm, strength, temp_c)."""
        b = self.buf + data  # rebuild instead of del-slicing: portable to MicroPython
        out = []
        i = 0
        n = len(b)
        while n - i >= 9:
            if b[i] != HEADER or b[i + 1] != HEADER:
                i += 1
                continue
            if (sum(b[i:i + 8]) & 0xFF) != b[i + 8]:
                self.bad_checksum += 1
                i += 1
                continue
            dist = b[i + 2] | (b[i + 3] << 8)
            strength = b[i + 4] | (b[i + 5] << 8)
            temp = (b[i + 6] | (b[i + 7] << 8)) / 8 - 256
            if strength < MIN_STRENGTH or strength == 65535 or dist in (0, 65535, 65534):
                dist = -1
            out.append((dist, strength, temp))
            i += 9
        rest = b[i:]
        if len(rest) > 64:  # never grow without bound on a noisy line
            rest = rest[-9:]
        self.buf = bytearray(rest)
        return out


def make_frame(dist, strength, temp_raw=0):
    """Build a valid frame (for tests)."""
    f = bytearray([HEADER, HEADER, dist & 0xFF, dist >> 8, strength & 0xFF, strength >> 8,
                   temp_raw & 0xFF, temp_raw >> 8])
    f.append(sum(f) & 0xFF)
    return bytes(f)


class TFmini:
    def __init__(self, uart_id, tx, rx, baud=115200):
        from machine import UART, Pin

        self.uart = UART(uart_id, baudrate=baud, tx=Pin(tx), rx=Pin(rx))
        self.parser = TFminiParser()
        self.dist = -1
        self.strength = 0

    def poll(self):
        n = self.uart.any()
        if n:
            frames = self.parser.feed(self.uart.read(n))
            if frames:
                self.dist, self.strength, _ = frames[-1]
        return self.dist, self.strength
