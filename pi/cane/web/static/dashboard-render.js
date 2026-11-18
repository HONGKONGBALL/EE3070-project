import { byId, text, display, facts, table, number, yesNo } from './dashboard-dom.js';

const STATES = { BOOT:'启动中', READY:'就绪', WALKING:'行走中', STOPPED:'已停步', CROSSING:'路口等待确认', FAULT:'故障', PAUSED:'已暂停' };
const TERRAINS = { unknown:'尚未识别', rough:'粗糙地面', soft:'松软地面', hard:'硬质地面', smooth:'平整地面', normal:'一般地面' };
const HAZARDS = { stop:'立即停止', front:'前方障碍', head:'头部高度障碍', drop:'前方落差', step_up:'向上台阶', rough:'粗糙地面', sensor_lost:'传感器失联' };
const LANGUAGES = { cmn:'普通话', zh:'普通话', 'zh-CN':'普通话', 'zh-HK':'粤语', yue:'粤语', en:'English' };
const RESULTS = { played:'播放', play:'播放', started:'播放', queued:'排队', dropped:'未播放', drop:'未播放', veto:'安全否决', interrupted:'被抢占', preempt:'被抢占', expired:'过期' };
const PRIORITIES = ['紧急', '安全', '系统', '导航', '信息'];
const VOICE_REASONS = { disabled:'主机语音输入未启用', text_input_only:'主机仅配置文字输入，尚未启用离线识别', cantonese_asr_unavailable:'当前粤语模式没有可用的离线识别模型', vosk_not_installed:'主机尚未安装 Vosk 离线识别组件', vosk_model_missing:'主机缺少当前语言的离线识别模型', offline_model_missing:'主机缺少离线识别模型', supply_load_reduction:'供电受限，主机离线识别暂不可用' };
const REASONS = { pico_timeout:'传感器连接超时', sensor_stale:'观测已过期', low_power:'低电压', sensor_missing:'传感器观测缺失', range_sensors_invalid:'连续多帧测距全部失效', not_aligned:'朝向未校准', fault:'主机故障', safety_hold:'安全提示保持中', fresh_sensors_required:'需要新的有效传感器数据', hazard_present:'风险仍然存在', safety_hold_active:'安全提示保持中', stationary_observation_unavailable:'缺少静止状态观测', stop_before_resume:'请先停步', critical_supply_voltage:'供电电压过低' };
export const reasonText = (values) => Array.isArray(values) ? values.map((value) => REASONS[value] || value).join('；') : display(values);

export function canScan(status) {
  return status.capabilities?.slope === true || status.capabilities?.calibrated_stereo === true;
}

export function controlDisabledReason(action, status) {
  if (action === 'scan' && !canScan(status)) return '缺少已标定双目或有效姿态数据，不能进行坡向扫描';
  if (action === 'light' && status.sim !== true && status.serial?.controls_available !== true) {
    return '当前串口仅接收观测，不支持照明等实体输出控制';
  }
  if (action === 'voice_input' || action === 'wake_enable') {
    const voice = status.voice || {};
    if (voice.listening || voice.recording) return '主机正在录音或识别，请等待结果';
    if (status.language === 'yue' && voice.cantonese_model !== true) return VOICE_REASONS.cantonese_asr_unavailable;
    if (voice.available !== true) return VOICE_REASONS[voice.reason] || '主机离线语音输入尚未就绪，请使用文字';
  }
  if (action === 'voice_stop' && status.voice?.can_stop_recording !== true) return '录音已结束';
  if (action === 'confirm' && !status.nav?.waiting_crossing) {
    if (status.voice?.search_state === 'searching') return '正在查找目的地';
    if (status.voice?.search_state === 'results') return '请先选择候选地点';
    if (status.voice?.search_state === 'resolving') return '正在读取所选地点';
  }
  return '';
}

function voiceCandidates(voice) {
  if (voice?.search_state !== 'results' || typeof voice.search_id !== 'string' || !voice.search_id || !Array.isArray(voice.candidates)) return [];
  return voice.candidates
    .filter((place) => typeof place?.id === 'string' && place.id && typeof place.name === 'string' && place.name.trim())
    .map(({ id, name, address, distance_m }) => ({
      id, name, address: typeof address === 'string' ? address : '',
      distance: Number.isInteger(distance_m) && distance_m >= 0 ? distance_m : null,
    }));
}

