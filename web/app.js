const logEl = document.getElementById("log");
const deviceList = document.getElementById("device-list");
const deviceMeta = document.getElementById("device-meta");
const sweepMeta = document.getElementById("sweep-meta");
const meterFill = document.getElementById("meter-fill");
const meterLabel = document.getElementById("meter-label");
const pauseBtn = document.getElementById("pause-btn");
const disconnectBtn = document.getElementById("disconnect-btn");
const connIndicator = document.getElementById("conn-indicator");
const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const sendBtn = document.getElementById("send-btn");
const rawCommand = document.getElementById("raw-command");
const iqPlot = document.getElementById("iq-plot");
const iqCtx = iqPlot.getContext("2d");
const memorySlot = document.getElementById("memory-slot");
const clearDataBtn = document.getElementById("clear-data-btn");
const saveMemBtn = document.getElementById("save-mem-btn");
const recallMemBtn = document.getElementById("recall-mem-btn");

let lastDevice = {};

function log(message) {
  const time = new Date().toLocaleTimeString();
  logEl.textContent += `[${time}] ${message}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

async function api(path, body) {
  const post = body !== undefined;
  const options = { method: post ? "POST" : "GET", headers: {} };
  if (post) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function fileName(path) {
  if (!path) return "";
  const parts = String(path).split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

function setSweepMeta(sweep) {
  setMeta(sweepMeta, [
    ["Last sweep", `#${sweep.index}`],
    ["I range", `${sweep.i_min} .. ${sweep.i_max}`],
    ["Q range", `${sweep.q_min} .. ${sweep.q_max}`],
    ["CSV", fileName(sweep.csv_path)],
  ]);
}

function setMeta(target, entries) {
  target.innerHTML = "";
  for (const [key, value] of entries) {
    if (value === undefined || value === null || value === "") continue;
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = key;
    dd.textContent = value;
    target.append(dt, dd);
  }
}

function renderDevices(devices, connected, watching) {
  deviceList.innerHTML = "";
  if (!devices || devices.length === 0) {
    const empty = document.createElement("li");
    empty.textContent = watching
      ? "Looking for Orbio… will connect automatically."
      : "Looking is paused. Resume to keep waiting for the remote.";
    deviceList.append(empty);
    return;
  }
  for (const device of devices) {
    const item = document.createElement("li");
    const label = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = device.name || "(no name)";
    if (device.likely_orbio) item.classList.add("likely-orbio");
    const detail = document.createElement("div");
    detail.className = "hint";
    const rssi = device.rssi != null ? `RSSI ${device.rssi} dBm` : "RSSI unknown";
    detail.textContent = `${device.address} · ${rssi}${device.simulated ? " · simulator" : ""}`;
    label.append(title, detail);
    item.append(label);
    deviceList.append(item);
  }
}

function renderStatus(status) {
  const watching = Boolean(status.watching);
  connIndicator.textContent = status.connected ? "Connected" : "Not Connected";
  connIndicator.classList.toggle("on", Boolean(status.connected));
  connIndicator.classList.toggle("off", !status.connected);
  disconnectBtn.disabled = !status.connected;
  startBtn.disabled = !status.connected;
  stopBtn.disabled = !status.connected;
  sendBtn.disabled = !status.connected;
  pauseBtn.disabled = Boolean(status.connected);
  pauseBtn.textContent = watching ? "Pause looking" : "Resume looking";

  const buffered = status.buffered_bytes || 0;
  const expected = status.expected_bytes || 4992;
  meterFill.style.width = `${Math.min(100, (buffered / expected) * 100)}%`;
  meterLabel.textContent = `${buffered} / ${expected} bytes`;

  const device = status.device || {};
  lastDevice = device;
  setMeta(deviceMeta, [
    ["Name", device.name],
    ["Address", device.address],
    ["RSSI", device.rssi != null ? `${device.rssi} dBm` : null],
    ["Firmware", status.fw_id],
    ["Parameters", status.parameters ? status.parameters.raw : null],
    ["Sweeps saved", status.sweep_count],
  ]);

  const sweep = status.last_sweep;
  if (sweep) setSweepMeta(sweep);

  if (status.connected) deviceList.innerHTML = "";
  else if (status.devices) renderDevices(status.devices, status.connected, watching);
  if (iqHistory.length === 0 && status.iq_points && status.iq_points.length) {
    setIqPoints(status.iq_points);
  }
}

