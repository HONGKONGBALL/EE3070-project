/** Fixed actions match the Pico's independent allowlist. */
export const TESTS = Object.freeze({
  motor: { duration: 5000, label: '马达' }, buzzer: { duration: 5000, label: '蜂鸣器' },
  cob: { duration: 3000, label: 'COB 灯' }, led: { duration: 3000, label: '板载 LED' },
  buttons: { duration: 45000, label: '按键检测' }, stop: { duration: 0, label: '全部输出' },
});
const validId = id => Number.isSafeInteger(id) && id > 0 && id <= 1e9;
export function commandFor(id, target) {
  if (!validId(id) || !Object.hasOwn(TESTS, target)) throw new Error('无效测试指令');
  return `HW_CMD,${id},${target},${TESTS[target].duration}\n`;
}
function parse(line) {
  if (line === 'HW_READY,1') return { kind: 'ready' };
  const prefix = /^(HW_DATA|HW_BUTTON),(.+)$/.exec(line);
  if (!prefix) return null;
  let value;
  try { value = JSON.parse(prefix[2]); } catch (error) {
    if (error instanceof SyntaxError) return null;
    throw error;
  }
  if (!value || Array.isArray(value) || !validId(value.id)
      || !Number.isSafeInteger(value.duration_ms) || value.duration_ms < 0 || value.duration_ms > 45000) return null;
  if (prefix[1] === 'HW_BUTTON') {
    if (![1, 2, 3, 4].includes(value.index) || !['pressed', 'released', 'long'].includes(value.state)) return null;
    return { kind: 'button', value };
  }
  if (!Object.hasOwn(TESTS, value.target) || !['started', 'done', 'stopped', 'link_lost', 'error'].includes(value.state)) return null;
  if (value.state === 'started' && value.duration_ms !== TESTS[value.target].duration) return null;
  return { kind: 'state', value };
}

/** Discard oversized records through their newline; never reinterpret their tail. */
export class HardwareDecoder {
  buffer = '';
  overflow = false;
  feed(chunk) {
    const events = [];
    for (const character of chunk) {
      if (character === '\n') {
        const event = this.overflow ? null : parse(this.buffer.trim());
        if (event) events.push(event);
        this.buffer = ''; this.overflow = false;
      } else if (!this.overflow) {
        this.buffer += character;
        if (this.buffer.length > 4096) { this.overflow = true; this.buffer = ''; }
      }
    }
    return events;
  }
}
