import { byId, text, feedback, request, errorMessage, responseFeedback } from './dashboard-dom.js';
import {
  renderStatus, renderDisconnected, reasonText, controlDisabledReason, renderOriginSearch, renderVoiceSearch, voiceSelection,
} from './dashboard-render.js';
import { setupReplay } from './dashboard-replay.js';

let currentStatus = null;
let connected = false;
let refreshing = false;
let posting = false;
let previewPending = false;
let offeredSearchId = '';
let offeredQuery = '';

function updateControls() {
  const voice = currentStatus?.voice;
  const wake = byId('wake-switch');
  const voiceButton = byId('host-voice-button');
  const voiceBench = currentStatus?.host?.mode === 'voice';
  const enabled = voice?.wake_enabled === true;
  const canStop = voice?.can_stop_recording === true;
  voiceButton.dataset.action = canStop ? 'voice_stop' : 'voice_input';
  text('host-voice-button', canStop ? '结束录音' : voiceBench ? '说出目的地' : '主机离线语音输入');
  byId('wake-controls').hidden = !connected || !voiceBench;
  byId('voice-provider').hidden = !connected || voice?.place_search_online !== true;
  text('voice-provider', voice?.place_search_source === 'apple'
    ? 'Apple 地图提供地点候选。'
    : '高德地图接收地点关键词并返回候选。Mac 在本机识别语音。');
  renderOriginSearch(connected && voiceBench && voice?.place_search_source === 'apple' ? voice : null, posting);
  const editor = byId('voice-text');
  if (connected && voice?.search_id && voice.search_id !== offeredSearchId) {
    offeredSearchId = voice.search_id;
    if (voice.query && (!editor.value || editor.value === offeredQuery) && document.activeElement !== editor) {
      editor.value = offeredQuery = voice.query;
    }
  }
  wake.dataset.action = enabled ? 'wake_disable' : 'wake_enable';
  wake.setAttribute('aria-pressed', String(enabled));
  text('wake-switch', enabled ? '暂停语音唤醒' : '开启语音唤醒');
  const phrase = `「${voice?.wake_phrase || '你好导航'}」`;
  text('wake-note', voice?.wake_retrying && enabled ? '请在系统设置中允许麦克风访问，主机会重试。'
    : enabled ? `说${phrase}，听到提示后说出目的地。` : `唤醒词是${phrase}。`);
  for (const button of document.querySelectorAll('[data-action]')) {
    const reason = currentStatus === null ? '等待主机状态' : controlDisabledReason(button.dataset.action, currentStatus);
    button.disabled = !connected || posting || Boolean(reason);
    button.title = reason;
  }
  const voiceReason = currentStatus === null ? '等待主机状态' : controlDisabledReason('voice_input', currentStatus);
  let voiceNote = voiceReason || '使用主机麦克风录音，并由本地模型识别目的地。';
  if (canStop) {
    voiceNote = voice.recording ? '正在录音。说完后，短按 K2 或点「结束录音」。' : '听到提示后开始说。';
  } else if (voice?.listening && voice.manual_stop) {
    voiceNote = '正在识别刚才的录音。';
  } else if (!voiceReason && voiceBench) {
    voiceNote = voice.manual_stop ? '短按 K2 或点击开始，说完后再按一次。' : '点击后，听到提示再说地名。';
  }
  text('host-voice-note', connected ? voiceNote : '主机尚未连接');
  byId('hardware-controls-note').hidden = !connected || currentStatus?.sim === true || currentStatus?.serial?.controls_available === true;
  byId('send-destination').disabled = !connected || posting;
  text('send-destination', voiceBench ? '搜索地点' : '识别目的地');
  byId('apply-scenario').disabled = !connected || posting || currentStatus?.sim !== true;
  byId('scenario').disabled = !connected || posting || currentStatus?.sim !== true;
  renderVoiceSearch(connected ? currentStatus?.voice : null, posting);
  const reasons = currentStatus?.resume_blocked_reasons;
  if (Array.isArray(reasons) && reasons.length) {
    document.querySelector('[data-action="resume"]').title = `恢复仍受限制：${reasonText(reasons)}`;
  }
}

