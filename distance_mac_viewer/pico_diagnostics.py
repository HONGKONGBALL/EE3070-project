"""RAM-only, bounded Pico component tests; prepend before distance_pico_stream.py."""


class BoardDiagnostics:
    """Own fixed outputs, command framing, deadlines, and debounced button events."""

    DURATIONS = {'motor': 5000, 'buzzer': 5000, 'cob': 3000,
                 'led': 3000, 'buttons': 45000, 'stop': 0}

    def __init__(self, machine, clock, stdin=None, poller=None, emit=print):
        import json
        if stdin is None:
            import sys
            stdin = sys.stdin
        if poller is None:
            import select
            poller = select.poll()
        self.machine, self.clock = machine, clock
        self.stdin, self.poller, self.emit, self.json = stdin, poller, emit, json
        self.pins = {n: machine.Pin(n, machine.Pin.OUT, value=0) for n in (14, 15, 16, 25)}
        self.pwm, self.active, self.buttons = None, None, []
        self.cutoff, self._pwm_zero = 0, None
        self._motor, self._cob = self.pins[14].value, self.pins[15].value
        self._buzzer, self._led = self.pins[16].value, self.pins[25].value
        self.buffer, self.discard = '', False
        self.ready_sent = False
        self.last_ping = clock.ticks_ms()
        poller.register(stdin, 1)
        self.timer = machine.Timer(period=50, mode=machine.Timer.PERIODIC,
                                   callback=self._cutoff_irq, hard=True)

    def _event(self, request_id, target, state, duration=0, reason=None):
        data = {'id': request_id, 'target': target, 'state': state, 'duration_ms': duration}
        if reason:
            data['reason'] = reason
        self.emit('HW_DATA,' + self.json.dumps(data))

    def _off(self):
        for gpio in (14, 15, 25):
            self.pins[gpio].value(0)
        if self.pwm is not None:
            pwm, self.pwm = self.pwm, None
            try:
                pwm.duty_u16(0)
            finally:
                try:
                    pwm.deinit()
                finally:
                    self.pins[16] = self.machine.Pin(16, self.machine.Pin.OUT, value=0)
                    self._buzzer = self.pins[16].value
                    self._pwm_zero = None
        else:
            self.pins[16].value(0)

    def _finish(self, state):
        active, self.active = self.active, None
        self.cutoff = 0
        self._off()
        self.buttons = []
        if active is not None:
            self._event(active[0], active[1], state, active[2])

    def close(self):
        """Always disable outputs; called from acquisition's finally path."""
        self.timer.deinit()
        self._finish('stopped')

    def _cutoff_irq(self, timer):
        """Hard IRQ: no allocation, JSON, printing, PWM deinit, or container writes."""
        active = self.active
        if active is None or self.cutoff:
            return
        now = self.clock.ticks_ms()
        if self.clock.ticks_diff(now, active[3]) >= active[2]:
            self.cutoff = 1
        elif self.clock.ticks_diff(now, self.last_ping) >= 2500:
            self.cutoff = 2
        if self.cutoff:
            self._motor(0)
            self._cob(0)
            self._led(0)
            if self._pwm_zero is not None:
                self._pwm_zero(0)
            self._buzzer(0)
        elif active[1] == 'led':
            elapsed = self.clock.ticks_diff(now, active[3])
            self._led(1 if (elapsed // 500) % 2 == 0 else 0)

    def _command(self, line):
        now = self.clock.ticks_ms()
        if line == 'HW_PING':
            self.last_ping = now
            return
        fields = line.split(',')
        if len(fields) != 4 or fields[0] != 'HW_CMD':
            return
        try:
            request_id, duration = int(fields[1]), int(fields[3])
        except ValueError:
            return
        if not 0 < request_id <= 1000000000:
            return
        target = fields[2]
        if target not in self.DURATIONS:
            self._event(request_id, 'stop', 'error', reason='invalid_command')
            return
        if duration != self.DURATIONS[target]:
            self._event(request_id, target, 'error', reason='invalid_duration')
            return
        if target == 'stop':
            self._finish('stopped')
            self._event(request_id, target, 'done')
            return
        if self.active is not None:
            self._event(request_id, target, 'error', reason='busy')
            return
        self._off()
        self.last_ping = now
        if target == 'buzzer':
            self.pwm = self.machine.PWM(self.pins[16], freq=3000, duty_u16=0)
            self._pwm_zero = self.pwm.duty_u16
            self.pwm.duty_u16(32768)
        elif target in ('motor', 'cob', 'led'):
            self.pins[{'motor': 14, 'cob': 15, 'led': 25}[target]].value(1)
        elif target == 'buttons':
            for gpio in (10, 11, 12, 13):
                pin = self.machine.Pin(gpio, self.machine.Pin.IN, self.machine.Pin.PULL_UP)
                level = pin.value()
                self.buttons.append([pin, level, 1, now, None, False])
        self.active = (request_id, target, duration, now)
        self._event(request_id, target, 'started', duration)

    def _input(self):
        for unused in range(128):
            if not self.poller.poll(0):
                return
            char = self.stdin.read(1)
            if not char:
                return
            if char == '\n':
                if not self.discard:
                    self._command(self.buffer.rstrip('\r'))
                self.buffer, self.discard = '', False
            elif not self.discard:
                if len(self.buffer) == 128:
                    self.buffer, self.discard = '', True
                else:
                    self.buffer += char

    def _button_event(self, index, state, duration):
        self.emit('HW_BUTTON,' + self.json.dumps({'id': self.active[0], 'index': index,
                                                'state': state, 'duration_ms': duration}))

    def _poll_buttons(self, now):
        diff = self.clock.ticks_diff
        for index, button in enumerate(self.buttons, 1):
            pin, raw, stable, changed, pressed, long_sent = button
            level = pin.value()
            if level != raw:
                raw, changed = level, now
            if raw != stable and diff(now, changed) >= 30:
                stable = raw
                if not stable:
                    pressed, long_sent = now, False
                    self._button_event(index, 'pressed', 0)
                elif pressed is not None:
                    self._button_event(index, 'released', diff(now, pressed))
                    pressed = None
            if not stable and pressed is not None and not long_sent and diff(now, pressed) >= 800:
                long_sent = True
                self._button_event(index, 'long', diff(now, pressed))
            button[1:] = [raw, stable, changed, pressed, long_sent]

    def _service(self):
        if self.active is None:
            return
        self._cutoff_irq(None)
        if self.cutoff:
            self._finish('done' if self.cutoff == 1 else 'link_lost')
            return
        if self.active[1] == 'buttons':
            self._poll_buttons(self.clock.ticks_ms())

    def poll(self):
        """Do bounded nonblocking input work and service deferred IRQ outcomes."""
        if not self.ready_sent:
            self.ready_sent = True
            self.emit('HW_READY,1')
        self._service()
        self._input()
        self._service()
