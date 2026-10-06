// PackCheck's window. Draws what gui/scanner.py and gui/model.py decided;
// makes no judgements of its own about a battery.
//
// The look is the approved mockup ("Fuel gauge, on red"): cells on the left,
// the verdict on the right, how hard it's been worked underneath. Every screen
// -- waiting, reading, result, no adapter -- keeps that same frame, so the app
// never jumps between layouts. The cell and current charts are the mockup's
// own drawing code.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MONO = "IBM Plex Mono, ui-monospace, monospace";

let api = null;
let last = null;          // last state from the scanner
let shownKey = null;      // what the main area is currently drawn for
let showAll = false;      // "Show all readings" open
let noLogoHelp = false;   // "nothing's happening?" screen open
let hiddenChips = new Set();
let stickerTimer = null;
let nameTimer = null;

const PILL = {
  no_adapter:      ["NO ADAPTER CONNECTED", "warn"],
  confirm_adapter: ["ADAPTER NOT TESTED", "warn"],
  waiting:         ["LINE LOW · SAFE TO CONNECT", ""],
  reading:         ["READING · KEEP IT CONNECTED", "busy"],
  attached:        ["BATTERY ATTACHED · UNPLUG BEFORE QUITTING", ""],
  closing:         ["CLOSING · UNPLUG THE BATTERY", "warn busy"],
  closed:          ["PORT CLOSED", ""],
};

// --- polling ---------------------------------------------------------------------

async function tick() {
  try {
    const s = await api.state();
    if (!last || s.version !== last.version || s.opened !== last.opened) {
      // A battery being plugged in takes over from a saved scan.
      if (s.phase === "reading" && last && last.phase !== "reading" && s.opened) {
        await api.close_scan();
        s.opened = null;
      }
      if (s.phase !== "waiting") noLogoHelp = false;
      last = s;
      render(s);
    }
  } catch (e) {
    console.error(e);
  }
  setTimeout(tick, 300);
}

function start() {
  api = window.pywebview.api;
  $("#open-btn").onclick = async () => { await api.open_scan(); last = null; };
  tick();
}
if (window.pywebview && window.pywebview.api) start();
else window.addEventListener("pywebviewready", start);

// --- drawing ---------------------------------------------------------------------

function render(s) {
  const [text, cls] = PILL[s.phase] || ["", ""];
  $("#pill-text").textContent = text;
  $("#pill").className = "safe " + cls;

  const view = s.opened ? s.opened.view : s.view;
  const key = JSON.stringify([s.opened && s.opened.file, s.phase, noLogoHelp, showAll, [...hiddenChips],
    s.phase === "confirm_adapter" ? s.adapter && s.adapter.device : null,
    view && [view.identity.serial, view.verdict.title], s.removed, s.saved && s.saved.file,
    !!(s.sticker && s.sticker.text === "UNREADABLE")]);
  if (key !== shownKey) {
    shownKey = key;
    $("#main").innerHTML = s.opened ? resultScreen(s.opened.view, s, s.opened.file) : screenFor(s, view);
    wire(s);
  }
  // Parts that change while the screen stays put.
  const bar = $("#progress-bar");
  if (bar) bar.style.width = s.progress + "%";
  const pct = $("#progress-pct");
  if (pct) pct.textContent = s.progress + "%";
  stickerStatus(s.sticker);
  const modal = $("#modal-root");
  if (s.phase === "closing" && !modal.dataset.closing) {
    modal.dataset.closing = "1";
    modal.innerHTML = closingModal();
    $("#keep-open").onclick = () => { api.cancel_close(); };
  } else if (s.phase !== "closing" && modal.dataset.closing) {
    delete modal.dataset.closing;
    modal.innerHTML = "";
  }
}

function screenFor(s, view) {
  switch (s.phase) {
    case "no_adapter": return noAdapter(s);
    case "confirm_adapter": return confirmAdapter(s.adapter);
    case "reading": return reading(s);
    case "closed": return frame(null, `<span class="status plain">Closed</span>
      <h2 class="vword">Port closed</h2><p class="vtext">It's safe to quit.</p>`, "port closed");
    case "attached":
    case "closing":
      return view ? resultScreen(view, s) : waiting(s);
    default:
      if (view && s.removed && !noLogoHelp) return resultScreen(view, s);
      return noLogoHelp ? noLogo() : waiting(s);
  }
}

