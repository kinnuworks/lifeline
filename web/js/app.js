// Lifeline website. Plain ES module, no build step.
// Data contract: see the report / README in the integrator notes. Every number on the page is read
// from data/<run>/*.json. Nothing here invents a value; missing values are hidden.

const CFG = window.LIFELINE_CONFIG || {};
const NAME = CFG.PRODUCT_NAME || "Lifeline";
const API = (CFG.API_BASE || "").replace(/\/$/, "");
const DATA = (CFG.DATA_ROOT || "data").replace(/\/$/, "");
const REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const has = (v) => v !== null && v !== undefined && v !== "" && !(typeof v === "number" && Number.isNaN(v));
const fmtInt = (n) => Math.round(n).toLocaleString("en-US");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtDate = (iso) => { if (!iso) return ""; const [y, m, d] = iso.slice(0, 10).split("-").map(Number); return `${d} ${MONTHS[m - 1]} ${y}`; };
const fmtMin = (m) => { if (!has(m)) return "–"; m = Math.round(m); if (m < 60) return `${m} min`; const h = Math.floor(m / 60), r = m % 60; return r ? `${h} h ${r} min` : `${h} h`; };
const fmtDur = (s) => { s = Math.round(s); if (s < 60) return `${s} s`; const m = Math.round(s / 60); return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`; };

const COLORS = { debris: "#C8792B", water: "#2B7FB8", cut: "#C0392B", risk: "#D99A2B", ok: "#2E7D4F", possible: "#E5A85A", blind: "#7E8B99", bldg: "#7A2E0E", ink: "#0F1E2E" };

const state = { index: null, runId: null, base: null, apiOnline: false, lang: "en", maps: {}, markers: {} };

async function getJSON(url) { const r = await fetch(url, { cache: "no-cache" }); if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); }
async function tryJSON(url) { try { return await getJSON(url); } catch { return null; } }
function timeout(ms) { const c = new AbortController(); setTimeout(() => c.abort(), ms); return c.signal; }

/* ------------------------------------------------------------------ data */
async function loadRun(base) {
  const files = ["summary.json", "overlays.json", "roads.geojson", "bridges.geojson", "buildings_hit.geojson", "settlements.geojson",
    "health.geojson", "chokepoints.json", "flowpath.geojson", "validation.json", "copilot_samples.json", "facts.json"];
  const res = await Promise.all(files.map((f) => tryJSON(base + f)));
  const [summary, overlays, roads, bridges, bldg, settle, health, choke, flow, validation, samples, facts] = res;
  state.optical = await tryJSON(base + "optical.json");
  state.alert = await tryJSON(base + "alert.json");
  state.cmpMode = null;
  Object.assign(state, { base, summary: summary || {}, overlays: overlays || [], roads, bridges, bldg, settle, health,
    choke: choke || [], flow, validation, samples: samples || [], facts: facts || [] });
  state.runId = state.summary.run_id || state.runId;
  state.factById = Object.fromEntries((state.facts || []).map((f) => [f.id, f]));
  state.ov = Object.fromEntries((state.overlays || []).map((o) => [o.id, { ...o, src: new URL(o.url, new URL(base, location.href)).href }]));
  renderAll();
}

function renderAll() {
  const s = state.summary;
  const ttl = s.title || s.area_name || "", when = fmtDate(s.event_date);
  $("#hero-title").textContent = [NAME, ttl, ttl.includes(when) ? "" : when].filter(Boolean).join(" · ");
  const isCase = (s.run_id || state.runId) === (state.index?.default || "trishuli-2026");
  const ht = $("#hit-title"); if (ht) ht.textContent = isCase ? "Damage · upper Bhote Koshi" : `Damage · ${ttl.split(",")[0] || "this run"}`;
  $("#score-area").textContent = s.area_name || "";
  for (const id of ["sitrep-link", "sitrep-en"]) $("#" + id).href = state.base + "sitrep_en.pdf";
  $("#sitrep-ne").href = state.base + "sitrep_ne.pdf";
  renderFrags(); renderScore(); renderChips(); renderCompare(); renderTTC(); renderTable(); renderChoke();
  renderValidation(); renderCopilotIntro(); renderTraceOut(null); renderFieldKit();
  for (const [kind, map] of Object.entries(state.maps)) if (map && map._llReady) applyRun(map, kind);
}

/* ------------------------------------------------------------------ hero fragments */
// a short, clean version of an answer for the hero card: no markdown marks, no fact ids, two sentences
function plainShort(t) {
  const clean = String(t || "").replace(/\*\*/g, "").replace(/\s*\[F[\d, F]+\]/g, "").trim();
  const parts = clean.split(/(?<=[।.!?])\s+/);
  return parts.slice(0, 2).join(" ");
}
function renderFrags() {
  const s = state.summary;
  const a = $(".float--a"), b = $(".float--b"), c = $(".float--c");
  if (has(s.villages_cut_off)) { $("[data-sum=villages_cut_off]").textContent = fmtInt(s.villages_cut_off); a.hidden = false; } else a.hidden = true;
  const ch = state.choke[0];
  if (ch) {
    $("#frag-choke-name").textContent = `Reopen ${ch.name}`;
    $("#frag-choke-text").textContent = `Reconnects ${ch.villages_reconnected} villages` + ((ch.people_reconnected > 0) ? `, about ${fmtInt(ch.people_reconnected)} people` : "");
    b.hidden = false;
  } else b.hidden = true;
  const ne = state.samples.find((x) => x.lang === "ne" && !x.refused);
  if (ne) { $("#frag-q").textContent = ne.q; $("#frag-a").textContent = plainShort(ne.answer); c.hidden = false; } else c.hidden = true;
}

/* ------------------------------------------------------------------ scoreboard */
function scoreItems(s) {
  const flooded = has(s.area_debris_km2) || has(s.area_water_km2) ? (s.area_debris_km2 || 0) + (s.area_water_km2 || 0) : null;
  return [
    { v: s.runtime_seconds, n: has(s.runtime_seconds) ? (s.runtime_seconds >= 60 ? Math.round(s.runtime_seconds / 60) : s.runtime_seconds) : null, unit: has(s.runtime_seconds) && s.runtime_seconds >= 60 ? "min" : "s", label: "From raw data to report", q: "where", how: "Wall-clock time of the whole run, from image search to the PDF, logged in the run's meta.json." },
    { v: s.radar_passes, label: "Radar passes compared", q: "where", how: `Sentinel-1 passes read for this run: for each orbit track over the area${s.radar_track_labels?.length ? ` (${s.radar_track_labels.join(", ")})` : ""}, the first pass after the flood, the last before it, and three earlier passes as the normal baseline.` },
    { v: s.buildings_checked, label: "Buildings checked", q: "what", how: `Every building in OpenStreetMap as it stood on ${s.osm_snapshot ? fmtDate(s.osm_snapshot) : "the snapshot date"}, before the flood.` },
    { v: s.bridges_assessed, label: "Bridges assessed", q: "what", how: "Every bridge in the pre-flood OpenStreetMap snapshot gets a hit probability." },
    { v: s.villages_routed, label: "Villages routed to care", q: "who", how: has(s.health_facilities) ? `Road routes from every village and town to the ${s.health_facilities} hospitals and clinics in OpenStreetMap.` : "Road routes from every village and town to the nearest hospital or clinic." },
    { v: s.villages_cut_off, label: "Villages cut off", q: "who", hot: true, how: "Villages whose chance of having no road route to any hospital or clinic is 0.5 or more." },
    { v: flooded, dec: 1, unit: "km²", label: "Ground flooded or buried", q: "where", how: "Open water plus debris and scour from radar change, with stripped valley ground from Sentinel-2 where the sky was clear." + (has(s.area_unobserved_km2) ? ` Another ${s.area_unobserved_km2} km² was hidden from radar by steep terrain and is not counted.` : "") },
    { v: has(s.check_buildings_hit_rate) ? s.check_buildings_hit_rate * 100 : null, unit: "%", label: "Official damaged buildings also found", q: "check", how: `Share of buildings marked damaged in the Copernicus EMS maps that ${NAME} also flags. Those maps are used only for this check.` },
    { v: s.check_bridges_flagged, of: s.check_bridges_total, label: "Official damaged bridges also found", q: "check", how: `Bridges the Copernicus EMS maps mark as damaged or destroyed, and how many of them ${NAME} flagged.` },
    { v: s.copilot_caught, of: s.copilot_attempts, label: "Invented numbers caught", q: "check", how: "Red-team prompts tried to make the copilot state numbers that are not in the facts ledger. This is how many the guard rejected." },
    { v: s.floods_tested, label: "Floods tested", q: "where", how: "Separate flood events the full pipeline has been run on, end to end." },
    { v: s.health_facilities, label: "Hospitals and clinics", q: "who", how: "Health facilities in the pre-flood OpenStreetMap snapshot used as routing targets." },
  ].filter((it) => has(it.v) && (it.of === undefined || has(it.of)));
}
function renderScore() {
  const dl = $("#score"); dl.innerHTML = "";
  scoreItems(state.summary).forEach((it, i) => {
    const target = it.n ?? it.v;
    const unit = it.unit || "";
    const cell = document.createElement("div");
    cell.className = "score__cell" + (it.hot ? " score__cell--hot" : "");
    if (it.q) cell.dataset.q = it.q;
    const tipId = `tip-${i}`;
    cell.innerHTML = `<dd class="score__num" data-target="${target}" data-dec="${it.dec || 0}" data-of="${it.of ?? ""}" data-unit="${unit}">${fmtNum(target, it.dec, it.of)}${unit ? `<small>${unit}</small>` : ""}</dd>
      <dt>${esc(it.label)}</dt>
      <button type="button" class="score__how" aria-label="How we know: ${esc(it.label)}" aria-describedby="${tipId}" aria-expanded="false">i</button>
      <span class="score__tip" role="tooltip" id="${tipId}">${esc(it.how)}</span>`;
    dl.appendChild(cell);
  });
  $$(".score__how", dl).forEach((b) => b.addEventListener("click", () => b.setAttribute("aria-expanded", b.getAttribute("aria-expanded") === "true" ? "false" : "true")));
  countUp();
}
function fmtNum(v, dec = 0, of) { const s = dec ? Number(v).toFixed(dec) : fmtInt(v); return of !== undefined && of !== "" && of !== null ? `${s}<small>/${fmtInt(of)}</small>` : s; }
function countUp() {
  const nums = $$(".score__num");
  const paint = (el, t) => {
    const target = +el.dataset.target, dec = +el.dataset.dec, of = el.dataset.of, unit = el.dataset.unit;
    const v = target * t;
    el.innerHTML = fmtNum(t >= 1 ? target : (dec ? v : Math.floor(v)), dec, of) + (unit ? `<small>${unit}</small>` : "");
  };
  if (REDUCE || !("IntersectionObserver" in window)) { nums.forEach((el) => paint(el, 1)); return; }
  nums.forEach((el) => paint(el, 0));
  const io = new IntersectionObserver((es) => {
    if (!es.some((e) => e.isIntersecting)) return;
    io.disconnect();
    const t0 = performance.now(), D = 1100;
    const step = (now) => { const p = Math.min(1, (now - t0) / D), e = 1 - Math.pow(1 - p, 3); nums.forEach((el) => paint(el, e)); if (p < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }, { threshold: 0.3 });
  io.observe($("#score"));
}

/* ------------------------------------------------------------------ chips */
function chipHTML(color, num, text) { return `<span class="chip">${color ? `<i style="background:${color}"></i>` : ""}<b>${num}</b> ${esc(text)}</span>`; }
function renderChips() {
  const s = state.summary;
  const w = [];
  if (has(s.area_debris_km2)) w.push(chipHTML(COLORS.debris, s.area_debris_km2, "km² debris"));
  if (has(s.area_water_km2)) w.push(chipHTML(COLORS.water, s.area_water_km2, "km² open water"));
  if (has(s.area_unobserved_km2)) w.push(chipHTML("repeating-linear-gradient(45deg,#7B7B86 0 2px,transparent 2px 4px)", s.area_unobserved_km2, "km² not observed"));
  if (has(s.radar_passes)) w.push(chipHTML(null, s.radar_passes, "radar passes"));
  $("#where-chips").innerHTML = w.join("");
  const h = [];
  if (has(s.buildings_hit)) h.push(chipHTML(COLORS.bldg, fmtInt(s.buildings_hit), "buildings likely hit"));
  if (has(s.roads_under_debris_km)) h.push(chipHTML(COLORS.cut, Math.round(s.roads_under_debris_km * 10) / 10, "km of road under debris"));
  else if (has(s.roads_destroyed_km)) h.push(chipHTML(COLORS.cut, s.roads_destroyed_km, "km of road destroyed"));
  if (has(s.bridges_destroyed)) h.push(chipHTML(null, s.bridges_destroyed, "bridges destroyed"));
  $("#hit-chips").innerHTML = h.join("");
  const c = [];
  if (has(s.villages_cut_off)) c.push(chipHTML(COLORS.cut, s.villages_cut_off, "villages cut off"));
  if (has(s.villages_at_risk)) c.push(chipHTML(COLORS.risk, s.villages_at_risk, "reachable only by detour"));
  if (has(s.villages_routed)) c.push(chipHTML(null, fmtInt(s.villages_routed), "villages routed"));
  $("#cut-chips").innerHTML = c.join("");
}

/* ------------------------------------------------------------------ radar compare */
function renderCompare() {
  const ov = state.ov, wrap = $("#compare");
  const stage = wrap.closest(".stage");
  const opt = state.optical, modeBox = $("#cmp-mode");
  modeBox.hidden = !opt;
  if (!opt) state.cmpMode = "radar";
  else state.cmpMode ||= "optical";
  $$("#cmp-mode button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === state.cmpMode)));
  const cls = $("#cmp-class"), tog = $("#cmp-toggle");
  const s = state.summary;
  if (state.cmpMode === "optical") {
    stage.hidden = false;
    $("#cmp-before").src = state.base + "optical_before.jpg"; $("#cmp-after").src = state.base + "optical_after.jpg";
    $("#cmp-title").textContent = state.runId === "trishuli-2026" ? "Sentinel-2 · Trishuli at Bidur" : `Sentinel-2 · ${(s.title || "").split(",")[0]}`;
    $("#cmp-tag-before").textContent = opt.before_dates?.length ? `Before · ${fmtDate(opt.before_dates.at(-1))}` : "Before";
    $("#cmp-tag-after").textContent = opt.after_dates?.length ? `After · ${fmtDate(opt.after_dates[0])}` : "After";
    tog.hidden = true; cls.hidden = true;
    return;
  }
  if (!ov.before || !ov.after) { stage.hidden = true; return; }
  stage.hidden = false;
  $("#cmp-title").textContent = "Sentinel-1 radar · VV";
  $("#cmp-before").src = ov.before.src; $("#cmp-after").src = ov.after.src;
  if (ov.flood_class) { cls.src = ov.flood_class.src; tog.hidden = false; } else tog.hidden = true;
  // before/after dates come from the first radar pair when known
  $("#cmp-tag-before").textContent = s.before_label || "Before";
  $("#cmp-tag-after").textContent = s.after_label || "After";
}
function initCompare() {
  $$("#cmp-mode button").forEach((b) => b.addEventListener("click", () => { state.cmpMode = b.dataset.mode; renderCompare(); }));
  const range = $("#cmp-range"), wrap = $("#compare");
  const set = (v) => {
    range.value = v; range.style.setProperty("--val", v + "%");
    $("#cmp-before-wrap").style.clipPath = `inset(0 ${100 - v}% 0 0)`; $("#cmp-line").style.left = v + "%";
  };
  range.addEventListener("input", () => set(+range.value));
  let drag = false;
  const fromEvt = (e) => { const r = wrap.getBoundingClientRect(); set(Math.max(0, Math.min(100, ((e.clientX - r.left) / r.width) * 100)).toFixed(1)); };
  wrap.addEventListener("pointerdown", (e) => { drag = true; wrap.setPointerCapture(e.pointerId); fromEvt(e); });
  wrap.addEventListener("pointermove", (e) => drag && fromEvt(e));
  wrap.addEventListener("pointerup", () => (drag = false));
  $("#cmp-toggle").addEventListener("click", (e) => {
    const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
    e.currentTarget.setAttribute("aria-pressed", on); $("#cmp-class").hidden = !on;
  });
  set(50);
}

/* ------------------------------------------------------------------ time to care chart */
function settlements() { return (state.settle?.features || []).map((f) => ({ ...f.properties, lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] })); }
function renderTTC() {
  const rows = settlements().filter((v) => v.cutoff && has(v.walk_minutes_after) && has(v.minutes_before))
    .sort((a, b) => b.walk_minutes_after - a.walk_minutes_after).slice(0, 7);
  const box = $("#ttc"), stage = box.closest(".stage");
  if (!rows.length) { stage.hidden = true; return; }
  stage.hidden = false;
  const max = Math.max(...rows.map((r) => r.walk_minutes_after));
  box.innerHTML = rows.map((r) => `<div class="ttc__row"><span class="ttc__name">${esc(r.name)}</span><div class="ttc__bars">
      <div class="ttc__bar" style="width:${(r.minutes_before / max) * 78}%"><span>${fmtMin(r.minutes_before)}</span></div>
      <div class="ttc__bar ttc__bar--walk" style="width:${(r.walk_minutes_after / max) * 78}%"><span>${fmtMin(r.walk_minutes_after)}</span></div></div></div>`).join("")
    + `<div class="ttc__legend"><span><i style="background:${COLORS.blind};opacity:.45"></i>Drive before the flood</span><span><i style="background:${COLORS.cut}"></i>Walk now, no road route</span></div>`;
}

/* ------------------------------------------------------------------ village table */
let showAll = false;
// the full reason stays in the data; the table shows only the repair that would reconnect the village
function shortFix(v) {
  const m = /reopening (.+?) \((road|bridge)\) would reconnect it/.exec(v.reason || "");
  if (m) return m[1].replace(/ bridge$/, "") + (m[2] === "bridge" ? " bridge" : " road");
  if ((v.p_cutoff ?? 0) < 0.5) return "Still connected";
  return "Several closed stretches";
}
function renderTable() {
  // places across the border (named only in Chinese) are routed to Nepali facilities only; keep them off the list
  const rows = settlements().filter((v) => !/[\u3400-\u9fff]/.test(v.name || "")).sort((a, b) => (b.p_cutoff ?? 0) - (a.p_cutoff ?? 0));
  const tb = $("#vt tbody"), shown = showAll ? rows : rows.slice(0, 10);
  tb.innerHTML = shown.map((v) => {
    const p = v.p_cutoff ?? 0, cls = p >= 0.5 ? "is-cut" : p >= 0.25 ? "is-risk" : "";
    return `<tr><td class="name">${esc(v.name)}${v.name_ne ? `<small lang="ne">${esc(v.name_ne)}</small>` : ""}</td>
      <td><div class="pbar" aria-label="${Math.round(p * 100)} percent"><div class="pbar__track"><div class="pbar__fill ${cls}" style="width:${p * 100}%"></div></div><b>${p.toFixed(2)}</b></div></td>
      <td class="num">${fmtMin(v.minutes_before)}</td>
      <td class="num">${has(v.minutes_after) ? fmtMin(v.minutes_after) : '<span class="gone">No route</span>'}</td>
      <td class="num">${fmtMin(v.walk_minutes_after)}</td>
      <td class="care">${esc(v.nearest_health || "–")}</td><td class="why">${esc(shortFix(v))}</td></tr>`;
  }).join("");
  const n = rows.length;
  $("#vt-count").textContent = n ? `Showing ${shown.length} of ${n} villages` : "";
  const more = $("#vt-more"); more.hidden = n <= 10; more.textContent = showAll ? "Show top 10" : "Show all villages";
}

/* ------------------------------------------------------------------ chokepoints */
function renderChoke() {
  const ol = $("#choke");
  ol.closest(".access__side").hidden = !state.choke.length;
  ol.innerHTML = state.choke.slice(0, 5).map((c, i) => `<li tabindex="0" data-i="${i}"><span class="choke__rank">${c.rank ?? i + 1}</span><div>
    <p class="choke__name">${esc(c.name)}</p>
    <p class="choke__text">${c.combined_repair && c.osm_ids?.length > 1 ? `Reopening the ${c.osm_ids.length} closed stretches on this route` : `Reopening this ${c.kind === "road" ? "road section" : "bridge"}`} reconnects <b>${c.villages_reconnected}</b> ${c.villages_reconnected === 1 ? "village" : "villages"}${(c.people_reconnected > 0) ? `, about <b>${fmtInt(c.people_reconnected)}</b> people` : ""}.</p>
    ${c.names?.length ? `<p class="choke__names">${[...new Set(c.names)].filter((n) => !/[\u3400-\u9fff]/.test(n)).map(esc).join(", ")}</p>` : ""}</div></li>`).join("");
  $$("li", ol).forEach((li) => {
    const go = () => { const c = state.choke[+li.dataset.i]; const m = state.maps.access; if (m) m.flyTo({ center: [c.lon, c.lat], zoom: 13, duration: REDUCE ? 0 : 1200 }); };
    li.addEventListener("click", go); li.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), go()));
  });
}

/* ------------------------------------------------------------------ validation + model */
function pct(x) { return has(x) ? `${Math.round(x * 100)}%` : "–"; }
function renderValidation() {
  const v = state.validation, sec = $("#checks");
  if (!v || !v.aois?.length) { sec.hidden = true; return; }
  sec.hidden = false;
  if (v.reference) $("#checks-lead").textContent = `${v.reference}. ${v.reference_note || "Used only to check results after the run. Never an input."}`;
  $("#val tbody").innerHTML = v.aois.map((a) => a.status === "cancelled"
    ? `<tr><td class="name">${esc(a.name)}<small>${esc(a.id || "")}</small></td><td colspan="4"><span class="status">Cancelled</span></td><td class="why">${esc(a.note || "")}</td></tr>`
    : `<tr><td class="name">${esc(a.name)}<small>${esc(a.id || "")}</small></td><td class="num">${has(a.flood_f1) ? a.flood_f1.toFixed(2) : "–"}</td>
       <td class="num">${pct(a.buildings_hit_rate)}</td><td class="num">${has(a.bridges_flagged) ? `${a.bridges_flagged} of ${a.bridges_total}` : "–"}</td>
       <td class="num">${pct(a.roads_agree_rate)}</td><td class="why">${esc(a.note || "")}</td></tr>`).join("");
  $("#val-notes").innerHTML = (v.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
}
async function renderModel() {
  const m = await tryJSON(`${DATA}/model_metrics.json`);
  if (!m || !m.test?.length) return;
  $("#model-card").hidden = false;
  $("#model-name").textContent = [m.model, m.trained_on?.length ? `Trained on ${m.trained_on.join(" and ")}.` : "", m.role || ""].filter(Boolean).join(". ").replace("..", ".");
  $("#model-rows").innerHTML = m.test.map((t) => `<div class="mrow"><p class="mrow__h">${esc(t.dataset)}${has(t.chips) ? `<span>${fmtInt(t.chips)} chips</span>` : ""}</p>
    <div class="mrow__m">${["iou", "f1", "precision", "recall"].filter((k) => has(t[k])).map((k) => `<div><b>${t[k].toFixed(2)}</b><small>${k === "iou" ? "IoU" : k === "f1" ? "F1" : k}</small></div>`).join("")}</div></div>`).join("");
  if (m.baseline && has(m.baseline.iou)) $("#model-base").textContent = `Baseline for comparison: ${m.baseline.name}, IoU ${m.baseline.iou.toFixed(2)}.`;
}

/* ------------------------------------------------------------------ copilot */
const CHAT_INTRO = {
  en: "Ask about damage, access or repairs in this run. Every number I give comes from the facts ledger, and I say so when the map does not know.",
  ne: "यो नक्साको क्षति, बाटो वा मर्मतबारे सोध्नुहोस्। मैले दिने हरेक अङ्क तथ्य तालिकाबाट आउँछ। नक्सालाई थाहा नभएको कुरा म भन्दिनँ।",
};
function renderCopilotIntro() {
  $("#chat-log").innerHTML = "";
  addMsg("a", CHAT_INTRO[state.lang], { lang: state.lang });
  renderSugg();
  const s = state.summary, rt = $("#redteam");
  if (has(s.copilot_caught) && has(s.copilot_attempts)) {
    rt.hidden = false; $("#redteam-num").textContent = `${s.copilot_caught}/${s.copilot_attempts}`;
    $("#redteam-text").textContent = `${s.copilot_caught} of ${s.copilot_attempts}`;
  } else rt.hidden = true;
  $("#chat-input").placeholder = state.lang === "ne" ? "प्रश्न सोध्नुहोस्" : "Ask about this flood";
}
function renderSugg() {
  const qs = state.samples.filter((x) => x.lang === state.lang);
  $("#chat-sugg").innerHTML = qs.map((x, i) => `<button type="button" class="chip" data-q="${esc(x.q)}" lang="${x.lang}">${esc(x.q)}</button>`).join("");
  $$("#chat-sugg .chip").forEach((b) => b.addEventListener("click", () => ask(b.dataset.q)));
}
function addMsg(kind, text, opt = {}) {
  const log = $("#chat-log"), el = document.createElement("div");
  el.className = `msg msg--${kind}` + (opt.refused ? " msg--refused" : "") + (opt.typing ? " msg--typing" : "");
  if (opt.lang) el.lang = opt.lang;
  if (opt.typing) el.innerHTML = '<span class="sr-only">Writing</span><i></i><i></i><i></i>';
  else {
    el.innerHTML = (opt.refused ? `<span class="msg__flag" lang="en">Refused · not in the facts ledger</span><br>` : "") + esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      + (opt.cites?.length ? `<div class="msg__cites" lang="en">${opt.cites.map((c) => { const id = typeof c === "string" ? c : c.id; const f = state.factById[id]; const tip = f ? `${state.lang === "ne" && f.label_ne ? f.label_ne : f.label_en}: ${f.value}${f.unit ? " " + f.unit : ""} (${f.source})` : "Fact in the ledger"; return `<span class="cite" title="${esc(tip)}">${esc(id)}</span>`; }).join("")}</div>` : "")
      + (opt.note ? `<p class="t-micro" style="margin-top:6px">${esc(opt.note)}</p>` : "");
  }
  log.appendChild(el); log.scrollTop = log.scrollHeight; return el;
}
const norm = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, "").trim();
async function ask(q) {
  q = q.trim(); if (!q) return;
  addMsg("q", q, { lang: /[ऀ-ॿ]/.test(q) ? "ne" : "en" });
  const typing = addMsg("a", "", { typing: true });
  let reply = null;
  if (state.apiOnline) {
    try {
      const r = await fetch(`${API}/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run_id: state.runId, question: q, lang: state.lang }), signal: timeout(30000) });
      if (r.ok) { const j = await r.json(); reply = { answer: j.answer, citations: j.citations || [], refused: !!j.refused }; }
    } catch { /* fall through to samples */ }
  }
  if (!reply) {
    const hit = state.samples.find((x) => norm(x.q) === norm(q));
    if (hit) reply = { answer: hit.answer, citations: hit.citations, refused: hit.refused };
  }
  await new Promise((r) => setTimeout(r, REDUCE ? 0 : 650));
  typing.remove();
  if (reply) addMsg("a", reply.answer, { refused: reply.refused, cites: reply.citations, lang: /[ऀ-ॿ]/.test(reply.answer) ? "ne" : "en" });
  else addMsg("a", state.lang === "ne" ? "प्रत्यक्ष कोपाइलट अहिले बन्द छ। तलका नमुना प्रश्नहरूको मात्र उत्तर उपलब्ध छ।" : "The live copilot is offline right now, so only the sample questions below have answers.", { lang: state.lang });
}
function initCopilot() {
  $$("#copilot .segmented button").forEach((b) => b.addEventListener("click", () => {
    $$("#copilot .segmented button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    state.lang = b.dataset.lang; renderCopilotIntro();
  }));
  $("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); const i = $("#chat-input"); ask(i.value); i.value = ""; });
  // Play one sample when the chat first scrolls into view.
  const io = new IntersectionObserver((es) => {
    if (!es[0].isIntersecting) return; io.disconnect();
    const first = state.samples.find((x) => x.lang === state.lang && !x.refused);
    if (first) setTimeout(() => ask(first.q), REDUCE ? 0 : 400);
  }, { threshold: 0.5 });
  io.observe($("#chat-log"));
}

/* ------------------------------------------------------------------ maps */
const BASE_STYLE = "https://tiles.openfreemap.org/styles/positron";
const DEM = { type: "raster-dem", tiles: ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"], encoding: "terrarium", tileSize: 256, maxzoom: 14 };

function makeMap(container, opts) {
  if (!window.maplibregl) { container.innerHTML = '<div class="map-fallback">The map library did not load. Check your connection and reload.</div>'; return null; }
  const map = new maplibregl.Map({
    container, style: BASE_STYLE, center: opts.center, zoom: opts.zoom, pitch: opts.pitch || 0, bearing: opts.bearing || 0,
    maxPitch: 75, attributionControl: { compact: true, customAttribution: "Terrain: AWS Terrain Tiles" }, cooperativeGestures: true, fadeDuration: REDUCE ? 0 : 300,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: !!opts.terrain, visualizePitch: true }), opts.terrain ? "bottom-right" : "top-right");
  if ("ResizeObserver" in window) new ResizeObserver(() => map.resize()).observe(container);
  map.on("error", (e) => { if (e?.error?.status !== 404) console.debug("map:", e?.error?.message || e); });
  map.once("load", () => container.querySelectorAll(".maplibregl-compact-show").forEach((el) => el.classList.remove("maplibregl-compact-show")));
  map.once("style.load", () => {
    const layers = map.getStyle().layers;
    map._firstSymbol = layers.find((l) => l.type === "symbol")?.id;
    for (const l of layers) if (l.type === "symbol" && (!opts.labels || /poi|housenum/.test(l.id))) map.setLayoutProperty(l.id, "visibility", "none");
    // warm the grey base map: paper land, sage forest, glacier-blue water
    for (const l of layers) {
      try {
        if (l.type === "background") map.setPaintProperty(l.id, "background-color", "#ECE6D9");
        else if (l.type === "fill" && /water/.test(l.id)) map.setPaintProperty(l.id, "fill-color", "#B9D3E4");
        else if (l.type === "fill" && /wood|forest|grass|park|landcover|scrub/.test(l.id)) map.setPaintProperty(l.id, "fill-color", "#D5DDC2");
        else if (l.type === "line" && /waterway|river/.test(l.id)) map.setPaintProperty(l.id, "line-color", "#8DB7D3");
      } catch { /* layer without that property */ }
    }
    map.addSource("hs", { ...DEM });
    map.addLayer({ id: "hill", type: "hillshade", source: "hs", paint: { "hillshade-exaggeration": 0.45, "hillshade-shadow-color": "#3E4C5A", "hillshade-highlight-color": "#FFFBF1", "hillshade-accent-color": "#6E6A55" } }, map._firstSymbol);
    if (opts.terrain) map.addSource("dem", { ...DEM });
  });
  return map;
}

function pointsFromLines(fc, filter) {
  return { type: "FeatureCollection", features: (fc?.features || []).filter((f) => !filter || filter(f.properties)).map((f) => {
    const c = f.geometry.type === "LineString" ? f.geometry.coordinates : f.geometry.type === "MultiLineString" ? f.geometry.coordinates.flat() : [f.geometry.coordinates];
    const m = c[Math.floor(c.length / 2)];
    return { type: "Feature", geometry: { type: "Point", coordinates: m }, properties: f.properties };
  }) };
}
const EMPTY = { type: "FeatureCollection", features: [] };
function setSrc(map, id, data) { const s = map.getSource(id); if (s) s.setData(data || EMPTY); else map.addSource(id, { type: "geojson", data: data || EMPTY }); }
function setImg(map, id, ov) {
  if (!ov) return false;
  const s = map.getSource(id);
  if (s) s.updateImage({ url: ov.src, coordinates: ov.bounds }); else map.addSource(id, { type: "image", url: ov.src, coordinates: ov.bounds });
  return true;
}
function addLayer(map, def, before) { if (!map.getLayer(def.id)) map.addLayer(def, before ?? (def.type === "symbol" ? undefined : map._firstSymbol)); }
const ROAD_COLOR = ["match", ["get", "status"], "destroyed_likely", COLORS.cut, "damaged_possible", COLORS.possible, "unobserved", COLORS.blind, "#8A8F99"];
const P_COLOR = ["step", ["coalesce", ["get", "p_cutoff"], 0], COLORS.ok, 0.25, COLORS.risk, 0.5, COLORS.cut];

function clearMarkers(kind) { (state.markers[kind] || []).forEach((m) => m.remove()); state.markers[kind] = []; }
function addMarker(kind, map, el, lngLat, popupHTML) {
  const m = new maplibregl.Marker({ element: el }).setLngLat(lngLat).addTo(map);
  if (popupHTML) { m.setPopup(new maplibregl.Popup({ offset: 14, closeButton: false }).setHTML(popupHTML)); el.setAttribute("role", "img"); }
  (state.markers[kind] ||= []).push(m); return m;
}
function villagePopup(v) {
  return `<b>${esc(v.name)}</b>${v.name_ne ? ` <span lang="ne">${esc(v.name_ne)}</span>` : ""}<br>Chance cut off: <b>${(v.p_cutoff ?? 0).toFixed(2)}</b><br>
    Drive before: ${fmtMin(v.minutes_before)} · now: ${has(v.minutes_after) ? fmtMin(v.minutes_after) : "no route"}<br>Walk now: ${fmtMin(v.walk_minutes_after)}`;
}

function fitToRun(map, kind) {
  if (!(kind === "hit" || kind === "access") || map._fitFor === state.runId) return;
  const first = map._fitFor === undefined; map._fitFor = state.runId;
  map._home ||= { center: map.getCenter(), zoom: map.getZoom() };
  const b = state.summary.bounds, isCase = state.runId === (state.index?.default || "trishuli-2026");
  if (isCase) { if (!first) map.jumpTo(map._home); }
  else if (b) map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 30, duration: 0 });
}
function applyRun(map, kind) {
  fitToRun(map, kind);
  const ov = state.ov;
  // raster overlays
  const ovIds = kind === "hero" ? ["flood_class", "before", "after"] : kind === "hit" ? ["flood_class"] : kind === "access" ? ["flood_class"] : [];
  for (const id of ovIds) if (setImg(map, `ov-${id}`, ov[id])) addLayer(map, { id: `ov-${id}`, type: "raster", source: `ov-${id}`, layout: { visibility: id === "flood_class" ? "visible" : "none" }, paint: { "raster-opacity": id === "flood_class" ? (kind === "hero" ? 0.92 : 0.75) : 0.9, "raster-fade-duration": 0 } });

  if (kind === "run") return;
  setSrc(map, "roads", state.roads);
  addLayer(map, { id: "roads-casing", type: "line", source: "roads", filter: ["!=", ["get", "status"], "clear"], paint: { "line-color": "#fff", "line-width": kind === "hero" ? 6 : 7, "line-opacity": 0.85 }, layout: { "line-cap": "round", "line-join": "round" } });
  addLayer(map, { id: "roads-clear", type: "line", source: "roads", filter: ["==", ["get", "status"], "clear"], paint: { "line-color": "#5D6270", "line-width": 2, "line-opacity": 0.7 }, layout: { "line-cap": "round" } });
  addLayer(map, { id: "roads-hit", type: "line", source: "roads", filter: ["in", ["get", "status"], ["literal", ["destroyed_likely", "damaged_possible"]]], paint: { "line-color": ROAD_COLOR, "line-width": kind === "hero" ? 4 : 4.5 }, layout: { "line-cap": "round", "line-join": "round" } });
  addLayer(map, { id: "roads-unobs", type: "line", source: "roads", filter: ["==", ["get", "status"], "unobserved"], paint: { "line-color": COLORS.blind, "line-width": 3.5, "line-dasharray": [1.2, 1] } });

  setSrc(map, "bridges", pointsFromLines(state.bridges));
  addLayer(map, { id: "bridges", type: "circle", source: "bridges", paint: { "circle-radius": kind === "hit" ? 6 : 4.5, "circle-color": ROAD_COLOR, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });

  if (kind === "hit") {
    setSrc(map, "bldg", state.bldg);
    addLayer(map, { id: "bldg", type: "circle", source: "bldg", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 1.8, 14, 4], "circle-color": COLORS.bldg, "circle-opacity": ["interpolate", ["linear"], ["coalesce", ["get", "hit_prob"], 0.5], 0.5, 0.45, 1, 0.95] } }, "bridges");
  }

  const vs = settlements();
  setSrc(map, "settle", state.settle);
  if (kind !== "hero") {
    addLayer(map, { id: "settle", type: "circle", source: "settle", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 4, 13, 8], "circle-color": P_COLOR, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
  }
  addLayer(map, { id: "settle-lbl", type: "symbol", source: "settle", filter: kind === "hero" ? [">=", ["get", "p_cutoff"], 0.5] : ["literal", true],
    layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": kind === "hero" ? 12 : 11.5, "text-offset": [0, 1.15], "text-anchor": "top", "text-optional": true },
    paint: { "text-color": "#1D1D1F", "text-halo-color": "rgba(255,255,255,.95)", "text-halo-width": 1.6 } });

  if (kind === "access" && state.flow) {
    setSrc(map, "flow", { type: "FeatureCollection", features: [] });
    addLayer(map, { id: "flow-casing", type: "line", source: "flow", paint: { "line-color": "#fff", "line-width": 8 }, layout: { "line-cap": "round", "line-join": "round" } }, "settle");
    addLayer(map, { id: "flow", type: "line", source: "flow", paint: { "line-color": "#0B7FAB", "line-width": 4.5 }, layout: { "line-cap": "round", "line-join": "round" } }, "settle");
  }

  clearMarkers(kind);
  // hospitals and clinics
  for (const f of state.health?.features || []) {
    const el = document.createElement("div"); el.className = "care-mark"; el.textContent = "+"; el.setAttribute("aria-label", f.properties.name);
    addMarker(kind, map, el, f.geometry.coordinates, `<b>${esc(f.properties.name)}</b><br>${esc((f.properties.kind || "").replace("_", " "))}`);
  }
  if (kind === "hero") {
    for (const v of vs.filter((v) => v.cutoff || (v.p_cutoff ?? 0) >= 0.5)) {
      const el = document.createElement("div"); el.className = "vmark vmark--cut"; el.setAttribute("aria-label", `${v.name}, cut off`);
      addMarker(kind, map, el, [v.lon, v.lat], villagePopup(v));
    }
  }
  if ((kind === "hero" || kind === "access") && state.choke[0]) {
    const c = state.choke[0], el = document.createElement("div"); el.className = "choke-mark";
    el.innerHTML = `<span>Reopen first</span>`; el.setAttribute("aria-label", `Priority repair: ${c.name}`);
    addMarker(kind, map, el, [c.lon, c.lat], `<b>${esc(c.name)}</b><br>Reconnects ${c.villages_reconnected} villages${(c.people_reconnected > 0) ? `, about ${fmtInt(c.people_reconnected)} people` : ""}`);
  }
  if (!map._llClick && kind !== "hero") {
    map._llClick = true;
    map.on("click", "settle", (e) => { if (map._tracing) return; const p = e.features[0].properties; new maplibregl.Popup({ offset: 10, closeButton: false }).setLngLat(e.lngLat).setHTML(villagePopup({ ...p, minutes_after: p.minutes_after === "null" ? null : p.minutes_after })).addTo(map); });
    map.on("mouseenter", "settle", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "settle", () => (map.getCanvas().style.cursor = ""));
  }
}

