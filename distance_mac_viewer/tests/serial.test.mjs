import assert from 'node:assert/strict';
import test from 'node:test';
import { PicoSerial } from '../serial.js';

const encoder = new TextEncoder();
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

class PicoWire {
  constructor(onWrite = () => {}) {
    this.onWrite = onWrite;
    this.writes = [];
    this.opens = 0;
    this.closes = 0;
  }
  async open(options) {
    this.options = options;
    this.opens += 1;
    this.readable = new ReadableStream({
      start: (controller) => { this.input = controller; },
      cancel: () => { this.cancelled = true; },
    });
    this.writable = new WritableStream({
      write: async (bytes) => {
        const copy = bytes.slice();
        this.writes.push(copy);
        await this.onWrite(new TextDecoder().decode(copy), this);
      },
    });
  }
  send(text) { this.input.enqueue(encoder.encode(text)); }
  async close() {
    assert.equal(this.readable.locked, false, 'read lock released before close');
    assert.equal(this.writable.locked, false, 'write lock released before close');
    this.closes += 1;
  }
  get output() {
    const decoder = new TextDecoder();
    return this.writes.map((bytes) => decoder.decode(bytes, { stream: true })).join('') + decoder.decode();
  }
}

function fixture(t, port, requestPort = async () => port, source = 'print("hello")\n') {
  const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const previousFetch = globalThis.fetch;
  const states = [];
  const errors = [];
  const text = [];
  const ended = deferred();
  const requests = [];
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true, value: { serial: { requestPort } },
  });
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return { ok: true, text: async () => source };
  };
  const serial = new PicoSerial({
    onText: (chunk) => text.push(chunk),
    onState: (state) => { states.push(state); if (state === 'disconnected') ended.resolve(); },
    onError: (message) => errors.push(message),
  });
  t.after(async () => {
    await serial.disconnect();
    Object.defineProperty(globalThis, 'navigator', previousNavigator);
    globalThis.fetch = previousFetch;
  });
  return { serial, states, errors, text, ended, requests };
}

test('permission cancellation restores idle without writing', async (t) => {
  // Given a chooser cancelled by the user.
  const port = new PicoWire();
  let requested = false;
  const { serial, states, errors } = fixture(t, port, () => {
    requested = true;
    return Promise.reject(new DOMException('cancelled', 'NotFoundError'));
  });
  // When connect is called from the click path.
  const connecting = serial.connect();
  assert.equal(requested, true, 'requestPort must run before connect yields');
  await connecting;
  // Then the controls recover and no device is opened.
  assert.equal(states.at(-1), 'idle');
  assert.deepEqual(errors, []);
  assert.equal(port.opens, 0);
  assert.equal(port.output, '');
});

for (const [channel, frame] of [
  ['US-only', 'US_DATA,1,1234,1020,2030'], ['TF-only', 'TF_DATA,1234000,-1,89,400'],
]) test(`upload succeeds with fragmented acknowledgement and ${channel} telemetry`, async (t) => {
  // Given a Pico that replies in fragmented USB reads.
  const source = `# ${'測'.repeat(90)}\nprint("US_DATA,example")\n`;
  const port = new PicoWire((value, wire) => {
    if (value === '\x05') {
      wire.send('paste mode; Ctrl-C to can');
      queueMicrotask(() => wire.send('cel, Ctrl-D to finish\r\n=== '));
    }
    if (value === '\x04') {
      wire.send(`\r\n${frame.slice(0, 5)}`);
      queueMicrotask(() => wire.send(`${frame.slice(5)}\r\n`));
    }
  });
  const { serial, states, errors, text, requests } = fixture(t, port, undefined, source);
  // When the RAM upload is connected.
  await serial.connect();
  // Then the actual firmware bytes are paced in at most 64-byte chunks.
  assert.deepEqual(port.options, { baudRate: 115200 });
  assert.equal(requests[0].url, '/distance_pico_stream.py');
  assert.equal(states.at(-1), 'receiving');
  assert.deepEqual(errors, []);
  assert.ok(text.join('').includes(frame));
  assert.equal(port.output, `\x03\x03\x05${source}\x04`);
  assert.ok(port.writes.every((bytes) => bytes.byteLength <= 64));
});

test('listen-only waits for a complete valid measurement and disconnect writes no bytes', async (t) => {
  // Given a Pico already running its own program.
  const port = new PicoWire();
  const { serial, states, requests, text } = fixture(t, port);
  // When the user listens, receives data, and disconnects.
  await serial.connect({ upload: false });
  port.send('US_DATA,not,a,valid,line\r\nTF_DATA,1500000,10000,45,200');
  await new Promise(setImmediate);
  assert.equal(states.at(-1), 'connected', 'a prefix or malformed line must not mark the connection ready');
  port.send('\r\n');
  await new Promise(setImmediate);
  assert.equal(states.at(-1), 'receiving');
  await serial.disconnect();
  // Then neither upload controls nor a stop command reached the Pico.
  assert.equal(port.output, '');
  assert.deepEqual(requests, []);
  assert.match(text.join(''), /TF_DATA,1500000,10000,45,200/);
  assert.equal(port.closes, 1);
});

test('unexpected read end reports an error and releases both locks', async (t) => {
  // Given an established listen-only connection.
  const port = new PicoWire();
  const { serial, errors, ended, states } = fixture(t, port);
  await serial.connect({ upload: false });
  // When the USB input stream ends unexpectedly.
  port.input.close();
  await ended.promise;
  // Then cleanup runs once and the reconnect button can recover.
  assert.equal(states.at(-1), 'disconnected');
  assert.equal(errors.length, 1);
  assert.equal(port.closes, 1);
  assert.equal(port.output, '');
});