function freqColor(mhz) {
  const t = Math.min(1, Math.max(0, (mhz - 700) / 380));
  return `hsl(${200 - t * 160} 80% 62%)`;
}

let iqHistory = [];

function setIqPoints(points) {
  iqHistory = points && points.length ? points.slice() : [];
  drawIqPlot(iqHistory);
}

function clearIqPlot() {
  setIqPoints([]);
}

function selectedSlot() {
  return Number(memorySlot.value) || 1;
}

function renderMemoryOptions(memories, keepSlot) {
  const current = keepSlot || selectedSlot();
  memorySlot.innerHTML = "";
  for (const item of memories || []) {
    const option = document.createElement("option");
    option.value = String(item.slot);
    option.textContent = item.label;
    memorySlot.append(option);
  }
  memorySlot.value = String(current);
}

async function refreshMemories() {
  try {
    const data = await api("/api/memories");
    renderMemoryOptions(data.memories);
  } catch (error) {
    log(`Memory list failed: ${error.message}`);
  }
}

async function saveMemory() {
  if (!iqHistory.length) {
    log("Nothing on the plot to save. Capture or recall a sweep first.");
    return;
  }
  const slot = selectedSlot();
  try {
    const result = await api("/api/memory/save", {
      slot,
      points: iqHistory,
      device_name: lastDevice.name || null,
      device_address: lastDevice.address || null,
    });
    renderMemoryOptions(result.memories, slot);
    log(`Saved ${result.n_samples} samples to memory ${slot}`);
  } catch (error) {
    log(`Save failed: ${error.message}`);
  }
}

async function recallMemory() {
  const slot = selectedSlot();
  try {
    const data = await api("/api/memory/recall", { slot });
    setIqPoints(data.points || []);
    log(`Recalled memory ${slot} (${(data.points || []).length} samples)`);
  } catch (error) {
    log(`Recall failed: ${error.message}`);
  }
}

async function clearStoredData() {
  const ok = window.confirm(
    "Delete all captured sweep files in data/? The five memory slots are kept."
  );
  if (!ok) return;
  try {
    const result = await api("/api/data/clear", {});
    renderMemoryOptions(result.memories, selectedSlot());
    log(`Cleared ${result.deleted} capture files. Memories were not deleted.`);
  } catch (error) {
    log(`Clear data failed: ${error.message}`);
  }
}

function drawIqPlot(points) {
  points = points || [];
  const box = iqPlot.parentElement;
  const availW = (box && box.clientWidth) || 0;
  const availH = (box && box.clientHeight) || 0;
  if (availW < 50 || availH < 50) return;
  const cssSize = Math.max(120, Math.floor(Math.min(availW, availH)));
  const dpr = window.devicePixelRatio || 1;
  const size = Math.round(cssSize * dpr);
  if (iqPlot.width !== size || iqPlot.height !== size) {
    iqPlot.width = size;
    iqPlot.height = size;
  }
  iqPlot.style.width = `${cssSize}px`;
  iqPlot.style.height = `${cssSize}px`;
  const ctx = iqCtx;
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = "#0b1117";
  ctx.fillRect(0, 0, size, size);

  const pad = Math.round(48 * dpr);
  const plot = size - pad * 2;
  ctx.font = `${Math.round(11 * dpr)}px Segoe UI, sans-serif`;
  ctx.lineWidth = Math.max(1, dpr);

  if (!points || points.length === 0) {
    ctx.fillStyle = "#93a4b8";
    ctx.textAlign = "center";
    ctx.fillText("Waiting for a sweep…", size / 2, size / 2);
    return;
  }

  let maxAbs = 1;
  for (const p of points) {
    maxAbs = Math.max(maxAbs, Math.abs(p.i), Math.abs(p.q));
  }
  maxAbs *= 1.08;

  const toX = (i) => pad + ((i + maxAbs) / (2 * maxAbs)) * plot;
  const toY = (q) => pad + ((maxAbs - q) / (2 * maxAbs)) * plot;
  const originX = toX(0);
  const originY = toY(0);

  ctx.strokeStyle = "#2a3b4d";
  ctx.beginPath();
  ctx.moveTo(pad, originY);
  ctx.lineTo(pad + plot, originY);
  ctx.moveTo(originX, pad);
  ctx.lineTo(originX, pad + plot);
  ctx.stroke();

  ctx.fillStyle = "#93a4b8";
  ctx.textAlign = "center";
  ctx.fillText("I", pad + plot - 8 * dpr, originY - 8 * dpr);
  ctx.textAlign = "left";
  ctx.fillText("Q", originX + 8 * dpr, pad + 12 * dpr);
  ctx.textAlign = "center";
  ctx.fillText(String(-Math.round(maxAbs)), pad, originY + 14 * dpr);
  ctx.fillText(String(Math.round(maxAbs)), pad + plot, originY + 14 * dpr);
  ctx.textAlign = "right";
  ctx.fillText(String(Math.round(maxAbs)), originX - 6 * dpr, pad + 10 * dpr);
  ctx.fillText(String(-Math.round(maxAbs)), originX - 6 * dpr, pad + plot);

  const r = Math.max(1.1 * dpr, 1.6);
  const stride = points.length > 40000 ? Math.ceil(points.length / 40000) : 1;
  for (let i = 0; i < points.length; i += stride) {
    const p = points[i];
    ctx.fillStyle = freqColor(p.f);
    ctx.beginPath();
    ctx.arc(toX(p.i), toY(p.q), r, 0, Math.PI * 2);
    ctx.fill();
  }
}

