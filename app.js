// ---------------------------------------------------------------- config --
const WS_URL = `ws://${location.host}/ws/telemetry`;
const RANGES = {
  freq: 60, rpm: 1900, slipPct: 15, current: 100, temp: 160, torque: 160,
};

// ------------------------------------------------------------------- DOM --
const el = (id) => document.getElementById(id);
const connChip = el("connChip"), connLabel = el("connLabel");
const tripChip = el("tripChip");
const twinAlarmEl = el("twinAlarm");
const logBody = el("logBody");
const scopeCanvas = el("residualScope");
const ctx = scopeCanvas.getContext("2d");

let residualHistory = [];
let alarmHistory = [];
const SCOPE_POINTS = 240;

// -------------------------------------------------------- motor cutaway --
// The fan's rotation is integrated every animation frame from the LAST
// telemetry sample's actual RPM (angle += rpm/60*360*dt). It is not a CSS
// keyframe loop and has no fixed duration -- if the physics engine reports
// 0 RPM, the fan visually stops; if it reports 1450 RPM, it spins at the
// rate a 1450 RPM shaft actually would.
const fanGroup = el("fanGroup");
const heatHalo = el("heatHalo");
const alarmRing = el("alarmRing");
const currentDots = el("currentDots");
const visualRpmReadout = el("visualRpmReadout");
const visualTempReadout = el("visualTempReadout");
const visualStateReadout = el("visualStateReadout");

let liveRpm = 0, liveCurrentA = 0, fanAngleDeg = 0, dotsPhase = 0;
let lastFrameTs = performance.now();

function visualFrame(ts) {
  const dt = Math.min(0.1, (ts - lastFrameTs) / 1000);
  lastFrameTs = ts;

  fanAngleDeg = (fanAngleDeg + (liveRpm / 60) * 360 * dt) % 360;
  fanGroup.setAttribute("transform", `rotate(${fanAngleDeg.toFixed(2)} 520 120)`);

  // dash offset speed scales with actual stator current -- faster "flow"
  // means more current, not a decorative constant-speed marquee
  dotsPhase = (dotsPhase - liveCurrentA * dt * 4) % 24;
  currentDots.setAttribute("style", `stroke-dashoffset:${dotsPhase.toFixed(2)}`);

  requestAnimationFrame(visualFrame);
}
requestAnimationFrame(visualFrame);

function renderMotorVisual(m, tw) {
  liveRpm = m.rpm;
  liveCurrentA = m.tripped ? 0 : m.i_stator_a;

  visualRpmReadout.textContent = Math.round(m.rpm);
  visualTempReadout.textContent = m.winding_temp_c.toFixed(0);

  // heat halo: ramps in from ambient (25C) toward trip temp (155C)
  const heatT = Math.max(0, Math.min(1, (m.winding_temp_c - 40) / (150 - 40)));
  heatHalo.setAttribute("opacity", (heatT * 0.75).toFixed(2));

  currentDots.setAttribute("opacity", liveCurrentA > 0.5 ? "0.9" : "0");

  alarmRing.setAttribute("opacity", m.tripped ? "1" : "0");

  visualStateReadout.className = "";
  if (m.tripped) {
    visualStateReadout.classList.add("state-trip");
    visualStateReadout.textContent = `TRIPPED — ${m.trip_reason || "fault"}`;
  } else if (tw && tw.twin_alarm) {
    visualStateReadout.classList.add("state-warn");
    visualStateReadout.textContent = "TWIN ALARM";
  } else if (m.winding_temp_c >= 120) {
    visualStateReadout.classList.add("state-warn");
    visualStateReadout.textContent = "ELEVATED TEMP";
  } else {
    visualStateReadout.classList.add("state-ok");
    visualStateReadout.textContent = "NOMINAL";
  }
}

// ------------------------------------------------------------ WebSocket --
let ws;
function connect() {
  ws = new WebSocket(WS_URL);
  ws.onopen = () => {
    connChip.className = "status-chip ok";
    connLabel.textContent = "LIVE";
  };
  ws.onclose = () => {
    connChip.className = "status-chip bad";
    connLabel.textContent = "DISCONNECTED";
    setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    render(data);
  };
}
connect();

// ------------------------------------------------------------- rendering --
function setStat(valueId, barId, value, decimals, range) {
  el(valueId).firstChild.textContent = value.toFixed(decimals);
  const pct = Math.max(0, Math.min(100, (value / range) * 100));
  el(barId).style.width = pct + "%";
}

function render(data) {
  const m = data.motor, tw = data.twin, sec = data.security, f = data.field;

  setStat("statFreq", "barFreq", m.applied_freq_hz, 1, RANGES.freq);
  setStat("statRpm", "barRpm", m.rpm, 0, RANGES.rpm);
  setStat("statSlip", "barSlip", m.slip * 100, 1, RANGES.slipPct);
  setStat("statCurrent", "barCurrent", m.i_stator_a, 1, RANGES.current);
  setStat("statTemp", "barTemp", m.winding_temp_c, 0, RANGES.temp);
  setStat("statTorque", "barTorque", m.torque_e_nm, 1, RANGES.torque);

  renderMotorVisual(m, tw);

  // trip status
  if (m.tripped) {
    tripChip.className = "status-chip tripped";
    tripChip.querySelector("span:last-child").textContent = `TRIPPED (${m.trip_reason})`;
  } else {
    tripChip.className = "status-chip running";
    tripChip.querySelector("span:last-child").textContent = "RUNNING";
  }

  // twin
  el("twinExpectedFreq").textContent = tw.expected_freq_hz.toFixed(1) + " Hz";
  el("twinExpectedRpm").textContent = Math.round(tw.expected_rpm) + " RPM";
  el("fieldFreq").textContent = m.applied_freq_hz.toFixed(1) + " Hz";
  el("fieldRpm").textContent = Math.round(m.rpm) + " RPM";
  el("residualRpm").textContent = Math.round(tw.residual_rpm);

  if (tw.twin_alarm) {
    twinAlarmEl.className = "twin-alarm alarm";
    twinAlarmEl.lastChild.textContent = "ANOMALY DETECTED";
  } else {
    twinAlarmEl.className = "twin-alarm";
    twinAlarmEl.lastChild.textContent = "NOMINAL";
  }

  residualHistory.push(tw.residual_rpm);
  alarmHistory.push(tw.twin_alarm);
  if (residualHistory.length > SCOPE_POINTS) { residualHistory.shift(); alarmHistory.shift(); }
  drawScope();

  // security counts + log
  el("acceptedCount").textContent = sec.accepted_count;
  el("rejectedCount").textContent = sec.rejected_count;
  renderLog(sec.events);

  el("toggleAuth").checked = sec.auth_enabled;
  el("togglePhysics").checked = sec.physics_filter_enabled;
}

