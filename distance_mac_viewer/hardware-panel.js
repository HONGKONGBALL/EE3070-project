const timeFormat = new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
});

/** Render controller state; physical operation is confirmed by the observer. */
export function createHardwarePanel({ onStart, onStop }) {
  const panel = document.getElementById('hardware-panel');
  const status = document.getElementById('hardware-status');
  const countdown = document.getElementById('hardware-countdown');
  const stop = document.getElementById('hardware-stop');
  const events = document.getElementById('hardware-events');
  const actions = [...panel.querySelectorAll('[data-hardware-target]')];
  let lastEvents = '';
  let lastTarget = null;

  function resetKeys() {
    for (let index = 1; index <= 4; index += 1) {
      const key = document.getElementById(`hardware-key-${index}`);
      key.dataset.state = 'idle';
      key.querySelector('b').textContent = '等待检测';
    }
  }

  for (const action of actions) {
    action.addEventListener('click', () => {
      const target = action.dataset.hardwareTarget;
      if (target === 'buttons') resetKeys();
      onStart(target);
    });
  }
  stop.addEventListener('click', onStop);

  function render(state) {
    const working = state.busy || state.pending;
    panel.dataset.state = !state.available ? 'unavailable' : state.status === 'error'
      ? 'error' : state.pending ? 'pending' : state.busy ? 'running' : 'ready';
    status.textContent = state.message;
    countdown.textContent = state.busy && state.remainingMs > 0
      ? `${(state.remainingMs / 1000).toFixed(1)} 秒`
      : state.pending ? '等待回执' : '—';
    stop.disabled = !state.available;
    for (const action of actions) {
      action.disabled = !state.available || working;
      action.dataset.active = String(working && action.dataset.hardwareTarget === state.target);
    }
    if (state.target === 'buttons' && lastTarget !== 'buttons' && state.busy) resetKeys();
    if (lastTarget === 'buttons' && !state.busy) {
      for (let index = 1; index <= 4; index += 1) {
        const key = document.getElementById(`hardware-key-${index}`);
        key.dataset.state = 'idle';
        key.querySelector('b').textContent = '检测已结束';
      }
    }
    lastTarget = state.busy ? state.target : null;
    const recent = state.events.slice(-6).reverse();
    const signature = JSON.stringify(recent);
    if (signature !== lastEvents) {
      events.replaceChildren();
      if (recent.length === 0) {
        const item = document.createElement('li');
        item.textContent = '尚未发送测试指令';
        events.append(item);
      }
      for (const event of recent) {
        const item = document.createElement('li');
        const time = document.createElement('time');
        time.dateTime = new Date(event.at).toISOString();
        time.textContent = timeFormat.format(event.at);
        item.append(time, document.createTextNode(event.text));
        events.append(item);
      }
      lastEvents = signature;
    }
  }

  function button(event) {
    const key = document.getElementById(`hardware-key-${event.index}`);
    key.dataset.state = event.state;
    const duration = `${(event.duration_ms / 1000).toFixed(1)} 秒`;
    const labels = { pressed: '已按下', released: `已松开 · ${duration}`, long: `长按 · ${duration}` };
    key.querySelector('b').textContent = labels[event.state];
  }

  return { render, button };
}
