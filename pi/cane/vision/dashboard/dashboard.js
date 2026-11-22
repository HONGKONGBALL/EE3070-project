import { drawChart } from './chart.js';

const byId = (id) => document.getElementById(id);
const camera = byId('camera');
const viewLink = byId('view-link');
const languageToggle = byId('language-toggle');
const chart = byId('chart');
const connection = byId('connection');
const ptt = byId('ptt');
const legend = [...document.querySelectorAll('.legend input')];
const styles = getComputedStyle(document.documentElement);
const channels = [
  { key: 'laser', min: 10, max: 1200 },
  { key: 'front', min: 2, max: 400 },
  { key: 'upper', min: 2, max: 400 },
].map((channel) => {
  const card = document.querySelector(`[data-channel="${channel.key}"]`);
  return { ...channel, card, color: styles.getPropertyValue(`--${channel.key}`).trim(),
    reading: card.querySelector('[data-reading]'), range: card.querySelector('[data-range]'),
    label: card.querySelector('[data-state-label]') };
});
const history = [];
const textTranslations = [...document.querySelectorAll('[data-en]')].map((element) =>
  [element, element.textContent, element.dataset.en]);
const attributeTranslations = [['aria-label', 'data-en-label'], ['alt', 'data-en-alt']].flatMap(([attribute, translation]) =>
  [...document.querySelectorAll(`[${translation}]`)].map((element) =>
    [element, attribute, element.getAttribute(attribute), element.getAttribute(translation)]));
const messages = {
  zh: {
    details: '详情 ↗', home: '← 返回首页', live: '实时画面', waitingVideo: '等待画面',
    noAlert: '暂无提醒', cameraUnavailable: '画面未就绪', waitingData: '等待新数据',
    noEcho: '无有效回波', noReading: '无有效读数', blindZone: '激光盲区', outOfRange: '超出量程', valid: '有效读数',
    piOffline: 'Pi 未连接', picoConnected: 'Pico 已连接', picoWaiting: 'Pico 等待数据', picoDisabled: 'Pico 未启用',
    chartEmpty: '等待树莓派测距数据', chartNow: '现在', chartNoChannels: '勾选上方通道以显示曲线',
    navOffline: '导航服务不可用', navLocation: '导航等待 iPhone 定位', navPico: '导航等待 Pico',
    navPlanning: '正在规划步行路线', navCrossing: '导航在路口等待确认', navActive: '步行导航中', navReady: '导航就绪',
  },
  en: {
    details: 'Details ↗', home: '← Back to overview', live: 'Live feed', waitingVideo: 'Waiting for video',
    noAlert: 'No active alert', cameraUnavailable: 'Camera unavailable', waitingData: 'Waiting for data',
    noEcho: 'No valid echo', noReading: 'No valid reading', blindZone: 'Laser blind zone', outOfRange: 'Out of range', valid: 'Valid reading',
    piOffline: 'Pi offline', picoConnected: 'Pico connected', picoWaiting: 'Waiting for Pico', picoDisabled: 'Pico disabled',
    chartEmpty: 'Waiting for Pi distance data', chartNow: 'Now', chartNoChannels: 'Select a channel to plot',
    navOffline: 'Navigation service unavailable', navLocation: 'Navigation waiting for iPhone location',
    navPico: 'Navigation waiting for Pico', navPlanning: 'Planning a walking route',
    navCrossing: 'Navigation waiting at a crossing', navActive: 'Walking navigation active', navReady: 'Navigation ready',
  },
};
const languageKey = 'ee3070-dashboard-language';
let language = 'zh';
try {
  if (localStorage.getItem(languageKey) === 'en') language = 'en';
} catch {
  // The switch still works when browser storage is unavailable.
}
const t = (key) => messages[language][key];
let lastPacket = '';
let lastStatus;
let showDetails = false;
let pttSession = null;
let pttError = '';
const assistantLabels = {
  IDLE: ['就绪', 'Ready'], RECORDING: ['录音中', 'Recording'], TRANSCRIBING: ['识别语音中', 'Transcribing'],
  ANALYZING: ['分析画面中', 'Analyzing'], SPEAKING: ['正在播报', 'Speaking'], ERROR: ['出错', 'Error'],
  PASSIVE_LISTENING: ['语音助手就绪', 'Ready'], WAKE_DETECTED: ['已触发唤醒', 'Wake triggered'],
  ACKNOWLEDGING: ['准备录音', 'Preparing to record'],
  LISTENING_FOR_COMMAND: ['正在听问题', 'Listening for command'], ROUTING_COMMAND: ['识别指令中', 'Routing command'],
  WAITING_FOR_CONFIRMATION: ['等待确认目的地', 'Waiting for destination confirmation'],
  PROCESSING: ['正在处理', 'Processing'], COOLDOWN: ['即将就绪', 'Returning to listening'],
  PROCESSING_EXCLUSIVE: ['正在回答，摄像头危险检测暂停', 'Answering. Camera hazard detection is paused.'],
  test_disabled: ['唤醒测试未启用', 'Wake test is disabled'],
  ptt_gpio_unavailable: ['实体按键不可用，可使用网页按键', 'Physical button unavailable. Dashboard button is available.'],
  mock: ['模拟模式 · 示例语音和回答', 'Mock mode · Sample speech and answer'],
  busy: ['正在处理上一条问题', 'An earlier request is still in progress'],
  recording_limit: ['录音超时，请松开后重试', 'Recording limit reached. Release and try again.'],
  empty_audio: ['没有识别到问题，请再说一次', 'No speech detected. Please try again.'],
  microphone_unavailable: ['麦克风不可用', 'Microphone unavailable'],
  camera_unavailable: ['暂时没有新的摄像头画面', 'No recent camera frame'],
  camera_quality: ['画面不够清晰，请重新对准再试', 'Image unclear. Reposition the camera and try again.'],
  asr_unavailable: ['语音识别或模型不可用', 'Speech recognition or its model is unavailable'],
  asr_timeout: ['语音识别超时', 'Speech recognition timed out'],
  missing_key: ['请在 Pi 后台配置 API key', 'Set the API key on the Pi'],
  missing_model: ['请在 Pi 后台选择视觉模型', 'Set a vision model on the Pi'],
  invalid_model: ['所选模型不可用', 'The selected model is unavailable'],
  image_rejected: ['所选模型拒绝图片输入', 'The selected model rejected image input'],
  provider_auth: ['视觉服务鉴权失败', 'Vision API authentication failed'],
  provider_busy: ['视觉服务请求过多，请稍后再试', 'Vision API rate limit reached'],
  budget_exhausted: ['已达到视觉请求限额', 'Vision request limit reached'],
  budget_unavailable: ['无法读取视觉请求限额', 'Vision request limit unavailable'],
  provider_timeout: ['视觉服务请求超时', 'Vision request timed out'],
  provider_error: ['视觉服务暂不可用', 'Vision API unavailable'],
  vision_busy: ['摄像头检测正忙，请重试', 'Camera detection is busy. Please try again.'],
  scene_expired: ['画面已过期，请重新提问', 'The scene has expired. Ask again for a fresh image.'],
  audio_disabled: ['请先启用音频输出', 'Enable audio output first'],
  audio_busy: ['音频正在播放其他提醒，请重试', 'Other audio is playing. Please try again.'],
  interrupted: ['本次回答已被提醒打断', 'Answer interrupted by an alert'],
  tts_unavailable: ['语音合成不可用', 'Speech synthesis unavailable'],
  playback_failed: ['回答播放失败', 'Answer playback failed'],
  assistant_error: ['语音助手请求失败，请重试', 'Assistant request failed. Please try again.'],
};

