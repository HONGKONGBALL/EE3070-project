// Shows the Pi dashboard's MJPEG preview. The page only loads images from the Pi;
// it sends no commands, so the camera service keeps running on its own.
export const DEFAULT_CAMERA_PORT = 8011;
const STORAGE_KEY = 'ee3070-camera-host';

/** Turn "192.168.0.161" or "pi.local:8011" into the dashboard origin, or null. */
export function cameraOrigin(input) {
  const text = String(input ?? '').trim().replace(/^http:\/\//i, '').replace(/\/+$/, '');
  const match = /^([a-z0-9]([a-z0-9.-]*[a-z0-9])?)(?::(\d{1,5}))?$/i.exec(text);
  if (!match) return null;
  const port = match[3] === undefined ? DEFAULT_CAMERA_PORT : Number(match[3]);
  if (port < 1 || port > 65535) return null;
  return `http://${match[1].toLowerCase()}:${port}`;
}

export function createCameraPanel({ document: doc = document, storage = safeStorage() } = {}) {
  const $ = id => doc.getElementById(id);
  const panel = $('camera-panel'), image = $('camera'), host = $('camera-host');
  let origin = null, timer = null;

  function show(state, message) {
    panel.dataset.state = state;
    $('camera-status').textContent = { idle: '未连接', loading: '正在连接', live: '实时画面', error: '画面不可用' }[state];
    $('camera-empty').textContent = message;
    image.hidden = state !== 'live';
    $('camera-empty').hidden = state === 'live';
    $('camera-stop').disabled = state === 'idle';
    $('camera-open').hidden = !origin;
    if (origin) $('camera-open').href = origin + '/';
  }
  function stop(message = '输入树莓派地址后点击“显示画面”。') {
    clearTimeout(timer); timer = null;
    image.removeAttribute('src'); origin = null;
    show('idle', message);
  }
  function start() {
    const next = cameraOrigin(host.value);
    if (!next) {
      stop(); show('error', '地址格式不对。请输入树莓派的 IP，例如 192.168.0.161。');
      return;
    }
    clearTimeout(timer);
    origin = next; storage?.setItem(STORAGE_KEY, host.value.trim());
    show('loading', `正在连接 ${origin} …`);
    image.src = `${origin}/stream?start=${Date.now()}`;
    timer = setTimeout(() => {
      if (panel.dataset.state === 'loading') fail();
    }, 6000);
  }
  function fail() {
    clearTimeout(timer); timer = null;
    const tried = origin;
    image.removeAttribute('src');
    show('error', `无法从 ${tried} 读取画面。请确认 Mac 与树莓派在同一 Wi-Fi，且树莓派的摄像头服务已启动。`);
  }

  image.addEventListener('load', () => {
    if (!image.hasAttribute('src')) return;
    clearTimeout(timer); timer = null; show('live', '');
  });
  image.addEventListener('error', () => { if (image.hasAttribute('src')) fail(); });
  $('camera-form').addEventListener('submit', event => { event.preventDefault(); start(); });
  $('camera-stop').addEventListener('click', () => stop());
  host.value = storage?.getItem(STORAGE_KEY) || '';
  stop();
  return { start, stop };
}

function safeStorage() {
  try { return globalThis.localStorage ?? null; } catch { return null; }
}
