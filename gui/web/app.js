// PackCheck's window. Draws what gui/scanner.py and gui/model.py decided;
// makes no judgements of its own about a battery.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let api = null;
let last = null;           // last state from the scanner
let shownKey = null;       // what the main area is currently drawn for
let showAll = false;       // "Show all readings" open
let hiddenChips = new Set();
let stickerTimer = null;

const PILL = {
  no_adapter:      ["No adapter connected", "warn"],
  confirm_adapter: ["Adapter not tested", "warn"],
  waiting:         ["Line low · safe to connect", ""],
  reading:         ["Reading · keep it connected", "busy"],
  attached:        ["Battery attached · unplug before quitting", ""],
  closing:         ["Closing · unplug the battery", "warn busy"],
  closed:          ["Port closed", ""],
};

// --- polling --------------------------------------------------------------

async function tick() {
  try {
    const s = await api.state();
    if (!last || s.version !== last.version || s.opened !== last.opened) {
      // A battery being plugged in takes over from a saved scan.
      if (s.phase === "reading" && last && last.phase !== "reading" && s.opened) {
        await api.close_scan();
        s.opened = null;
      }
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

// --- drawing -----------------------------------------------------------------

function render(s) {
  const [text, cls] = PILL[s.phase] || ["", ""];
  $("#pill-text").textContent = text;
  $("#pill").className = "pill " + cls;

  const view = s.opened ? s.opened.view : s.view;
  const key = JSON.stringify([s.opened && s.opened.file, s.phase === "reading" ? "reading" : s.phase,
    s.phase === "confirm_adapter" ? s.adapter && s.adapter.device : null,
    view && [view.identity.serial, view.verdict.title], s.removed, s.saved && s.saved.file]);
  if (key !== shownKey) {
    shownKey = key;
    $("#main").innerHTML = s.opened ? resultScreen(s.opened.view, s, s.opened.file)
      : screenFor(s, view);
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
    case "attached":
    case "closing":
      return view ? resultScreen(view, s) : waiting(s);
    default:
      return view && s.removed ? resultScreen(view, s) : waiting(s);
  }
}

// --- empty states ---------------------------------------------------------------

const PLUG = `<svg viewBox="0 0 64 64" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <rect x="14" y="22" width="36" height="30" rx="4"/><path d="M24 22V12M40 22V12M26 36h12"/></svg>`;

function noAdapter(s) {
  return `<section class="center">
    <div class="socket">${PLUG}</div>
    <h1>Plug in your adapter</h1>
    <p>Connect the USB serial adapter to this computer. PackCheck finds it on its own.</p>
    ${s.error ? `<p><b>${esc(s.error)}</b></p>` : ""}
  </section>`;
}

function confirmAdapter(a) {
  return `<section class="center">
    <div class="caution">
      <div class="caps">Adapter not tested</div>
      <h2>${esc(a.chip)} on ${esc(a.device)}</h2>
      <p>${esc(a.message)}</p>
      <p style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px">
        <button class="btn dark" id="accept-adapter">Use it anyway</button>
      </p>
      <p style="margin:0;font-size:14px">Or unplug it and connect a different adapter. This screen updates by itself.</p>
    </div>
  </section>`;
}

function waiting(s) {
  return `<section class="center">
    <div class="socket">${PLUG}</div>
    <h1>Plug in a battery</h1>
    <p>PackCheck reads it the moment it's connected. It takes about 25 seconds.</p>
    <p class="caps">Adapter: ${esc(s.adapter ? s.adapter.chip : "")} · ${esc(s.adapter ? s.adapter.device : "")}</p>
    <button class="btn" id="nothing-happening">Battery plugged in but nothing's happening?</button>
  </section>`;
}

function reading(s) {
  return `<section class="center">
    <h1>Reading the battery</h1>
    <p>Keep it plugged in. Everything it knows about itself is being read and saved.</p>
    <div class="progress" aria-label="Reading progress"><div id="progress-bar" style="width:${s.progress}%"></div></div>
    <div class="caps num" id="progress-pct">${s.progress}%</div>
    ${stickerField(s.sticker, "While you wait: type the case sticker")}
  </section>`;
}

function stickerField(st, label) {
  const value = st && st.text && st.text !== "UNREADABLE" ? st.text : "";
  return `<div class="sticker">
    <label for="sticker">${esc(label)}</label>
    <div class="row">
      <input id="sticker" autocomplete="off" spellcheck="false" placeholder="e.g. ABCD1234 180720 7654323" value="${esc(value)}">
      <button class="btn" id="no-sticker" title="Records the sticker as unreadable">Can't read it</button>
    </div>
    <div class="status" id="sticker-status"></div>
  </div>`;
}

function stickerStatus(st) {
  const el = $("#sticker-status");
  if (!el) return;
  let msg = "", warn = false;
  if (st && st.text === "UNREADABLE") msg = "Sticker recorded as unreadable.";
  else if (st && st.date_ok === false) { msg = `Check the sticker: ${st.date_message}. Is it the right battery?`; warn = true; }
  else if (st && st.problem) { msg = `Saved as typed. Unusual format: ${st.problem}.`; warn = true; }
  else if (st && st.date_ok) msg = "Sticker matches the battery's build date.";
  else if (st && st.text) msg = "Saved.";
  el.textContent = msg;
  el.className = "status" + (warn ? " warn" : "");
}

// --- the battery -------------------------------------------------------------------

function resultScreen(v, s, openedFile) {
  const banner = openedFile
    ? `<div class="banner"><span>Saved scan: ${esc(openedFile)}</span><span class="spacer"></span>
         <button class="btn" id="close-scan">Back to live</button></div>`
    : s.removed
      ? `<div class="banner"><span>Battery removed. Plug in the next one.</span></div>`
      : `<div class="banner"><span>Unplug the battery when you're done.</span>
         ${s.saved ? `<span class="spacer"></span><span>Saved as ${esc(s.saved.file)}${s.saved.rescan ? " (re-scan, kept beside the earlier one)" : ""}</span>` : ""}</div>`;
  const live = !openedFile && !s.removed && v.verdict.result !== "DROPPED";
  return `${banner}
  <div class="grid">
    ${verdictPanel(v, live ? s : null)}
    ${v.cells ? cellsPanel(v.cells) : ""}
    ${v.numbers.length ? statsPanel(v.numbers) : ""}
    ${v.usage ? usagePanel(v.usage) : ""}
    ${v.wear.length ? `<div class="cols2">${factsPanel("Wear", v.wear)}${factsPanel("Details", v.details)}</div>` : ""}
    ${v.history.length > 1 ? historyPanel(v.history) : ""}
    ${v.readings.total ? readingsPanel(v.readings) : ""}
  </div>`;
}

function verdictPanel(v, liveState) {
  const d = v.verdict, id = v.identity;
  const chip = { good: "Good", look: "Needs a look", dead: "Dead" }[d.state];
  const rows = [
    ["Sticker", liveState ? null : id.sticker === "UNREADABLE" ? "unreadable" : id.sticker],
    ["Serial", id.serial],
    ["Model", id.model],
    ["Scanned", id.captured],
  ].filter(([, x]) => x !== null && x !== undefined && x !== "");
  return `<section class="panel verdict">
    <div>
      <span class="chip ${d.state}">${chip}</span>
      <h1>${esc(d.title)}</h1>
      <p class="summary">${esc(d.summary)}</p>
      ${d.action ? `<p class="action">${esc(d.action)}</p>` : ""}
      ${d.reasons.length ? `<ul>${d.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    </div>
    <div>
      <dl class="ident">${rows.map(([k, x]) => `<dt>${k}:</dt><dd class="num">${esc(x)}</dd>`).join("")}</dl>
      ${liveState ? `<div style="margin-top:18px">${stickerField(liveState.sticker, "Sticker:")}</div>` : ""}
    </div>
  </section>`;
}

const SEGMENTS = 12;   // 3.0 to 4.2 V, 0.1 V each

function cellsPanel(c) {
  if (!c.cells.length) {
    return `<section class="panel"><h2 class="caps">The five cells</h2>
      <p style="margin:0"><span class="missing">blank</span> The battery didn't report its cell voltages.</p></section>`;
  }
  const level = { ok: "Within the normal range", slight: "Slightly out", balance: "Out of balance" }[c.imbalance_level] || "";
  const cells = c.cells.map((cell) => {
    const lit = cell.mv === null ? 0 : Math.max(1, Math.round(cell.fraction * SEGMENTS));
    const leds = Array.from({ length: SEGMENTS }, (_, i) => `<div class="led${i < lit ? " on" : ""}"></div>`).join("");
    return `<div class="cell${cell.low ? " low" : ""}">
      <div class="leds" aria-hidden="true">${leds}</div>
      <div class="v num">${cell.volts === null ? '<span class="missing">blank</span>' : esc(cell.volts) + " V"}</div>
      <div class="n">Cell ${cell.n}</div>
      ${cell.low ? '<div class="flag">Lowest</div>' : ""}
    </div>`;
  }).join("");
  return `<section class="panel">
    <h2 class="caps">The five cells</h2>
    <div class="cells-wrap">
      <div class="scale num" aria-hidden="true"><span>4.2 V</span><span>3.6 V</span><span>3.0 V</span></div>
      <div class="cells">${cells}</div>
    </div>
    <div class="cellfacts">
      <div><div class="caps">Cell imbalance (ΔV)</div>
        <div class="big num">${c.imbalance_mv === null ? '<span class="missing">blank</span>' : `${esc(c.imbalance_mv)} <small>mV</small>`}</div>
        <div>${esc(level)}${c.imbalance_level ? " · normal is 10 mV or less, readings vary ±5 mV" : ""}</div></div>
      ${c.pack_volts ? `<div><div class="caps">Pack voltage</div><div class="big num">${esc(c.pack_volts)} <small>V</small></div></div>` : ""}
    </div>
  </section>`;
}

function valueOf(st) {
  if (!st.valid) return `<span class="missing">blank</span>`;
  return `${esc(st.value)}${st.unit ? ` <small>${esc(st.unit)}</small>` : ""}`;
}

function statsPanel(nums) {
  return `<section class="stats">${nums.map((n) => `<div class="panel stat">
      <div class="caps">${esc(n.label)}</div>
      <div class="big num">${valueOf(n)}</div>
      ${n.note ? `<div class="note">${esc(n.note)}</div>` : ""}
    </div>`).join("")}</section>`;
}

function factsPanel(title, rows) {
  return `<section class="panel"><h2 class="caps">${esc(title)}</h2>
    <dl class="facts">${rows.map((r) => `<dt>${esc(r.label)}</dt><dd class="num">${
      r.valid ? esc(r.value) + (r.unit ? " " + esc(r.unit) : "") : '<span class="missing">blank</span>'}</dd>`).join("")}</dl>
  </section>`;
}

function usagePanel(u) {
  if (!u.valid) {
    return `<section class="panel"><h2 class="caps">How hard it's been worked</h2>
      <p style="margin:0"><span class="missing">blank</span> This battery's record of how it was used is empty.</p></section>`;
  }
  const max = Math.max(1, ...u.bars.map((b) => b.seconds || 0));
  const cols = u.bars.map((b) => {
    const h = b.seconds ? Math.max(2, (100 * b.seconds) / max) : 0;
    const t = b.seconds === null ? "blank" : `${Math.round(b.seconds / 60)} min`;
    return `<div class="col${b.hard ? " hard" : ""}${h ? "" : " zero"}" style="height:${h}%" title="${esc(b.range)}: ${t}"></div>`;
  }).join("");
  const axis = u.bars.map((b) => `<span>${b.from_amps}</span>`).join("");
  return `<section class="panel">
    <h2 class="caps">How hard it's been worked</h2>
    <p style="margin:-6px 0 16px">${esc(u.total)} of tool use, by how much current it was delivering. ${esc(u.hard_text || "")}</p>
    <div class="usage" role="img" aria-label="Time spent at each current">${cols}</div>
    <div class="usage-axis num" aria-hidden="true">${axis}</div>
    <div class="legend"><span><i style="background:#fff"></i>10–110 A</span>
      <span><i style="background:#0A0A0A"></i>110 A and up: heavy work</span><span>Amps, in 10 A steps</span></div>
  </section>`;
}

function historyPanel(points) {
  return `<section class="panel">
    <h2 class="caps">Cell imbalance (ΔV) over time</h2>
    <p style="margin:-6px 0 12px">If the gap grows back after balancing, a cell is weak.</p>
    <div class="history">${points.map((p) => `<div class="pt"><span>${esc(p.date)}</span><b class="num">${esc(p.mv)} mV</b></div>`).join("")}</div>
  </section>`;
}

const CHIP_CLASS = {
  "recorded": "recorded", "not kept by this model": "notkept", "never written": "never",
  "blank": "blank", "unreadable": "unreadable", "not read": "notread",
};

function readingsPanel(r) {
  if (!showAll) {
    return `<button class="toggle" id="toggle-all" aria-expanded="false">
      Show all ${r.total} readings <span class="spacer"></span>
      ${r.counts.map((c) => `<span class="rchip ${CHIP_CLASS[c.chip]}">${c.n} ${esc(c.chip)}</span>`).join(" ")}
    </button>`;
  }
  const rows = r.rows.filter((x) => !hiddenChips.has(x.chip)).map((x) => `<tr>
      <td class="num">${x.id}</td><td class="addr">${esc(x.addr)}</td><td>${esc(x.label)}</td>
      <td class="val num">${x.value === null ? "" : esc(x.value)}</td>
      <td><span class="rchip ${CHIP_CLASS[x.chip]}">${esc(x.chip)}</span></td></tr>`).join("");
  return `<section class="panel">
    <button class="toggle" id="toggle-all" aria-expanded="true" style="padding:0 0 14px;background:none">
      Hide the ${r.total} readings</button>
    <div class="chipkey">${r.counts.map((c) => `<button class="rchip ${CHIP_CLASS[c.chip]}" data-chip="${esc(c.chip)}"
      aria-pressed="${!hiddenChips.has(c.chip)}">${c.n} ${esc(c.chip)}</button>`).join("")}</div>
    <p style="margin:0 0 12px;font-size:14px"><b>Not kept by this model</b> is normal: each generation of battery
      records a different set. <b>Never written</b> means the battery has nothing stored there yet.
      <b>Blank</b> means a record the battery should have is empty.</p>
    <table class="readings"><thead><tr><th>#</th><th class="addr">Address</th><th>Reading</th><th>Value</th><th>State</th></tr></thead>
      <tbody>${rows}</tbody></table>
  </section>`;
}

// --- modals --------------------------------------------------------------------------

function cantReadModal() {
  return `<div class="overlay" id="cant-read"><div class="modal" role="dialog" aria-modal="true" aria-labelledby="cr-title">
    <h2 id="cr-title">Can't read this battery</h2>
    <p>A battery that can't share its diagnostics sends nothing at all, which looks
      exactly like nothing being plugged in. So check which kind you have:</p>
    <div class="two">
      <div><h3>No wireless logo by the serial number</h3>
        <p>This battery was made without diagnostics. It isn't faulty. There's simply nothing it can tell
          PackCheck.</p>
        <p><b>Unplug it before you quit PackCheck.</b> It can't be detected, so PackCheck can't warn you
          that it's still attached.</p></div>
      <div><h3>Has the logo</h3>
        <p>Unplug it and plug it back in firmly.</p>
        <p>If that doesn't help, check the three wires: adapter TX to J2, RX to J1, ground to B−.</p></div>
    </div>
    <div class="actions"><button class="btn solid" id="cr-close">Got it</button></div>
  </div></div>`;
}

function closingModal() {
  return `<div class="overlay"><div class="modal" role="alertdialog" aria-modal="true">
    <h2>Unplug the battery to quit</h2>
    <p>Closing now would let the data line go high, and a battery left on it permanently counts
      that as a charge on a non-Milwaukee charger.</p>
    <p>PackCheck closes by itself as soon as the battery is unplugged.</p>
    <p style="font-size:14px">A battery without the wireless logo can't be detected. If one is attached, unplug it too.</p>
    <div class="actions"><button class="btn solid" id="keep-open">Keep PackCheck open</button></div>
  </div></div>`;
}

// --- events -----------------------------------------------------------------------------

function wire(s) {
  const on = (id, fn) => { const el = $("#" + id); if (el) el.onclick = fn; };
  on("accept-adapter", () => api.accept_adapter(s.adapter.device));
  on("nothing-happening", () => {
    $("#modal-root").innerHTML = cantReadModal();
    $("#cr-close").onclick = () => { $("#modal-root").innerHTML = ""; };
    $("#cant-read").onclick = (e) => { if (e.target.id === "cant-read") $("#modal-root").innerHTML = ""; };
  });
  on("close-scan", async () => { await api.close_scan(); last = null; });
  on("toggle-all", () => { showAll = !showAll; shownKey = null; render(last); });
  document.querySelectorAll(".chipkey [data-chip]").forEach((b) => {
    b.onclick = () => {
      const c = b.dataset.chip;
      hiddenChips.has(c) ? hiddenChips.delete(c) : hiddenChips.add(c);
      shownKey = null; render(last);
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
    on("no-sticker", async () => { input.value = ""; await api.set_sticker("?"); });
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("#cant-read")) $("#modal-root").innerHTML = "";
});