// The frame every screen shares: cells well, readout, worked well, footer.
function frame(cells, readout, emptyMsg, extras = "", usage = null, footRight = "") {
  return `${extras.notice || ""}
  <div class="well">
    <span class="lbl">Cells · volts${cells && cells.imbalance_mv != null ? ` · cell imbalance (ΔV) ${esc(cells.imbalance_mv)} mV` : ""}</span>
    ${cellsSVG(cells, emptyMsg)}
  </div>
  <div class="readout">${readout}</div>
  ${workedWell(usage)}
  ${extras.after || ""}
  <div class="foot"><span>PackCheck is independent and not affiliated with Milwaukee Tool.</span><span>${footRight}</span></div>`;
}

// --- the cells chart (from the mockup) ---------------------------------------------

function cellsSVG(c, emptyMsg) {
  const W = 420, H = 214, pad = { l: 36, r: 6, t: 10, b: 38 };
  const lo = 3000, hi = 4200, y = (v) => pad.t + (H - pad.t - pad.b) * (1 - (v - lo) / (hi - lo));
  const bw = (W - pad.l - pad.r) / 5;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Cell voltages, 3.0 to 4.2 volt scale">`;
  [3000, 3400, 3800, 4200].forEach((v) => {
    s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="#fff" stroke-opacity=".28"/>`;
    s += `<text x="${pad.l - 6}" y="${y(v) + 4}" text-anchor="end" font-size="10.5" font-weight="600" fill="#fff" font-family="${MONO}">${(v / 1000).toFixed(1)}</text>`;
  });
  const cells = c && c.cells ? c.cells : [];
  if (!cells.length) {
    s += `<text x="${(W + pad.l) / 2}" y="${(H - pad.b) / 2 + 6}" text-anchor="middle" font-size="13" font-weight="600" fill="#fff" font-family="${MONO}">${esc(emptyMsg || "cell readings blank")}</text>`;
    for (let i = 0; i < 5; i++) s += `<rect x="${pad.l + i * bw + 9}" y="${y(lo) - 4}" width="${bw - 18}" height="3" fill="#fff" fill-opacity=".35"/>`;
    return s + `</svg>`;
  }
  const real = cells.filter((x) => x.mv !== null).map((x) => x.mv);
  const mn = Math.min(...real), mx = Math.max(...real);
  const spread = c.imbalance_mv;
  cells.forEach((cell, i) => {
    const x = pad.l + i * bw + 9, w = bw - 18;
    if (cell.mv === null) {
      s += `<rect x="${x}" y="${y(lo) - 4}" width="${w}" height="3" fill="#fff" fill-opacity=".35"/>`;
      s += `<text x="${x + w / 2}" y="${H - 21}" text-anchor="middle" font-size="11" font-weight="600" fill="#fff" font-family="${MONO}">blank</text>`;
      s += `<text x="${x + w / 2}" y="${H - 6}" text-anchor="middle" font-size="10" fill="#fff" font-family="${MONO}">cell ${cell.n}</text>`;
      return;
    }
    const v = Math.max(lo, Math.min(hi, cell.mv));
    const segs = 12, top = y(v), segH = (y(lo) - top) / segs;
    const col = cell.low ? "#FFD23F" : "#FFFFFF";   // model marks the lowest only when ΔV is over 10 mV
    for (let k = 0; k < segs; k++) {
      const yy = y(lo) - (k + 1) * segH;
      s += `<rect x="${x}" y="${(yy + 1).toFixed(1)}" width="${w}" height="${Math.max(segH - 2.2, 1).toFixed(1)}" rx="1" fill="${col}" fill-opacity="${(0.5 + 0.5 * (k + 1) / segs).toFixed(2)}"/>`;
    }
    const tag = spread > 5 ? (cell.mv === mn ? "low" : cell.mv === mx ? "high" : "") : "";
    s += `<text x="${x + w / 2}" y="${H - 21}" text-anchor="middle" font-size="12" font-weight="600" fill="#fff" font-family="${MONO}">${cell.mv}</text>`;
    s += `<text x="${x + w / 2}" y="${H - 6}" text-anchor="middle" font-size="10" fill="#fff" font-family="${MONO}">cell ${cell.n}${tag ? " · " + tag : ""}</text>`;
  });
  return s + `</svg>`;
}

// --- how hard it's been worked (from the mockup) -------------------------------------

const RANGES = Array.from({ length: 19 }, (_, i) => `${(i + 1) * 10}`).concat([">200"]);

function workedWell(u) {
  const label = `<span class="lbl">How hard it's been worked · share of time at each current · black = 110 A and up</span>`;
  if (u && !u.valid) {
    return `<div class="well full">${label}${bucketsSVG(null)}
      <p class="vtext" style="font-size:14px"><span class="blank">blank</span> This battery's record of how it was used is empty.</p></div>`;
  }
  const extra = u && u.total ? `<p class="vtext" style="font-size:14px;max-width:none">${esc(u.total)} of tool use. ${esc(u.hard_text || "")}</p>` : "";
  return `<div class="well full">${label}${bucketsSVG(u)}${extra}</div>`;
}

function bucketsSVG(u) {
  const W = 1000, H = 120, pad = { l: 6, r: 6, t: 8, b: 24 }, n = 20, bw = (W - pad.l - pad.r) / n, max = 35;
  const secs = u && u.bars ? u.bars.map((b) => b.seconds || 0) : Array(20).fill(0);
  const total = secs.reduce((a, b) => a + b, 0);
  const pct = secs.map((s) => (total ? Math.round((100 * s) / total) : 0));
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Share of time spent at each current, 10 A to over 200 A">`;
  s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${H - pad.b}" y2="${H - pad.b}" stroke="#fff" stroke-opacity=".45"/>`;
  pct.forEach((v, i) => {
    const h = ((H - pad.t - pad.b) * Math.min(v, max)) / max, x = pad.l + i * bw + 3;
    s += `<rect x="${x.toFixed(1)}" y="${(H - pad.b - h).toFixed(1)}" width="${(bw - 6).toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${i >= 10 ? "#0A0A0A" : "#FFFFFF"}"/>`;
    if (v > 0) s += `<text x="${(x + (bw - 6) / 2).toFixed(1)}" y="${(H - pad.b - h - 4).toFixed(1)}" text-anchor="middle" font-size="11" font-weight="600" fill="#fff" font-family="${MONO}">${v}%</text>`;
    if (i % 3 === 0 || i === 19) s += `<text x="${(x + (bw - 6) / 2).toFixed(1)}" y="${H - 7}" text-anchor="middle" font-size="11" fill="#fff" font-family="${MONO}">${RANGES[i]} A</text>`;
  });
  return s + `</svg>`;
}

// --- screens before a battery ---------------------------------------------------------

function noAdapter(s) {
  return frame(null, `<span class="status plain">No adapter</span>
    <h2 class="vword">Plug in your adapter</h2>
    <p class="vtext">Connect the USB serial adapter to this computer. PackCheck finds it on its own.</p>
    ${s.error ? `<div class="advice info"><b>i</b><span>${esc(s.error)}</span></div>` : ""}`, "no adapter connected");
}

function confirmAdapter(a) {
  return frame(null, `<span class="status look">! Adapter not tested</span>
    <h2 class="vword">Check your adapter</h2>
    <p class="vtext"><span class="num">${esc(a.chip)}</span> on <span class="num">${esc(a.device)}</span></p>
    <div class="advice look"><b>!</b><div><span>${esc(a.message)}</span>
      <div class="act"><button type="button" class="btn dark" id="accept-adapter">Use it anyway</button></div></div></div>
    <p class="vtext" style="font-size:14px">Or unplug it and connect a different adapter. This screen updates by itself.</p>`,
    "waiting for an adapter");
}

function waiting(s) {
  return frame(null, `<span class="status wait">Ready</span>
    <h2 class="vword">Plug in a battery</h2>
    <p class="vtext">Reading starts on its own. You'll be asked for the sticker number while it reads, about 25 seconds.</p>
    <button type="button" class="hint" id="nothing">Battery plugged in but nothing's happening?</button>
    ${s.adapter ? `<div class="who"><span>Adapter: <span class="num">${esc(s.adapter.chip)}</span></span><span class="num">${esc(s.adapter.device)}</span></div>` : ""}`,
    "waiting for a battery");
}

const LOGO_ICON = `<svg viewBox="0 0 44 44" aria-hidden="true"><rect width="44" height="44" rx="6" fill="#fff"/>
  <g fill="none" stroke="#A60115" stroke-width="3.2" stroke-linecap="round"><path d="M10 20a17 17 0 0 1 24 0"/><path d="M14.5 24.5a10.5 10.5 0 0 1 15 0"/></g><circle cx="22" cy="30" r="2.8" fill="#A60115"/></svg>`;
const RESEAT_ICON = `<svg viewBox="0 0 44 44" aria-hidden="true"><rect width="44" height="44" rx="6" fill="#FFD23F"/>
  <g fill="none" stroke="#0A0A0A" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="M31 17a10 10 0 1 0 1.5 8"/><path d="M32 10v7h-7"/></g></svg>`;

function noLogo() {
  return frame(null, `<span class="status plain">No response</span>
    <h2 class="vword">Can't read this battery</h2>
    <p class="vtext">The battery isn't answering. Only packs with the wifi-style logo next to the serial number can report their history.</p>
    <div class="steps">
      <div class="step">${LOGO_ICON}<span><b>No logo by the serial?</b>This model has no diagnostics built in. Nothing can read it, and it isn't a fault. Unplug it before you quit: PackCheck can't see it, so it can't remind you.</span></div>
      <div class="step">${RESEAT_ICON}<span><b>Has the logo?</b>Then it's the connection. Unplug it, check the three wires (adapter TX to J2, RX to J1, ground to B−), plug it back in. It'll be read automatically.</span></div>
    </div>
    <button type="button" class="hint" id="nothing-back">Back</button>`, "no answer from the battery");
}

function reading(s) {
  return frame(null, `<span class="status wait">Reading</span>
    <h2 class="vword">Reading the battery</h2>
    <p class="vtext">Keep it plugged in. Everything it knows about itself is being read and saved.</p>
    <div class="progress" aria-label="Reading progress"><div id="progress-bar" style="width:${s.progress}%"></div></div>
    <span class="lbl" id="progress-pct">${s.progress}%</span>
    ${stickerField(s.sticker, "While you wait, type the case sticker:")}`, "reading…");
}

function stickerField(st, label) {
  // Worn off: the sticker is recorded as unreadable, and the user gives the
  // pack a name of their own instead. The name is stored apart from the
  // sticker, so it is never mistaken for a printed serial.
  if (st && st.text === "UNREADABLE") {
    return `<div class="sticker">
      <span class="lbl">Sticker: worn off</span>
      <label for="pack-name">Name it something you'll recognise:</label>
      <div class="row"><input id="pack-name" class="free" autocomplete="off" maxlength="60"
        placeholder="e.g. Garage pack 3, blue tape" value="${esc(st.name || "")}"></div>
      <button type="button" class="hint" id="sticker-back">Type the sticker after all</button>
      <div class="msg" id="sticker-status"></div>
    </div>`;
  }
  const value = st && st.text ? st.text : "";
  return `<div class="sticker">
    <label for="sticker">${esc(label)}</label>
    <div class="row">
      <input id="sticker" autocomplete="off" spellcheck="false" placeholder="ABCD1234 180720 7654323" value="${esc(value)}">
      <button type="button" class="btn" id="worn-off">Sticker worn off</button>
    </div>
    <div class="msg" id="sticker-status"></div>
  </div>`;
}

function stickerStatus(st) {
  const el = $("#sticker-status");
  if (!el) return;
  let msg = "", warn = false;
  if (st && st.text === "UNREADABLE") msg = st.name ? "Saved." : "";
  else if (st && st.date_ok === false) { msg = `Check the sticker: ${st.date_message}. Is it the right battery?`; warn = true; }
  else if (st && st.problem) { msg = `Saved as typed. Unusual format: ${st.problem}.`; warn = true; }
  else if (st && st.date_ok) msg = "Sticker matches the battery's build date.";
  else if (st && st.text) msg = "Saved.";
  el.innerHTML = warn ? `<div class="advice look"><b>!</b><span>${esc(msg)}</span></div>` : esc(msg);
}

// --- the battery -----------------------------------------------------------------------

function resultScreen(v, s, openedFile) {
  const d = v.verdict, id = v.identity;
  const live = !openedFile && !s.removed && d.result !== "DROPPED";

  let notice = "";
  if (openedFile) notice = `<div class="notice"><span class="lbl">Saved scan: ${esc(openedFile)}</span><span class="spacer"></span>
      <button type="button" class="hint" id="close-scan">Back to live</button></div>`;
  else if (s.removed) notice = `<div class="notice"><span class="lbl">Battery removed · plug in the next one</span></div>`;

  const adviceKind = { good: "info", look: "look", dead: "dead" }[d.state];
  const adviceIcon = { info: "i", look: "!", dead: "×" }[adviceKind];
  let readout = `<span class="status ${d.state}">${chipText(d.status)}</span>
    <h2 class="vword ${d.state === "dead" ? "dead" : ""}">${esc(d.title)}</h2>
    <p class="vtext">${esc(d.summary)}</p>`;
  if (d.action) readout += `<div class="advice ${adviceKind}"><b>${adviceIcon}</b><span>${esc(d.action)}</span></div>`;
  readout += (d.reasons || []).map((r) => `<div class="advice info"><b>i</b><span>${esc(r)}</span></div>`).join("");
  if (v.tiles && v.tiles.length) {
    readout += `<div class="grid3">${v.tiles.map((t) => `<div class="cell"><span class="k">${esc(t.label)}</span>
      <span class="v${t.label === "Built" ? " sm" : ""}">${t.valid ? esc(t.value) : '<span class="blank">blank</span>'}</span></div>`).join("")}</div>`;
  }
  if (live) readout += stickerField(s.sticker, "Sticker:");
  const who = [];
  if (!live && id.sticker) who.push(`<span>Sticker: <span class="num">${esc(id.sticker === "UNREADABLE" ? "worn off" : id.sticker)}</span></span>`);
  if (!live && id.name) who.push(`<span>Name: <b>${esc(id.name)}</b></span>`);
  if (id.serial) who.push(`<span>Serial: <span class="num">${esc(id.serial)}</span></span>`);
  if (id.model) who.push(`<span>${esc(id.model)}</span>`);
  if (who.length) readout += `<div class="who">${who.join("")}</div>`;

  let after = "";
  if (v.history && v.history.length > 1) {
    after += `<div class="well full"><span class="lbl">Cell imbalance (ΔV) over time · if the gap grows back after balancing, a cell is weak</span>
      <div class="hist">${v.history.map((p) => `<div class="pt"><span>${esc(p.date)}</span><b class="num">${esc(p.mv)} mV</b></div>`).join("")}</div></div>`;
  }
  if (v.readings && v.readings.total) after += readingsPanel(v.readings);

  const foot = [id.captured ? `scanned ${esc(id.captured)}` : "",
    !openedFile && s.saved ? `saved as ${esc(s.saved.file)}${s.saved.rescan ? " (re-scan, beside the earlier one)" : ""}` : ""]
    .filter(Boolean).join(" · ");
  const empty = d.result === "DROPPED" ? "reading didn't finish" : "cell readings blank";
  return frame(v.cells, readout, empty, { notice, after }, d.result === "DROPPED" ? null : v.usage, foot);
}

// IBM Plex Mono has no tick glyph, and the fallback draws a square-root sign.
const TICK = `<svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true"><path d="M1.5 6.5l3 3 6-7"
  fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
function chipText(s) {
  return s.startsWith("✓") ? TICK + esc(s.slice(1)) : esc(s);
}

const CHIP_CLASS = {
  "recorded": "c-ok", "not kept by this model": "c-nr", "never written": "c-nw",
  "blank": "c-bl", "unreadable": "c-un", "not read": "c-un",
};

function readingsPanel(r) {
  const summary = r.counts.map((c) => `${c.n} ${c.chip}`).join(", ");
  let h = `<div class="more"><button type="button" class="btn" id="toggle-all" aria-expanded="${showAll}">
    ${showAll ? "Hide" : "Show"} all ${r.total} readings · ${esc(summary)}</button>`;
  if (showAll) {
    const rows = r.rows.filter((x) => !hiddenChips.has(x.chip)).map((x) => `<tr>
        <td class="num">${x.id}</td><td class="num">${esc(x.addr)}</td><td class="label">${esc(x.label)}</td>
        <td class="num">${x.value === null ? "—" : esc(x.value)}</td>
        <td><span class="chip ${CHIP_CLASS[x.chip]}">${esc(x.chip)}</span></td></tr>`).join("");
    h += `<div class="chipkey">${r.counts.map((c) => `<button type="button" class="chip ${CHIP_CLASS[c.chip]}" data-chip="${esc(c.chip)}"
        aria-pressed="${!hiddenChips.has(c.chip)}">${c.n} ${esc(c.chip)}</button>`).join("")}</div>
      <p class="regs-note"><b>Not kept by this model</b> is normal: each generation of battery records a different set.
        <b>Never written</b> means nothing is stored there yet. <b>Blank</b> means a record the battery should have is empty.</p>
      <div class="regs"><table><thead><tr><th>#</th><th>Address</th><th>Reading</th><th>Value</th><th>State</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
  }
  return h + `</div>`;
}

