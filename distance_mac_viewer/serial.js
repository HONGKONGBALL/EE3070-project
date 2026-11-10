const encoder = new TextEncoder();
const telemetry = /(?:^|[\r\n])(?:US_DATA,\d+,\d+,-?\d+,-?\d+|TF_DATA,\d+,-?\d+,\d+,\d+)(?=[\r\n])/;
const abortError = () => new DOMException('连接已取消', 'AbortError');
const controlDurations = Object.freeze({ motor: '5000', buzzer: '5000', cob: '3000', led: '3000', buttons: '45000', stop: '0' });

function validControl(line) {
  if (line === 'HW_PING\n') return true;
  if (typeof line !== 'string') return false;
  const match = /^HW_CMD,([1-9]\d{0,9}),(motor|buzzer|cob|led|buttons|stop),(\d+)\n$/.exec(line);
  return match !== null && match[0] === line && Number(match[1]) <= 1000000000
    && controlDurations[match[2]] === match[3];
}

/** One cancellable Web Serial session; uploaded MicroPython runs only in RAM. */
export class PicoSerial {
  constructor({ onText = () => {}, onState = () => {}, onError = () => {}, programPath = '/distance_pico_stream.py', diagnosticsPaths = [] } = {}) {
    Object.assign(this, { onText, onState, onError, programPath, diagnosticsPaths, session: null });
  }

  async connect({ upload = true } = {}) {
    if (this.session) return;
    if (!globalThis.navigator?.serial) {
      this.onError('请使用支持 Web Serial 的 Mac Chrome 打开此页。');
      this.onState('idle');
      return;
    }
    const session = {
      abort: new AbortController(), decoder: new TextDecoder(), listeners: new Set(),
      output: '', uploadStarted: false, ready: false, uploading: upload,
      uploaded: false, commandTail: Promise.resolve(),
    };
    this.session = session;
    this.onState('connecting');
    try {
      // Keep the chooser in the original click's user-activation call stack.
      const choice = navigator.serial.requestPort();
      session.port = await choice;
      this.check(session);
      session.openTask = session.port.open({ baudRate: 115200 }).then(() => {
        session.opened = true;
      });
      await session.openTask;
      this.check(session);
      session.reader = session.port.readable.getReader();
      if (upload) session.writer = session.port.writable.getWriter();
      session.readTask = this.read(session);
      session.readTask.then((error) => {
        if (!session.abort.signal.aborted) {
          this.onError(error || '串口数据流已结束，请重新连接 Pico。');
          void this.end(session);
        }
      });
      if (upload) {
        this.onState('uploading');
        await this.upload(session);
        session.uploaded = true;
      }
      this.check(session);
      session.uploading = false;
      this.onState('connected');
      if (session.ready && !session.abort.signal.aborted) this.onState('receiving');
    } catch (error) {
      const cancelled = error.name === 'NotFoundError' || error.name === 'AbortError';
      if (!cancelled && !session.abort.signal.aborted) this.onError(error.message || String(error));
      await this.end(session, error.name === 'NotFoundError' ? 'idle' : 'disconnected');
    }
  }

  async disconnect() {
    if (this.session) await this.end(this.session);
  }

  async sendCommand(line) {
    if (!validControl(line)) throw new Error('无效的组件测试指令。');
    const session = this.session;
    if (!session?.uploaded || session.uploading || !session.writer) {
      throw new Error('请先连接并临时运行测距与测试程序。');
    }
    this.check(session);
    const pending = session.commandTail.then(() => this.write(session, encoder.encode(line)));
    // A failed command is reported to its caller; subsequent queued commands still check this session.
    session.commandTail = pending.catch(() => {});
    return pending;
  }

  check(session) {
    if (session.abort.signal.aborted || this.session !== session) throw abortError();
  }

  async read(session) {
    try {
      while (!session.abort.signal.aborted) {
        const { value, done } = await session.reader.read();
        if (done) break;
        if (value) this.consume(session, session.decoder.decode(value, { stream: true }));
      }
      const remaining = session.decoder.decode();
      if (remaining) this.consume(session, remaining);
      return null;
    } catch (error) {
      return `串口读取中断：${error.message || error}`;
    } finally {
      session.reader.releaseLock();
      session.reader = null;
    }
  }

  consume(session, chunk) {
    session.output = (session.output + chunk).slice(-32768);
    this.onText(chunk);
    if (telemetry.test(session.output) && !session.ready) {
      session.ready = true;
      if (!session.uploading && !session.abort.signal.aborted) this.onState('receiving');
    }
    for (const listener of [...session.listeners]) listener();
  }

