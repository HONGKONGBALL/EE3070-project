export function drawChart(canvas, channels, now = Date.now()) {
  const { width, height } = canvas.getBoundingClientRect();
  if (!width) return;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, width, height);
  const left = 40, right = width - 9, top = 17, bottom = height - 26;
  const series = channels.map(channel => ({ ...channel, points: (channel.points || []).filter(point => point.time >= now - 30000) }));
  const maxCm = Math.max(100, ...series.flatMap(channel => channel.points.map(point => point.cm || 0)));
  const scale = Math.ceil(maxCm / 100) * 100;
  ctx.font = '10px "SFMono-Regular", Consolas, monospace'; ctx.fillStyle = '#8495a1';
  ctx.strokeStyle = '#e5edf1'; ctx.lineWidth = 1;
  for (let step = 0; step <= 4; step += 1) {
    const y = bottom - (bottom - top) * step / 4;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillText(String(Math.round(scale * step / 4)), 2, y + 3);
  }
  ctx.fillText('cm', 2, 8); ctx.fillText('−30 s', left, height - 5);
  ctx.fillText('−15 s', (left + right) / 2 - 16, height - 5); ctx.fillText('现在', right - 24, height - 5);
  for (const channel of series) {
    ctx.strokeStyle = channel.color; ctx.lineWidth = 2; ctx.beginPath();
    let previousTime = null;
    for (const point of channel.points) {
      if (point.cm === null) { previousTime = null; continue; }
      const x = left + (right - left) * (point.time - (now - 30000)) / 30000;
      const y = bottom - (bottom - top) * point.cm / scale;
      if (previousTime === null || point.time - previousTime > 1000) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      previousTime = point.time;
    }
    ctx.stroke();
  }
  if (!series.some(channel => channel.points.length)) {
    ctx.font = '12px -apple-system, sans-serif'; ctx.fillStyle = '#8b9ba6'; ctx.textAlign = 'center';
    ctx.fillText(channels.length ? '连接 Pico，或点击“查看演示”' : '勾选上方通道以显示曲线', (left + right) / 2, (top + bottom) / 2);
    ctx.textAlign = 'start';
  }
}
