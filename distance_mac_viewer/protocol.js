/** Both sensor modules share one line-delimited USB stream. */
export function parseLine(line) {
  const tf = /^TF_DATA,(\d+),(-?\d+),(\d+),(\d+)$/.exec(line.trim());
  if (tf) {
    const [tickUs, deltaUs, raw, strength] = tf.slice(1).map(Number);
    if (![tickUs, deltaUs, raw, strength].every(Number.isSafeInteger)) return null;
    if (tickUs > 2147483647 || deltaUs < -1 || deltaUs > 2147483647) return null;
    if (raw > 65535 || strength > 65535) return null;
    return { kind: 'tfmini', tickUs, deltaUs, raw, strength };
  }
  const match = /^US_DATA,(\d+),(\d+),(-?\d+),(-?\d+)$/.exec(line.trim());
  if (!match) return null;
  const [sequence, tickMs, front, upper] = match.slice(1).map(Number);
  if (![sequence, tickMs, front, upper].every(Number.isSafeInteger)) return null;
  if (sequence < 1 || tickMs < 0 || tickMs > 2147483647) return null;
  if ([front, upper].some(pulse => pulse < -2 || pulse > 30000)) return null;
  return { kind: 'ultrasonic', sequence, tickMs, pulses: [front, upper] };
}

/** Retains incomplete USB chunks until a complete line arrives. */
export class FrameDecoder {
  buffer = '';
  feed(chunk) {
    const lines = (this.buffer + chunk).split('\n');
    this.buffer = lines.pop().slice(-4096);
    return lines.map(parseLine).filter(frame => frame !== null);
  }
}

export function pulseReading(pulseUs) {
  if (pulseUs === -2) return { cm: null, status: 'no_echo', label: '未收到回波' };
  if (pulseUs === -1) return { cm: null, status: 'echo_high', label: 'Echo 高电平超时' };
  const cm = pulseUs * 0.0343 / 2;
  if (cm < 2 || cm > 400) return { cm: null, status: 'out_of_range', label: '超出 2–400 cm 范围' };
  return { cm, status: 'ok', label: '测距正常' };
}

/** Preserve the validity and range rules in the supplied TFmini viewer. */
export function tfReading(raw, strength, unit = 'cm') {
  if ([0, 65535, 65534, 65532].includes(raw) || strength < 100 || strength === 65535) {
    return { cm: null, status: 'invalid_signal', label: '信号不足 / 测距无效' };
  }
  const cm = unit === 'mm' ? raw / 10 : raw;
  if (cm < 10) return { cm: null, status: 'too_close', label: '小于 10 cm 盲区' };
  if (cm > 1200) return { cm: null, status: 'out_of_range', label: '超出 1200 cm 范围' };
  return { cm, status: 'ok', label: '测距正常' };
}

export function exportCsv(samples) {
  const rows = ['received_at,type,sequence,pico_tick,pico_unit,tf_cm,tf_status,tf_raw,tf_unit,tf_strength,tf_parse_delta_us,front_cm,front_status,front_echo_us,upper_cm,upper_status,upper_echo_us'];
  for (const sample of samples) {
    const row = Array(17).fill('');
    row[0] = new Date(sample.receivedAt).toISOString(); row[1] = sample.kind;
    switch (sample.kind) {
      case 'tfmini': {
        const reading = tfReading(sample.raw, sample.strength, sample.tfUnit);
        row.splice(3, 8, sample.tickUs, 'us', reading.cm === null ? '' : reading.cm.toFixed(2),
          reading.status, sample.raw, sample.tfUnit, sample.strength, sample.deltaUs);
        break;
      }
      case 'ultrasonic': {
        row.splice(2, 3, sample.sequence, sample.tickMs, 'ms');
        row.splice(11, 6, ...sample.pulses.flatMap(pulse => {
          const reading = pulseReading(pulse);
          return [reading.cm === null ? '' : reading.cm.toFixed(2), reading.status, pulse];
        }));
        break;
      }
      default: throw new Error('Unknown sensor frame type');
    }
    rows.push(row.join(','));
  }
  return rows.join('\r\n') + '\r\n';
}
