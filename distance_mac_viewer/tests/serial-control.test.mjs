import assert from 'node:assert/strict';
import test from 'node:test';
import { PicoSerial } from '../serial.js';

const encoder = new TextEncoder();
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

class Wire {
  writes = [];
  constructor(onWrite = () => {}) { this.onWrite = onWrite; }
  async open() {
    this.readable = new ReadableStream({ start: input => { this.input = input; } });
    this.writable = new WritableStream({ write: async bytes => {
      const text = new TextDecoder().decode(bytes);
      this.writes.push(text);
      if (text === '\x05') this.send('paste mode; Ctrl-C to cancel, Ctrl-D to finish\r\n');
      if (text === '\x04') this.send('HW_READY,1\nUS_DATA,1,123,1000,2000\n');
      await this.onWrite(text);
    } });
  }
  send(text) { this.input.enqueue(encoder.encode(text)); }
  async close() {
    assert.equal(this.readable.locked, false);
    assert.equal(this.writable.locked, false);
  }
}

function setup(t, { diagnosticsPaths = [], onWrite, missing } = {}) {
  const oldNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  const oldFetch = globalThis.fetch;
  const wire = new Wire(onWrite);
  const requests = [];
  Object.defineProperty(globalThis, 'navigator', { configurable: true,
    value: { serial: { requestPort: async () => wire } } });
  globalThis.fetch = async (path, options) => {
    requests.push({ path, options });
    return { ok: path !== missing, text: async () => `# ${path}\r\n` };
  };
  const serial = new PicoSerial({ diagnosticsPaths });
  t.after(async () => {
    await serial.disconnect();
    Object.defineProperty(globalThis, 'navigator', oldNavigator);
    globalThis.fetch = oldFetch;
  });
  return { serial, wire, requests };
}

test('diagnostics sources precede acquisition in one RAM upload', async t => {
  // Given two optional helpers; when connecting; then upload contains each source in order.
  const { serial, wire, requests } = setup(t, { diagnosticsPaths: ['/cutoff.py', '/pico_diagnostics.py'] });
  await serial.connect();
  assert.deepEqual(requests.map(request => request.path), ['/cutoff.py', '/pico_diagnostics.py', '/distance_pico_stream.py']);
  assert.equal(wire.writes.join(''), '\x03\x03\x05# /cutoff.py\n\n# /pico_diagnostics.py\n\n# /distance_pico_stream.py\n\x04');
  assert.ok(requests.every(request => request.options.cache === 'no-store'));
});

test('a missing helper fails before interrupting the board', async t => {
  // Given an incomplete bundle; when connecting; then no bytes reach the board.
  const { serial, wire } = setup(t, { diagnosticsPaths: ['/missing.py'], missing: '/missing.py' });
  await serial.connect();
  assert.equal(wire.writes.join(''), '');
  await assert.rejects(() => serial.sendCommand('HW_CMD,1,motor,5000\n'));
});

test('ready uploads accept only bounded hardware commands', async t => {
  // Given a running uploaded session; when valid commands are sent; then exact lines reach Pico.
  const { serial, wire } = setup(t);
  await serial.connect();
  const before = wire.writes.length;
  await serial.sendCommand('HW_CMD,1,motor,5000\n');
  await serial.sendCommand('HW_PING\n');
  await serial.sendCommand('HW_CMD,1000000000,stop,0\n');
  assert.deepEqual(wire.writes.slice(before), ['HW_CMD,1,motor,5000\n', 'HW_PING\n', 'HW_CMD,1000000000,stop,0\n']);
});

test('invalid control lines cannot become arbitrary serial writes', async t => {
  // Given a running session; when malformed or unbounded requests arrive; then nothing is written.
  const { serial, wire } = setup(t);
  await serial.connect();
  const before = wire.writes.length;
  for (const line of ['HW_CMD,0,motor,5000\n', 'HW_CMD,1000000001,motor,5000\n',
    'HW_CMD,1,motor,10000\n', 'HW_CMD,1,buzzer,05000\n', 'HW_CMD,1,pin,5000\n',
    'HW_CMD,1,motor,5000\n\n', 'HW_PING\nprint(1)\n', '\x03', 'HW_PING', null]) {
    await assert.rejects(() => serial.sendCommand(line));
  }
  assert.equal(wire.writes.length, before);
});

test('listen-only mode and disconnected sessions never write controls', async t => {
  // Given receive-only operation; when tests are attempted; then no control bytes are written.
  const { serial, wire, requests } = setup(t);
  await assert.rejects(() => serial.sendCommand('HW_PING\n'));
  await serial.connect({ upload: false });
  await assert.rejects(() => serial.sendCommand('HW_CMD,1,motor,5000\n'));
  await serial.disconnect();
  assert.equal(wire.writes.join(''), '');
  assert.equal(requests.length, 0);
});

test('control is rejected while RAM upload is waiting for execution', async t => {
  // Given upload still waiting on paste mode; when control is attempted; then no command is added.
  const transfer = deferred(), release = deferred();
  const { serial, wire } = setup(t, { onWrite: async text => {
    if (text.startsWith('#')) { transfer.resolve(); await release.promise; }
  } });
  const connecting = serial.connect();
  await transfer.promise;
  await assert.rejects(() => serial.sendCommand('HW_CMD,1,motor,5000\n'));
  release.resolve(); await connecting;
  assert.equal(wire.writes.some(text => text.startsWith('HW_CMD')), false);
});

test('control lines serialize and queued work cannot outlive disconnect', async t => {
  // Given a blocked motor command; when heartbeat and disconnect race; then queued heartbeat is cancelled.
  const started = deferred(), release = deferred();
  const { serial, wire } = setup(t, { onWrite: async text => {
    if (text.startsWith('HW_CMD')) { started.resolve(); await release.promise; }
  } });
  await serial.connect();
  const first = serial.sendCommand('HW_CMD,1,motor,5000\n');
  const ping = serial.sendCommand('HW_PING\n');
  await started.promise;
  const closing = serial.disconnect();
  await assert.rejects(first, { name: 'AbortError' });
  await assert.rejects(ping, { name: 'AbortError' });
  await closing;
  release.resolve();
  assert.equal(wire.writes.includes('HW_PING\n'), false);
});
