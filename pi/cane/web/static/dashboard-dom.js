export const byId = (id) => document.getElementById(id);
export const text = (id, value) => { byId(id).textContent = value ?? '—'; };
export const display = (value) => {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
};
export const yesNo = (value, yes = '是', no = '否') => value === true ? yes : value === false ? no : '未知';
export const number = (value, digits = 1, suffix = '') => Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : '—';

export function facts(id, values) {
  const fragment = document.createDocumentFragment();
  for (const [label, value] of values) {
    const term = document.createElement('dt');
    const definition = document.createElement('dd');
    term.textContent = label;
    definition.textContent = display(value);
    fragment.append(term, definition);
  }
  byId(id).replaceChildren(fragment);
}

export function table(id, rows, empty = '尚无记录') {
  const fragment = document.createDocumentFragment();
  for (const cells of rows) {
    const row = document.createElement('tr');
    for (const cell of cells) {
      const td = document.createElement('td');
      td.textContent = display(cell);
      row.append(td);
    }
    fragment.append(row);
  }
  if (!rows.length) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 4;
    cell.className = 'empty';
    cell.textContent = empty;
    row.append(cell);
    fragment.append(row);
  }
  byId(id).replaceChildren(fragment);
}

export function feedback(message, error = false) {
  const box = byId('action-feedback');
  box.hidden = !message;
  box.classList.toggle('error', error);
  box.textContent = message;
}

export async function request(path, body, format = 'json') {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    cache: 'no-store', credentials: 'same-origin', signal: AbortSignal.timeout(8000),
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`;
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      const data = await response.json();
      if (typeof data.detail === 'string') detail += `：${data.detail}`;
    }
    throw new Error(detail);
  }
  return format === 'text' ? response.text() : response.json();
}

export function errorMessage(error) {
  return error instanceof Error ? error.message : '请求未完成，请检查主机连接';
}

export function responseFeedback(path, result, label, voiceBench = false) {
  if (result.accepted === false || result.queued === false || result.error) {
    return { message: `${label}未接受：${result.error || result.reason || '请查看主机状态'}`, error: true };
  }
  if (path === '/api/voice/text') {
    if (result.search_state) {
      return { message: '', error: false };
    }
    if (result.matched === null && !result.queued && !result.accepted) {
      return { message: '未匹配到目的地，请换一个说法或使用已配置的目的地名称。', error: true };
    }
    if (result.name) {
      return { message: `已匹配“${result.name}”，请核对读回结果，再按“确认 / 继续”。`, error: false };
    }
  }
  if (path === '/api/voice/select' && (result.accepted === true || result.name)) {
    const name = result.name ? `“${result.name}”` : '目的地';
    if (voiceBench) return { message: `已选择${name}。`, error: false };
    return { message: `已选择${name}，请按“确认 / 继续”开始导航。`, error: false };
  }
  return { message: `${label}已送交主机。请从状态和最近输出核实处理结果。`, error: false };
}