function renderAssistant(assistant) {
  byId('voice-assistant').hidden = !assistant;
  if (!assistant) return;
  const label = (key) => (assistantLabels[key] || assistantLabels.assistant_error)[language === 'en' ? 1 : 0];
  const status = [label(assistant.state)];
  if (assistant.ptt_gpio_unavailable) status.push(label('ptt_gpio_unavailable'));
  if (assistant.wake_enabled && assistant.state === 'PASSIVE_LISTENING' && !assistant.error) status.push(assistant.wake_phrase);
  if (assistant.mode === 'mock') status.push(label('mock'));
  if (pttError || assistant.error) status.push(label(pttError || assistant.error));
  const feedback = byId('ptt-status');
  const feedbackText = status.join(' · ');
  if (feedback.textContent !== feedbackText) feedback.textContent = feedbackText;
  ptt.setAttribute('aria-pressed', String(Boolean(pttSession && !pttSession.action && assistant.state !== 'ERROR')));
  // Keep keyup and pointerup available while the current press is held.
  const ready = ['ollama', 'psyche_grok'].includes(assistant.mode)
    ? assistant.state === 'PASSIVE_LISTENING' : ['IDLE', 'ERROR'].includes(assistant.state);
  ptt.disabled = !pttSession && !ready;
  byId('test-wake').hidden = !assistant.dashboard_wake_test;
  byId('test-wake').disabled = !ready || Boolean(pttSession);
  const answer = byId('ptt-answer');
  answer.hidden = !assistant.answer;
  answer.textContent = assistant.answer || '';
  answer.lang = assistant.answer_language === 'zh' ? 'zh-Hans' : 'en';
}

async function buttonRequest(action, requestId) {
  const response = await fetch('./ptt', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action, request_id: requestId}), signal: AbortSignal.timeout(3000),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'assistant_error');
  return data;
}

