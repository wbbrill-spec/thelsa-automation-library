"""
dashboard.py — the Cross-Border Shipment Dashboard page (single-file HTML/JS).

Served by web.py at /crossborder. It reads /crossborder/api/shipments and
renders everything client-side: KPI tiles, a pipeline board (one column per
stage), an alerts list (stalled / docs incomplete / on hold / payment
pending), a hub view (fills in once the Remisiones sheet is reachable) and a
sortable table. Filters: agent, flag, hub, free-text search.
"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cross-Border Shipments · Thelsa</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f6f8; color: #1a1a2e; }
header { background: #fff; padding: 0 28px; display: flex; align-items: center; justify-content: space-between; height: 64px; box-shadow: 0 2px 12px rgba(0,0,0,.08); border-bottom: 3px solid #c0392b; position: sticky; top: 0; z-index: 5; }
.brand { display: flex; align-items: center; gap: 14px; }
.brand img { height: 38px; }
.brand h1 { font-size: 17px; font-weight: 800; }
.brand h1 span { color: #c0392b; }
.hdr-right { display: flex; align-items: center; gap: 14px; font-size: 12px; color: #666; }
.hdr-right a { color: #c0392b; text-decoration: none; font-weight: 600; }
.pill { background: #f3f4f6; border: 1px solid #e0e0e0; border-radius: 20px; padding: 4px 12px; font-size: 11px; font-weight: 600; color: #555; }
.pill.warn { background: #fff4e5; border-color: #ffd9a8; color: #b45309; }
.pill.ok { background: #e6f4ea; border-color: #b7e1c1; color: #1e7e34; }
main { max-width: 1500px; margin: 0 auto; padding: 22px 24px 60px; }

.toolbar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 18px; }
.toolbar select, .toolbar input { font: inherit; font-size: 13px; padding: 7px 10px; border: 1px solid #d9dbe0; border-radius: 8px; background: #fff; }
.toolbar input { min-width: 240px; }
.btn { font: inherit; font-size: 13px; font-weight: 600; padding: 7px 14px; border-radius: 8px; border: 1px solid #e0e0e0; background: #f3f4f6; color: #444; cursor: pointer; text-decoration: none; }
.btn:hover { opacity: .85; }
.btn.primary { background: #c0392b; color: #fff; border-color: #c0392b; }
.btn.active { background: #1a1a2e; color: #fff; border-color: #1a1a2e; }
.spacer { flex: 1; }

.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 22px; }
.kpi { background: #fff; border: 1px solid #e8e8e8; border-radius: 12px; padding: 14px 16px; cursor: pointer; transition: box-shadow .15s; }
.kpi:hover { box-shadow: 0 4px 14px rgba(0,0,0,.08); }
.kpi.sel { outline: 2px solid #c0392b; }
.kpi .v { font-size: 28px; font-weight: 800; line-height: 1.1; }
.kpi .l { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .8px; color: #777; margin-top: 4px; }
.kpi .s { font-size: 11px; color: #999; margin-top: 4px; }
.kpi.red .v { color: #c0392b; }
.kpi.amber .v { color: #b45309; }
.kpi.green .v { color: #1e7e34; }
.kpi.blue .v { color: #1967d2; }

.section { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #888; margin: 22px 0 10px; display: flex; align-items: center; gap: 10px; }
.section .cnt { background: #e8e8e8; color: #555; border-radius: 10px; padding: 1px 8px; font-size: 11px; }

.board { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 8px; }
.col { flex: 0 0 210px; background: #ecedf0; border-radius: 12px; padding: 10px; min-height: 120px; }
.col h3 { font-size: 12px; font-weight: 700; color: #333; display: flex; justify-content: space-between; margin-bottom: 8px; padding: 0 2px; }
.col h3 .n { background: #fff; border-radius: 10px; padding: 0 8px; font-size: 11px; color: #555; }
.col .lv { font-size: 10px; color: #777; font-weight: 500; margin: -6px 2px 8px; }
.card { background: #fff; border-radius: 9px; padding: 9px 10px; margin-bottom: 8px; border-left: 4px solid #cfd2d8; box-shadow: 0 1px 3px rgba(0,0,0,.06); cursor: pointer; }
.card:hover { box-shadow: 0 3px 10px rgba(0,0,0,.12); }
.card.f-stalled { border-left-color: #f59e0b; }
.card.f-docs_incomplete { border-left-color: #1967d2; }
.card.f-on_hold, .card.f-payment_pending, .card.f-unresponsive { border-left-color: #c0392b; }
.card.f-in_progress { border-left-color: #1e7e34; }
.card .nm { font-size: 13px; font-weight: 700; line-height: 1.25; }
.src { display: inline-block; font-size: 9px; font-weight: 800; letter-spacing: .5px; padding: 0 5px; border-radius: 4px; background: #1a1a2e; color: #fff; vertical-align: middle; margin-left: 4px; }
.card .ag { font-size: 11px; color: #666; margin-top: 2px; }
.card .dest { font-size: 11px; color: #444; margin-top: 4px; }
.card .meta { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.tag { font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 10px; background: #f3f4f6; color: #555; }
.tag.stalled { background: #fff4e5; color: #b45309; }
.tag.docs_incomplete { background: #e8f0fe; color: #1967d2; }
.tag.on_hold, .tag.payment_pending { background: #fce8e6; color: #c0392b; }
.tag.in_storage { background: #f3e8ff; color: #7b1fa2; }
.tag.certificate_pending, .tag.visa_pending, .tag.docs_pending { background: #fef9e7; color: #92400e; }
.tag.unresponsive { background: #fce8e6; color: #c0392b; }
.tag.awaiting_green_light, .tag.awaiting_booking { background: #eef1f5; color: #555; }
.tag.in_progress { background: #e6f4ea; color: #1e7e34; }
.tag.vol { background: #eef1f5; color: #333; }
.card .step { font-size: 10px; color: #888; margin-top: 5px; }
.card .bar { height: 4px; background: #eee; border-radius: 3px; margin-top: 5px; overflow: hidden; }
.card .bar i { display: block; height: 100%; background: #c0392b; }

.grid2 { display: grid; grid-template-columns: 1.4fr 1fr; gap: 18px; }
@media (max-width: 1000px) { .grid2 { grid-template-columns: 1fr; } }
.panel { background: #fff; border: 1px solid #e8e8e8; border-radius: 12px; padding: 14px 16px; }
.alerts .row { display: flex; gap: 10px; align-items: flex-start; padding: 9px 0; border-bottom: 1px solid #f0f0f0; font-size: 13px; cursor: pointer; }
.alerts .row:last-child { border-bottom: 0; }
.alerts .ico { width: 26px; height: 26px; border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 13px; flex: 0 0 26px; }
.ico.stalled { background: #fff4e5; } .ico.docs_incomplete { background: #e8f0fe; } .ico.on_hold, .ico.payment_pending, .ico.unresponsive { background: #fce8e6; } .ico.certificate_pending, .ico.docs_pending, .ico.visa_pending { background: #fef9e7; }
.alerts .t { font-weight: 700; }
.alerts .d { color: #666; font-size: 12px; margin-top: 1px; }
.alerts .who { margin-left: auto; font-size: 11px; color: #999; white-space: nowrap; }
.hubs .hub { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
.hubs .hub:last-child { border-bottom: 0; }
.hubs .hn { width: 110px; font-weight: 700; }
.hubs .hb { flex: 1; height: 14px; background: #f0f1f4; border-radius: 7px; overflow: hidden; position: relative; }
.hubs .hb i { display: block; height: 100%; background: #c0392b; }
.hubs .hb i.ok { background: #1e7e34; }
.hubs .hv { width: 150px; text-align: right; font-size: 12px; color: #555; }
.note { font-size: 12px; color: #888; background: #fafafa; border: 1px dashed #ddd; border-radius: 8px; padding: 10px 12px; margin-top: 8px; line-height: 1.5; }

table { width: 100%; border-collapse: collapse; font-size: 12.5px; background: #fff; border: 1px solid #e8e8e8; border-radius: 12px; overflow: hidden; }
th, td { padding: 8px 10px; text-align: left; border-bottom: 1px solid #f0f0f0; white-space: nowrap; }
th { background: #fafafa; font-size: 11px; text-transform: uppercase; letter-spacing: .6px; color: #777; cursor: pointer; user-select: none; }
th.sorted { color: #c0392b; }
tr:hover td { background: #fcfcfd; }
td a { color: #1967d2; text-decoration: none; }
.tblwrap { overflow-x: auto; }

.empty { text-align: center; color: #999; padding: 40px; font-size: 14px; }
.spin { display: inline-block; width: 14px; height: 14px; border: 2px solid #ddd; border-top-color: #c0392b; border-radius: 50%; animation: sp .8s linear infinite; vertical-align: middle; margin-right: 6px; }
@keyframes sp { to { transform: rotate(360deg); } }

/* drawer */
#drawer { position: fixed; top: 0; right: -520px; width: 500px; max-width: 95vw; height: 100vh; background: #fff; box-shadow: -6px 0 24px rgba(0,0,0,.15); transition: right .2s; z-index: 20; overflow-y: auto; padding: 22px; }
#drawer.open { right: 0; }
#drawer h2 { font-size: 18px; font-weight: 800; margin-bottom: 2px; }
#drawer .sub { color: #666; font-size: 13px; margin-bottom: 14px; }
#drawer .close { position: absolute; top: 14px; right: 16px; background: none; border: 0; font-size: 22px; cursor: pointer; color: #888; }
.kv { display: grid; grid-template-columns: 140px 1fr; gap: 6px 10px; font-size: 13px; margin-bottom: 16px; }
.kv b { color: #777; font-weight: 600; font-size: 12px; }
.ms { font-size: 13px; }
.ms div { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid #f3f3f3; }
.ms .done { color: #1e7e34; }
.ms .todo { color: #bbb; }
#overlay { position: fixed; inset: 0; background: rgba(0,0,0,.2); display: none; z-index: 15; }
#overlay.open { display: block; }

/* consolidation planner */
.plan-sum { display: flex; gap: 22px; flex-wrap: wrap; background: #fff; border: 1px solid #e8e8e8; border-radius: 12px; padding: 14px 18px; margin-bottom: 16px; }
.plan-sum .m { font-size: 12px; color: #777; }
.plan-sum .m b { display: block; font-size: 22px; font-weight: 800; color: #1a1a2e; line-height: 1.1; }
.plan-hub { margin-bottom: 18px; }
.plan-hub-h { font-size: 13px; font-weight: 800; color: #1a1a2e; margin: 0 0 9px; display: flex; align-items: center; gap: 8px; }
.plan-hub-h .cnt { background: #e8e8e8; color: #555; border-radius: 10px; padding: 1px 8px; font-size: 11px; font-weight: 700; }
.plan-trucks { display: grid; grid-template-columns: repeat(auto-fill, minmax(265px, 1fr)); gap: 12px; }
.ptruck { background: #fff; border: 1px solid #e8e8e8; border-radius: 12px; padding: 12px 14px; border-top: 3px solid #c0392b; }
.ptruck.ready { border-top-color: #1e7e34; }
.pt-h { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
.pt-t { font-weight: 800; font-size: 13px; }
.pt-fill { font-size: 11px; color: #666; font-variant-numeric: tabular-nums; }
.pgauge { height: 8px; background: #f0f1f4; border-radius: 5px; overflow: hidden; margin-bottom: 9px; }
.pgauge i { display: block; height: 100%; background: #c0392b; }
.pgauge i.ok { background: #1e7e34; }
.pt-items { display: flex; flex-direction: column; gap: 4px; margin-bottom: 9px; }
.pt-item { font-size: 12px; padding: 3px 7px; border-radius: 6px; background: #fafafa; cursor: pointer; display: flex; align-items: center; gap: 7px; }
.pt-item:hover { background: #f0f1f4; }
.pt-item .psrc { font-size: 9px; font-weight: 800; color: #fff; border-radius: 8px; padding: 0 5px; letter-spacing: .3px; }
.pt-item .psrc.tim { background: #1967d2; } .pt-item .psrc.tms { background: #c0392b; }
.pt-item .pnm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pt-item .pt-v { margin-left: auto; color: #555; font-variant-numeric: tabular-nums; white-space: nowrap; }
.pt-foot { font-size: 11px; font-weight: 700; color: #b45309; }
.ptruck.ready .pt-foot { color: #1e7e34; }
</style>
</head>
<body>
<header>
  <div class="brand"><a href="/"><img src="/static/thelsa_logo.png" alt="Thelsa"></a><h1>Cross-Border <span>Shipments</span></h1></div>
  <div class="hdr-right">
    <span id="src-tim" class="pill">ClickUp · —</span>
    <span id="src-rem" class="pill">Remisiones · —</span>
    <span id="src-tms" class="pill">Moveware · pending</span>
    <span id="asof">—</span>
    <a href="/">← Library</a>
  </div>
</header>
<main>
  <div class="toolbar">
    <select id="f-source"><option value="">TIM + TMS</option><option value="TIM">TIM (ClickUp)</option><option value="TMS">TMS (Moveware)</option></select>
    <select id="f-agent"><option value="">All agents</option></select>
    <select id="f-flag"><option value="">All flags</option></select>
    <select id="f-hub"><option value="">All hubs</option></select>
    <select id="f-stage"><option value="">All stages</option></select>
    <input id="f-q" type="search" placeholder="Search customer / reference / destination…">
    <button class="btn" id="clear">Clear</button>
    <span class="spacer"></span>
    <label style="font-size:12px;color:#666;display:flex;align-items:center;gap:6px;white-space:nowrap"><input type="checkbox" id="f-closed"> include completed</label>
    <button class="btn" id="view-board">Board</button>
    <button class="btn" id="view-plan">Plan loads</button>
    <button class="btn" id="view-table">Table</button>
    <button class="btn primary" id="refresh">↻ Refresh</button>
  </div>

  <div class="kpis" id="kpis"></div>

  <div id="board-view">
    <div class="section">Pipeline <span class="cnt" id="cnt-board">0</span></div>
    <div class="board" id="board"></div>

    <div class="grid2">
      <div>
        <div class="section">Needs attention <span class="cnt" id="cnt-alerts">0</span></div>
        <div class="panel alerts" id="alerts"></div>
      </div>
      <div>
        <div class="section">Hub load (open shipments)</div>
        <div class="panel hubs" id="hubs"></div>
      </div>
    </div>
  </div>

  <div id="table-view" style="display:none">
    <div class="section">All shipments <span class="cnt" id="cnt-table">0</span></div>
    <div class="tblwrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>

  <div id="plan-view" style="display:none">
    <div class="section">Suggested consolidated loads <span class="cnt" id="cnt-plan">0</span></div>
    <div id="plan"></div>
  </div>
</main>

<div id="overlay"></div>
<div id="drawer"><button class="close" id="dclose">×</button><div id="dbody"></div></div>

<script>
const STAGES = [
  ["booked","Booked"],["docs_pending","Docs pending"],["green_light","Green light"],
  ["in_transit_to_border","To border"],["customs_clearance","Customs"],["at_hub","At hub"],
  ["onward_leg","Onward leg"],["out_for_delivery","Out for delivery"],["delivered","Delivered"],
  ["closed","Closed"],["unknown","Unmapped"]];
const STAGE_LABEL = Object.fromEntries(STAGES);
const FLAG_LABEL = {stalled:"Stalled ≥7 days", docs_incomplete:"Docs incomplete", on_hold:"On hold",
  payment_pending:"Payment pending", in_storage:"In storage", certificate_pending:"Certificate pending",
  in_progress:"In progress", window_risk:"Delivery window at risk", docs_pending:"Waiting on documents",
  visa_pending:"Visa pending", unresponsive:"Customer unresponsive", awaiting_green_light:"Awaiting green light",
  awaiting_booking:"Awaiting booking"};
const FLAG_ICON = {stalled:"⏳", docs_incomplete:"📄", on_hold:"⛔", payment_pending:"💳", in_storage:"🏬", certificate_pending:"📝", in_progress:"▶", window_risk:"⚠️", unresponsive:"📵", docs_pending:"📄", visa_pending:"🛂", awaiting_green_light:"🟢", awaiting_booking:"📅"};
const ALERT_FLAGS = ["on_hold","payment_pending","window_risk","unresponsive","docs_incomplete","docs_pending","certificate_pending","visa_pending","stalled"];
const HUBS = ["Monterrey","Mexico City","Guadalajara","Querétaro","Mérida","Torreón","Unknown"];
const TRUCK_LV = 13;

let ALL = [], STATUS = {}, DIAG = {}, view = "board", sortKey = "customer_name", sortDir = 1, kpiSel = null;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtN = n => n == null ? "—" : (Math.round(n*10)/10).toLocaleString();
const fmtD = s => s ? s.slice(5).replace("-", "/") : "—";
const money = n => n == null ? "—" : "$" + Math.round(n).toLocaleString();

async function load(force) {
  $("#asof").innerHTML = '<span class="spin"></span>loading…';
  const closed = $("#f-closed").checked ? "&completed=1" : "";
  const r = await fetch(`/crossborder/api/shipments?v=${Date.now()}${force ? "&refresh=1" : ""}${closed}`);
  const j = await r.json();
  ALL = j.shipments || []; STATUS = j.status || {}; DIAG = j.diagnostics || {};
  if (STATUS.refreshing && !ALL.length) { $("#asof").innerHTML = '<span class="spin"></span>first pull running (~40 s)…'; setTimeout(() => load(false), 8000); return; }
  if (STATUS.refreshing) setTimeout(() => load(false), 8000);
  const age = STATUS.cache_age_s;
  $("#asof").textContent = STATUS.refreshing ? "refreshing…" : (age == null ? "—" : `as of ${age < 60 ? age + " s" : Math.round(age/60) + " min"} ago`);
  const timN = ALL.filter(s => s.source === "TIM").length;
  $("#src-tim").textContent = `ClickUp · ${timN}`; $("#src-tim").className = "pill ok";
  const t = DIAG.tms || {};
  const tmsN = ALL.filter(s => s.source === "TMS").length;
  if (t.error) { $("#src-tms").textContent = "Moveware · error"; $("#src-tms").className = "pill warn"; $("#src-tms").title = t.error; }
  else if (t.count != null) { $("#src-tms").textContent = `Moveware · ${tmsN}${t.env && t.env !== "prod" ? " (" + t.env + ")" : ""}`; $("#src-tms").className = "pill ok"; $("#src-tms").title = `${t.rows_seen} jobs updated in window · ${t.cross_border} cross-border · ${t.requests_made} calls`; }
  else { $("#src-tms").textContent = "Moveware · pending"; $("#src-tms").className = "pill"; }
  const rem = DIAG.remisiones || {};
  if (rem.error) { $("#src-rem").textContent = "Remisiones · no access"; $("#src-rem").className = "pill warn"; $("#src-rem").title = rem.error; }
  else if (rem.matched != null) { $("#src-rem").textContent = `Remisiones · ${rem.matched} matched (${rem.week || "latest"})`; $("#src-rem").className = "pill ok"; }
  buildFilters(); render();
}

function buildFilters() {
  const keep = id => $(id).value;
  const fill = (id, vals, label) => { const cur = keep(id); const el = $(id);
    el.innerHTML = `<option value="">${label}</option>` + vals.map(v => `<option value="${esc(v[0])}">${esc(v[1])}</option>`).join("");
    el.value = cur; };
  const agents = [...new Set(ALL.map(s => s.agent || "?"))].sort();
  fill("#f-agent", agents.map(a => [a, a]), "All agents");
  const flags = [...new Set(ALL.flatMap(s => s.status_flags))].sort();
  fill("#f-flag", flags.map(f => [f, FLAG_LABEL[f] || f]), "All flags");
  const hubs = HUBS.filter(h => ALL.some(s => s.destination_hub === h));
  fill("#f-hub", hubs.map(h => [h, h]), "All hubs");
  fill("#f-stage", STAGES.filter(st => ALL.some(s => s.stage === st[0])), "All stages");
}

function filtered() {
  const a = $("#f-agent").value, f = $("#f-flag").value, h = $("#f-hub").value, st = $("#f-stage").value, src = $("#f-source").value, q = $("#f-q").value.trim().toLowerCase();
  return ALL.filter(s =>
    (!src || s.source === src) && (!a || (s.agent || "?") === a) && (!f || s.status_flags.includes(f)) && (!h || s.destination_hub === h) &&
    (!st || s.stage === st) &&
    (!kpiSel || kpiSel(s)) &&
    (!q || [s.customer_name, s.reference_number, s.destination, s.agent, s.current_step].join(" ").toLowerCase().includes(q)));
}

function render() {
  const rows = filtered();
  renderKpis();
  if (view === "board") { renderBoard(rows); renderAlerts(rows); renderHubs(rows); }
  else if (view === "plan") renderPlan(rows);
  else renderTable(rows);
  $("#board-view").style.display = view === "board" ? "" : "none";
  $("#plan-view").style.display = view === "plan" ? "" : "none";
  $("#table-view").style.display = view === "table" ? "" : "none";
  $("#view-board").className = "btn" + (view === "board" ? " active" : "");
  $("#view-plan").className = "btn" + (view === "plan" ? " active" : "");
  $("#view-table").className = "btn" + (view === "table" ? " active" : "");
}

// ── Consolidation planner ──────────────────────────────────────────────
// Bins open shipments (with a known hub + volume) into 13-LVE trucks per hub,
// first-fit-decreasing. A prototype of the load planner in the spec: the real
// engine will also weigh ready/clearance dates and the 30-day window. Works
// across both silos (TIM + TMS) since it reads the unified shipment feed.
function windowRisk(s) {
  if (!s.delivery_date) return false;
  const d = (new Date(s.delivery_date) - new Date()) / 86400000;
  return d <= 7;
}
function planTrucks(rows) {
  const open = rows.filter(s => s.is_open && (s.lift_van_equivalents || 0) > 0
    && s.destination_hub && s.destination_hub !== "Unknown");
  const byHub = {};
  open.forEach(s => (byHub[s.destination_hub] = byHub[s.destination_hub] || []).push(s));
  const trucks = [];
  Object.keys(byHub).forEach(hub => {
    const items = byHub[hub].slice().sort((a, b) => (b.lift_van_equivalents || 0) - (a.lift_van_equivalents || 0));
    const hubTrucks = [];
    items.forEach(s => {
      const lv = s.lift_van_equivalents || 0;
      let t = hubTrucks.find(t => t.lve + lv <= TRUCK_LV + 1e-9);
      if (!t) { t = { hub, lve: 0, items: [] }; hubTrucks.push(t); }
      t.lve += lv; t.items.push(s);
    });
    hubTrucks.forEach((t, i) => { t.n = i + 1; t.of = hubTrucks.length; });
    trucks.push(...hubTrucks);
  });
  return trucks;
}
function truckHtml(t) {
  const pct = Math.min(100, Math.round(100 * t.lve / TRUCK_LV));
  const ready = t.lve >= 0.85 * TRUCK_LV;
  const room = TRUCK_LV - t.lve;
  const items = t.items.map(s => {
    const v = s.lift_vans ? `${s.lift_vans} LV` : s.u_boxes ? `${s.u_boxes} UB` : `${fmtN(s.lift_van_equivalents)} LV`;
    return `<div class="pt-item" data-id="${esc(s.id)}"><span class="psrc ${s.source === "TMS" ? "tms" : "tim"}">${esc(s.source)}</span><span class="pnm">${esc(s.customer_name)}</span>${windowRisk(s) ? ' <span class="tag window_risk" title="delivery within 7 days">⚠️</span>' : ""}<span class="pt-v">${v}</span></div>`;
  }).join("");
  return `<div class="ptruck ${ready ? "ready" : ""}">
    <div class="pt-h"><span class="pt-t">Truck ${t.n}/${t.of}</span><span class="pt-fill">${fmtN(t.lve)} / ${TRUCK_LV} LV · ${pct}%</span></div>
    <div class="pgauge"><i class="${ready ? "ok" : ""}" style="width:${pct}%"></i></div>
    <div class="pt-items">${items}</div>
    <div class="pt-foot">${ready ? "✓ Ready to dispatch" : `Gathering — room for ${fmtN(room)} more LV`}</div>
  </div>`;
}
function renderPlan(rows) {
  const trucks = planTrucks(rows);
  const open = rows.filter(s => s.is_open);
  const noVol = open.filter(s => !((s.lift_van_equivalents || 0) > 0));
  const unkHub = open.filter(s => (s.lift_van_equivalents || 0) > 0 && (!s.destination_hub || s.destination_hub === "Unknown"));
  const totLve = trucks.reduce((t, x) => t + x.lve, 0);
  const readyN = trucks.filter(t => t.lve >= 0.85 * TRUCK_LV).length;
  const avgFill = trucks.length ? Math.round(100 * totLve / (trucks.length * TRUCK_LV)) : 0;
  $("#cnt-plan").textContent = trucks.length;

  const byHub = {};
  trucks.forEach(t => (byHub[t.hub] = byHub[t.hub] || []).push(t));
  const hubHtml = HUBS.filter(h => byHub[h]).map(h => {
    const ts = byHub[h];
    return `<div class="plan-hub"><div class="plan-hub-h">${esc(h)} <span class="cnt">${ts.length} truck${ts.length > 1 ? "s" : ""}</span></div>
      <div class="plan-trucks">${ts.map(truckHtml).join("")}</div></div>`;
  }).join("");

  const summary = `<div class="plan-sum">
    <div class="m"><b>${trucks.length}</b>trucks suggested</div>
    <div class="m"><b>${fmtN(totLve)}</b>lift-van equiv. planned</div>
    <div class="m"><b>${readyN}</b>ready to dispatch (≥85%)</div>
    <div class="m"><b>${avgFill}%</b>average fill</div>
  </div>`;

  const notes = [];
  if (noVol.length) notes.push(`${noVol.length} open shipment${noVol.length > 1 ? "s" : ""} without a parsed volume — not yet plannable (volume comes from the Remisiones workbook / source).`);
  if (unkHub.length) notes.push(`${unkHub.length} open shipment${unkHub.length > 1 ? "s" : ""} with volume but no mapped hub — extend the destination → hub table.`);
  notes.push("Prototype: first-fit binning per hub against a 13-LV truck. Fill % is against a 53 ft truck; the real engine adds ready/clearance dates and the 30-day window. Includes both TIM and TMS open shipments.");
  const noteHtml = `<div class="note">${notes.map(esc).join("<br>")}</div>`;

  $("#plan").innerHTML = trucks.length
    ? summary + hubHtml + noteHtml
    : `<div class="empty">No plannable shipments yet — open shipments need a volume and a mapped destination hub.</div>` + noteHtml;
  document.querySelectorAll("#plan .pt-item").forEach(el => el.onclick = () => openDrawer(el.dataset.id));
}

function renderKpis() {
  const open = ALL.filter(s => s.is_open);
  const cnt = fn => open.filter(fn).length;
  const lv = open.reduce((t, s) => t + (s.lift_van_equivalents || 0), 0);
  const withVol = open.filter(s => s.lift_van_equivalents || s.volume_m3).length;
  const tiles = [
    ["open", "", open.length, "Open shipments", "", s => s.is_open],
    ["red", "red", cnt(s => s.status_flags.includes("on_hold") || s.status_flags.includes("payment_pending")), "On hold / payment", "", s => s.status_flags.includes("on_hold") || s.status_flags.includes("payment_pending")],
    ["docs", "blue", cnt(s => s.status_flags.includes("docs_incomplete")), "Docs incomplete", "", s => s.status_flags.includes("docs_incomplete")],
    ["stalled", "amber", cnt(s => s.status_flags.includes("stalled")), "Stalled ≥ 7 days", "no checklist progress", s => s.status_flags.includes("stalled")],
    ["border", "", cnt(s => ["in_transit_to_border","customs_clearance"].includes(s.stage)), "At / crossing border", "", s => ["in_transit_to_border","customs_clearance"].includes(s.stage)],
    ["mx", "green", cnt(s => ["at_hub","onward_leg","out_for_delivery"].includes(s.stage)), "In Mexico, delivering", "", s => ["at_hub","onward_leg","out_for_delivery"].includes(s.stage)],
    ["lv", "", fmtN(lv), "Lift-van equiv. open", withVol ? `${withVol} with volume · ${fmtN(lv/TRUCK_LV)} trucks` : "needs Remisiones sheet", null],
  ];
  $("#kpis").innerHTML = tiles.map(t => `<div class="kpi ${t[1]} ${kpiSel && kpiSel.__k === t[0] ? "sel" : ""}" data-k="${t[0]}"><div class="v">${t[2]}</div><div class="l">${t[3]}</div>${t[4] ? `<div class="s">${t[4]}</div>` : ""}</div>`).join("");
  document.querySelectorAll(".kpi").forEach((el, i) => el.onclick = () => {
    const t = tiles[i]; if (!t[5]) return;
    if (kpiSel && kpiSel.__k === t[0]) kpiSel = null; else { kpiSel = t[5]; kpiSel.__k = t[0]; }
    render();
  });
}

function cardHtml(s) {
  const flags = s.status_flags.filter(f => f !== "in_progress");
  const prim = ALERT_FLAGS.find(f => s.status_flags.includes(f)) || (s.status_flags.includes("in_progress") ? "in_progress" : "");
  const vol = s.lift_vans ? `${s.lift_vans} LV` : s.u_boxes ? `${s.u_boxes} U-Box` : s.volume_m3 ? `${s.volume_m3} m³` : "";
  const pct = s.steps_total ? Math.round(100 * s.steps_done / s.steps_total) : 0;
  return `<div class="card f-${prim}" data-id="${esc(s.id)}">
    <div class="nm">${esc(s.customer_name)}${s.source === "TMS" ? ' <span class="src">TMS</span>' : ""}</div>
    <div class="ag">${esc(s.agent || "?")}${s.reference_number ? " · " + esc(s.reference_number) : ""}</div>
    ${s.destination ? `<div class="dest">→ ${esc(s.destination)}${s.destination_hub && s.destination_hub !== "Unknown" ? ` <span style="color:#999">(${esc(s.destination_hub)})</span>` : ""}</div>` : ""}
    <div class="meta">${vol ? `<span class="tag vol">${esc(vol)}</span>` : ""}${flags.map(f => `<span class="tag ${f}">${esc(FLAG_LABEL[f] || f)}</span>`).join("")}</div>
    ${s.current_step ? `<div class="step">${esc(s.current_step)}${s.days_since_progress != null ? ` · ${s.days_since_progress}d` : ""}</div>` : ""}
    ${s.steps_total ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ""}
  </div>`;
}

function renderBoard(rows) {
  const showClosed = $("#f-closed").checked;
  const cols = STAGES.filter(st => (st[0] !== "closed" || showClosed) && (st[0] !== "unknown" || rows.some(s => s.stage === "unknown")));
  $("#board").innerHTML = cols.map(st => {
    const items = rows.filter(s => s.stage === st[0]);
    const lv = items.reduce((t, s) => t + (s.lift_van_equivalents || 0), 0);
    return `<div class="col"><h3>${st[1]} <span class="n">${items.length}</span></h3>${lv ? `<div class="lv">${fmtN(lv)} LV equiv.</div>` : ""}${items.map(cardHtml).join("") || '<div style="font-size:11px;color:#aaa;text-align:center;padding:10px">—</div>'}</div>`;
  }).join("");
  $("#cnt-board").textContent = rows.length;
  document.querySelectorAll("#board .card").forEach(el => el.onclick = () => openDrawer(el.dataset.id));
}

function renderAlerts(rows) {
  const items = [];
  rows.filter(s => s.is_open).forEach(s => {
    const f = ALERT_FLAGS.find(x => s.status_flags.includes(x));
    if (!f) return;
    let detail = "";
    if (f === "stalled") detail = `${s.days_since_progress} days without progress · ${s.current_step || STAGE_LABEL[s.stage]}`;
    else if (f === "docs_incomplete") detail = `${s.current_step || "Documents"} · ${s.days_since_progress ?? "?"}d`;
    else detail = (s.extra && s.extra.remisiones_status) || s.current_step || STAGE_LABEL[s.stage];
    items.push({s, f, detail, rank: ALERT_FLAGS.indexOf(f), days: s.days_since_progress || 0});
  });
  items.sort((a, b) => a.rank - b.rank || b.days - a.days);
  $("#cnt-alerts").textContent = items.length;
  $("#alerts").innerHTML = items.length ? items.map(it => `<div class="row" data-id="${esc(it.s.id)}">
      <div class="ico ${it.f}">${FLAG_ICON[it.f]}</div>
      <div><div class="t">${esc(it.s.customer_name)} <span style="color:#888;font-weight:500">· ${esc(it.s.agent || "?")}</span></div><div class="d">${esc(FLAG_LABEL[it.f])} — ${esc(it.detail)}</div></div>
      <div class="who">${esc((it.s.assignees || []).join(", "))}</div></div>`).join("")
    : '<div class="empty">Nothing needs attention 🎉</div>';
  document.querySelectorAll("#alerts .row").forEach(el => el.onclick = () => openDrawer(el.dataset.id));
}

function renderHubs(rows) {
  const open = rows.filter(s => s.is_open);
  const known = open.filter(s => s.destination_hub && s.destination_hub !== "Unknown");
  const html = HUBS.filter(h => h !== "Unknown").map(h => {
    const items = open.filter(s => s.destination_hub === h);
    const lv = items.reduce((t, s) => t + (s.lift_van_equivalents || 0), 0);
    const pct = Math.min(100, Math.round(100 * lv / TRUCK_LV));
    return `<div class="hub"><div class="hn">${h}</div><div class="hb"><i class="${pct >= 85 ? "ok" : ""}" style="width:${pct}%"></i></div><div class="hv">${items.length} shpt · ${fmtN(lv)} / ${TRUCK_LV} LV</div></div>`;
  }).join("");
  const unk = open.length - known.length;
  const rem = DIAG.remisiones || {};
  $("#hubs").innerHTML = html + (unk ? `<div class="note">${unk} open shipment${unk > 1 ? "s" : ""} without a destination hub.${rem.error ? " Destinations and volumes come from the Remisiones workbook — waiting on Files.Read.All access for the Graph app." : " Extend the destination → hub table for the unmapped cities."}</div>` : "");
}

const COLS = [["customer_name","Customer"],["source","Src"],["agent","Agent"],["reference_number","Reference"],["stage","Stage"],["current_step","Current step"],["days_since_progress","Days idle"],["destination","Destination"],["destination_hub","Hub"],["lift_van_equivalents","LV eq."],["status_flags","Flags"],["assignees","Assigned"],["milestones.green_light","Green light"],["milestones.crossed","Crossed"],["milestones.delivered","Delivered"]];
const get = (s, k) => k.includes(".") ? k.split(".").reduce((o, p) => o && o[p], s) : s[k];
function renderTable(rows) {
  rows = [...rows].sort((a, b) => { let x = get(a, sortKey), y = get(b, sortKey); if (Array.isArray(x)) x = x.join(","); if (Array.isArray(y)) y = y.join(",");
    if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * sortDir; });
  $("#tbl thead").innerHTML = "<tr>" + COLS.map(c => `<th data-k="${c[0]}" class="${sortKey === c[0] ? "sorted" : ""}">${c[1]}${sortKey === c[0] ? (sortDir > 0 ? " ▲" : " ▼") : ""}</th>`).join("") + "</tr>";
  $("#tbl tbody").innerHTML = rows.map(s => "<tr data-id=\"" + esc(s.id) + "\">" + COLS.map(c => {
    let v = get(s, c[0]);
    if (c[0] === "stage") v = STAGE_LABEL[v] || v;
    else if (c[0] === "status_flags") v = (v || []).map(f => `<span class="tag ${f}">${esc(FLAG_LABEL[f] || f)}</span>`).join(" ");
    else if (c[0] === "assignees") v = esc((v || []).join(", "));
    else if (c[0].startsWith("milestones")) v = fmtD(v);
    else if (c[0] === "lift_van_equivalents") v = v ? fmtN(v) : "—";
    else if (c[0] === "customer_name") v = s.url ? `<a href="${esc(s.url)}" target="_blank" onclick="event.stopPropagation()">${esc(v)}</a>` : esc(v);
    else v = esc(v ?? "—");
    return `<td>${v}</td>`; }).join("") + "</tr>").join("");
  $("#cnt-table").textContent = rows.length;
  document.querySelectorAll("#tbl th").forEach(th => th.onclick = () => { const k = th.dataset.k; if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = 1; } render(); });
  document.querySelectorAll("#tbl tbody tr").forEach(tr => tr.onclick = () => openDrawer(tr.dataset.id));
}

function openDrawer(id) {
  const s = ALL.find(x => x.id === id); if (!s) return;
  const ms = s.source === "TMS" ? ["booked","uplift","delivered","closed"] : ["booked","docs_complete","green_light","at_border_warehouse","docs_to_broker","crossed","at_hub","delivery_scheduled","delivered","closed"];
  const ex = s.extra || {};
  $("#dbody").innerHTML = `<h2>${esc(s.customer_name)}</h2>
    <div class="sub">${s.source === "TMS" ? '<span class="src">TMS</span> ' : ""}${esc(s.agent || "?")}${s.reference_number ? " · " + esc(s.reference_number) : ""}${s.url ? ` · <a href="${esc(s.url)}" target="_blank" style="color:#1967d2">open in ClickUp ↗</a>` : " · Moveware job"}</div>
    <div class="meta" style="margin-bottom:14px">${s.status_flags.map(f => `<span class="tag ${f}">${esc(FLAG_LABEL[f] || f)}</span>`).join(" ")}</div>
    <div class="kv">
      <b>Stage</b><span>${esc(STAGE_LABEL[s.stage] || s.stage)}</span>
      <b>Current step</b><span>${s.steps_total ? `${esc(s.current_step || "—")} (${s.steps_done}/${s.steps_total}, ${s.process_format || "?"})` : esc(s.source_status || "—") + (ex.direction ? ` · ${esc(ex.direction)}` : "") + (ex.method ? ` · ${esc(ex.method)}` : "")}</span>
      <b>Last progress</b><span>${s.last_progress_at ? esc(s.last_progress_at) + ` · ${s.days_since_progress} days ago` : "—"}</span>
      <b>Assigned</b><span>${esc((s.assignees || []).join(", ") || "—")}</span>
      <b>Origin → Dest.</b><span>${esc(s.origin || "?")} → ${esc(s.destination || "?")}${s.destination_hub !== "Unknown" ? ` (${esc(s.destination_hub)} hub)` : ""}</span>
      <b>Volume</b><span>${s.lift_vans ? s.lift_vans + " lift van(s) · " : ""}${s.u_boxes ? s.u_boxes + " U-Box(es) · " : ""}${s.volume_m3 ? s.volume_m3 + " m³ · " : ""}${esc(ex.volume_text || "")}${!(s.lift_vans || s.u_boxes || s.volume_m3 || ex.volume_text) ? "—" : ""}</span>
      <b>Sale value</b><span>${money(ex.sale_value)}</span>
      ${s.weight ? `<b>Weight</b><span>${s.weight} kg</span>` : ""}
      ${s.source === "TMS" ? "" : `<b>Remisiones</b><span>${ex.remisiones_block ? esc(ex.remisiones_block) + (ex.remisiones_week ? " · " + esc(ex.remisiones_week) : "") + (ex.remisiones_status ? "<br>" + esc(ex.remisiones_status) : "") : "not on the sheet"}</span>`}
    </div>
    <div class="section" style="margin-top:0">Milestones</div>
    <div class="ms">${ms.map(m => `<div class="${s.milestones && s.milestones[m] ? "done" : "todo"}"><span>${m.replace(/_/g, " ")}</span><span>${s.milestones && s.milestones[m] ? esc(s.milestones[m]) : "—"}</span></div>`).join("")}</div>`;
  $("#drawer").classList.add("open"); $("#overlay").classList.add("open");
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#overlay").classList.remove("open"); }

["#f-source","#f-agent","#f-flag","#f-hub","#f-stage"].forEach(id => $(id).onchange = render);
$("#f-q").oninput = render;
$("#f-closed").onchange = () => load(false);
$("#clear").onclick = () => { ["#f-source","#f-agent","#f-flag","#f-hub","#f-stage"].forEach(id => $(id).value = ""); $("#f-q").value = ""; kpiSel = null; render(); };
$("#view-board").onclick = () => { view = "board"; render(); };
$("#view-plan").onclick = () => { view = "plan"; render(); };
$("#view-table").onclick = () => { view = "table"; render(); };
$("#refresh").onclick = () => load(true);
$("#dclose").onclick = closeDrawer; $("#overlay").onclick = closeDrawer;
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
load(false);
setInterval(() => load(false), 5 * 60 * 1000);
</script>
</body>
</html>
"""