function initHeroMap() {
  const s = state.summary, small = innerWidth < 640;
  const map = makeMap($("#hero-map"), { terrain: true, center: small ? [85.32, 28.15] : [85.315, 28.165], zoom: small ? 10.5 : 11.25, pitch: small ? 50 : 58, bearing: 16 });
  if (!map) return;
  state.maps.hero = map;
  map.on("load", () => {
    map.setTerrain({ source: "dem", exaggeration: 1.35 });
    map._llReady = true; applyRun(map, "hero");
  });
  $$(".segmented--map button").forEach((b) => b.addEventListener("click", () => {
    $$(".segmented--map button").forEach((x) => x.setAttribute("aria-pressed", x === b));
    for (const id of ["flood_class", "before", "after"]) if (map.getLayer(`ov-${id}`)) map.setLayoutProperty(`ov-${id}`, "visibility", id === b.dataset.layer ? "visible" : "none");
  }));
  $("#hero-3d").addEventListener("click", (e) => {
    const on = e.currentTarget.getAttribute("aria-pressed") !== "true"; e.currentTarget.setAttribute("aria-pressed", on);
    if (on) { map.setTerrain({ source: "dem", exaggeration: 1.35 }); map.easeTo({ pitch: 60, bearing: 18, duration: REDUCE ? 0 : 900 }); }
    else { map.setTerrain(null); map.easeTo({ pitch: 0, bearing: 0, duration: REDUCE ? 0 : 900 }); }
  });
}

