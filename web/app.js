const logEl = document.getElementById("log");
const deviceList = document.getElementById("device-list");
const deviceMeta = document.getElementById("device-meta");
const sweepMeta = document.getElementById("sweep-meta");
const meterFill = document.getElementById("meter-fill");
const meterLabel = document.getElementById("meter-label");
const backendBadge = document.getElementById("backend-badge");
const linkBadge = document.getElementById("link-badge");
const scanBtn = document.getElementById("scan-btn");
const disconnectBtn = document.getElementById("disconnect-btn");
const startBtn = document.getElementById("start-btn");
const stopBtn = document.getElementById("stop-btn");
const sendBtn = document.getElementById("send-btn");
const scanTimeout = document.getElementById("scan-timeout");
const rawCommand = document.getElementById("raw-command");

function log(message) {
  const time = new Date().toLocaleTimeString();
  logEl.textContent += `[${time}] ${message}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

async function api(path, body) {
  const options = { method: body ? "POST" : "GET", headers: {} };
  if (body) {
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

function renderDevices(devices, connected) {
  deviceList.innerHTML = "";
  if (!devices || devices.length === 0) {
    const empty = document.createElement("li");
    empty.textContent = "No devices yet. Scan while the remote is advertising.";
    deviceList.append(empty);
    return;
  }
    for (const device of devices) {
    const item = document.createElement("li");
    const label = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = device.name || "Unknown";
    const detail = document.createElement("div");
    detail.className = "hint";
    detail.textContent = `${device.address}${device.rssi != null ? ` · ${device.rssi} dBm` : ""}${
      device.simulated ? " · simulator" : ""
    }`;
    label.append(title, detail);
    const button = document.createElement("button");
    button.textContent = "Connect";
    button.disabled = connected;
    button.addEventListener("click", () => connect(device.address));
    item.append(label, button);
    deviceList.append(item);
  }
}

function renderStatus(status) {
  backendBadge.textContent = status.backend === "simulator" ? "simulator" : "live BLE";
  linkBadge.textContent = status.connected ? "connected" : status.scanning ? "scanning" : "disconnected";
  linkBadge.classList.toggle("on", Boolean(status.connected));
  disconnectBtn.disabled = !status.connected;
  startBtn.disabled = !status.connected;
  stopBtn.disabled = !status.connected;
  sendBtn.disabled = !status.connected;
  scanBtn.disabled = Boolean(status.connected || status.scanning);

  const buffered = status.buffered_bytes || 0;
  const expected = status.expected_bytes || 4992;
  meterFill.style.width = `${Math.min(100, (buffered / expected) * 100)}%`;
  meterLabel.textContent = `${buffered} / ${expected} bytes`;

  const device = status.device || {};
  setMeta(deviceMeta, [
    ["Name", device.name],
    ["Address", device.address],
    ["Firmware", status.fw_id],
    ["Parameters", status.parameters ? status.parameters.raw : null],
    ["Sweeps saved", status.sweep_count],
  ]);

  const sweep = status.last_sweep;
  if (sweep) {
    setMeta(sweepMeta, [
      ["Last sweep", `#${sweep.index}`],
      ["I range", `${sweep.i_min} .. ${sweep.i_max}`],
      ["Q range", `${sweep.q_min} .. ${sweep.q_max}`],
      ["CSV", sweep.csv_path],
    ]);
  }

  if (status.devices) renderDevices(status.devices, status.connected);
}

async function scan() {
  try {
    log("Scanning...");
    const timeout_s = Number(scanTimeout.value || 12);
    const result = await api("/api/scan", { timeout_s });
    renderDevices(result.devices || [], false);
  } catch (error) {
    log(`Scan failed: ${error.message}`);
  }
}

async function connect(address) {
  try {
    log(`Connecting to ${address}...`);
    const status = await api("/api/connect", { address });
    renderStatus(status);
  } catch (error) {
    log(`Connect failed: ${error.message}`);
  }
}

async function disconnect() {
  try {
    const status = await api("/api/disconnect");
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

scanBtn.addEventListener("click", scan);
disconnectBtn.addEventListener("click", disconnect);
startBtn.addEventListener("click", () => send("1"));
stopBtn.addEventListener("click", () => send("2"));
sendBtn.addEventListener("click", () => {
  const command = rawCommand.value.trim();
  if (command) send(command);
});

const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.event === "log") log(message.payload.message);
  if (message.event === "status") renderStatus(message.payload);
  if (message.event === "devices") renderDevices(message.payload.devices || [], false);
  if (message.event === "packet") {
    const buffered = message.payload.buffered_bytes || 0;
    const expected = message.payload.expected_bytes || 4992;
    meterFill.style.width = `${Math.min(100, (buffered / expected) * 100)}%`;
    meterLabel.textContent = `${buffered} / ${expected} bytes (last chunk ${message.payload.bytes})`;
  }
  if (message.event === "sweep") {
    setMeta(sweepMeta, [
      ["Last sweep", `#${message.payload.index}`],
      ["I range", `${message.payload.i_min} .. ${message.payload.i_max}`],
      ["Q range", `${message.payload.q_min} .. ${message.payload.q_max}`],
      ["CSV", message.payload.csv_path],
    ]);
  }
});
socket.addEventListener("open", () => log("UI connected to local capture service"));
socket.addEventListener("close", () => log("UI lost the local capture service"));

api("/api/status").then(renderStatus).catch((error) => log(error.message));
