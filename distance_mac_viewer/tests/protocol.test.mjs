import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseLine, FrameDecoder, pulseReading, tfReading, exportCsv } from '../protocol.js';

test('parses both channels when one sensor has no echo', () => {
  // Given a complete telemetry line with an upper sensor timeout.
  const line = 'US_DATA,12,3490,5831,-2\r';
  // When the serial boundary parses it.
  const result = parseLine(line);
  // Then each raw pulse is retained independently.
  assert.deepEqual(result, { kind: 'ultrasonic', sequence: 12, tickMs: 3490, pulses: [5831, -2] });
});

test('rejects malformed data and echoed Python source', () => {
  // Given incomplete, extra, and nonnumeric fields.
  const lines = ['US_DATA,1,20,300,', 'US_DATA,1,20,300,400,9', 'US_DATA,1,20,NaN,2',
    'print("US_DATA,1,20,300,400")', 'US_DATA,1,20,-3,400', 'US_DATA,1,20,999999,400'];
  // When each line is parsed.
  const results = lines.map(parseLine);
  // Then none becomes a measurement.
  assert.deepEqual(results, lines.map(() => null));
});

test('waits for newline when USB splits the final pulse digits', () => {
  // Given a stream decoder and partial last field.
  const decoder = new FrameDecoder();
  assert.deepEqual(decoder.feed('banner\r\nUS_DATA,1,100,5831,1'), []);
  // When the rest of that field and a second frame arrive.
  const frames = decoder.feed('1662\r\nUS_DATA,2,240,-1,5831\n');
  // Then no truncated reading was emitted.
  assert.deepEqual(frames.map(frame => frame.pulses), [[5831, 11662], [-1, 5831]]);
});

test('bounds an unterminated noisy stream and recovers next line', () => {
  // Given a noisy serial source without newlines.
  const decoder = new FrameDecoder();
  decoder.feed('x'.repeat(20000));
  // When a valid line follows a terminator.
  const frames = decoder.feed('\nUS_DATA,1,1,5831,5831\n');
  // Then decoding recovers.
  assert.equal(frames.length, 1);
});

test('distinguishes physical range from no-echo and stuck-high timeouts', () => {
  // Given representative raw echo pulse widths.
  const pulses = [5831, -2, -1, 0, 50, 30000];
  // When pulse readings are converted with sound speed 343m/s.
  const results = pulses.map(pulseReading);
  // Then invalid samples never appear as zero distance.
  assert.ok(Math.abs(results[0].cm - 100) < 0.1);
  assert.deepEqual(results.slice(1).map(result => result.status),
    ['no_echo', 'echo_high', 'out_of_range', 'out_of_range', 'out_of_range']);
  assert.ok(results.slice(1).every(result => result.cm === null));
});

test('exports independent invalid channels as empty distances with status', () => {
  // Given one complete sample.
  const samples = [{ kind: 'ultrasonic', receivedAt: 1793000000000, sequence: 1, tickMs: 10, pulses: [5831, -2] }];
  // When a CSV is exported.
  const csv = exportCsv(samples);
  // Then both raw pulse and explicit status survive export.
  assert.match(csv, /front_cm,front_status,front_echo_us,upper_cm,upper_status,upper_echo_us/);
  assert.match(csv, /100\.00,ok,5831,,no_echo,-2/);
});

test('decodes interleaved TFmini and ultrasonic data across USB chunks', () => {
  // Given a shared USB stream carrying both kinds of sensor frames.
  const decoder = new FrameDecoder();
  assert.deepEqual(decoder.feed('TF_DATA,1000,-1,120,9'), []);
  // When the split TF frame and a US frame arrive together.
  const frames = decoder.feed('00\r\nUS_DATA,1,2,5831,-2\nTF_DATA,2000,1000,121,901\n');
  // Then both protocols keep their independent fields and ordering.
  assert.deepEqual(frames.map(frame => frame.kind), ['tfmini', 'ultrasonic', 'tfmini']);
  assert.deepEqual(frames[0], { kind: 'tfmini', tickUs: 1000, deltaUs: -1, raw: 120, strength: 900 });
});

test('rejects damaged TF telemetry while accepting raw invalid sensor values', () => {
  // Given transport corruption and sensor-level invalid sentinels.
  const lines = ['TF_DATA,1,-2,10,900', 'TF_DATA,1,0,65536,900',
    'TF_DATA,1,0,10,NaN', 'TF_DATA,1,0,10,900,extra'];
  // When wire lines are parsed.
  const invalid = lines.map(parseLine);
  // Then transport corruption is dropped but a real sentinel can reach its status logic.
  assert.deepEqual(invalid, lines.map(() => null));
  assert.equal(parseLine('TF_DATA,1,-1,65535,0').raw, 65535);
});

test('keeps TF raw-unit conversion separate from ultrasonic centimetres', () => {
  // Given the same TF raw field in cm versus mm and an ultrasonic echo.
  const cm = tfReading(1000, 900, 'cm');
  const mm = tfReading(1000, 900, 'mm');
  // When each sensor type is interpreted with its own unit contract.
  const ultrasound = pulseReading(5831);
  // Then changing TF units does not rescale ultrasound.
  assert.equal(cm.cm, 1000);
  assert.equal(mm.cm, 100);
  assert.ok(Math.abs(ultrasound.cm - 100) < 0.1);
});

test('marks weak or sentinel TF readings invalid without graphing a fake distance', () => {
  // Given invalid TF distance or signal-strength values.
  const frames = [[65535, 1000], [65534, 1000], [65532, 1000], [100, 0], [100, 99], [100, 65535], [0, 900]];
  // When the viewer applies the supplied TFmini viewer rules.
  const readings = frames.map(([raw, strength]) => tfReading(raw, strength, 'cm'));
  // Then all invalid readings leave chart gaps.
  assert.ok(readings.every(reading => reading.cm === null));
});

test('exports both frame types without inventing synchronized measurements', () => {
  // Given independent TF and US events with different Pico time units.
  const samples = [{kind:'tfmini', receivedAt:1793000000000000 / 1000, tickUs:1234, deltaUs:-1, raw:1000, strength:900, tfUnit:'mm'},
    {kind:'ultrasonic', receivedAt:1793000000001, sequence:1, tickMs:8, pulses:[5831,-2]}];
  // When a shared CSV is generated.
  const rows = exportCsv(samples).trim().split('\r\n');
  // Then each original event has its own row, timestamp unit and source.
  assert.equal(rows.length, 3);
  assert.match(rows[1], /,tfmini,,1234,us,100\.00,ok,1000,mm,900,-1,/);
  assert.match(rows[2], /,ultrasonic,1,8,ms,/);
});
