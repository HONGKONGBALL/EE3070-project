import { HardwareDecoder, TESTS, commandFor } from './hardware-protocol.js';

/** Track command acknowledgements; never infer a physical pass from software state. */
export class HardwareController {
  constructor({ send, onChange = () => {}, onButton = () => {}, now = Date.now, timers = globalThis }) {
    Object.assign(this, { send, onChange, onButton, now, timers, nextId: 1, events: [],
      active: null, stopId: null, ready: false, connected: false, allowed: false,
      transport: 'idle', status: 'idle', message: '连接 Pico 后，可在这里单独测试组件。',
      decoder: new HardwareDecoder(), lastPing: 0, epoch: 0 });
    this.timer = timers.setInterval(() => this.tick(), 250);
    this.notify();
  }
  get state() {
    const remainingMs = this.active?.startedAt === undefined ? 0
      : Math.max(0, TESTS[this.active.target].duration - (this.now() - this.active.startedAt));
    return { available: this.connected && this.ready && this.allowed,
      busy: !!this.active || this.stopId !== null,
      pending: this.active?.status === 'pending' || this.stopId !== null,
      target: this.active?.target || null, status: this.status, remainingMs,
      message: this.message, events: [...this.events] };
  }
  notify() { this.onChange(this.state); }
  record(text) {
    this.events.push({ at: this.now(), text });
    if (this.events.length > 8) this.events.shift();
  }
  connection(state, { upload = true } = {}) {
    this.transport = state;
    if (state === 'connecting' || !['uploading', 'connected', 'receiving'].includes(state)) {
      this.epoch += 1;
      const interrupted = !!this.active || this.stopId !== null;
      this.ready = false; this.active = null; this.stopId = null;
      this.decoder = new HardwareDecoder();
      this.status = 'idle';
      this.message = interrupted ? '连接已中断，本轮实物结果未确认。'
        : state === 'demo' ? '演示模式不控制实物；连接 Pico 后可测试。'
        : state === 'connecting' ? '等待 Pico 测试程序准备。' : '连接 Pico 后，可在这里单独测试组件。';
    }
    this.allowed = upload && state !== 'demo';
    this.connected = ['connected', 'receiving'].includes(state);
    if (!upload) this.message = '只接收模式不发送测试指令，请改用临时运行模式后连接。';
    else if (this.connected && !this.ready) this.message = '等待 Pico 的测试功能就绪回执。';
    else if (this.state.available && !this.state.busy) this.message = '测试已就绪。选择一项，观察实物动作。';
    this.notify();
  }
  receive(text) {
    if (!this.allowed || !['uploading', 'connected', 'receiving'].includes(this.transport)) return;
    for (const event of this.decoder.feed(text)) {
      switch (event.kind) {
        case 'ready': this.ready = true; break;
        case 'button':
          if (this.active?.target === 'buttons' && event.value.id === this.active.id) this.onButton(event.value);
          break;
        case 'state': this.handle(event.value); break;
        default: throw new Error('未知测试事件');
      }
    }
    this.notify();
  }
  handle(event) {
    if (event.id === this.stopId && event.target === 'stop') {
      if (event.state === 'done') {
        this.stopId = null; this.active = null; this.status = 'stopped';
        this.message = 'Pico 已确认全部输出关闭。'; this.record(this.message);
      } else if (event.state === 'error') this.fail('Pico 未能确认关闭，请断开后重新连接。');
      return;
    }
    if (!this.active || event.id !== this.active.id || event.target !== this.active.target) return;
    const label = TESTS[event.target].label;
    if (event.state === 'started') {
      if (this.active.status !== 'pending' || this.stopId !== null) return;
      this.active.status = 'running'; this.active.startedAt = this.now(); this.status = 'running';
      this.message = `${label}正在执行，请观察实物。`; this.record(`${label}：Pico 已确认开始`);
      return;
    }
    const messages = { done: `${label}指令已完成，请以实物观察确认效果。`,
      stopped: `${label}指令已停止。`, link_lost: 'Pico 因心跳中断已关闭输出。',
      error: `${label}未执行：${event.reason === 'busy' ? 'Pico 正忙' : '指令被拒绝'}。` };
    this.active = null; this.status = event.state; this.message = messages[event.state]; this.record(this.message);
  }
  async start(target) {
    if (!this.state.available || this.state.busy || target === 'stop' || !Object.hasOwn(TESTS, target)) return false;
    const id = this.nextId++;
    const epoch = this.epoch;
    this.active = { id, target, status: 'pending', sentAt: this.now() };
    this.status = 'starting'; this.message = `等待 Pico 确认${TESTS[target].label}指令。`;
    this.lastPing = this.now(); this.record(`${TESTS[target].label}：已请求测试`); this.notify();
    try { await this.send(commandFor(id, target)); return true; }
    catch (error) {
      if (epoch === this.epoch) this.fail(`指令发送失败，输出状态未确认：${error.message}`);
      return false;
    }
  }
  async stop(message = '正在请求立即停止，等待 Pico 回执。') {
    if (!this.state.available || this.stopId !== null) return false;
    const epoch = this.epoch;
    this.stopId = this.nextId++; this.stopAt = this.now(); this.status = 'stopping'; this.message = message; this.notify();
    try { await this.send(commandFor(this.stopId, 'stop')); return true; }
    catch (error) {
      if (epoch === this.epoch) this.fail(`停止状态未确认：${error.message}`);
      return false;
    }
  }
  fail(message) {
    this.ready = false; this.active = null; this.stopId = null; this.status = 'error';
    this.message = message; this.record(message); this.notify();
  }
  tick() {
    const now = this.now();
    if (this.stopId !== null && now - this.stopAt > 2000) this.fail('未收到停止回执，输出状态未确认，请断开后重新连接。');
    else if (this.active && this.stopId === null) {
      const waiting = this.active.status === 'pending';
      const expired = waiting ? now - this.active.sentAt > 2000
        : now - this.active.startedAt > TESTS[this.active.target].duration + 1500;
      if (expired) void this.stop('未收到 Pico 执行回执，已请求停止。');
      else if (now - this.lastPing >= 500) {
        const epoch = this.epoch;
        this.lastPing = now;
        void this.send('HW_PING\n').catch(error => {
          if (epoch === this.epoch) this.fail(`心跳发送失败，输出状态未确认：${error.message}`);
        });
      }
    }
    this.notify();
  }
  dispose() { this.timers.clearInterval(this.timer); }
}