byId('test-wake').addEventListener('click', async () => {
  pttError = '';
  try {
    await buttonRequest('wake');
  } catch (error) {
    pttError = error.message;
  }
  renderAssistant(lastStatus?.assistant);
});

function beginPtt() {
  if (pttSession || ptt.disabled || !lastStatus?.assistant) return;
  pttError = '';
  const session = { action: null, press: buttonRequest('press') };
  pttSession = session;
  renderAssistant(lastStatus.assistant);
  session.press.catch((error) => {
    if (pttSession !== session) return;
    pttSession = null;
    pttError = error.message;
    renderAssistant(lastStatus?.assistant);
  });
}

async function finishPtt(action) {
  const session = pttSession;
  if (!session || session.action) return;
  session.action = action;
  renderAssistant(lastStatus?.assistant);
  try {
    const data = await session.press;
    await buttonRequest(action, data.request_id);
  } catch (error) {
    pttError = error.message;
  } finally {
    if (pttSession === session) pttSession = null;
    renderAssistant(lastStatus?.assistant);
  }
}

ptt.addEventListener('pointerdown', (event) => {
  if (event.button !== 0 || ptt.disabled) return;
  ptt.setPointerCapture(event.pointerId);
  beginPtt();
});
ptt.addEventListener('pointerup', () => void finishPtt('release'));
ptt.addEventListener('pointercancel', () => void finishPtt('cancel'));
ptt.addEventListener('lostpointercapture', () => void finishPtt('cancel'));
ptt.addEventListener('keydown', (event) => {
  if (![' ', 'Enter'].includes(event.key)) return;
  event.preventDefault();
  if (!event.repeat) beginPtt();
});
ptt.addEventListener('keyup', (event) => {
  if (![' ', 'Enter'].includes(event.key)) return;
  event.preventDefault();
  void finishPtt('release');
});
ptt.addEventListener('blur', () => void finishPtt('cancel'));
window.addEventListener('blur', () => void finishPtt('cancel'));

function applyLanguage() {
  const english = language === 'en';
  document.documentElement.lang = english ? 'en' : 'zh-Hans';
  for (const [element, zh, en] of textTranslations) element.textContent = english ? en : zh;
  for (const [element, attribute, zh, en] of attributeTranslations) element.setAttribute(attribute, english ? en : zh);
  languageToggle.textContent = english ? '中文' : 'EN';
  languageToggle.lang = english ? 'zh-Hans' : 'en';
  languageToggle.setAttribute('aria-label', english ? '切换到中文' : 'Switch to English');
  changeView();
}

function badge(element, label, state = '') {
  if (element.textContent !== label) element.textContent = label;
  element.dataset.state = state;
}

function showCameraFrame(show) {
  camera.hidden = !show;
  byId('camera-empty').hidden = show;
  badge(byId('camera-status'), t(show ? 'live' : 'waitingVideo'), show ? 'live' : '');
}

camera.addEventListener('load', () => showCameraFrame(camera.hasAttribute('src')));
camera.addEventListener('error', () => {
  camera.removeAttribute('src');
  showCameraFrame(false);
});

function renderCamera(status) {
  const state = status?.camera;
  if (state?.ready && !showDetails) {
    if (!camera.hasAttribute('src')) camera.src = `./stream?start=${Date.now()}`;
    else if (!camera.hidden) showCameraFrame(true);
  } else {
    camera.removeAttribute('src');
    showCameraFrame(false);
  }
  byId('camera-fps').textContent = state?.ready && Number.isFinite(state.fps) ? `${state.fps} FPS` : '—';
  const alert = byId('alert');
  const message = state?.ready && status.alert;
  alert.textContent = message || t(state?.ready ? 'noAlert' : 'cameraUnavailable');
  alert.lang = message ? 'en' : document.documentElement.lang;
}

function readingFor(channel, pico) {
  if (!pico?.fresh) return { cm: null, usable: null, state: 'stale', label: t('waitingData') };
  const cm = pico[`${channel.key}_cm`];
  if (!Number.isFinite(cm)) return { cm: null, usable: null, state: 'invalid', label: t(channel.key === 'laser' ? 'noReading' : 'noEcho') };
  if (cm < channel.min || cm > channel.max) {
    const label = t(channel.key === 'laser' && cm < channel.min ? 'blindZone' : 'outOfRange');
    return { cm, usable: null, state: 'invalid', label };
  }
  return { cm, usable: cm, state: 'ok', label: t('valid') };
}

