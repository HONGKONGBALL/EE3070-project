import assert from 'node:assert/strict';
import test from 'node:test';
import { HardwareController } from '../hardware.js';
import { HardwareDecoder, commandFor } from '../hardware-protocol.js';

function fixture(t) {
  let time = 0;
  const writes = [], changes = [], buttons = [];
  const control = new HardwareController({
    send: async line => { writes.push(line); }, now: () => time,
    onChange: state => changes.push(state), onButton: event => buttons.push(event),
    timers: { setInterval: () => 1, clearInterval() {} },
  });
  t.after(() => control.dispose());
  return { control, writes, changes, buttons,
    advance(ms) { time += ms; control.tick(); },
    ready() {
      control.connection('connecting'); control.connection('uploading');
      control.receive('HW_READY,1\r\n'); control.connection('connected');
    },
    event(id, target, state, duration_ms = 0) {
      control.receive(`HW_DATA,${JSON.stringify({ id, target, state, duration_ms })}\n`);
    },
  };
}

test('complete new-program handshake is required; demo and listen never enable tests', t => {
  // Given connected receive-only, demo and uploaded sessions; when ready arrives; then only upload enables.
  const { control, writes } = fixture(t);
  control.connection('connected', { upload: false }); control.receive('HW_READY,1\n');
  assert.equal(control.state.available, false);
  control.connection('demo'); control.receive('HW_READY,1\n');
  assert.equal(control.state.available, false);
  control.connection('connecting'); control.connection('uploading');
  control.receive('HW_READY,'); control.connection('connected');
  assert.equal(control.state.available, false);
  control.receive('1\n'); assert.equal(control.state.available, true);
  assert.deepEqual(writes, []);
});

test('motor uses fixed five seconds, acknowledgement starts countdown, duplicate clicks send once', async t => {
  // Given a ready Pico; when motor is double-clicked; then one command waits for acknowledgement.
  const f = fixture(t); f.ready();
  await Promise.all([f.control.start('motor'), f.control.start('motor')]);
  assert.deepEqual(f.writes, ['HW_CMD,1,motor,5000\n']);
  assert.equal(f.control.state.pending, true);
  f.event(1, 'motor', 'started', 5000); f.advance(1000);
  assert.equal(f.control.state.remainingMs, 4000);
  assert.equal(f.control.state.busy, true);
  f.event(1, 'motor', 'done', 5000);
  assert.equal(f.control.state.busy, false);
  assert.match(f.control.state.message, /完成/);
  assert.doesNotMatch(f.control.state.message, /通过|已振动/);
});

test('heartbeat is sent during an active test; stop waits for its own acknowledgement', async t => {
  // Given an active test; when stop is requested; then its completion is confirmed independently.
  const f = fixture(t); f.ready(); await f.control.start('motor');
  f.event(1, 'motor', 'started', 5000); f.advance(500);
  assert.equal(f.writes.at(-1), 'HW_PING\n');
  await f.control.stop();
  assert.equal(f.writes.at(-1), 'HW_CMD,2,stop,0\n');
  f.event(1, 'motor', 'stopped', 5000);
  assert.equal(f.control.state.pending, true);
  f.event(2, 'stop', 'done');
  assert.equal(f.control.state.busy, false);
  assert.match(f.control.state.message, /关闭|停止/);
});

test('missing startup reply requests stop and a missing stop reply disables further controls', async t => {
  // Given a command with no reply; when bounded acknowledgement timeouts expire; then controls fail closed.
  const f = fixture(t); f.ready(); await f.control.start('cob');
  f.advance(2100); await Promise.resolve();
  assert.equal(f.writes.at(-1), 'HW_CMD,2,stop,0\n');
  f.advance(2100);
  assert.equal(f.control.state.available, false);
  assert.match(f.control.state.message, /未确认|未收到/);
});

test('disconnect and reconnect discard stale capability and stale execution messages', async t => {
  // Given an active old session; when it disconnects; then old ready/data cannot revive it.
  const f = fixture(t); f.ready(); await f.control.start('led');
  f.control.connection('disconnected'); f.event(1, 'led', 'done', 3000);
  assert.equal(f.control.state.available, false);
  f.control.connection('connecting'); f.control.connection('connected');
  assert.equal(f.control.state.available, false);
  f.control.receive('HW_READY,1\n'); await f.control.start('motor');
  f.event(1, 'led', 'done', 3000);
  assert.equal(f.control.state.target, 'motor');
});

test('button events require a matching active button session', async t => {
  // Given button telemetry; when it belongs to the active test; then only validated records reach UI.
  const f = fixture(t); f.ready(); await f.control.start('buttons');
  f.event(1, 'buttons', 'started', 45000);
  f.control.receive('HW_BUTTON,{"id":1,"index":2,"state":"long","duration_ms":850}\n');
  f.control.receive('HW_BUTTON,{"id":9,"index":2,"state":"pressed","duration_ms":0}\n');
  f.control.receive('HW_BUTTON,{"id":1,"index":8,"state":"pressed","duration_ms":0}\n');
  assert.deepEqual(f.buttons, [{ id: 1, index: 2, state: 'long', duration_ms: 850 }]);
});

test('late write failure from an old connection does not disable a new session', async t => {
  // Given a pending write from the old session; when a new session opens; then its state survives rejection.
  const f = fixture(t); f.ready();
  let reject;
  f.control.send = () => new Promise((resolve, fail) => { reject = fail; });
  const pending = f.control.start('motor');
  f.control.connection('disconnected'); f.ready();
  reject(new Error('old port closed')); await pending;
  assert.equal(f.control.state.available, true);
});

test('command builder rejects arbitrary pins, durations and invalid IDs', () => {
  // Given unsupported commands; when encoded; then no arbitrary hardware request is produced.
  for (const [id, target] of [[0, 'motor'], [1, 'gp14'], [1e10, 'motor']])
    assert.throws(() => commandFor(id, target));
  assert.equal(commandFor(4, 'buzzer'), 'HW_CMD,4,buzzer,5000\n');
});

test('hardware decoder handles mixed fragmented lines and rejects malformed or oversized events', () => {
  // Given interleaved sensor and control bytes; when decoded; then only complete valid events survive.
  const decoder = new HardwareDecoder();
  assert.deepEqual(decoder.feed('US_DATA,1,80,580,1000\nHW_REA'), []);
  assert.deepEqual(decoder.feed('DY,1\r\nHW_DATA,broken\n'), [{ kind: 'ready' }]);
  assert.deepEqual(decoder.feed('x'.repeat(5000) + 'HW_READY,1\n'), []);
  assert.deepEqual(decoder.feed('HW_READY,1\n'), [{ kind: 'ready' }]);
});