export function voiceSelection(voice, searchId, candidateId) {
  if (searchId !== voice?.search_id || !voiceCandidates(voice).some((place) => place.id === candidateId)) return null;
  return { search_id: searchId, candidate_id: candidateId };
}

export function searchOriginText(voice) {
  const origin = voice?.search_origin;
  if (origin?.kind === 'iphone') return '出发点 · iPhone 当前位置';
  if (origin?.kind === 'manual' && origin.name) return `出发点 · ${origin.name}`;
  return '要用 iPhone 当前位置，还是改用一个地点？';
}

export function renderOriginSearch(voice, posting) {
  byId('origin-controls').hidden = !voice;
  byId('search-origin').disabled = !voice || posting;
  byId('use-phone-origin').disabled = !voice || posting;
  const originStatus = byId('origin-status');
  const current = searchOriginText(voice);
  if (originStatus.textContent !== current) originStatus.textContent = current;

  const search = voice?.origin_search;
  const editor = byId('origin-editor');
  const searchId = search?.search_id || '';
  if (searchId && searchId !== editor.dataset.searchId) editor.open = true;
  editor.dataset.searchId = searchId;

  const candidates = voiceCandidates(search);
  const messages = {
    waiting_origin: '要用 iPhone 当前位置，还是改用一个地点？',
    searching: '正在查找出发点…',
    results: candidates.length ? '请选择出发点。' : '请重新搜索出发点。',
    empty: '未找到出发点，请试试加入地区或入口名称。',
    resolving: '正在读取所选出发点…',
    error: '暂时无法查找出发点，请稍后重试。',
  };
  const message = Object.hasOwn(messages, search?.search_state) ? messages[search.search_state] : '';
  byId('origin-search').hidden = !message;
  const note = byId('origin-search-note');
  if (note.textContent !== message) note.textContent = message;
  renderCandidateList(byId('origin-candidates'), search, candidates, posting);
}

export function renderVoiceSearch(voice, posting) {
  const query = typeof voice?.query === 'string' && voice.query ? `“${voice.query}”` : '目的地';
  const candidates = voiceCandidates(voice);
  const messages = {
    searching: `正在查找${query}…`,
    results: candidates.length ? `为${query}找到 ${candidates.length} 个地点。${voice?.distance_ordered
      ? '按距出发点的直线距离排序。' : '请选择。'}` : '请重新搜索目的地。',
    empty: `未找到${query}，请试试加入地区或入口名称。`,
    resolving: '正在读取所选地点…',
    error: `暂时无法查找${query}，请稍后重试。`,
  };
  const message = Object.hasOwn(messages, voice?.search_state) ? messages[voice.search_state] : '';
  byId('destination-search').hidden = !message;
  const note = byId('destination-search-note');
  if (note.textContent !== message) note.textContent = message;
  renderCandidateList(byId('destination-candidates'), voice, candidates, posting);
}

function renderCandidateList(list, search, candidates, posting) {
  const key = JSON.stringify([candidates.length ? search.search_id : null, candidates]);
  if (key !== list.dataset.renderKey) {
    list.dataset.renderKey = key;
    const rows = candidates.map((place) => {
      const row = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'destination-candidate';
      button.dataset.searchId = search.search_id;
      button.dataset.candidateId = place.id;
      const name = document.createElement('strong');
      name.textContent = place.name;
      button.append(name);
      if (place.distance !== null) {
        const distance = document.createElement('span');
        distance.className = 'muted';
        distance.textContent = place.distance >= 1000 ? `约 ${(place.distance / 1000).toFixed(1)} 公里`
          : `约 ${place.distance} 米`;
        button.append(distance);
      }
      if (place.address) {
        const address = document.createElement('span');
        address.className = 'muted';
        address.textContent = place.address;
        button.append(address);
      }
      row.append(button);
      return row;
    });
    list.replaceChildren(...rows);
  }
  for (const button of list.querySelectorAll('button')) button.disabled = posting;
}

export function slopeText(value, positive, negative) {
  if (!Number.isFinite(value)) return '—';
  if (value === 0) return '0.0°';
  return `${value > 0 ? positive : negative} ${Math.abs(value).toFixed(1)}°`;
}