function lazy(el, init) {
  if (!("IntersectionObserver" in window)) return init();
  const io = new IntersectionObserver((es) => { if (es[0].isIntersecting) { io.disconnect(); init(); } }, { rootMargin: "300px 0px" });
  io.observe(el);
}

function initHitMap() {
  const map = makeMap($("#hit-map"), { center: [85.355, 28.205], zoom: innerWidth < 640 ? 11.2 : 11.6 });
  if (!map) return; state.maps.hit = map;
  map.on("load", () => { map._llReady = true; applyRun(map, "hit"); });
}
function initAccessMap() {
  const b = state.summary.bounds;
  const map = makeMap($("#access-map"), { center: [85.29, 28.09], zoom: 10 });
  if (!map) return; state.maps.access = map;
  map.on("load", () => {
    map._llReady = true; applyRun(map, "access");
    const vs = settlements();
    if (vs.length) {
      const xs = vs.map((v) => v.lon), ys = vs.map((v) => v.lat);
      map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: { top: 60, bottom: 60, left: 40, right: 40 }, duration: 0 });
    } else if (b) map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 30, duration: 0 });
  });
  const btn = $("#trace-btn");
  if (!state.flow && !API) btn.hidden = true;
  btn.addEventListener("click", () => {
    const on = btn.getAttribute("aria-pressed") !== "true"; btn.setAttribute("aria-pressed", on); map._tracing = on;
    map.getCanvas().style.cursor = on ? "crosshair" : "";
    if (on) renderTraceOut({ hint: true });
  });
  map.on("click", async (e) => {
    if (!map._tracing) return;
    let fc = null, live = false;
    if (state.apiOnline) {
      try { const r = await fetch(`${API}/flowpath`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run_id: state.runId, lon: e.lngLat.lng, lat: e.lngLat.lat }), signal: timeout(20000) }); if (r.ok) { fc = await r.json(); live = true; } } catch { }
    }
    if (!fc) fc = state.flow;
    if (!fc) return;
    map.getSource("flow")?.setData({ type: "FeatureCollection", features: fc.features.filter((f) => f.geometry.type !== "Point") });
    renderTraceOut({ fc, live });
    btn.setAttribute("aria-pressed", "false"); map._tracing = false; map.getCanvas().style.cursor = "";
  });
}
function renderTraceOut(o) {
  const box = $("#trace-out");
  if (!o) { box.hidden = true; return; }
  box.hidden = false;
  if (o.hint) { box.innerHTML = `Click any point upstream on the map. ${NAME} follows the water downhill and lists the villages along the way.`; return; }
  const path = o.fc.features.find((f) => f.geometry.type !== "Point"), vs = o.fc.features.filter((f) => f.properties.kind === "village" || f.geometry.type === "Point");
  const len = path?.properties?.length_km;
  box.innerHTML = `<p>${o.live ? "Flood path traced from the point you clicked" : "The live engine is offline, so this is the precomputed path from the top of the valley"}${has(len) ? `, ${len} km long` : ""}. Villages along it:</p>
    <ol>${vs.map((f) => `<li>${esc(f.properties.name)}${has(f.properties.km_downstream) ? ` <small>${f.properties.km_downstream} km</small>` : ""}</li>`).join("")}</ol>`;
}