function render(status) {
  const pico = status?.pico;
  if (!status) badge(connection, t('piOffline'), 'offline');
  else if (pico.fresh) badge(connection, t('picoConnected'), 'live');
  else badge(connection, t(pico.source === 'pico' ? 'picoWaiting' : 'picoDisabled'));
  const readings = {};
  for (const channel of channels) {
    const reading = readingFor(channel, pico);
    readings[channel.key] = reading;
    channel.card.dataset.state = reading.state;
    channel.reading.textContent = reading.cm ?? '—';
    channel.label.textContent = reading.label;
    channel.range.style.width = reading.usable === null ? '0%' : `${Math.max(1, 100 * reading.usable / channel.max)}%`;
    if (showDetails) {
      byId(`raw-${channel.key}`).textContent = reading.cm === null ? '—' : `${reading.cm} cm`;
      byId(`detail-${channel.key}`).textContent = reading.label;
    }
  }
  if (showDetails) {
    byId('packet-count').textContent = pico?.frame ?? '—';
    byId('pico-clock').textContent = Number.isFinite(pico?.pico_ms) ? `${pico.pico_ms} ms` : '—';
    byId('packet-age').textContent = Number.isFinite(pico?.age_ms) ? `${pico.age_ms} ms` : '—';
    byId('laser-strength').textContent = pico?.fresh ? (pico.laser_strength ?? '—') : '—';
  }
  renderCamera(status);
  const navigation = status?.navigation;
  const navStatus = byId('navigation-status');
  navStatus.hidden = !navigation;
  if (navigation) {
    const state = !navigation.route_service_configured ? 'navOffline'
      : !navigation.location_valid ? 'navLocation' : !navigation.pico_ok ? 'navPico'
      : navigation.waiting_crossing ? 'navCrossing' : navigation.route_pending ? 'navPlanning'
      : navigation.active ? 'navActive' : 'navReady';
    navStatus.textContent = t(state);
  }
  renderAssistant(status?.assistant);
  return readings;
}

function renderTrend() {
  if (!showDetails) return;
  const selected = new Set(legend.filter((input) => input.checked).map((input) => input.value));
  drawChart(chart, channels.filter(({key}) => selected.has(key)).map(({key, color}) => ({
    color, points: history.map((row) => ({ time: row.time, cm: row[key] })),
  })), Date.now(), t('chartEmpty'), {nowLabel: t('chartNow'), noChannels: t('chartNoChannels')});
  byId('export').disabled = !history.some((row) => row.frame !== null);
}

function record(status, readings) {
  const pico = status?.pico;
  const now = Date.now();
  const packet = pico?.fresh ? `${status.run_id}:${pico.frame}` : '';
  if (packet !== lastPacket) {
    history.push({ time: now, frame: packet ? pico.frame : null,
      laser: readings.laser.usable, front: readings.front.usable, upper: readings.upper.usable });
    lastPacket = packet;
  }
  while (history.length && history[0].time < now - 30000) history.shift();
}

function changeView(focus = false) {
  showDetails = location.hash === '#details';
  byId('details').hidden = !showDetails;
  byId('overview').hidden = showDetails;
  viewLink.href = showDetails ? '#overview' : '#details';
  viewLink.textContent = t(showDetails ? 'home' : 'details');
  if (focus) {
    window.scrollTo(0, 0);
    byId(showDetails ? 'details-title' : 'overview-title').focus({ preventScroll: true });
  }
  if (lastStatus !== undefined) render(lastStatus);
  renderTrend();
}

window.addEventListener('hashchange', () => changeView(true));
window.addEventListener('resize', renderTrend);
languageToggle.addEventListener('click', () => {
  language = language === 'zh' ? 'en' : 'zh';
  try { localStorage.setItem(languageKey, language); } catch { /* Browser storage is optional. */ }
  applyLanguage();
});
legend.forEach((input) => input.addEventListener('change', renderTrend));
byId('clear').addEventListener('click', () => {
  history.length = 0;
  renderTrend();
});
byId('export').addEventListener('click', () => {
  const rows = history.filter((row) => row.frame !== null).map((row) =>
    [new Date(row.time).toISOString(), row.frame, row.laser, row.front, row.upper].join(','));
  const data = ['received_at,frame,laser_cm,front_cm,upper_cm', ...rows].join('\n');
  const url = URL.createObjectURL(new Blob([data], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'ee3070-distance.csv';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function poll() {
  const request = new AbortController();
  const timeout = setTimeout(() => request.abort(), 2500);
  try {
    const response = await fetch('./status', { cache: 'no-store', signal: request.signal });
    if (!response.ok) throw new Error('Status unavailable');
    const status = await response.json();
    if (!status.camera || !status.pico) throw new Error('Invalid status');
    lastStatus = status;
  } catch {
    lastStatus = null;
  } finally {
    clearTimeout(timeout);
  }
  record(lastStatus, render(lastStatus));
  renderTrend();
  setTimeout(poll, 400);
}

applyLanguage();
void poll();