function renderSensors(status) {
  const sensors = status.sensors || {};
  const fresh = status.pico_ok === true && Number.isFinite(sensors.age_s);
  for (const [name, key] of [['front','front_m'], ['up','up_m'], ['ground','tfmini_m']]) {
    const capability = { front: 'front_obstacle', up: 'head_obstacle', ground: 'ground_range' }[name];
    const capabilityInvalid = status.capabilities?.[capability] === false && !(name === 'ground' && status.tfmini_role === 'front');
    const valid = fresh && !capabilityInvalid && Number.isFinite(sensors[key]) && sensors[key] > 0;
    text(`${name}-distance`, valid ? number(sensors[key], 2) : '—');
    let health = valid ? (status.sim ? '模拟观测' : '有效观测') : (status.pico_ok ? '测距失效 / 无回波' : '未连接 / 已超时');
    if (name === 'ground' && valid) health += ` · 强度 ${display(sensors.tfmini_strength)}`;
    text(`${name}-health`, health);
    byId(`sensor-${name}`).classList.toggle('invalid', !valid);
  }
  text('sensor-age', Number.isFinite(sensors.age_s) ? `距采样 ${number(sensors.age_s, 2, ' s')}` : '尚无传感器数据');
  text('ground-role', status.tfmini_role === 'front' ? '前方 · TFmini-S' : '地面 · TFmini-S');
  const hazards = Array.isArray(status.hazards) ? status.hazards : [];
  text('hazards', hazards.length ? hazards.map((hazard) => HAZARDS[hazard] || hazard).join(' · ') : (fresh ? '当前未报告风险事件' : '数据不可用，无法评估'));
  byId('hazards').classList.toggle('risk', hazards.length > 0 || !fresh);
}

function renderNavigation(status) {
  const nav = status.nav || {};
  const location = status.location || nav.location || {};
  text('nav-badge', nav.waiting_crossing ? '路口等待' : (status.paused ? '已暂停' : (nav.active ? '导航中' : '未启动')));
  text('destination', nav.pending_destination ? `待确认：${nav.pending_destination}` : nav.destination_name || nav.destination || (nav.active ? (status.sim ? '合成路线演示' : '导航进行中') : '尚未选择目的地'));
  byId('crossing-note').hidden = !nav.waiting_crossing;
  const navRows = [
    ['待确认目的地', nav.pending_destination || '无'],
    ['导航进度', nav.step === null || nav.step === undefined ? '—' : `第 ${nav.step + 1} 段`],
    ['至本段终点', number(nav.to_step_end_m, 1, ' m')],
    ['定位数据龄期', number(nav.location_age_s, 1, ' s')],
    ['定位精度', number(location.accuracy_m ?? location.accuracy ?? nav.location_accuracy_m, 1, ' m')],
    ['路线服务', status.sim ? '合成路线演示（非实际导航）' : yesNo(nav.amap_key_configured, '已配置', '未配置')],
  ];
  if (Number.isFinite(location.lng) && Number.isFinite(location.lat)) navRows.push(['经纬度', `${number(location.lng, 6)}, ${number(location.lat, 6)}`]);
  facts('navigation-status', navRows);
}

function renderTerrain(status) {
  const imu = status.imu || {};
  const scan = status.scan || status.last_scan || {};
  text('terrain-badge', TERRAINS[status.terrain] || display(status.terrain));
  facts('terrain-status', [
    ['运动状态', yesNo(imu.moving, '正在移动', '静止')],
    ['俯仰角', number(imu.pitch_deg, 1, '°')],
    ['前后坡向', slopeText(scan.uphill_deg ?? scan.uphill, '上坡', '下坡')],
    ['侧向下坡', slopeText(scan.left_down_deg ?? scan.left_down, '向左下斜', '向右下斜')],
    ['台阶距离', number(scan.step_distance_m ?? scan.step_d, 2, ' m')],
    ['朝向校准', yesNo(status.aligned, '已校准', '待停步校准')],
    ['转向提示', yesNo(status.orientation_ok, '允许', '受限')],
  ]);
}

function renderDevices(status) {
  const host = status.host || {};
  const serial = status.serial || {};
  const audio = status.audio || {};
  const camera = status.camera || status.vision || {};
  const voice = status.voice || {};
  facts('device-status', [
    ['主机平台', host.platform || host.system || 'Mac 主机'],
    ['串口', status.sim ? '模拟 Pico' : (serial.port || serial.configured_port || '未报告')],
    ['串口连接', status.sim ? '模拟连接' : yesNo(serial.connected ?? status.pico_ok, '已连接', '未连接')],
    ['实体输出控制', status.sim ? '模拟输出' : yesNo(serial.controls_available, '支持', '仅接收观测')],
    ['音频输出', audio.backend || audio.player || '未报告'],
    ['音频状态', audio.error || (audio.backend === 'console' ? '仅控制台记录，未播放声音' : audio.available === false ? '不可用' : (status.speaking ? '正在播报' : '空闲'))],
    ['最近播报完成', audio.last_completed || '尚无完成记录'],
    ['相机', camera.error || yesNo(camera.ready, '画面可用', '未连接 / 未就绪')],
    ['主机语音识别', voice.reason || (voice.model_available === false ? '本地模型未安装' : voice.backend || '未报告')],
    ['文字目的地', '可用'],
    ['照明', yesNo(status.light_on, '已开启', '已关闭')],
  ]);
}