let lastLogSignature = "";
function renderLog(events) {
  const sig = JSON.stringify(events.slice(0, 3));
  if (sig === lastLogSignature) return;
  lastLogSignature = sig;

  if (!events.length) {
    logBody.innerHTML = '<div class="log-empty">Awaiting events&hellip;</div>';
    return;
  }
  logBody.innerHTML = events.map((e) => `
    <div class="log-row">
      <span class="log-time">t+${e.time_s.toFixed(1)}s</span>
      <span class="log-source ${e.source}">${e.source}</span>
      <span class="log-detail" title="${escapeHtml(e.label)} — ${escapeHtml(e.detail)}">${escapeHtml(e.label)} &mdash; ${escapeHtml(e.detail)}</span>
      <span class="log-verdict ${e.verdict}">${e.verdict.replace(/_/g, " ")}</span>
    </div>
  `).join("");
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// ------------------------------------------------------------ scope draw --
function resizeCanvas() {
  const rect = scopeCanvas.getBoundingClientRect();
  scopeCanvas.width = rect.width * devicePixelRatio;
  scopeCanvas.height = rect.height * devicePixelRatio;
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
}
window.addEventListener("resize", resizeCanvas);
resizeCanvas();

function drawScope() {
  const w = scopeCanvas.getBoundingClientRect().width;
  const h = scopeCanvas.getBoundingClientRect().height;
  ctx.clearRect(0, 0, w, h);

  const maxVal = 300; // rpm residual scale
  const threshold = 60;

  // grid lines
  ctx.strokeStyle = "rgba(255,255,255,0.05)";
  ctx.lineWidth = 1;
  for (let i = 1; i < 4; i++) {
    const y = h - (h * (i * 75)) / maxVal;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
  }

  // threshold line
  const ty = h - (h * threshold) / maxVal;
  ctx.strokeStyle = "rgba(255,176,32,0.35)";
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(0, ty); ctx.lineTo(w, ty); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "rgba(255,176,32,0.5)";
  ctx.font = "10px 'IBM Plex Mono', monospace";
  ctx.fillText("alarm threshold", 6, ty - 4);

  if (residualHistory.length < 2) return;

  const stepX = w / (SCOPE_POINTS - 1);
  const offset = SCOPE_POINTS - residualHistory.length;

  // fill under curve
  ctx.beginPath();
  ctx.moveTo(offset * stepX, h);
  residualHistory.forEach((v, i) => {
    const x = (offset + i) * stepX;
    const y = h - Math.min(h, (h * v) / maxVal);
    ctx.lineTo(x, y);
  });
  ctx.lineTo((offset + residualHistory.length - 1) * stepX, h);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(255,84,87,0.18)");
  grad.addColorStop(1, "rgba(255,84,87,0.0)");
  ctx.fillStyle = grad;
  ctx.fill();

  // line, colored per-segment by alarm state
  ctx.lineWidth = 1.75;
  ctx.beginPath();
  residualHistory.forEach((v, i) => {
    const x = (offset + i) * stepX;
    const y = h - Math.min(h, (h * v) / maxVal);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  const anyAlarm = alarmHistory.some(Boolean);
  ctx.strokeStyle = anyAlarm ? "#ff5457" : "#35d488";
  ctx.stroke();
}

// -------------------------------------------------------------- controls --
const freqSlider = el("freqSlider"), freqSliderVal = el("freqSliderVal");
freqSlider.addEventListener("input", () => { freqSliderVal.textContent = parseFloat(freqSlider.value).toFixed(1); });

async function sendCommand(freq) {
  await fetch("/api/command", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ frequency_hz: freq }),
  });
}

el("sendCommandBtn").addEventListener("click", () => sendCommand(parseFloat(freqSlider.value)));
document.querySelectorAll(".btn-op[data-freq]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const f = parseFloat(btn.dataset.freq);
    freqSlider.value = f; freqSliderVal.textContent = f.toFixed(1);
    sendCommand(f);
  });
});

document.querySelectorAll(".btn-atk[data-attack]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    await fetch("/api/attack", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ type: btn.dataset.attack, frequency_hz: 60.0 }),
    });
  });
});

async function pushSecurityConfig() {
  await fetch("/api/security/config", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      auth_enabled: el("toggleAuth").checked,
      physics_filter_enabled: el("togglePhysics").checked,
    }),
  });
}
el("toggleAuth").addEventListener("change", pushSecurityConfig);
el("togglePhysics").addEventListener("change", pushSecurityConfig);

el("resetBtn").addEventListener("click", async () => {
  await fetch("/api/reset", { method: "POST" });
  residualHistory = []; alarmHistory = [];
});