window.addEventListener("resize", () => {
  drawIqPlot(iqHistory);
});

if (window.ResizeObserver && iqPlot.parentElement) {
  new ResizeObserver(() => drawIqPlot(iqHistory)).observe(iqPlot.parentElement);
}

async function toggleLooking() {
  const resume = pauseBtn.textContent.includes("Resume");
  try {
    if (resume) await api("/api/watch", { name_contains: "Orbio", pass_s: 20 });
    else await api("/api/watch/stop", {});
  } catch (error) {
    log(`${resume ? "Resume" : "Pause"} failed: ${error.message}`);
  }
}

async function disconnect() {
  try {
    const status = await api("/api/disconnect", {});
    renderStatus(status);
  } catch (error) {
    log(`Disconnect failed: ${error.message}`);
  }
}

async function send(command) {
  try {
    await api("/api/command", { command });
  } catch (error) {
    log(`Command failed: ${error.message}`);
  }
}

pauseBtn.addEventListener("click", toggleLooking);
disconnectBtn.addEventListener("click", disconnect);
startBtn.addEventListener("click", () => send("1"));
stopBtn.addEventListener("click", () => send("2"));
document.getElementById("clear-iq-btn").addEventListener("click", clearIqPlot);
clearDataBtn.addEventListener("click", clearStoredData);
saveMemBtn.addEventListener("click", saveMemory);
recallMemBtn.addEventListener("click", recallMemory);
sendBtn.addEventListener("click", () => {
  const command = rawCommand.value.trim();
  if (command) send(command);
});

const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.event === "log") log(message.payload.message);
  if (message.event === "status") renderStatus(message.payload);
  if (message.event === "devices") renderDevices(message.payload.devices || [], false, true);
  if (message.event === "packet") {
    const buffered = message.payload.buffered_bytes || 0;
    const expected = message.payload.expected_bytes || 4992;
    meterFill.style.width = `${Math.min(100, (buffered / expected) * 100)}%`;
    meterLabel.textContent = `${buffered} / ${expected} bytes (last chunk ${message.payload.bytes})`;
  }
  if (message.event === "sweep") {
    setSweepMeta(message.payload);
    if (message.payload.points) setIqPoints(message.payload.points);
  }
});
socket.addEventListener("open", () => log("UI connected to local capture service"));
socket.addEventListener("close", () => {
  log("UI lost the local capture service. Restart python -m orbio.app, then refresh this page.");
});
socket.addEventListener("error", () => {
  log("Capture service is not reachable. Is python -m orbio.app still running?");
});

api("/api/status").then((status) => {
  renderStatus(status);
  refreshMemories();
  if (iqHistory.length) return;
  if (status.iq_points && status.iq_points.length) {
    setIqPoints(status.iq_points);
    return;
  }
  return api("/api/last-sweep").then((sweep) => {
    setIqPoints(sweep.points);
    log(`Loaded ${sweep.name} into the I/Q plot (${sweep.n_samples} samples)`);
  }).catch(() => drawIqPlot(iqHistory));
}).catch((error) => log(error.message));