export function renderStatus(status) {
  const power = status.power || {};
  const voiceBench = status.host?.mode === 'voice';
  text('connection', 'Mac 主机已连接');
  byId('connection-dot').className = 'dot online';
  text('updated', `更新于 ${new Date().toLocaleTimeString('zh-CN')}`);
  document.body.classList.toggle('voice-bench', voiceBench);
  text('page-heading', voiceBench ? '语音目的地' : '盲杖主机控制台');
  text('mode', voiceBench ? 'Mac 麦克风 · 本地语音识别'
    : status.sim === true ? '模拟主机 · 演示观测' : '真实主机 · 硬件观测');
  byId('mode').parentElement.classList.toggle('real', status.sim === false);
  text('test-mode', `测试模式：${yesNo(status.test_mode, '开启', '关闭')}`);
  text('hardware-note', voiceBench ? (status.serial?.connected ? 'Pico USB 已连接' : '等待 Pico USB')
    : status.sim ? '距离、路线及位置用于软件演练' : yesNo(status.pico_ok, 'Pico 数据在线', '等待连接 Pico'));
  text('state-badge', status.paused ? '导航暂停' : STATES[status.state] || display(status.state));
  facts('system-status', [
    ['运行状态', STATES[status.state] || display(status.state)],
    ['播报语言', LANGUAGES[status.language] || display(status.language)],
    ['安全提示保持', yesNo(status.safety_hold, '生效', '未生效')],
    ['当前播报', status.speaking || '空闲'],
    ['等待播报', Array.isArray(status.queue) ? status.queue.join('、') || '无' : '—'],
    ['供电电压', number(power.vsys_v ?? status.vsys, 2, ' V')],
    ['电量 / 续航', '未标定，不提供估算'],
    ['本地求助', yesNo(status.help_requested, '已请求本地播报', '未请求')],
    ['待处理操作', status.control_pending ?? '—'],
    ['上次操作', status.last_control ? `${status.last_control.action} · ${status.last_control.blocked_reason ? '受限：' + status.last_control.blocked_reason : '已处理'}` : '无'],
    ['恢复限制', Array.isArray(status.resume_blocked_reasons) && status.resume_blocked_reasons.length ? reasonText(status.resume_blocked_reasons) : '无已报告限制'],
  ]);
  const faults = Array.isArray(status.fault_reasons) ? status.fault_reasons : [];
  byId('fault-banner').hidden = status.state !== 'FAULT' && !faults.length;
  text('fault-banner', `主机故障：${faults.length ? reasonText(faults) : '观测不可用，请检查设备连接'}`);
  byId('scenario').disabled = status.sim !== true;
  byId('apply-scenario').disabled = status.sim !== true;
  text('simulation-note', status.sim ? '模拟模式已启用。选择场景后，结果从观测与输出中核实。' : '真实主机：演练已禁用，测试模式也不会开放模拟数据。');
  renderSensors(status);
  renderNavigation(status);
  renderTerrain(status);
  renderDevices(status);
  table('recent-outputs', (status.recent_outputs || []).slice().reverse().map((event) => [number(event.t, 1, ' s'), RESULTS[event.action] || event.action, event.prompt_id, PRIORITIES[event.priority] ?? event.priority]), '尚无输出记录');
}

export function renderDisconnected(message) {
  text('connection', '主机连接中断');
  byId('connection-dot').className = 'dot offline';
  byId('fault-banner').hidden = false;
  text('fault-banner', `无法更新状态，以下旧数据不可用于判断：${message}`);
  for (const name of ['front', 'up', 'ground']) {
    text(`${name}-distance`, '—');
    text(`${name}-health`, '连接中断，数据无效');
    byId(`sensor-${name}`).classList.add('invalid');
  }
  text('hazards', '主机状态未知，无法评估');
  byId('hazards').classList.add('risk');
  byId('scenario').disabled = true;
  byId('apply-scenario').disabled = true;
}