// --- dialogs ---------------------------------------------------------------------------

function closingModal() {
  return `<div class="overlay"><div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="close-title">
    <span class="status look">! Battery attached</span>
    <h2 class="vword" id="close-title">Unplug the battery to quit</h2>
    <p>Closing now would let the data line go high, and a battery left on it permanently counts that as a
      charge on a non-Milwaukee charger.</p>
    <p>PackCheck closes by itself as soon as the battery is unplugged.</p>
    <p style="font-size:14px">A battery without the wifi-style logo can't be detected. If one is attached, unplug it too.</p>
    <div class="actions"><button type="button" class="btn solid" id="keep-open">Keep PackCheck open</button></div>
  </div></div>`;
}

// --- events ----------------------------------------------------------------------------

function redraw() { shownKey = null; render(last); }

function wire(s) {
  const on = (id, fn) => { const el = $("#" + id); if (el) el.onclick = fn; };
  on("accept-adapter", () => api.accept_adapter(s.adapter.device));
  on("nothing", () => { noLogoHelp = true; redraw(); });
  on("nothing-back", () => { noLogoHelp = false; redraw(); });
  on("close-scan", async () => { await api.close_scan(); last = null; });
  on("toggle-all", () => { showAll = !showAll; redraw(); });
  document.querySelectorAll(".chipkey [data-chip]").forEach((b) => {
    b.onclick = () => {
      const c = b.dataset.chip;
      hiddenChips.has(c) ? hiddenChips.delete(c) : hiddenChips.add(c);
      redraw();
    };
  });
  const input = $("#sticker");
  if (input) {
    const send = async () => {
      const info = await api.set_sticker(input.value);
      if (document.activeElement !== input && info.text && info.text !== "UNREADABLE") input.value = info.text;
    };
    input.oninput = () => { clearTimeout(stickerTimer); stickerTimer = setTimeout(send, 500); };
    input.onblur = send;
    input.onkeydown = (e) => { if (e.key === "Enter") input.blur(); };
    on("worn-off", async () => { clearTimeout(stickerTimer); await api.set_sticker("?"); });
  }
  const nameInput = $("#pack-name");
  if (nameInput) {
    const sendName = () => api.set_name(nameInput.value);
    nameInput.oninput = () => { clearTimeout(nameTimer); nameTimer = setTimeout(sendName, 500); };
    nameInput.onblur = sendName;
    nameInput.onkeydown = (e) => { if (e.key === "Enter") nameInput.blur(); };
    on("sticker-back", async () => { clearTimeout(nameTimer); await api.set_name(""); await api.set_sticker(""); });
  }
}
