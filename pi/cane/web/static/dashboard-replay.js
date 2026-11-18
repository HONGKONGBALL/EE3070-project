import { byId, text, table, request, errorMessage } from './dashboard-dom.js';

let events = [];
let position = 0;
let timer = null;
let selectedName = '';
let loadedName = '';
let files = [];

function stop() {
  if (timer !== null) clearInterval(timer);
  timer = null;
  text('replay-play', '播放');
}

function showEvent(next) {
  position = Math.min(Math.max(next, 0), Math.max(events.length - 1, 0));
  const event = events[position];
  byId('replay-position').value = String(position);
  text('replay-count', `${position + 1} / ${events.length}`);
  byId('replay-prev').disabled = position === 0;
  byId('replay-next').disabled = position >= events.length - 1;
  const timestamp = Number.isFinite(event.ts) ? new Date(event.ts * 1000).toLocaleString('zh-CN') : event.t ?? '—';
  const details = { ...event };
  delete details.ts;
  delete details.kind;
  table('replay-event', [[timestamp, event.kind || '未分类', JSON.stringify(details)]]);
}

function selectLog() {
  selectedName = byId('log-select').value;
  const download = byId('download-log');
  download.setAttribute('aria-disabled', String(!selectedName));
  if (selectedName) download.href = `/api/logs/${encodeURIComponent(selectedName)}`;
  else download.removeAttribute('href');
  download.download = selectedName;
  const selected = files.find((file) => file.name === selectedName);
  const tooLarge = selected && selected.bytes > 20 * 1024 * 1024;
  byId('load-replay').disabled = !selectedName || tooLarge;
  byId('download-csv').disabled = !events.length || loadedName !== selectedName;
  if (tooLarge) text('log-note', '此日志超过 20 MB，请下载原始文件后在本地分析。');
}

async function loadFiles() {
  try {
    const list = await request('/api/logs');
    const signature = JSON.stringify(list);
    if (signature === JSON.stringify(files) && byId('log-select').options[0]?.value) return;
    files = list;
    const fragment = document.createDocumentFragment();
    for (const file of files) {
      const option = document.createElement('option');
      option.value = file.name;
      option.textContent = `${file.name} · ${(file.bytes / 1024).toFixed(1)} KB`;
      fragment.append(option);
    }
    if (!files.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = '主机尚无日志';
      fragment.append(option);
    }
    byId('log-select').replaceChildren(fragment);
    if (files.some((file) => file.name === selectedName)) byId('log-select').value = selectedName;
    selectLog();
  } catch (error) {
    text('log-note', `无法读取日志列表：${errorMessage(error)}`);
  }
}

async function loadReplay() {
  if (!selectedName) return;
  const name = selectedName;
  stop();
  byId('load-replay').disabled = true;
  try {
    const content = await request(`/api/logs/${encodeURIComponent(name)}`, undefined, 'text');
    const parsed = [];
    let skipped = 0;
    const lines = content.split('\n').filter((line) => line.trim());
    for (const line of lines.slice(-50000)) {
      try {
        const value = JSON.parse(line);
        if (value && typeof value === 'object' && !Array.isArray(value)) parsed.push(value);
        else skipped++;
      } catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
        skipped++;
      }
    }
    events = parsed;
    loadedName = name;
    byId('replay').hidden = !events.length;
    byId('download-csv').disabled = !events.length || name !== selectedName;
    byId('replay-position').max = String(Math.max(events.length - 1, 0));
    if (events.length) showEvent(0);
    text('log-note', `已载入 ${events.length} 条事件，跳过 ${skipped} 条非 JSON 记录。${lines.length > 50000 ? '仅载入最后 50,000 行。' : ''}回放仅查看记录，不会执行控制。`);
  } catch (error) {
    text('log-note', `载入失败：${errorMessage(error)}`);
  } finally {
    selectLog();
  }
}

export function csvCell(value) {
  const raw = value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const safe = /^[-+=@\t\r]/.test(raw) ? `'${raw}` : raw;
  return `"${safe.replaceAll('"', '""')}"`;
}

function downloadCsv() {
  const keys = [...new Set(events.flatMap((event) => Object.keys(event)))];
  const lines = [keys.map(csvCell).join(','), ...events.map((event) => keys.map((key) => csvCell(event[key])).join(','))];
  const url = URL.createObjectURL(new Blob(['\ufeff', lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = loadedName.replace(/\.[^.]+$/, '') + '.csv';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function setupReplay() {
  byId('log-select').addEventListener('change', selectLog);
  byId('load-replay').addEventListener('click', loadReplay);
  byId('download-csv').addEventListener('click', downloadCsv);
  byId('replay-prev').addEventListener('click', () => { stop(); showEvent(position - 1); });
  byId('replay-next').addEventListener('click', () => { stop(); showEvent(position + 1); });
  byId('replay-position').addEventListener('input', () => { stop(); showEvent(Number(byId('replay-position').value)); });
  byId('replay-play').addEventListener('click', () => {
    if (timer !== null) { stop(); return; }
    if (position >= events.length - 1) showEvent(0);
    text('replay-play', '暂停');
    timer = setInterval(() => {
      if (position >= events.length - 1) { stop(); return; }
      showEvent(position + 1);
    }, 700);
  });
  loadFiles();
  setInterval(loadFiles, 15000);
}
