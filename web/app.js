const logEl = document.getElementById("log");
const deviceList = document.getElementById("device-list");
const deviceMeta = document.getElementById("device-meta");
const sweepMeta = document.getElementById("sweep-meta");
const meterFill = document.getElementById("meter-fill");
const meterLabel = document.getElementById("meter-label");
const pauseBtn = document.getElementById("pause-btn");
const disconnectBtn = document.getElementById("disconnect-btn");
const connIndicator = document.getElementById("conn-indicator");
const sendBtn = document.getElementById("send-btn");
const rawCommand = document.getElementById("raw-command");
const iqPlot = document.getElementById("iq-plot");
const iqCtx = iqPlot.getContext("2d");
const memorySlot = document.getElementById("memory-slot");
const clearDataBtn = document.getElementById("clear-data-btn");
const saveMemBtn = document.getElementById("save-mem-btn");
const recallMemBtn = document.getElementById("recall-mem-btn");
const packetLossEl = document.getElementById("packet-loss");
const clearStatsBtn = document.getElementById("clear-stats-btn");
const plotCenterI = document.getElementById("plot-center-i");
const plotCenterQ = document.getElementById("plot-center-q");
const plotFreq = document.getElementById("plot-freq");
const sweepsShownEl = document.getElementById("sweeps-shown");
const plotCard = document.getElementById("plot-card");
const plotTitle = document.getElementById("plot-title");

let lastDevice = {};
let plotMode = "iq";
const TIME_WINDOW = 500;
const PPG_COLORS = ["#5b8def", "#3ec6b4", "#e2b15a", "#e07a6a", "#c084fc", "#f472b6", "#4ade80", "#fb923c"];
const ACCEL_COLORS = { x: "#fb923c", y: "#f472b6", z: "#4ade80" };
let ppgSeries = emptyPpgSeries();
let accelSeries = emptyAccelSeries();

function emptyPpgSeries() {
  return Array.from({ length: 8 }, () => []);
}

function emptyAccelSeries() {
  return { x: [], y: [], z: [] };
}

function log(message) {
  const time = new Date().toLocaleTimeString();
  logEl.textContent += `[${time}] ${message}\n`;
  if (logEl.textContent.length > 80000) {
    logEl.textContent = logEl.textContent.slice(-40000);
  }
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
  const got = sweep.n_bytes != null ? sweep.n_bytes : 4992;
  const expected = sweep.expected_bytes || 4992;
  const ok = sweep.byte_count_ok !== false && got === expected;
  setMeta(sweepMeta, [
    ["Last sweep", `#${sweep.index}`],
    ["Bytes", `${got} / ${expected}${ok ? "" : " MISMATCH"}`],
    ["I range", `${sweep.i_min} .. ${sweep.i_max}`],
    ["Q range", `${sweep.q_min} .. ${sweep.q_max}`],
    ["CSV", fileName(sweep.csv_path)],
  ]);
}

function setPpgMeta(ppg) {
  setMeta(sweepMeta, [
    ["Last PPG", `#${ppg.index}`],
    ["Samples", ppg.n_samples],
    ["Bytes", ppg.n_bytes],
    ["Value range", `${ppg.value_min} .. ${ppg.value_max}`],
    ["CSV", fileName(ppg.csv_path)],
  ]);
}