function refreshPreview() {
  const camera = currentStatus?.camera || {};
  const preview = byId('camera-preview');
  text('camera-badge', camera.ready ? '实时画面' : '不可用');
  if (!connected || camera.ready !== true) {
    preview.hidden = true;
    preview.removeAttribute('src');
    text('camera-note', camera.error || '相机未就绪。连接并启动相机后显示真实画面。');
    return;
  }
  if (previewPending) return;
  previewPending = true;
  preview.src = `/api/camera/frame.jpg?t=${Date.now()}`;
}
byId('camera-preview').addEventListener('load', () => {
  previewPending = false;
  byId('camera-preview').hidden = false;
  text('camera-note', '真实相机画面。视觉提示交由主机统一仲裁。');
});
byId('camera-preview').addEventListener('error', () => {
  previewPending = false;
  byId('camera-preview').hidden = true;
  text('camera-badge', '暂无新帧');
  text('camera-note', '无法读取新画面，请检查相机状态。');
});

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    currentStatus = await request('/api/status');
    connected = true;
    renderStatus(currentStatus);
    refreshPreview();
  } catch (error) {
    connected = false;
    renderDisconnected(errorMessage(error));
  } finally {
    refreshing = false;
    updateControls();
  }
}

async function send(path, payload, label) {
  if (posting) return;
  posting = true;
  updateControls();
  try {
    const result = await request(path, payload);
    const message = responseFeedback(path, result, label, currentStatus?.host?.mode === 'voice');
    feedback(path.startsWith('/api/voice/origin/') && !message.error ? '' : message.message, message.error);
    await refresh();
  } catch (error) {
    feedback(`${label}失败：${errorMessage(error)}`, true);
  } finally {
    posting = false;
    updateControls();
  }
}

for (const button of document.querySelectorAll('[data-action]')) {
  button.addEventListener('click', () => {
    if (!button.disabled) send('/api/control', { action: button.dataset.action }, button.textContent);
  });
}
byId('voice-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const value = byId('voice-text').value.trim();
  if (value) send('/api/voice/text', { text: value }, '目的地识别');
});
byId('destination-candidates').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-candidate-id]');
  if (!connected || posting || !button || button.disabled) return;
  const selection = voiceSelection(currentStatus?.voice, button.dataset.searchId, button.dataset.candidateId);
  if (selection) send('/api/voice/select', selection, '选择目的地');
});
byId('origin-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const value = byId('origin-text').value.trim();
  if (value && !byId('search-origin').disabled) send('/api/voice/origin/search', { text: value }, '搜索出发点');
});
byId('origin-candidates').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-candidate-id]');
  if (byId('origin-controls').hidden || !button || button.disabled) return;
  const selection = voiceSelection(currentStatus?.voice?.origin_search, button.dataset.searchId, button.dataset.candidateId);
  if (selection) send('/api/voice/origin/select', selection, '选择出发点');
});
byId('use-phone-origin').addEventListener('click', () => {
  if (!byId('use-phone-origin').disabled) send('/api/voice/origin/phone', {}, '使用 iPhone 当前位置');
});
byId('apply-scenario').addEventListener('click', () => send('/api/sim/scenario', { name: byId('scenario').value }, '模拟场景'));
byId('refresh-button').addEventListener('click', refresh);

function setupSpeech() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const button = byId('speech-button');
  if (!Recognition || !window.isSecureContext) {
    button.disabled = true;
    text('speech-note', !Recognition ? '此浏览器不支持语音输入，请使用文字。' : '麦克风输入需要 HTTPS 或 localhost，请使用文字。');
    return;
  }
  const recognition = new Recognition();
  recognition.continuous = false;
  recognition.interimResults = false;
  button.addEventListener('click', () => {
    recognition.lang = currentStatus?.language === 'en' ? 'en-US' : currentStatus?.language === 'yue' ? 'zh-HK' : 'zh-CN';
    try {
      recognition.start();
      button.disabled = true;
      text('speech-note', '正在聆听，请说出目的地…');
    } catch (error) {
      text('speech-note', `无法开始识别：${errorMessage(error)}。请使用文字。`);
    }
  });
  recognition.addEventListener('result', (event) => {
    byId('voice-text').value = event.results[0][0].transcript.slice(0, 200);
    text('speech-note', '识别文字已填入，请核对后点击“识别目的地”。');
    byId('voice-text').focus();
  });
  recognition.addEventListener('error', (event) => text('speech-note', `语音输入未完成（${event.error}），请使用文字。`));
  recognition.addEventListener('end', () => { button.disabled = false; });
  text('speech-note', '点击后授权麦克风；识别结果先填入文本框。');
}

updateControls();
setupSpeech();
setupReplay();
refresh();
setInterval(refresh, 1000);