test('disconnect interrupts upload wait and permits a clean reconnect', async (t) => {
  // Given a Pico that has not acknowledged paste mode yet.
  const pasteRequested = deferred();
  const port = new PicoWire((value) => { if (value === '\x05') pasteRequested.resolve(); });
  const { serial, errors } = fixture(t, port);
  const connecting = serial.connect();
  await pasteRequested.promise;
  // When upload is cancelled, then the same adapter is connected again.
  await serial.disconnect();
  await connecting;
  await serial.connect({ upload: false });
  // Then the first session stopped, all old locks cleared, and no upload resumed.
  assert.equal(port.output, '\x03\x03\x05\x03');
  assert.equal(port.opens, 2);
  assert.equal(port.closes, 1);
  assert.deepEqual(errors, []);
  await serial.disconnect();
  assert.equal(port.closes, 2);
});

test('a pending chooser cannot open a stale session after disconnect', async (t) => {
  // Given a permission chooser still awaiting selection.
  const choice = deferred();
  const port = new PicoWire();
  const { serial, states } = fixture(t, port, () => choice.promise);
  const connecting = serial.connect();
  // When disconnect happens before the user chooses the device.
  await serial.disconnect();
  choice.resolve(port);
  await connecting;
  // Then the late choice never opens or writes to the port.
  assert.equal(port.opens, 0);
  assert.equal(port.output, '');
  assert.equal(states.at(-1), 'disconnected');
});

test('a Pico traceback fails upload and tears down the serial resources', async (t) => {
  // Given a Pico that acknowledges paste mode but fails at runtime.
  const port = new PicoWire((value, wire) => {
    if (value === '\x05') wire.send('paste mode; Ctrl-C to cancel, Ctrl-D to finish\r\n=== ');
    if (value === '\x04') wire.send('\r\nTraceback (most recent call last):\r\nValueError: bad pin\r\n');
  });
  const { serial, errors, states } = fixture(t, port);
  // When the firmware is run in RAM.
  await serial.connect();
  // Then the failure is visible and the port is reusable.
  assert.equal(errors.length, 1);
  assert.match(errors[0], /Traceback|Pico/);
  assert.equal(states.at(-1), 'disconnected');
  assert.equal(port.closes, 1);
});

test('duplicate connect clicks create only one permission request', async (t) => {
  // Given an unresolved first permission chooser.
  const choice = deferred();
  const port = new PicoWire();
  let requests = 0;
  const { serial } = fixture(t, port, () => { requests += 1; return choice.promise; });
  const first = serial.connect({ upload: false });
  // When connect is invoked again before the chooser completes.
  await serial.connect({ upload: false });
  choice.resolve(port);
  await first;
  // Then only one device opening occurs.
  assert.equal(requests, 1);
  assert.equal(port.opens, 1);
});

test('disconnect during open closes the port after open settles', async (t) => {
  // Given a USB port whose open call is still pending.
  const opening = deferred();
  const permitOpen = deferred();
  const port = new PicoWire();
  const open = port.open.bind(port);
  port.open = async (options) => { opening.resolve(); await permitOpen.promise; await open(options); };
  const { serial, errors } = fixture(t, port);
  const connecting = serial.connect({ upload: false });
  await opening.promise;
  // When disconnect races with completion of open.
  const closing = serial.disconnect();
  permitOpen.resolve();
  await Promise.all([connecting, closing]);
  // Then no reader or writer survives the late open.
  assert.equal(port.closes, 1);
  assert.equal(port.output, '');
  assert.deepEqual(errors, []);
});

test('disconnect aborts a stuck firmware write and releases locks without waiting forever', { timeout: 2000 }, async (t) => {
  // Given an in-flight firmware transfer.
  const writing = deferred();
  const permitWrite = deferred();
  const port = new PicoWire(async (value, wire) => {
    if (value === '\x05') wire.send('paste mode; Ctrl-C to cancel, Ctrl-D to finish\r\n=== ');
    if (value.startsWith('#')) { writing.resolve(); await permitWrite.promise; }
  });
  const { serial, errors } = fixture(t, port, undefined, `#${'x'.repeat(200)}\n`);
  const connecting = serial.connect();
  await writing.promise;
  // When disconnect is requested before the pending write finishes.
  const closing = serial.disconnect();
  await Promise.all([connecting, closing]);
  // Then only the in-flight block was sent and the queued stop is aborted.
  assert.equal(port.output, `\x03\x03\x05#${'x'.repeat(63)}`);
  assert.equal(port.closes, 1);
  assert.deepEqual(errors, []);
  permitWrite.resolve();
  await port.writable.abort();
  assert.equal(port.output.includes('\x04'), false);
});

test('writer acquisition failure releases the reader acquired during setup', async (t) => {
  // Given a port that opens but refuses its output writer.
  const port = new PicoWire();
  const open = port.open.bind(port);
  port.open = async (options) => {
    await open(options);
    port.writable.getWriter = () => { throw new Error('writer unavailable'); };
  };
  const { serial, errors, states } = fixture(t, port);
  // When connect fails after acquiring the input reader.
  await serial.connect();
  // Then cleanup unlocks input even though the read loop never started.
  assert.equal(port.closes, 1);
  assert.equal(port.readable.locked, false);
  assert.equal(states.at(-1), 'disconnected');
  assert.match(errors[0], /writer unavailable/);
});