function setAccelMeta(accel) {
  setMeta(sweepMeta, [
    ["Last accel", `#${accel.index}`],
    ["Samples", accel.n_samples],
    ["Bytes", accel.n_bytes],
    ["X range", `${accel.x_min} .. ${accel.x_max}`],
    ["Y range", `${accel.y_min} .. ${accel.y_max}`],
    ["Z range", `${accel.z_min} .. ${accel.z_max}`],
    ["CSV", fileName(accel.csv_path)],
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
      ? "Scanning for Orbio… will connect automatically."
      : "Scanning is paused. Resume Scanning to keep waiting for the remote.";
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
  connIndicator.classList.remove("on", "off", "scan");
  if (status.connected) {
    connIndicator.textContent = "Connected";
    connIndicator.classList.add("on");
  } else if (watching) {
    connIndicator.textContent = "Scanning";
    connIndicator.classList.add("scan");
  } else {
    connIndicator.textContent = "Not Connected";
    connIndicator.classList.add("off");
  }
  disconnectBtn.disabled = !status.connected;
  sendBtn.disabled = !status.connected;
  pauseBtn.disabled = Boolean(status.connected);
  pauseBtn.textContent = watching ? "Pause Scanning" : "Resume Scanning";

  const buffered = status.buffered_bytes || 0;
  const expected = status.expected_bytes || 4992;
  meterFill.style.width = `${Math.min(100, (buffered / expected) * 100)}%`;
  meterLabel.textContent = `${buffered} / ${expected} bytes`;
  setPacketLoss(status.packet_loss || 0, status.packet_count || 0);

  const device = status.device || {};
  lastDevice = device;
  setMeta(deviceMeta, [
    ["Name", device.name],
    ["Address", device.address],
    ["RSSI", device.rssi != null ? `${device.rssi} dBm` : null],
    ["MTU", status.mtu != null ? `${status.mtu} bytes` : null],
    ["Conn interval", status.connection_interval_ms != null ? `${status.connection_interval_ms} ms` : null],
    ["Firmware", status.fw_id],
    ["Parameters", status.parameters ? status.parameters.raw : null],
    ["Sweeps saved", status.sweep_count],
    ["PPG saved", status.ppg_count],
    ["Accel saved", status.accel_count],
  ]);

  if (status.plot_mode) setPlotMode(status.plot_mode);
  if (status.plot_mode === "ppg" && status.last_ppg) setPpgMeta(status.last_ppg);
  else if (status.plot_mode === "accel" && status.last_accel) setAccelMeta(status.last_accel);
  else if (status.last_sweep) setSweepMeta(status.last_sweep);

  if (status.connected) deviceList.innerHTML = "";
  else if (status.devices) renderDevices(status.devices, status.connected, watching);
}

function setPacketLoss(count, total) {
  const n = Number(count) || 0;
  const t = Number(total) || 0;
  packetLossEl.textContent = `Packet Loss # = ${n} / ${t}`;
  packetLossEl.classList.toggle("alert", n > 0);
}

const FREQ_MHZ = [];
for (let mhz = 700; mhz <= 1080; mhz += 10) FREQ_MHZ.push(mhz);

let selectedFreqIndex = -1;

function selectedFreqMhz() {
  return selectedFreqIndex < 0 ? null : FREQ_MHZ[selectedFreqIndex];
}

function syncFreqDisplay() {
  const mhz = selectedFreqMhz();
  plotFreq.value = mhz == null ? "All" : `${mhz} MHz`;
}

function setSelectedFreq(index) {
  if (index < -1) index = FREQ_MHZ.length - 1;
  else if (index >= FREQ_MHZ.length) index = -1;
  selectedFreqIndex = index;
  syncFreqDisplay();
  redrawPlot();
}

function stepFreq(delta) {
  if (selectedFreqIndex < 0) {
    setSelectedFreq(delta > 0 ? 0 : FREQ_MHZ.length - 1);
    return;
  }
  setSelectedFreq(selectedFreqIndex + delta);
}

function onFreqWheel(event) {
  if (plotMode !== "iq") return;
  event.preventDefault();
  stepFreq(event.deltaY > 0 ? 1 : -1);
}

function freqColor(mhz) {
  const t = Math.min(1, Math.max(0, (mhz - 700) / 380));
  return `hsl(${200 - t * 160} 80% 62%)`;
}

const SWEEP_BUFFER_MAX = 200;
let iqSweeps = [];
let iqHistory = [];

function sweepsShownLimit() {
  let n = Math.round(Number(sweepsShownEl.value));
  if (!Number.isFinite(n) || n < 1) n = 1;
  if (n > SWEEP_BUFFER_MAX) n = SWEEP_BUFFER_MAX;
  if (String(n) !== sweepsShownEl.value) sweepsShownEl.value = String(n);
  return n;
}

function rebuildIqHistory() {
  iqHistory = iqSweeps.slice(-sweepsShownLimit()).flat();
  redrawPlot();
}

function setIqPoints(points, replace) {
  const incoming = points && points.length ? points.slice() : [];
  if (replace) {
    iqSweeps = incoming.length ? [incoming] : [];
  } else if (incoming.length) {
    iqSweeps.push(incoming);
    if (iqSweeps.length > SWEEP_BUFFER_MAX) {
      iqSweeps.splice(0, iqSweeps.length - SWEEP_BUFFER_MAX);
    }
  }
  rebuildIqHistory();
}

function clearIqBuffers() {
  iqSweeps = [];
  iqHistory = [];
}

function clearCurrentPlot() {
  if (plotMode === "ppg") ppgSeries = emptyPpgSeries();
  else if (plotMode === "accel") accelSeries = emptyAccelSeries();
  else clearIqBuffers();
  redrawPlot();
}

function appendPpgSamples(samples) {
  for (const row of samples || []) {
    const ch = Number(row.ch);
    if (ch < 1 || ch > 8) continue;
    const series = ppgSeries[ch - 1];
    series.push(Number(row.v));
    if (series.length > TIME_WINDOW) series.splice(0, series.length - TIME_WINDOW);
  }
  schedulePlot();
}

function appendAccelSamples(samples) {
  for (const row of samples || []) {
    accelSeries.x.push(Number(row.x));
    accelSeries.y.push(Number(row.y));
    accelSeries.z.push(Number(row.z));
  }
  for (const key of ["x", "y", "z"]) {
    if (accelSeries[key].length > TIME_WINDOW) {
      accelSeries[key].splice(0, accelSeries[key].length - TIME_WINDOW);
    }
  }
  schedulePlot();
}

let plotRaf = 0;
function schedulePlot() {
  if (plotRaf) return;
  plotRaf = requestAnimationFrame(() => {
    plotRaf = 0;
    redrawPlot();
  });
}

function setPlotMode(mode) {
  const next = mode === "ppg" || mode === "accel" ? mode : "iq";
  const changed = next !== plotMode;
  plotMode = next;
  if (plotCard) plotCard.dataset.mode = plotMode;
  if (plotTitle) {
    plotTitle.textContent = plotMode === "ppg" ? "PPG" : plotMode === "accel" ? "Accel" : "I / Q";
  }
  iqPlot.classList.toggle("time-plot", plotMode !== "iq");
  if (changed) refreshMemories();
}

function redrawPlot() {
  if (plotMode === "ppg") {
    drawStackedPlot(
      ppgSeries.map((values, i) => ({
        label: `Ch ${i + 1}`,
        color: PPG_COLORS[i],
        values,
      })),
      "Waiting for PPG…"
    );
    return;
  }
  if (plotMode === "accel") {
    drawStackedPlot(
      [
        { label: "X", color: ACCEL_COLORS.x, values: accelSeries.x },
        { label: "Y", color: ACCEL_COLORS.y, values: accelSeries.y },
        { label: "Z", color: ACCEL_COLORS.z, values: accelSeries.z },
      ],
      "Waiting for accel…"
    );
    return;
  }
  drawIqPlot(iqHistory);
}

function seriesExtent(values) {
  if (!values.length) return { min: 0, max: 1 };
  let min = values[0];
  let max = values[0];
  for (const v of values) {
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.08;
  return { min: min - pad, max: max + pad };
}

function drawStackedPlot(channels, emptyLabel) {
  const box = iqPlot.parentElement;
  const cssW = Math.max(160, Math.floor((box && box.clientWidth) || 480));
  const cssH = Math.max(200, Math.floor((box && box.clientHeight) || 360));
  const dpr = window.devicePixelRatio || 1;
  const width = Math.round(cssW * dpr);
  const height = Math.round(cssH * dpr);
  if (iqPlot.width !== width || iqPlot.height !== height) {
    iqPlot.width = width;
    iqPlot.height = height;
  }
  iqPlot.style.width = `${cssW}px`;
  iqPlot.style.height = `${cssH}px`;
  const ctx = iqCtx;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#0b1117";
  ctx.fillRect(0, 0, width, height);

  const hasData = channels.some((ch) => ch.values.length);
  if (!hasData) {
    ctx.fillStyle = "#93a4b8";
    ctx.font = `${Math.round(12 * dpr)}px Segoe UI, sans-serif`;
    ctx.textAlign = "center";
    ctx.fillText(emptyLabel, width / 2, height / 2);
    return;
  }

  const n = channels.length;
  const left = Math.round(52 * dpr);
  const right = Math.round(46 * dpr);
  const top = Math.round(6 * dpr);
  const gap = Math.round(4 * dpr);
  const stripH = Math.floor((height - top - gap * (n - 1)) / n);
  const plotW = width - left - right;
  ctx.font = `${Math.round(10 * dpr)}px Segoe UI, sans-serif`;
  ctx.lineWidth = Math.max(1, dpr);

  for (let i = 0; i < n; i++) {
    const ch = channels[i];
    const y0 = top + i * (stripH + gap);
    ctx.fillStyle = "#101820";
    ctx.fillRect(left, y0, plotW, stripH);
    ctx.strokeStyle = "#2a3b4d";
    ctx.strokeRect(left + 0.5, y0 + 0.5, plotW - 1, stripH - 1);

    const { min, max } = seriesExtent(ch.values);
    const span = max - min || 1;
    const toX = (index) => left + (index / Math.max(1, TIME_WINDOW - 1)) * plotW;
    const toY = (value) => y0 + stripH - ((value - min) / span) * stripH;

    if (ch.values.length > 1) {
      ctx.beginPath();
      ctx.strokeStyle = ch.color;
      ctx.lineWidth = Math.max(1.2, 1.2 * dpr);
      ch.values.forEach((value, index) => {
        const x = toX(index);
        const y = toY(value);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    ctx.fillStyle = "#93a4b8";
    ctx.textAlign = "right";
    ctx.fillText(String(Math.round(max)), left - 4 * dpr, y0 + 10 * dpr);
    ctx.fillText(String(Math.round(min)), left - 4 * dpr, y0 + stripH - 2 * dpr);
    ctx.fillStyle = ch.color;
    ctx.textAlign = "left";
    ctx.fillText(ch.label, left + plotW + 6 * dpr, y0 + stripH / 2);
  }
}

function selectedSlot() {
  return Number(memorySlot.value) || 1;
}

function currentMemoryKind() {
  return plotMode === "ppg" || plotMode === "accel" ? plotMode : "iq";
}

function memoryKindLabel(kind) {
  if (kind === "ppg") return "PPG";
  if (kind === "accel") return "accel";
  return "I/Q";
}

function currentPlotHasData() {
  if (plotMode === "ppg") return ppgSeries.some((channel) => channel.length);
  if (plotMode === "accel") return accelSeries.x.length || accelSeries.y.length || accelSeries.z.length;
  return iqHistory.length > 0;
}

function clonePpgSeries(series) {
  const next = emptyPpgSeries();
  if (!Array.isArray(series)) return next;
  for (let i = 0; i < 8; i += 1) {
    next[i] = Array.isArray(series[i]) ? series[i].slice(-TIME_WINDOW) : [];
  }
  return next;
}

function cloneAccelSeries(series) {
  const next = emptyAccelSeries();
  if (!series || typeof series !== "object") return next;
  for (const key of ["x", "y", "z"]) {
    next[key] = Array.isArray(series[key]) ? series[key].slice(-TIME_WINDOW) : [];
  }
  return next;
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
    const kind = currentMemoryKind();
    const data = await api(`/api/memories?kind=${encodeURIComponent(kind)}`);
    renderMemoryOptions(data.memories);
  } catch (error) {
    log(`Memory list failed: ${error.message}`);
  }
}

async function saveMemory() {
  if (!currentPlotHasData()) {
    log("Nothing on the plot to save. Capture or recall data first.");
    return;
  }
  const slot = selectedSlot();
  const kind = currentMemoryKind();
  const body = {
    slot,
    kind,
    device_name: lastDevice.name || null,
    device_address: lastDevice.address || null,
  };
  if (kind === "ppg") body.series = ppgSeries;
  else if (kind === "accel") body.series = accelSeries;
  else body.points = iqHistory;
  try {
    const result = await api("/api/memory/save", body);
    renderMemoryOptions(result.memories, slot);
    log(`Saved ${result.n_samples} samples to ${memoryKindLabel(kind)} memory ${slot}`);
  } catch (error) {
    log(`Save failed: ${error.message}`);
  }
}

async function recallMemory() {
  const slot = selectedSlot();
  const kind = currentMemoryKind();
  try {
    const data = await api("/api/memory/recall", { slot, kind });
    if (kind === "ppg") {
      ppgSeries = clonePpgSeries(data.series);
      redrawPlot();
      const n = ppgSeries.reduce((sum, channel) => sum + channel.length, 0);
      log(`Recalled PPG memory ${slot} (${n} samples)`);
    } else if (kind === "accel") {
      accelSeries = cloneAccelSeries(data.series);
      redrawPlot();
      log(`Recalled accel memory ${slot} (${(accelSeries.x || []).length} samples)`);
    } else {
      setIqPoints(data.points || [], true);
      log(`Recalled I/Q memory ${slot} (${(data.points || []).length} samples)`);
    }
  } catch (error) {
    log(`Recall failed: ${error.message}`);
  }
}

async function clearStats() {
  try {
    const status = await api("/api/stats/clear", {});
    setPacketLoss(status.packet_loss || 0, status.packet_count || 0);
  } catch (error) {
    log(`Clear stats failed: ${error.message}`);
  }
}

async function clearStoredData() {
  const ok = window.confirm(
    "Delete all captured sweep, PPG, and accel files in data/? Memory slots (I/Q, PPG, and accel) are kept."
  );
  if (!ok) return;
  try {
    const result = await api("/api/data/clear", {});
    await refreshMemories();
    log(`Cleared ${result.deleted} capture files. Memories were not deleted.`);
  } catch (error) {
    log(`Clear data failed: ${error.message}`);
  }
}

let userSetCenterI = false;
let userSetCenterQ = false;
let syncingCenter = false;

function median(values) {
  if (!values.length) return 0;
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2) return sorted[mid];
  return Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

function plotCenter(points) {
  let i = Number(plotCenterI.value);
  let q = Number(plotCenterQ.value);
  const needI = !userSetCenterI || plotCenterI.value === "" || !Number.isFinite(i);
  const needQ = !userSetCenterQ || plotCenterQ.value === "" || !Number.isFinite(q);
  if (points.length && (needI || needQ)) {
    syncingCenter = true;
    if (needI) {
      i = median(points.map((p) => p.i));
      plotCenterI.value = String(i);
    }
    if (needQ) {
      q = median(points.map((p) => p.q));
      plotCenterQ.value = String(q);
    }
    syncingCenter = false;
  }
  return {
    i: Number.isFinite(i) ? i : 0,
    q: Number.isFinite(q) ? q : 0,
  };
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

  const center = plotCenter(points);
  let half = 1;
  for (const p of points) {
    half = Math.max(half, Math.abs(p.i - center.i), Math.abs(p.q - center.q));
  }
  half *= 1.08;
  const mid = pad + plot / 2;
  const toX = (i) => mid + ((i - center.i) / half) * (plot / 2);
  const toY = (q) => mid - ((q - center.q) / half) * (plot / 2);
  const originX = mid;
  const originY = mid;
  const iMin = center.i - half;
  const iMax = center.i + half;
  const qMin = center.q - half;
  const qMax = center.q + half;

  ctx.strokeStyle = "#2a3b4d";
  ctx.beginPath();
  ctx.moveTo(pad, originY);
  ctx.lineTo(pad + plot, originY);
  ctx.moveTo(originX, pad);
  ctx.lineTo(originX, pad + plot);
  ctx.stroke();

  const tick = (value) => String(Math.round(value));
  ctx.fillStyle = "#93a4b8";
  ctx.textAlign = "center";
  ctx.fillText("I", pad + plot - 8 * dpr, originY - 8 * dpr);
  ctx.textAlign = "left";
  ctx.fillText("Q", originX + 8 * dpr, pad + 12 * dpr);
  ctx.textAlign = "center";
  ctx.fillText(tick(iMin), pad, originY + 14 * dpr);
  ctx.fillText(tick(center.i), originX, originY + 14 * dpr);
  ctx.fillText(tick(iMax), pad + plot, originY + 14 * dpr);
  ctx.textAlign = "right";
  ctx.fillText(tick(qMax), originX - 6 * dpr, pad + 10 * dpr);
  ctx.fillText(tick(center.q), originX - 6 * dpr, originY);
  ctx.fillText(tick(qMin), originX - 6 * dpr, pad + plot);

  const selected = selectedFreqMhz();
  const r = Math.max(1.1 * dpr, 1.6);
  const rHi = r * 1.8;
  const stride = points.length > 40000 ? Math.ceil(points.length / 40000) : 1;
  for (let i = 0; i < points.length; i += stride) {
    const p = points[i];
    const active = selected == null || p.f === selected;
    if (active) continue;
    ctx.globalAlpha = 0.16;
    ctx.fillStyle = freqColor(p.f);
    ctx.beginPath();
    ctx.arc(toX(p.i), toY(p.q), r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
  for (let i = 0; i < points.length; i += stride) {
    const p = points[i];
    const active = selected == null || p.f === selected;
    if (!active) continue;
    ctx.fillStyle = freqColor(p.f);
    ctx.beginPath();
    ctx.arc(toX(p.i), toY(p.q), selected == null ? r : rHi, 0, Math.PI * 2);
    ctx.fill();
  }
}

window.addEventListener("resize", () => {
  redrawPlot();
});

if (window.ResizeObserver && iqPlot.parentElement) {
  new ResizeObserver(() => redrawPlot()).observe(iqPlot.parentElement);
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
document.getElementById("clear-iq-btn").addEventListener("click", clearCurrentPlot);
plotCenterI.addEventListener("input", () => {
  if (syncingCenter) return;
  userSetCenterI = plotCenterI.value.trim() !== "";
  redrawPlot();
});
plotCenterQ.addEventListener("input", () => {
  if (syncingCenter) return;
  userSetCenterQ = plotCenterQ.value.trim() !== "";
  redrawPlot();
});
plotCenterI.addEventListener("change", () => redrawPlot());
plotCenterQ.addEventListener("change", () => redrawPlot());
sweepsShownEl.addEventListener("change", rebuildIqHistory);
plotFreq.addEventListener("wheel", onFreqWheel, { passive: false });
plotFreq.addEventListener("click", () => setSelectedFreq(-1));
iqPlot.parentElement.addEventListener("wheel", onFreqWheel, { passive: false });
clearStatsBtn.addEventListener("click", clearStats);
clearDataBtn.addEventListener("click", clearStoredData);
saveMemBtn.addEventListener("click", saveMemory);
recallMemBtn.addEventListener("click", recallMemory);
function submitControl() {
  if (sendBtn.disabled) return;
  const command = rawCommand.value.trim();
  if (command) send(command);
}

sendBtn.addEventListener("click", submitControl);
rawCommand.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  submitControl();
});
document.querySelectorAll(".commands li").forEach((item) => {
  item.addEventListener("click", () => {
    const code = item.querySelector("code");
    if (code) rawCommand.value = code.textContent;
  });
});

let socket = null;
let reconnectTimer = 0;
let reconnectAttempt = 0;
const MAX_RECONNECTS = 20;

function captureWsUrl() {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
}

function handleCaptureMessage(event) {
  const message = JSON.parse(event.data);
  if (message.event === "log") log(message.payload.message);
  if (message.event === "status") renderStatus(message.payload);
  if (message.event === "devices") renderDevices(message.payload.devices || [], false, true);
  if (message.event === "packet_loss") {
    setPacketLoss(message.payload.count, message.payload.total);
  }
  if (message.event === "packet") {
    const buffered = message.payload.buffered_bytes || 0;
    const expected = message.payload.expected_bytes || 4992;
    meterFill.style.width = `${Math.min(100, (buffered / expected) * 100)}%`;
    meterLabel.textContent = `${buffered} / ${expected} bytes (last chunk ${message.payload.bytes})`;
    setPacketLoss(message.payload.packet_loss, message.payload.packet_count);
  }
  if (message.event === "plot_mode") setPlotMode(message.payload.mode);
  if (message.event === "plot_reset") {
    if (message.payload && message.payload.mode) setPlotMode(message.payload.mode);
    clearCurrentPlot();
  }
  if (message.event === "sweep") {
    setSweepMeta(message.payload);
    if (plotMode === "iq" && message.payload.points) setIqPoints(message.payload.points);
  }
  if (message.event === "ppg") {
    setPpgMeta(message.payload);
    appendPpgSamples(message.payload.samples || []);
  }
  if (message.event === "accel") {
    setAccelMeta(message.payload);
    appendAccelSamples(message.payload.samples || []);
  }
}

function scheduleReconnect() {
  if (reconnectAttempt >= MAX_RECONNECTS) {
    log("Could not reconnect to the capture service. Close this window and launch Orbio BioSensor BLE from the desktop icon.");
    return;
  }
  const delay = Math.min(8000, 400 * 2 ** reconnectAttempt);
  reconnectAttempt += 1;
  log(`Capture service disconnected. Reconnecting (${reconnectAttempt}/${MAX_RECONNECTS})…`);
  window.clearTimeout(reconnectTimer);
  reconnectTimer = window.setTimeout(connectCaptureSocket, delay);
}

function connectCaptureSocket() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }
  socket = new WebSocket(captureWsUrl());
  socket.addEventListener("message", handleCaptureMessage);
  socket.addEventListener("open", () => {
    reconnectAttempt = 0;
    log("UI connected to local capture service");
    api("/api/status").then((status) => {
      renderStatus(status);
      refreshMemories();
    }).catch((error) => log(error.message));
  });
  socket.addEventListener("close", (event) => {
    if (event.target !== socket) return;
    scheduleReconnect();
  });
}

connectCaptureSocket();

api("/api/status").then((status) => {
  renderStatus(status);
  refreshMemories();
  redrawPlot();
}).catch((error) => log(error.message));