  async upload(session) {
    const sources = await Promise.all([...this.diagnosticsPaths, this.programPath].map(async path => {
      const response = await fetch(path, { cache: 'no-store', signal: session.abort.signal });
      if (!response.ok) throw new Error(`无法加载 Pico 程序：${path}`);
      return (await response.text()).replace(/\r\n/g, '\n');
    }));
    const program = encoder.encode(sources.join('\n'));
    this.check(session);
    session.uploadStarted = true;
    await this.write(session, encoder.encode('\x03\x03'));
    await this.pause(session, 180);
    session.output = '';
    await this.write(session, encoder.encode('\x05'));
    await this.waitFor(session, /paste mode; Ctrl-C to cancel, Ctrl-D to finish/, 4000,
      'Pico 未响应 MicroPython 粘贴模式；请查看串口诊断后重试。');
    for (let offset = 0; offset < program.length; offset += 64) {
      await this.write(session, program.slice(offset, offset + 64));
      await this.pause(session, 15);
    }
    session.output = '';
    session.ready = false;
    await this.write(session, encoder.encode('\x04'));
    await this.waitFor(session, telemetry, 10000, 'Pico 未输出完整测距数据（TF_DATA 或 US_DATA）；请查看串口诊断。');
  }

  async write(session, bytes) {
    this.check(session);
    await new Promise((resolve, reject) => {
      const finish = (callback, result) => {
        session.abort.signal.removeEventListener('abort', cancelled);
        callback(result);
      };
      const cancelled = () => finish(reject, abortError());
      session.abort.signal.addEventListener('abort', cancelled, { once: true });
      session.writer.write(bytes).then(
        (result) => finish(resolve, result), (error) => finish(reject, error),
      );
    });
    this.check(session);
  }

  pause(session, milliseconds) {
    this.check(session);
    return new Promise((resolve, reject) => {
      const finish = (error) => {
        clearTimeout(timer);
        session.abort.signal.removeEventListener('abort', cancelled);
        if (error) reject(error); else resolve();
      };
      const cancelled = () => finish(abortError());
      const timer = setTimeout(() => finish(), milliseconds);
      session.abort.signal.addEventListener('abort', cancelled, { once: true });
    });
  }

  waitFor(session, pattern, milliseconds, message) {
    this.check(session);
    return new Promise((resolve, reject) => {
      const finish = (error) => {
        clearTimeout(timer);
        session.listeners.delete(inspect);
        session.abort.signal.removeEventListener('abort', cancelled);
        if (error) reject(error); else resolve();
      };
      const inspect = () => {
        if (/Traceback \(most recent call last\):|(?:SyntaxError|IndentationError|MemoryError):/.test(session.output)) {
          finish(new Error(`Pico 执行程序失败：${session.output.trim().slice(-1000)}`));
        } else if (pattern.test(session.output)) finish();
      };
      const cancelled = () => finish(abortError());
      const timer = setTimeout(() => finish(new Error(message)), milliseconds);
      session.listeners.add(inspect);
      session.abort.signal.addEventListener('abort', cancelled, { once: true });
      inspect();
    });
  }

  async end(session, state = 'disconnected') {
    if (session.closing) return session.closing;
    session.abort.abort();
    session.closing = Promise.resolve().then(async () => {
      // A disconnect can arrive while open() is still pending.
      try { await session.openTask; } catch { /* open failure owns no stream locks */ }
      let stop;
      if (session.writer && session.uploadStarted) {
        stop = session.writer.write(encoder.encode('\x03')).catch(() => {});
      }
      if (session.reader) {
        const reader = session.reader;
        try { await reader.cancel(); } catch { /* already disconnected */ }
        if (!session.readTask) { reader.releaseLock(); session.reader = null; }
      }
      await session.readTask;
      if (session.writer) {
        if (stop) {
          let timer;
          const finished = await Promise.race([
            stop.then(() => true), new Promise((resolve) => { timer = setTimeout(() => resolve(false), 300); }),
          ]);
          clearTimeout(timer);
          if (!finished) void session.writer.abort(abortError()).catch(() => {});
        }
        session.writer.releaseLock();
        session.writer = null;
      }
      if (session.opened) {
        try { await session.port.close(); } catch (error) {
          if (this.session === session) this.onError(`关闭串口失败：${error.message || error}`);
        }
        session.opened = false;
      }
      if (this.session === session) {
        this.session = null;
        this.onState(state);
      }
    });
    return session.closing;
  }
}
