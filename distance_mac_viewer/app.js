import { FrameDecoder, pulseReading, tfReading, exportCsv } from './protocol.js';
import { drawChart } from './chart.js';
import { PicoSerial } from './serial.js';

const $ = id => document.getElementById(id);
const channels = [
  { id: 'tf', color: '#b17725', max: 1200 },
  { id: '0', color: '#137e99', max: 400 },
  { id: '1', color: '#9862bf', max: 400 },
];
let decoder = new FrameDecoder(), samples = [], latestTf = null;
let counts = { tf: 0, us: 0 }, sessionAt = Date.now(), demoTimer = null, demoSequence = 0;
let active = false, errorMessage = '', logText = '', sessionSource = 'device';
const timeFormat = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const canvas = $('chart');
function notice(message, kind = '') {
  if ($('notice').textContent !== message) $('notice').textContent = message;
  $('notice').dataset.kind = kind;
}
function log(text) {
  logText = (logText + text).slice(-6000);
  $('log').textContent = logText || '尚无串口数据';
}
function renderChart() {
  drawChart(canvas, channels.filter(channel => $(`show-${channel.id}`).checked));
}
function emptyReadings(label = '等待测距') {
  for (const channel of channels) {
    $(`sensor-${channel.id}`).dataset.state = 'idle';
    $(`distance-${channel.id}`).textContent = '—';
    $(`status-${channel.id}`).textContent = label;
    $(`age-${channel.id}`).textContent = '尚无数据';
    $(`range-${channel.id}`).style.width = '0%';
  }
  for (const id of ['strength', 'raw-tf', 'interval-tf', 'pulse-0', 'pulse-1', 'tick-0', 'tick-1']) $(id).textContent = '—';
}
function resetSession() {
  decoder = new FrameDecoder(); samples = []; latestTf = null; counts = { tf: 0, us: 0 };
  sessionAt = Date.now(); errorMessage = ''; logText = ''; log('');
  for (const channel of channels) { channel.at = 0; channel.points = []; }
  emptyReadings();
  $('count-tf').textContent = '0'; $('count-us').textContent = '0';
  $('rate-tf').textContent = '—'; $('rate-us').textContent = '—';
  $('updated').textContent = '—'; $('export').disabled = true; renderChart();
}
function showReading(channel, reading, receivedAt, addPoint = true) {
  channel.at = receivedAt;
  $(`distance-${channel.id}`).textContent = reading.cm === null ? '—' : reading.cm.toFixed(1);
  $(`status-${channel.id}`).textContent = reading.label;
  $(`sensor-${channel.id}`).dataset.state = reading.cm === null ? 'invalid' : 'ok';
  $(`age-${channel.id}`).textContent = '刚刚更新';
  $(`range-${channel.id}`).style.width = `${reading.cm === null ? 0 : reading.cm / channel.max * 100}%`;
  if (addPoint) {
    channel.points.push({ time: receivedAt, cm: reading.cm });
    channel.points = channel.points.filter(point => point.time >= receivedAt - 30000);
  }
}
function receive(text) {
  log(text);
  for (const frame of decoder.feed(text)) {
    const sample = { ...frame, receivedAt: Date.now(), tfUnit: $('tf-unit').value };
    samples.push(sample); if (samples.length > 10000) samples.shift();
    switch (frame.kind) {
      case 'tfmini':
        latestTf = sample; counts.tf += 1;
        showReading(channels[0], tfReading(frame.raw, frame.strength, sample.tfUnit), sample.receivedAt);
        $('strength').textContent = String(frame.strength); $('raw-tf').textContent = String(frame.raw);
        $('interval-tf').textContent = frame.deltaUs < 0 ? '—' : `${(frame.deltaUs / 1000).toFixed(2)} ms`;
        $('count-tf').textContent = counts.tf.toLocaleString('zh-CN');
        break;
      case 'ultrasonic':
        counts.us += 1;
        frame.pulses.forEach((pulse, index) => {
          showReading(channels[index + 1], pulseReading(pulse), sample.receivedAt);
          $(`pulse-${index}`).textContent = String(pulse); $(`tick-${index}`).textContent = String(frame.tickMs);
        });
        $('count-us').textContent = counts.us.toLocaleString('zh-CN');
        break;
      default: throw new Error('Unknown sensor frame type');
    }
    $('updated').textContent = timeFormat.format(sample.receivedAt) + '.' + String(sample.receivedAt % 1000).padStart(3, '0');
    $('export').disabled = false;
    if (!demoTimer && active && !errorMessage) notice('正在接收 Pico 数据。激光和两路超声波独立更新、独立显示状态。');
  }
}
function controls(busy) {
  active = busy; $('connect').disabled = busy; $('disconnect').disabled = !busy;
  $('mode').disabled = busy; $('demo').disabled = busy && !demoTimer;
}
function onState(state) {
  const busy = ['connecting', 'uploading', 'connected', 'receiving'].includes(state);
  controls(busy);
  const labels = { idle: '未连接设备', connecting: '正在连接', uploading: '正在准备 Pico',
    connected: 'Pico 已连接', receiving: 'Pico 已连接', disconnected: '已断开连接' };
  $('connection').textContent = labels[state] || state; $('connection').dataset.state = busy ? 'live' : '';
  if (state === 'connecting') notice('请在 Chrome 串口窗口中选择 Pico / USB Serial。');
  if (state === 'uploading') notice('正在临时运行激光与超声波合并读取程序…');
  if (state === 'connected' || state === 'receiving') notice('Pico 已连接。三路分别更新，无数据或超时会单独标记。');
  if (!busy && !demoTimer) {
    emptyReadings('已断开'); notice(errorMessage || '串口已释放。可再次连接 Pico，或查看演示。', errorMessage ? 'error' : '');
  }
}
const serial = new PicoSerial({ onText: receive, onState, onError(message) {
  errorMessage = message; notice(message, 'error'); log(`\n[错误] ${message}\n`);
} });
function stopDemo() {
  if (demoTimer === null) return;
  clearInterval(demoTimer); demoTimer = null; $('demo').textContent = '查看演示'; controls(false);
  $('connection').textContent = '演示已停止'; $('connection').dataset.state = '';
  emptyReadings('演示已停止'); notice('演示已停止。点击“连接 Pico”读取真实传感器。');
}
function demoFrame() {
  demoSequence += 1;
  const elapsed = Date.now() - sessionAt;
  const front = Math.round((95 + 44 * Math.sin(elapsed / 2700)) / 0.01715);
  const upper = demoSequence % 67 > 60 ? -2 : Math.round((170 + 36 * Math.cos(elapsed / 3800)) / 0.01715);
  const tfCm = Math.round(150 + 80 * Math.sin(elapsed / 4300));
  const tfRaw = $('tf-unit').value === 'mm' ? tfCm * 10 : tfCm;
  receive(`TF_DATA,${elapsed * 1000},${demoSequence === 1 ? -1 : 160000},${tfRaw},920\nUS_DATA,${demoSequence},${elapsed},${front},${upper}\n`);
}
$('connect').addEventListener('click', () => {
  stopDemo(); resetSession(); sessionSource = 'device'; void serial.connect({ upload: $('mode').value === 'upload' });
});
$('disconnect').addEventListener('click', () => { if (demoTimer !== null) stopDemo(); else void serial.disconnect(); });
$('demo').addEventListener('click', () => {
  if (demoTimer !== null) { stopDemo(); return; }
  resetSession(); demoSequence = 0; sessionSource = 'demo';
  demoTimer = setInterval(demoFrame, 160); controls(true); $('demo').disabled = false;
  $('demo').textContent = '停止演示'; $('connection').textContent = '演示数据'; $('connection').dataset.state = 'demo';
  notice('演示数据 · 未连接传感器。停止演示后连接 Pico，读取三个真实通道。', 'demo'); demoFrame();
});
$('clear').addEventListener('click', resetSession);
$('tf-unit').addEventListener('change', () => {
  channels[0].points = [];
  if (latestTf) showReading(channels[0], tfReading(latestTf.raw, latestTf.strength, $('tf-unit').value), latestTf.receivedAt, false);
  renderChart();
});
for (const channel of channels) $(`show-${channel.id}`).addEventListener('change', renderChart);
$('export').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob(['\uFEFF', exportCsv(samples)], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url;
  link.download = `distance_${sessionSource === 'demo' ? 'demo_' : ''}${new Date().toISOString().replaceAll(':', '-')}.csv`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('resize', renderChart);
setInterval(() => {
  const now = Date.now();
  if (active) {
    for (const channel of channels) {
      const ageMs = channel.at ? now - channel.at : now - sessionAt;
      if (channel.at) $(`age-${channel.id}`).textContent = ageMs < 1000 ? '刚刚更新' : `${(ageMs / 1000).toFixed(1)} 秒前`;
      if ((channel.at && ageMs > 1500) || (!channel.at && ageMs > 6000)) {
        $(`distance-${channel.id}`).textContent = '—';
        $(`status-${channel.id}`).textContent = channel.at ? '数据停止更新' : '未收到该路数据';
        $(`sensor-${channel.id}`).dataset.state = 'stale'; $(`range-${channel.id}`).style.width = '0%';
      }
    }
    for (const [key, channel] of [['tf', channels[0]], ['us', channels[1]]]) {
      const points = channel.points.filter(point => point.time >= now - 5000);
      const span = points.length > 1 ? (points.at(-1).time - points[0].time) / 1000 : 0;
      $(`rate-${key}`).textContent = !channel.at || now - channel.at > 1500 ? '0 Hz' : span > 0 ? `${((points.length - 1) / span).toFixed(1)} Hz` : '—';
    }
  }
  renderChart();
}, 250);
resetSession();
if (!('serial' in navigator)) notice('请在电脑 Google Chrome 中打开以连接串口；当前仍可查看演示。', 'error');
