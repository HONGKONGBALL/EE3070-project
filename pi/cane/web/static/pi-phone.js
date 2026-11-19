"use strict";
const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
const headers = { Authorization: "Bearer " + token, "Content-Type": "application/json" };
const share = document.getElementById("sharing");
const locationNote = document.getElementById("location");
const statusNote = document.getElementById("status");
const branches = document.getElementById("branches");
const form = document.getElementById("correction");
const transcript = document.getElementById("transcript");
let shareGeneration = 0;
let sharing = false;
let locationTimer = null;
let screenLock = null;
let shownTranscript = "";
let selectedInFlight = false;

function write(node, message) {
  if (node.textContent !== message) node.textContent = message;
}
async function request(path, body) {
  const response = await fetch("/api/phone/" + path, {
    headers, cache: "no-store", method: body ? "POST" : "GET",
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}
async function acquireScreenLock() {
  if (!sharing || !navigator.wakeLock || document.hidden || screenLock) return;
  try {
    const lock = await navigator.wakeLock.request("screen");
    if (!sharing) { await lock.release(); return; }
    screenLock = lock;
    lock.addEventListener("release", () => { if (screenLock === lock) screenLock = null; });
  } catch { /* position refresh resumes when Safari is active */ }
}
function requestFix(generation) {
  if (!sharing || generation !== shareGeneration) return;
  navigator.geolocation.getCurrentPosition(async (position) => {
    if (!sharing || generation !== shareGeneration) return;
    try {
      if (Date.now() - position.timestamp > 10000) throw new Error("stale");
      const { longitude: lng, latitude: lat, accuracy } = position.coords;
      await request("location", { lng, lat, accuracy, token });
      if (sharing && generation === shareGeneration) {
        write(locationNote, "位置已发给 Pi 5 · 精度约 " + Math.round(accuracy) + " 米");
      }
    } catch {
      if (sharing && generation === shareGeneration) write(locationNote, "正在重试向 Pi 5 发送定位。");
    }
    if (sharing && generation === shareGeneration) {
      locationTimer = setTimeout(() => requestFix(generation), 4000);
    }
  }, (error) => {
    if (!sharing || generation !== shareGeneration) return;
    if (error.code === 1) {
      stopSharing();
      write(locationNote, "请在 iPhone 设置中允许 Safari 定位，然后重新开始共享。");
    } else {
      write(locationNote, "正在获取更准确的 iPhone 位置。");
      locationTimer = setTimeout(() => requestFix(generation), 4000);
    }
  }, { enableHighAccuracy: true, maximumAge: 0, timeout: 8000 });
}
function stopSharing() {
  sharing = false;
  shareGeneration++;
  clearTimeout(locationTimer);
  if (screenLock) screenLock.release().catch(() => {});
  write(share, "开始共享定位");
}
share.addEventListener("click", () => {
  if (sharing) {
    stopSharing();
    write(locationNote, "定位共享已停止。");
    return;
  }
  if (!window.isSecureContext || !navigator.geolocation) {
    write(locationNote, "请通过 Pi 5 的 HTTPS 配对链接打开此页。");
    return;
  }
  sharing = true;
  write(share, "停止共享定位");
  write(locationNote, "正在获取 iPhone 位置。");
  requestFix(++shareGeneration);
  acquireScreenLock();
});
document.addEventListener("visibilitychange", acquireScreenLock);

const actionNote = document.getElementById("action-message");
const originOptions = document.getElementById("origin-options");
const originForm = document.getElementById("origin-form");
const originText = document.getElementById("origin-text");
const originStatus = document.getElementById("origin-status");
const currentOrigin = document.getElementById("current-origin");
const origins = document.getElementById("origins");
const signatures = new WeakMap();
let refreshGeneration = 0;
let promptedOrigin = "";

async function action(path, body) {
  if (selectedInFlight) return;
  selectedInFlight = true;
  try {
    const result = await request(path, body);
    write(actionNote, result.accepted === false ? result.error || "请重新选择或输入地点。" : "");
    await refresh();
  } catch {
    write(actionNote, "请求未送达，请重试。");
  } finally {
    selectedInFlight = false;
  }
}

function renderChoices(list, state, endpoint) {
  const rows = state.search_state === "results" ? state.candidates : [];
  const signature = JSON.stringify([state.search_id, rows]);
  if (signatures.get(list) === signature) return;
  signatures.set(list, signature);
  list.replaceChildren();
  for (const place of rows) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    const parts = [place.name, place.address];
    if (Number.isFinite(place.distance_m)) {
      const distance = place.distance_m >= 1000 ? (place.distance_m / 1000).toFixed(1) + " 公里"
        : place.distance_m + " 米";
      parts.push("约 " + distance);
    }
    button.textContent = parts.filter(Boolean).join(" · ");
    button.addEventListener("click", () => action(endpoint, { search_id: state.search_id, candidate_id: place.id }));
    li.append(button);
    list.append(li);
  }
}

const searchMessage = {
  idle: "等待目的地。",
  waiting_origin: "请选择用 iPhone 当前位置，或修改出发点。",
  searching: "正在查找附近地点。",
  results: "候选已按直线距离排列，请选择目的地。",
  empty: "附近没有查到这个地点，可以修改搜索词。",
  error: "地点服务暂时无法返回结果，请重试。",
};
const routeMessage = {
  route_failed: "路线查询失败，请重试。",
  walk_route_blocked: "当前步行路线无法通行，请留意语音提示。",
  walk_route_unavailable: "没有找到可用的步行路线，请留意语音提示。",
  walk_route_alternative: "已找到另一条步行路线，请听语音提示。",
};

async function refresh() {
  const generation = ++refreshGeneration;
  try {
    const state = await request("voice");
    if (generation !== refreshGeneration) return;
    const voice = state.voice;
    if (voice.recording) write(statusNote, "正在录音。说完后按 K2 结束。");
    else if (voice.can_stop_recording) write(statusNote, "请听提示音。");
    else if (voice.listening) write(statusNote, "正在识别录音。");
    else if (state.selected) write(statusNote, "已选 " + state.selected + "。在 Pico 上按 K1 确认。");
    else if (state.route_pending) write(statusNote, "Pi 5 正在规划步行路线。");
    else if (routeMessage[state.announcement]) write(statusNote, routeMessage[state.announcement]);
    else if (state.navigating) write(statusNote, "步行导航中，请听 Pi 5 的语音提示。");
    else if (!state.pico_ok && voice.search_state === "idle") write(statusNote, "等待 Pico USB 连接。");
    else if (!voice.available && voice.search_state === "idle") write(statusNote, "语音录入待就绪。可先输入地点名称搜索。");
    else write(statusNote, searchMessage[voice.search_state] || searchMessage.idle);
    renderChoices(branches, voice, "voice/select");
    const origin = voice.search_origin;
    write(currentOrigin, origin?.kind === "manual" ? origin.name
      : voice.phone_location_ready ? "iPhone 当前位置" : "等待 iPhone 提供准确定位");
    if (voice.search_state === "waiting_origin" && promptedOrigin !== voice.search_id) {
      promptedOrigin = voice.search_id;
      originOptions.open = true;
    }
    if (voice.origin_search) {
      const originMessages = {
        searching: "正在查找出发点。", empty: "未找到这个出发点。",
        error: "出发点查询失败，请重试。",
      };
      write(originStatus, originMessages[voice.origin_search.search_state] || "");
      renderChoices(origins, voice.origin_search, "origin/select");
    }
    if (voice.query !== shownTranscript && (!transcript.value || transcript.value === shownTranscript)) {
      transcript.value = voice.query;
    }
    shownTranscript = voice.query;
  } catch {
    if (generation === refreshGeneration) write(statusNote, "连不上 Pi 5，请重新打开配对链接。");
  }
}

async function refreshLoop() {
  await refresh();
  setTimeout(refreshLoop, 1200);
}
form.addEventListener("submit", event => {
  event.preventDefault();
  const text = transcript.value.trim();
  if (text) action("voice/text", { text });
});
originForm.addEventListener("submit", event => {
  event.preventDefault();
  const text = originText.value.trim();
  if (text) action("origin/search", { text });
});
document.getElementById("use-iphone").addEventListener("click", () => action("origin/iphone", {}));
if (token) refreshLoop();
else {
  share.disabled = true;
  write(statusNote, "请从 Pi 5 扫描步行导航配对码。");
}