/* ------------------------------------------------------------------ run any valley */
const PRESETS = {
  quick: { name: "Bidur, 10 km (fast demo)", bbox: [85.12, 27.88, 85.22, 27.98], date: "2026-08-26" },
  trishuli: { name: "Trishuli 2026", bbox: [84.93, 27.78, 85.45, 28.30], date: "2026-08-26", run: "trishuli-2026" },
  melamchi: { name: "Melamchi 2021", bbox: [85.48, 27.78, 85.68, 28.05], date: "2021-06-15", run: "melamchi-2021" },
  teesta: { name: "Teesta 2023", bbox: [88.40, 27.15, 88.70, 27.65], date: "2023-10-04", run: "teesta-2023" },
  beas: { name: "Beas, Mandi 2023", bbox: [76.85, 31.62, 77.17, 31.92], date: "2023-07-09", run: "beas-mandi-2023" },
  chamoli: { name: "Chamoli 2021", bbox: [79.55, 30.38, 79.85, 30.62], date: "2021-02-07", run: "chamoli-2021" },
};
const STAGES = [["find_images", "Finding images"], ["radar_change", "Radar change"], ["osm", "OpenStreetMap"], ["damage", "Damage"], ["cutoff", "Cut off"], ["report", "Report"]];
const run = { bbox: PRESETS.trishuli.bbox.slice(), preset: "trishuli", drawing: false, polling: null };
const bboxFC = (b) => ({ type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] } }] });
function showBBox(fit) {
  const b = run.bbox.map((x) => +x.toFixed(3));
  $("#bbox-out").textContent = `${b[0]}, ${b[1]}, ${b[2]}, ${b[3]}`;
  const map = state.maps.run;
  if (map?._llReady) { map.getSource("bbox").setData(bboxFC(run.bbox)); if (fit) map.fitBounds([[run.bbox[0], run.bbox[1]], [run.bbox[2], run.bbox[3]]], { padding: 50, duration: REDUCE ? 0 : 900 }); }
}
function renderStages(stages) {
  const by = Object.fromEntries((stages || []).map((s) => [s.key, s]));
  $("#stages").innerHTML = STAGES.map(([k, label]) => { const s = by[k] || {}; const st = s.status || "pending";
    return `<li class="is-${st}"><span class="st" aria-hidden="true"></span>${esc(s.label || label)}<span class="sr-only">, ${st}</span>${has(s.seconds) ? `<time>${fmtDur(s.seconds)}</time>` : ""}</li>`; }).join("");
}
function runMsg(html) { const m = $("#run-msg"); m.hidden = !html; m.innerHTML = html || ""; }
function offlineMsg() {
  const runs = state.index?.runs || [];
  runMsg(`<span>The live engine is offline. You can open a precomputed run instead.</span><span class="chips">${runs.map((r) => `<button type="button" class="btn btn-ink btn-sm" data-open="${esc(r.run_id)}">Open ${esc(r.title)}</button>`).join("")}</span>`);
  $$("#run-msg [data-open]").forEach((b) => b.addEventListener("click", () => openRun(b.dataset.open, true)));
}
async function checkEngine() {
  const el = $("#engine"), t = $("#engine-text");
  if (!API) { state.apiOnline = false; el.className = "engine is-off"; t.textContent = "Live engine not configured. Precomputed runs only."; return; }
  try { const r = await fetch(`${API}/health`, { signal: timeout(2500) }); state.apiOnline = r.ok; } catch { state.apiOnline = false; }
  el.className = "engine " + (state.apiOnline ? "is-live" : "is-off");
  t.textContent = state.apiOnline ? "Live engine online. A run takes a while; you can keep reading while it works." : "Live engine offline. Precomputed runs still work.";
  const mode = $("#chat-mode"); mode.textContent = state.apiOnline ? "Live" : "Samples"; mode.classList.toggle("is-live", state.apiOnline);
}
function initRunPanel() {
  renderStages([]);
  $$("#presets .chip").forEach((b) => b.addEventListener("click", () => {
    $$("#presets .chip").forEach((x) => x.setAttribute("aria-pressed", x === b));
    const p = PRESETS[b.dataset.preset]; run.preset = b.dataset.preset; run.bbox = p.bbox.slice(); $("#run-date").value = p.date;
    $("#run-title").textContent = p.name; showBBox(true); runMsg("");
  }));
  $("#draw-btn").addEventListener("click", (e) => {
    const map = state.maps.run; if (!map) return;
    run.drawing = e.currentTarget.getAttribute("aria-pressed") !== "true"; e.currentTarget.setAttribute("aria-pressed", run.drawing);
    $("#draw-hint").hidden = !run.drawing; map.getContainer().classList.toggle("is-drawing", run.drawing);
    run.drawing ? map.dragPan.disable() : map.dragPan.enable();
  });
  $("#run-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (run.polling) return;
    await checkEngine();
    if (!state.apiOnline) { renderStages([]); offlineMsg(); return; }
    const go = $("#run-go"); go.disabled = true; go.textContent = "Running";
    runMsg(""); renderStages([{ key: "find_images", status: "running" }]);
    try {
      const r = await fetch(`${API}/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bbox: run.bbox, date: $("#run-date").value, name: PRESETS[run.preset]?.name || "Custom area" }) });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        const detail = Array.isArray(d.detail) ? d.detail.map((x) => String(x.msg || "").replace(/^Value error, /, "")).join(" ") : d.detail;
        throw new Error(detail || (r.status === 429 ? "Two runs are already going. Try again in a few minutes." : `The engine refused the run (${r.status}).`));
      }
      const { id } = await r.json(); const t0 = Date.now(); let misses = 0;
      const poll = async () => {
        try {
          const j = await getJSON(`${API}/runs/${encodeURIComponent(id)}`); misses = 0;
          renderStages(j.stages);
          const warn = (j.warnings || []).filter((w) => !/web export/i.test(w)).map((w) => `<span class="t-micro">${esc(w)}</span>`).join("");
          if (j.status === "done") {
            stopPoll();
            if (j.data_url) {
              const rel = j.data_url.endsWith("/") ? j.data_url : j.data_url + "/";
              const base = /^https?:/.test(rel) ? rel : API + rel;
              addRunOption({ run_id: id, title: j.label || j.title || "New run", path: base, absolute: true, live: true });
              await openRun(id, false);
            }
            runMsg(`<span>Finished in ${fmtDur(j.runtime_seconds ?? (Date.now() - t0) / 1000)}. The maps and the copilot now show this run.</span>${warn}<a class="btn btn-ink btn-sm" href="#top">See the map</a>`);
          } else if (j.status === "failed") { stopPoll(); runMsg(`<span>${esc(j.error || "The run stopped.")}</span>${warn}`); }
          else if (j.status === "finishing") { runMsg(`<span>Maps done. Preparing them for the website.</span>`); }
        } catch {
          if (++misses >= 15) { stopPoll(); runMsg(`<span>Lost contact with the engine. The run may still finish; reload the page in a few minutes.</span>`); }
        }
      };
      run.polling = setInterval(poll, 2000); poll();
    } catch (err) { stopPoll(); runMsg(`<span>${esc(err.message)}</span>`); }
  });
}
function stopPoll() { clearInterval(run.polling); run.polling = null; const go = $("#run-go"); go.disabled = false; go.textContent = "Run"; }
function initRunMap() {
  const map = makeMap($("#run-map"), { labels: true, center: [85.27, 28.07], zoom: 8.6 });
  if (!map) return; state.maps.run = map;
  map.on("load", () => {
    map.addSource("bbox", { type: "geojson", data: bboxFC(run.bbox) });
    map.addLayer({ id: "bbox-fill", type: "fill", source: "bbox", paint: { "fill-color": COLORS.ink, "fill-opacity": 0.08 } }, map._firstSymbol);
    map.addLayer({ id: "bbox-line", type: "line", source: "bbox", paint: { "line-color": COLORS.ink, "line-width": 2, "line-dasharray": [2, 1.5] } });
    map._llReady = true; showBBox(true);
  });
  let start = null;
  const move = (e) => { if (!start) return; const a = start, b = e.lngLat; run.bbox = [Math.min(a.lng, b.lng), Math.min(a.lat, b.lat), Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)]; showBBox(false); };
  map.on("mousedown", (e) => { if (!run.drawing) return; start = e.lngLat; });
  map.on("touchstart", (e) => { if (!run.drawing || e.points.length !== 1) return; e.preventDefault(); start = e.lngLat; });
  map.on("mousemove", move); map.on("touchmove", (e) => { if (start) { e.preventDefault(); move(e); } });
  const end = () => {
    if (!start) return; start = null; run.drawing = false; run.preset = null;
    $("#draw-btn").setAttribute("aria-pressed", "false"); $("#draw-hint").hidden = true; map.getContainer().classList.remove("is-drawing"); map.dragPan.enable();
    $$("#presets .chip").forEach((x) => x.setAttribute("aria-pressed", "false")); $("#run-title").textContent = "Custom area";
  };
  map.on("mouseup", end); map.on("touchend", end);
}

/* ------------------------------------------------------------------ runs index */
function addRunOption(r) {
  state.index ||= { runs: [] };
  if (!state.index.runs.find((x) => x.run_id === r.run_id)) state.index.runs.push(r);
  const sel = $("#run-select");
  if (![...sel.options].some((o) => o.value === r.run_id)) sel.add(new Option(r.title, r.run_id));
  sel.closest(".run-select").hidden = sel.options.length < 2;
}
async function openRun(id, scroll) {
  const r = state.index.runs.find((x) => x.run_id === id); if (!r) return;
  $("#run-select").value = id;
  const base = r.absolute ? r.path : `${DATA}/${r.path || id + "/"}`;
  await loadRun(base);
  const b = state.summary.bounds, hero = state.maps.hero;
  if (hero && b) hero.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 40, pitch: 55, bearing: 18, duration: REDUCE ? 0 : 1200 });
  if (scroll) $("#top").scrollIntoView({ behavior: REDUCE ? "auto" : "smooth" });
}

/* ------------------------------------------------------------------ boot */
function applyName() {
  $$("[data-name]").forEach((el) => (el.textContent = NAME));
  document.title = `${NAME} · Radar-to-rescue intelligence for flood disasters`;
  $$('meta[property="og:title"]').forEach((m) => (m.content = document.title));
  const d = $('meta[name="description"]'); if (d) d.content = d.content.replace(/^\S+/, NAME);
  $$("[data-name-label]").forEach((el) => el.setAttribute("aria-label", NAME + el.dataset.nameLabel));
}
async function boot() {
  applyName();
  if (CFG.GITHUB_URL) $("#gh-link").href = CFG.GITHUB_URL; else $("#gh-link").closest("li").hidden = true;
  initCompare(); initCopilot(); initRunPanel();
  state.index = (await tryJSON(`${DATA}/index.json`)) || { runs: [{ run_id: "trishuli-2026", title: "Trishuli 2026", path: "trishuli-2026/" }], default: "trishuli-2026" };
  const sel = $("#run-select"); sel.innerHTML = "";
  state.index.runs.forEach((r) => sel.add(new Option(r.title, r.run_id)));
  sel.closest(".run-select").hidden = state.index.runs.length < 2;
  sel.addEventListener("change", () => openRun(sel.value, false));
  const first = state.index.runs.find((r) => r.run_id === state.index.default) || state.index.runs[0];
  sel.value = first.run_id;
  await loadRun(`${DATA}/${first.path || first.run_id + "/"}`);
  initHeroMap();
  lazy($("#hit-map"), initHitMap);
  lazy($("#access-map"), initAccessMap);
  lazy($("#run-map"), initRunMap);
  renderModel();
  checkEngine();
  $("#vt-more").addEventListener("click", () => { showAll = !showAll; renderTable(); });

  // reveal-on-scroll (design system)
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { rootMargin: "0px 0px -8% 0px" });
    $$(".reveal").forEach((el) => io.observe(el));
  } else $$(".reveal").forEach((el) => el.classList.add("in"));
}
window.__lifeline = state; // handy for debugging in the console
boot();

/* menu bar: mark the section in view */
(() => {
  const links = [...document.querySelectorAll(".nav__links a[href^='#']")];
  const targets = links.map((a) => document.querySelector(a.getAttribute("href"))).filter(Boolean);
  if (!("IntersectionObserver" in window) || !targets.length) return;
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) links.forEach((a) => a.classList.toggle("is-active", a.getAttribute("href") === "#" + e.target.id));
  }, { rootMargin: "-45% 0px -50% 0px" });
  targets.forEach((t) => io.observe(t));
})();

/* ------------------------------------------------------------------ field kit */
let alertLang = "en";
function renderFieldKit() {
  const box = $("#fieldkit"), a = state.alert;
  box.hidden = !a;
  if (!a) return;
  const t = a[alertLang] || a.en || "";
  const el = $("#alert-text"); el.textContent = t; el.lang = alertLang;
  $("#alert-len").textContent = `${t.length} characters`;
  $$("[data-alang]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.alang === alertLang)));
  const b = state.base;
  const set = (id, f) => { const e = $("#" + id); if (e) e.href = b + f; };
  set("dl-cut-kml", "cutoff_villages.kml"); set("dl-cut-geo", "cutoff_villages.geojson");
  set("dl-road-kml", "damaged_roads.kml"); set("dl-road-geo", "damaged_roads.geojson");
  set("dl-rep-kml", "repairs.kml"); set("dl-sitrep-en", "sitrep_en.pdf"); set("dl-sitrep-ne", "sitrep_ne.pdf");
}
$$("[data-alang]").forEach((b) => b.addEventListener("click", () => { alertLang = b.dataset.alang; renderFieldKit(); }));
$("#alert-copy")?.addEventListener("click", async () => {
  const btn = $("#alert-copy");
  try { await navigator.clipboard.writeText($("#alert-text").textContent); btn.textContent = "Copied"; }
  catch { btn.textContent = "Select the text to copy"; }
  setTimeout(() => (btn.textContent = "Copy for SMS or WhatsApp"), 1800);
});

/* ------------------------------------------------------------------ guided tour */
let touring = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function runTour() {
  const map = state.maps.hero; if (!map || !map._llReady) return;
  const id = ++touring, s = state.summary, cap = $("#tour-cap");
  const path = (state.flow?.features || []).find((f) => f.properties?.kind === "path")?.geometry?.coordinates;
  const top = (state.choke || [])[0];
  const villages = (state.flow?.features || []).filter((f) => f.properties?.kind === "village").length;
  const say = async (n, text, ms) => { if (id !== touring) throw 0; $("#tour-step").textContent = `${n} / 4`; $("#tour-text").textContent = text; cap.hidden = false; await sleep(REDUCE ? 600 : ms); if (id !== touring) throw 0; };
  const fly = (o) => map.flyTo({ duration: REDUCE ? 0 : o.d || 3800, essential: true, ...o });
  try {
    if (path?.length > 2) {
      if (!map.getSource("tour-path")) {
        map.addSource("tour-path", { type: "geojson", data: { type: "Feature", geometry: { type: "LineString", coordinates: path } } });
        map.addLayer({ id: "tour-path", type: "line", source: "tour-path", paint: { "line-color": COLORS.water, "line-width": 4, "line-opacity": 0.9, "line-dasharray": [1.5, 1] } });
      }
      const a = path[0], m = path[Math.floor(path.length * 0.25)];
      fly({ center: a, zoom: 12.3, pitch: 62, bearing: 200 });
      await say(1, `${fmtDate(s.event_date)}. Upstream, near the border, a glacier collapse sends a debris flood down the Bhote Koshi.`, 4600);
      fly({ center: m, zoom: 11.6, pitch: 60, bearing: 205, d: 5200 });
      await say(2, `Lifeline traces where it ran on the elevation model${villages ? `: ${villages} settlements sit on its path` : ""}. Orange is debris the radar saw; grey is ground it could not see.`, 5600);
    }
    if (top) {
      fly({ center: [top.lon, top.lat], zoom: 13.2, pitch: 64, bearing: 170, d: 4200 });
      await say(3, `Here the highway and a bridge are gone. ${s.villages_cut_off ?? "Many"} settlements across the area have lost their road to a hospital or clinic.`, 5200);
      fly({ center: [top.lon, top.lat], zoom: 11.4, pitch: 50, bearing: 160, d: 3600 });
      document.querySelectorAll(".maplibregl-marker").forEach((el) => el.classList.add("tour-glow"));
      await say(4, `Reopen this first: ${top.name} and the closed stretches next to it reconnect ${top.villages_reconnected} settlements, ${[...new Set(top.names || [])].slice(0, 3).join(", ")} among them.`, 6500);
    }
  } catch { /* stopped */ }
  document.querySelectorAll(".tour-glow").forEach((el) => el.classList.remove("tour-glow"));
  if (id === touring) { cap.hidden = true; touring++; }
}
$("#tour-btn")?.addEventListener("click", () => runTour());
$("#tour-stop")?.addEventListener("click", () => { touring++; $("#tour-cap").hidden = true; });
$("#tour-cta")?.addEventListener("click", () => { $("#hero-map").scrollIntoView({ behavior: REDUCE ? "auto" : "smooth", block: "center" }); setTimeout(runTour, REDUCE ? 0 : 700); });
