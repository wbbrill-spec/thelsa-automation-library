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

.loads { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 12px; }
.load { background: #fff; border: 1px solid #e8e8e8; border-radius: 12px; padding: 12px 14px; }
.load.light { border-color: #ffd9a8; }
.load h4 { font-size: 13px; font-weight: 800; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.load .fillbar { height: 12px; background: #f0f1f4; border-radius: 6px; overflow: hidden; margin: 8px 0 6px; }
.load .fillbar i { display: block; height: 100%; background: #c0392b; }
.load .fillbar i.ok { background: #1e7e34; }
.load .fillbar i.lt { background: #f59e0b; }
.load .fl { font-size: 11px; color: #666; display: flex; justify-content: space-between; }
.load .row { display: flex; gap: 8px; font-size: 12px; padding: 5px 0; border-top: 1px solid #f3f3f3; cursor: pointer; }
.load .row .who { flex: 1; min-width: 0; }
.load .row .m3 { white-space: nowrap; color: #333; font-weight: 600; }
.load .adv { font-size: 12px; color: #92400e; background: #fff8ec; border-radius: 8px; padding: 8px 10px; margin-top: 8px; line-height: 1.45; }
.load .rs { color: #999; font-size: 11px; }
.tag.full { background: #e6f4ea; color: #1e7e34; } .tag.light { background: #fff4e5; color: #b45309; } .tag.xs { background: #1a1a2e; color: #fff; } .tag.anchor { background: #e8f0fe; color: #1967d2; } .tag.risk { background: #fce8e6; color: #c0392b; }
.plan-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 22px 0 10px; }
.plan-head .section { margin: 0; }
.plan-stats { font-size: 12px; color: #666; }
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
.langtog { display: inline-flex; border: 1px solid #d9dbe0; border-radius: 7px; overflow: hidden; }
.langtog button { background: #fff; color: #666; border: 0; padding: 3px 9px; font: inherit; font-size: 11px; font-weight: 700; cursor: pointer; }
.langtog button.on { background: #c0392b; color: #fff; }
/* demo mode — simulated data must be impossible to mistake for the real board */
#demobar { display: none; background: repeating-linear-gradient(135deg,#7c2d12,#7c2d12 14px,#9a3412 14px,#9a3412 28px); color: #fff; font-size: 12.5px; font-weight: 700; letter-spacing: .3px; padding: 9px 28px; display: none; align-items: center; gap: 12px; }
#demobar.on { display: flex; }
#demobar .x { margin-left: auto; color: #fff; text-decoration: underline; font-weight: 600; }
.tag.demo, .src.demo { background: #7c2d12; color: #fff; }
.card.demo { border-left-color: #9a3412 !important; }
body.demo header { border-bottom-color: #7c2d12; }
</style>
</head>
<body>
<header>
  <div class="brand"><a href="/"><img src="/static/thelsa_logo.png" alt="Thelsa"></a><h1>Cross-Border <span>Shipments</span></h1></div>
  <div class="hdr-right">
    <span id="src-tim" class="pill">ClickUp · —</span>
    <span id="src-rem" class="pill">Remisiones · —</span>
    <span id="src-tms" class="pill">Moveware · —</span>
    <span id="src-trs" class="pill" style="display:none">SIT/TRS · —</span>
    <span id="asof">—</span>
    <span class="langtog"><button id="lang-en" class="on">EN</button><button id="lang-es">ES</button></span>
    <a href="/" id="lib-link">← Library</a>
  </div>
</header>
<div id="demobar"><span id="demotext"></span><a class="x" id="demo-off" href="?demo=0">turn demo data off</a></div>
<main>
  <div class="toolbar">
    <select id="f-source"><option value="">All sources</option><option value="TIM">TIM (ClickUp)</option><option value="TMS">TMS (Moveware)</option><option value="TRS">TRS (SIT domestic)</option></select>
    <select id="f-agent"><option value="">All agents</option></select>
    <select id="f-flag"><option value="">All flags</option></select>
    <select id="f-hub"><option value="">All hubs</option></select>
    <select id="f-stage"><option value="">All stages</option></select>
    <input id="f-q" type="search" placeholder="Search customer / reference / destination…">
    <button class="btn" id="clear">Clear</button>
    <span class="spacer"></span>
    <label style="font-size:12px;color:#666;display:flex;align-items:center;gap:6px;white-space:nowrap"><input type="checkbox" id="f-closed"> include completed</label>
    <button class="btn" id="view-board">Board</button>
    <button class="btn" id="view-table">Table</button>
    <button class="btn primary" id="refresh">↻ Refresh</button>
  </div>

  <div class="kpis" id="kpis"></div>

  <div id="board-view">
    <div class="section"><span data-i18n="Pipeline">Pipeline</span> <span class="cnt" id="cnt-board">0</span></div>
    <div class="board" id="board"></div>

    <div class="plan-head">
      <div class="section"><span data-i18n="Suggested loads">Suggested loads</span> <span class="cnt" id="cnt-loads">0</span></div>
      <span class="plan-stats" id="plan-stats"></span>
      <span class="spacer"></span>
      <button class="btn" id="plan-draft">✉ Draft today's load email</button>
      <span class="plan-stats" id="plan-msg"></span>
    </div>
    <div class="loads" id="loads"></div>
    <div id="coming" class="note" style="display:none"></div>

    <div class="grid2">
      <div>
        <div class="plan-head">
          <div class="section"><span data-i18n="Needs attention">Needs attention</span> <span class="cnt" id="cnt-alerts">0</span></div>
          <span class="spacer"></span>
          <button class="btn" id="alert-draft">✉ Draft owner alerts</button>
        </div>
        <div class="plan-stats" id="alert-msg" style="margin-bottom:8px"></div>
        <div class="panel alerts" id="alerts"></div>
      </div>
      <div>
        <div class="section" data-i18n="Hub load (open imports, m³ vs one 53' trailer)">Hub load (open imports, m³ vs one 53' trailer)</div>
        <div class="panel hubs" id="hubs"></div>
      </div>
    </div>
  </div>

  <div id="table-view" style="display:none">
    <div class="section"><span data-i18n="All shipments">All shipments</span> <span class="cnt" id="cnt-table">0</span></div>
    <div class="tblwrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
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
const TRUCK_M3 = 88;   // 53' trailer ≈ 20,000 lb HHG at 6.5 lb/cuft ≈ 3,077 cuft ≈ 88 m³ (Bill, 2026-09-09)
const pm3 = s => s.planning_m3 || 0;

let ALL = [], STATUS = {}, DIAG = {}, view = "board", sortKey = "customer_name", sortDir = 1, kpiSel = null;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtN = n => n == null ? "—" : (Math.round(n*10)/10).toLocaleString();
const fmtD = s => s ? s.slice(5).replace("-", "/") : "—";
const money = n => n == null ? "—" : "$" + Math.round(n).toLocaleString();

// ── i18n: English → Spanish. Keys are the English UI strings; tr() returns the
// active-language text (falls back to the key, so any unlisted string stays EN).
const ES = {
  "Cross-Border": "Transfronterizo", "Shipments": "Envíos",
  "← Library": "← Biblioteca",
  "TIM + TMS": "TIM + TMS", "TIM (ClickUp)": "TIM (ClickUp)", "TMS (Moveware)": "TMS (Moveware)",
  "All agents": "Todos los agentes", "All flags": "Todas las alertas", "All hubs": "Todos los hubs",
  "All stages": "Todas las etapas",
  "Search customer / reference / destination…": "Buscar cliente / referencia / destino…",
  "Clear": "Limpiar", "include completed": "incluir completados",
  "Board": "Tablero", "Table": "Tabla", "↻ Refresh": "↻ Actualizar",
  "Pipeline": "Flujo", "Suggested loads": "Cargas sugeridas",
  "✉ Draft today's load email": "✉ Borrador del correo de cargas de hoy",
  "Needs attention": "Requiere atención",
  "Hub load (open imports, m³ vs one 53' trailer)": "Carga por hub (importaciones abiertas, m³ vs. un tráiler de 53')",
  "All shipments": "Todos los envíos",
  // stages
  "Booked": "Reservado", "Docs pending": "Docs pendientes", "Green light": "Luz verde",
  "To border": "A la frontera", "Customs": "Aduana", "At hub": "En hub", "Onward leg": "Tramo siguiente",
  "Out for delivery": "En reparto", "Delivered": "Entregado", "Closed": "Cerrado", "Unmapped": "Sin mapear",
  // flags
  "Stalled ≥7 days": "Estancado ≥7 días", "Docs incomplete": "Docs incompletos", "On hold": "En espera",
  "Payment pending": "Pago pendiente", "In storage": "En almacenaje", "Certificate pending": "Certificado pendiente",
  "In progress": "En progreso", "Delivery window at risk": "Ventana de entrega en riesgo",
  "Waiting on documents": "Esperando documentos", "Visa pending": "Visa pendiente",
  "Customer unresponsive": "Cliente sin responder", "Awaiting green light": "Esperando luz verde",
  "Awaiting booking": "Esperando reserva",
  // KPIs
  "Open shipments": "Envíos abiertos", "On hold / payment": "En espera / pago",
  "Stalled ≥ 7 days": "Estancado ≥ 7 días", "no checklist progress": "sin avance en la lista",
  "At / crossing border": "En / cruzando frontera", "In Mexico, delivering": "En México, entregando",
  "m³ open": "m³ abiertos", "needs Remisiones sheet": "requiere hoja de Remisiones",
  // alerts / hubs
  "Documents": "Documentos", "Nothing needs attention 🎉": "Nada requiere atención 🎉",
  "Extend the destination → hub table for the unmapped cities.": "Amplía la tabla destino → hub para las ciudades sin mapear.",
  "Destinations and volumes come from the Remisiones workbook — waiting on Files.Read.All access for the Graph app.": "Los destinos y volúmenes vienen del libro de Remisiones — esperando acceso Files.Read.All para la app de Graph.",
  // plan
  "No consolidatable shipments are ready right now.": "No hay envíos consolidables listos en este momento.",
  "Full": "Lleno", "Running light": "Va ligero", "anchor": "ancla", "creating draft…": "creando borrador…",
  "Could not create draft: ": "No se pudo crear el borrador: ",
  // table headers
  "Customer": "Cliente", "Src": "Fuente", "Agent": "Agente", "Reference": "Referencia", "Stage": "Etapa",
  "Current step": "Paso actual", "Days idle": "Días inactivo", "Destination": "Destino", "Hub": "Hub",
  "Flags": "Alertas", "Assigned": "Asignado", "Crossed": "Cruzado",
  // drawer
  "Last progress": "Último avance", "Origin → Dest.": "Origen → Destino", "Volume": "Volumen",
  "Sale value": "Valor de venta", "Weight": "Peso", "Milestones": "Hitos", "not on the sheet": "no está en la hoja",
  "open in ClickUp ↗": "abrir en ClickUp ↗", "Moveware job": "servicio Moveware",
  "loading…": "cargando…", "refreshing…": "actualizando…",
  "with volume": "con volumen", "trailers of": "tráilers de", "window risk": "en riesgo de ventana",
  "depart by": "salir antes del", "shpt": "env", "2 FTL jobs share": "2 servicios FTL comparten",
  "Coming (not yet ready):": "Próximos (aún no listos):", "days without progress": "días sin avance",
  "open shipments without a destination hub": "envíos abiertos sin hub de destino",
  "Could not create draft: ": "No se pudo crear el borrador: ",
  "✉ Draft owner alerts": "✉ Borrador de alertas por responsable",
};
let LANG = (function(){ try { return localStorage.getItem("cb_lang") || "en"; } catch(e){ return "en"; } })();
function tr(s){ if (LANG !== "es" || s == null) return s; return (s in ES) ? ES[s] : s; }
function applyStaticLang(){
  document.documentElement.lang = LANG;
  $("#lang-en").classList.toggle("on", LANG === "en");
  $("#lang-es").classList.toggle("on", LANG === "es");
  const H = $(".brand h1"); if (H) H.innerHTML = LANG === "es" ? 'Envíos <span>Transfronterizos</span>' : 'Cross-Border <span>Shipments</span>';
  const lib = $("#lib-link"); if (lib) lib.textContent = tr("← Library");
  $("#f-agent").querySelector('option[value=""]') && ($("#f-agent").querySelector('option[value=""]').textContent = tr("All agents"));
  $("#f-flag").querySelector('option[value=""]') && ($("#f-flag").querySelector('option[value=""]').textContent = tr("All flags"));
  $("#f-hub").querySelector('option[value=""]') && ($("#f-hub").querySelector('option[value=""]').textContent = tr("All hubs"));
  $("#f-stage").querySelector('option[value=""]') && ($("#f-stage").querySelector('option[value=""]').textContent = tr("All stages"));
  $("#f-q").placeholder = tr("Search customer / reference / destination…");
  $("#clear").textContent = tr("Clear");
  const inc = document.querySelector('label input#f-closed'); if (inc && inc.parentNode) inc.parentNode.lastChild.textContent = " " + tr("include completed");
  $("#view-board").textContent = tr("Board"); $("#view-table").textContent = tr("Table");
  $("#refresh").textContent = tr("↻ Refresh");
  $("#plan-draft").textContent = tr("✉ Draft today's load email");
  if ($("#alert-draft")) $("#alert-draft").textContent = tr("✉ Draft owner alerts");
  document.querySelectorAll("[data-i18n]").forEach(el => el.textContent = tr(el.getAttribute("data-i18n")));
}
function setLang(l){ LANG = l; try { localStorage.setItem("cb_lang", l); } catch(e){}
  applyStaticLang(); buildFilters(); render(); renderPlan(); renderDemoBar(DIAG.demo); }


// Demo data is opt-in per request: whatever ?demo= the page was opened with is
// forwarded to every API call, so the board and the plan agree about it and a
// bookmark of the live board can never pick it up by accident.
const DEMOQ = (function(){ const v = new URLSearchParams(location.search).get("demo");
  return v == null ? "" : "&demo=" + encodeURIComponent(v); })();

async function load(force) {
  $("#asof").innerHTML = '<span class="spin"></span>loading…';
  const closed = $("#f-closed").checked ? "&completed=1" : "";
  const r = await fetch(`/crossborder/api/shipments?v=${Date.now()}${force ? "&refresh=1" : ""}${closed}${DEMOQ}`);
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
  if (t.stale) {
    // Moveware is refusing; the board is showing the last good pull so the TMS
    // half does not silently vanish. Say so, and say how old it is.
    const mins = t.stale_age_s == null ? null : Math.round(t.stale_age_s / 60);
    const when = t.stale_since ? new Date(t.stale_since * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) : null;
    $("#src-tms").textContent = `Moveware · unreachable — ${tmsN} from ${when || (mins + " min ago")}`;
    $("#src-tms").className = "pill warn";
    $("#src-tms").title = `${t.stale_reason || "Moveware unavailable"}\nShowing the last successful pull${mins != null ? " (" + mins + " min old)" : ""}. Retrying periodically.`;
  }
  else if (t.error) { $("#src-tms").textContent = "Moveware · error"; $("#src-tms").className = "pill warn"; $("#src-tms").title = t.error; }
  else if (t.count != null) { $("#src-tms").textContent = `Moveware · ${tmsN}${t.env && t.env !== "prod" ? " (" + t.env + ")" : ""}`; $("#src-tms").className = "pill ok"; $("#src-tms").title = `${t.rows_seen} jobs updated in window · ${t.cross_border} cross-border · ${t.requests_made} calls`; }
  else { $("#src-tms").textContent = "Moveware · pending"; $("#src-tms").className = "pill"; }
  const trsN = ALL.filter(s => s.source === "TRS").length;
  if (trsN) { $("#src-trs").style.display = ""; $("#src-trs").textContent = `SIT/TRS · ${trsN}`; $("#src-trs").className = "pill ok"; }
  else $("#src-trs").style.display = "none";
  const rem = DIAG.remisiones || {};
  if (rem.error) { $("#src-rem").textContent = "Remisiones · no access"; $("#src-rem").className = "pill warn"; $("#src-rem").title = rem.error; }
  else if (rem.matched != null) { $("#src-rem").textContent = `Remisiones · ${rem.matched} matched (${rem.week || "latest"})`; $("#src-rem").className = "pill ok"; }
  renderDemoBar(DIAG.demo);
  buildFilters(); render();
  loadPlan();
}

// A standing, unmissable banner whenever any row on the page is simulated.
// Nothing about demo mode is subtle on purpose: this board is shown to the
// people who run the business, and a fake shipment must never read as a real one.
function renderDemoBar(dg) {
  const on = !!(dg && dg.demo);
  document.body.classList.toggle("demo", on);
  $("#demobar").classList.toggle("on", on);
  // The two buttons that can put words in a coordinator's inbox stay off while
  // any of the board is simulated — the server refuses them too.
  ["#plan-draft", "#alert-draft"].forEach(id => { const b = $(id); if (!b) return;
    b.disabled = on; b.title = on ? "Disabled while demo data is showing" : ""; b.style.opacity = on ? .45 : 1; });
  if (!on) return;
  const real = ALL.length - (dg.count || 0);
  $("#demotext").textContent = LANG === "es"
    ? `⚠ DATOS DE DEMOSTRACIÓN — ${dg.count} envíos simulados (${dg.tms} TMS, ${dg.trs} TRS domésticos)${dg.mode === "only" ? "; los datos reales están ocultos" : ` junto a ${real} reales`}. No son de ClickUp, Moveware ni SIT. Los borradores de correo están desactivados.`
    : `⚠ DEMO DATA — ${dg.count} simulated shipments (${dg.tms} TMS, ${dg.trs} domestic TRS)${dg.mode === "only" ? "; live data hidden" : `, alongside ${real} real ones`}. Not from ClickUp, Moveware or SIT. Email drafting is disabled.`;
}
const isDemo = s => !!(s.extra && s.extra.demo);

let PLAN = null;
async function loadPlan() {
  try { PLAN = await fetch(`/crossborder/api/plan?v=${Date.now()}${DEMOQ}`).then(r => r.json()); } catch (e) { PLAN = {error: String(e), loads: []}; }
  renderPlan();
}

function renderPlan() {
  const p = PLAN; if (!p) return;
  const src = $("#f-source").value;
  const loads = (p.loads || []).filter(l => !src || l.sources[src]);
  $("#cnt-loads").textContent = loads.length;
  const s = p.summary || {};
  $("#plan-stats").textContent = p.error ? p.error : `53' trailer = ${p.truck_m3} m³ · ${p.ready} ready · ${p.coming} coming · ${p.unsized || 0} without volume · avg fill ${s.avg_fill_pct || 0}% · ${s.light || 0} light · ${s.cross_silo || 0} TIM+TMS`;
  const oppByLane = Object.fromEntries((p.opportunities || []).map(o => [o.lane, o]));
  $("#loads").innerHTML = loads.length ? loads.map((l, n) => {
    const pct = Math.min(100, l.fill_pct);
    const cls = l.fill_pct >= 85 ? "ok" : (l.light ? "lt" : "");
    const opp = oppByLane[l.lane];
    return `<div class="load ${l.light ? "light" : ""}">
      <h4>${esc(l.lane)} ${l.fill_pct >= 85 ? `<span class="tag full">${tr("Full")}</span>` : ""}${l.light ? `<span class="tag light">${tr("Running light")}</span>` : ""}${l.cross_silo ? '<span class="tag xs">TIM + TMS</span>' : ""}${l.window_risk.length ? `<span class="tag risk">${l.window_risk.length} ${tr("window risk")}</span>` : ""}${l.anchors > 1 ? `<span class="tag anchor">${tr("2 FTL jobs share")}</span>` : ""}</h4>
      <div class="fillbar"><i class="${cls}" style="width:${pct}%"></i></div>
      <div class="fl"><span>${l.m3} / ${l.truck_m3} m³ · ${l.fill_pct}%${l.kg ? ` · ${fmtN(l.kg)} kg` : ""}</span><span>${l.depart_by ? tr("depart by") + " " + fmtD(l.depart_by) : ""}</span></div>
      ${l.shipments.map(it => `<div class="row" data-id="${esc(it.id)}"><div class="who"><b>${esc(it.customer)}</b>${it.anchor ? ' <span class="tag anchor">anchor</span>' : ""}${l.window_risk.includes(it.id) ? ' <span class="tag risk">by ' + fmtD(it.deadline) + '</span>' : ""}<br><span class="rs">${esc(it.source)} · ${esc(it.agent || "")}${it.reference ? " · " + esc(it.reference) : ""} · → ${esc(it.destination || "?")}${it.service ? " · " + esc(it.service) : ""}</span></div><div class="m3">${it.m3} m³</div></div>`).join("")}
      ${opp && opp.advice ? `<div class="adv">${esc(opp.advice)}</div>` : ""}
    </div>`; }).join("") : `<div class="empty">${p.error ? "" : tr("No consolidatable shipments are ready right now.")}</div>`;
  document.querySelectorAll("#loads .row").forEach(el => el.onclick = () => openDrawer(el.dataset.id));
  const cb = p.coming_by_lane || {}; const lanes = Object.keys(cb).filter(k => !src || cb[k].some(i => i.source === src));
  const ub = p.unsized_by_lane || {}; const ulanes = Object.keys(ub).filter(k => !src || ub[k].some(i => i.source === src));
  const parts = [];
  if (lanes.length) parts.push(("<b>" + tr("Coming (not yet ready):") + "</b> ") + lanes.map(k => `${esc(k)}: ` + cb[k].filter(i => !src || i.source === src).map(i => `${esc(i.customer)} (${i.m3} m³${i.ready_date ? ", " + fmtD(i.ready_date) : ""})`).join(", ")).join(" · "));
  if (ulanes.length) parts.push(`<b>Not plannable — no volume on record (${p.unsized}):</b> ` + ulanes.map(k => `${esc(k)}: ` + ub[k].filter(i => !src || i.source === src).map(i => esc(i.customer)).join(", ")).join(" · "));
  $("#coming").style.display = parts.length ? "" : "none";
  $("#coming").innerHTML = parts.join("<br><br>");
}

$("#plan-draft").onclick = async () => {
  const b = $("#plan-draft"); b.disabled = true; $("#plan-msg").textContent = tr("creating draft…");
  try {
    const r = await fetch("/crossborder/plan/draft", {method: "POST"}).then(x => x.json());
    $("#plan-msg").textContent = r.ok ? `Draft saved in ${r.folder || "Drafts"} for ${r.to.join(", ")} — review and send from Outlook.` : `Could not create draft: ${r.reason}`;
  } catch (e) { $("#plan-msg").textContent = "Could not create draft: " + e; }
  b.disabled = false;
};

// One draft per responsible person listing their shipments that need attention.
// Drafts only — they land in the Thelsa mailbox for a human to review and send.
$("#alert-draft").onclick = async () => {
  const b = $("#alert-draft"), m = $("#alert-msg");
  b.disabled = true; m.textContent = "creating drafts…";
  try {
    const r = await fetch("/crossborder/alerts/draft", {method: "POST"}).then(x => x.json());
    if (r.skipped_reason && !r.alert_count) {
      m.textContent = r.skipped_reason;
    } else {
      const ok = (r.drafts || []).filter(d => d.ok);
      const bad = (r.drafts || []).filter(d => !d.ok);
      const unres = ok.filter(d => !d.resolved).map(d => d.owner);
      m.textContent = `${ok.length} draft${ok.length === 1 ? "" : "s"} saved (${ok.map(d => `${d.owner}: ${d.shipments}`).join(", ")}) — review and send from Outlook.`
        + (unres.length ? ` No email on file for ${unres.join(", ")} — those went to the fallback inbox.` : "")
        + (bad.length ? ` ${bad.length} failed: ${bad.map(d => d.error).join("; ")}` : "")
        + (r.reason ? ` ${r.reason}` : "");
    }
  } catch (e) { m.textContent = "Could not create drafts: " + e; }
  b.disabled = false;
};

function buildFilters() {
  const keep = id => $(id).value;
  const fill = (id, vals, label) => { const cur = keep(id); const el = $(id);
    el.innerHTML = `<option value="">${label}</option>` + vals.map(v => `<option value="${esc(v[0])}">${esc(v[1])}</option>`).join("");
    el.value = cur; };
  const agents = [...new Set(ALL.map(s => s.agent || "?"))].sort();
  fill("#f-agent", agents.map(a => [a, a]), tr("All agents"));
  const flags = [...new Set(ALL.flatMap(s => s.status_flags))].sort();
  fill("#f-flag", flags.map(f => [f, tr(FLAG_LABEL[f] || f)]), tr("All flags"));
  const hubs = HUBS.filter(h => ALL.some(s => s.destination_hub === h));
  fill("#f-hub", hubs.map(h => [h, h]), tr("All hubs"));
  fill("#f-stage", STAGES.filter(st => ALL.some(s => s.stage === st[0])).map(st => [st[0], tr(st[1])]), tr("All stages"));
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
  else renderTable(rows);
  $("#board-view").style.display = view === "board" ? "" : "none";
  $("#table-view").style.display = view === "table" ? "" : "none";
  $("#view-board").className = "btn" + (view === "board" ? " active" : "");
  $("#view-table").className = "btn" + (view === "table" ? " active" : "");
}

function renderKpis() {
  const open = ALL.filter(s => s.is_open);
  const cnt = fn => open.filter(fn).length;
  const lv = open.reduce((t, s) => t + pm3(s), 0);
  const withVol = open.filter(s => pm3(s) > 0).length;
  const tiles = [
    ["open", "", open.length, tr("Open shipments"), "", s => s.is_open],
    ["red", "red", cnt(s => s.status_flags.includes("on_hold") || s.status_flags.includes("payment_pending")), tr("On hold / payment"), "", s => s.status_flags.includes("on_hold") || s.status_flags.includes("payment_pending")],
    ["docs", "blue", cnt(s => s.status_flags.includes("docs_incomplete")), tr("Docs incomplete"), "", s => s.status_flags.includes("docs_incomplete")],
    ["stalled", "amber", cnt(s => s.status_flags.includes("stalled")), tr("Stalled ≥ 7 days"), tr("no checklist progress"), s => s.status_flags.includes("stalled")],
    ["border", "", cnt(s => ["in_transit_to_border","customs_clearance"].includes(s.stage)), tr("At / crossing border"), "", s => ["in_transit_to_border","customs_clearance"].includes(s.stage)],
    ["mx", "green", cnt(s => ["at_hub","onward_leg","out_for_delivery"].includes(s.stage)), tr("In Mexico, delivering"), "", s => ["at_hub","onward_leg","out_for_delivery"].includes(s.stage)],
    ["lv", "", fmtN(lv), tr("m³ open"), withVol ? `${withVol} ${tr("with volume")} · ${fmtN(lv/TRUCK_M3)} ${tr("trailers of")} ${TRUCK_M3} m³` : tr("needs Remisiones sheet"), null],
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
  return `<div class="card f-${prim}${isDemo(s) ? " demo" : ""}" data-id="${esc(s.id)}">
    <div class="nm">${esc(s.customer_name)}${s.source !== "TIM" ? ` <span class="src">${esc(s.source)}</span>` : ""}${isDemo(s) ? ' <span class="src demo">DEMO</span>' : ""}</div>
    <div class="ag">${esc(s.agent || "?")}${s.reference_number ? " · " + esc(s.reference_number) : ""}</div>
    ${s.destination ? `<div class="dest">→ ${esc(s.destination)}${s.destination_hub && s.destination_hub !== "Unknown" ? ` <span style="color:#999">(${esc(s.destination_hub)})</span>` : ""}</div>` : ""}
    <div class="meta">${vol ? `<span class="tag vol">${esc(vol)}</span>` : ""}${flags.map(f => `<span class="tag ${f}">${esc(tr(FLAG_LABEL[f] || f))}</span>`).join("")}</div>
    ${s.current_step ? `<div class="step">${esc(s.current_step)}${s.days_since_progress != null ? ` · ${s.days_since_progress}d` : ""}</div>` : ""}
    ${s.steps_total ? `<div class="bar"><i style="width:${pct}%"></i></div>` : ""}
  </div>`;
}

function renderBoard(rows) {
  const showClosed = $("#f-closed").checked;
  const cols = STAGES.filter(st => (st[0] !== "closed" || showClosed) && (st[0] !== "unknown" || rows.some(s => s.stage === "unknown")));
  $("#board").innerHTML = cols.map(st => {
    const items = rows.filter(s => s.stage === st[0]);
    const lv = items.reduce((t, s) => t + pm3(s), 0);
    return `<div class="col"><h3>${tr(st[1])} <span class="n">${items.length}</span></h3>${lv ? `<div class="lv">${fmtN(lv)} m³</div>` : ""}${items.map(cardHtml).join("") || '<div style="font-size:11px;color:#aaa;text-align:center;padding:10px">—</div>'}</div>`;
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
    if (f === "stalled") detail = `${s.days_since_progress} ${tr("days without progress")} · ${s.current_step || STAGE_LABEL[s.stage]}`;
    else if (f === "docs_incomplete") detail = `${s.current_step || tr("Documents")} · ${s.days_since_progress ?? "?"}d`;
    else detail = (s.extra && s.extra.remisiones_status) || s.current_step || STAGE_LABEL[s.stage];
    items.push({s, f, detail, rank: ALERT_FLAGS.indexOf(f), days: s.days_since_progress || 0});
  });
  items.sort((a, b) => a.rank - b.rank || b.days - a.days);
  $("#cnt-alerts").textContent = items.length;
  $("#alerts").innerHTML = items.length ? items.map(it => `<div class="row" data-id="${esc(it.s.id)}">
      <div class="ico ${it.f}">${FLAG_ICON[it.f]}</div>
      <div><div class="t">${esc(it.s.customer_name)} <span style="color:#888;font-weight:500">· ${esc(it.s.agent || "?")}</span></div><div class="d">${esc(tr(FLAG_LABEL[it.f]))} — ${esc(it.detail)}</div></div>
      <div class="who">${esc((it.s.assignees || []).join(", "))}</div></div>`).join("")
    : `<div class="empty">${tr("Nothing needs attention 🎉")}</div>`;
  document.querySelectorAll("#alerts .row").forEach(el => el.onclick = () => openDrawer(el.dataset.id));
}

function renderHubs(rows) {
  const open = rows.filter(s => s.is_open);
  const known = open.filter(s => s.destination_hub && s.destination_hub !== "Unknown");
  const html = HUBS.filter(h => h !== "Unknown").map(h => {
    const items = open.filter(s => s.destination_hub === h);
    const lv = items.reduce((t, s) => t + pm3(s), 0);
    const pct = Math.min(100, Math.round(100 * lv / TRUCK_M3));
    return `<div class="hub"><div class="hn">${h}</div><div class="hb"><i class="${pct >= 85 ? "ok" : ""}" style="width:${pct}%"></i></div><div class="hv">${items.length} ${tr("shpt")} · ${fmtN(lv)} / ${TRUCK_M3} m³</div></div>`;
  }).join("");
  const unk = open.length - known.length;
  const rem = DIAG.remisiones || {};
  $("#hubs").innerHTML = html + (unk ? `<div class="note">${unk} ${tr("open shipments without a destination hub")}${rem.error ? (" " + tr("Destinations and volumes come from the Remisiones workbook — waiting on Files.Read.All access for the Graph app.")) : (" " + tr("Extend the destination → hub table for the unmapped cities."))}</div>` : "");
}

const COLS = [["customer_name","Customer"],["source","Src"],["agent","Agent"],["reference_number","Reference"],["stage","Stage"],["current_step","Current step"],["days_since_progress","Days idle"],["destination","Destination"],["destination_hub","Hub"],["planning_m3","m³"],["status_flags","Flags"],["assignees","Assigned"],["milestones.green_light","Green light"],["milestones.crossed","Crossed"],["milestones.delivered","Delivered"]];
const get = (s, k) => k.includes(".") ? k.split(".").reduce((o, p) => o && o[p], s) : s[k];
function renderTable(rows) {
  rows = [...rows].sort((a, b) => { let x = get(a, sortKey), y = get(b, sortKey); if (Array.isArray(x)) x = x.join(","); if (Array.isArray(y)) y = y.join(",");
    if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * sortDir; });
  $("#tbl thead").innerHTML = "<tr>" + COLS.map(c => `<th data-k="${c[0]}" class="${sortKey === c[0] ? "sorted" : ""}">${tr(c[1])}${sortKey === c[0] ? (sortDir > 0 ? " ▲" : " ▼") : ""}</th>`).join("") + "</tr>";
  $("#tbl tbody").innerHTML = rows.map(s => "<tr data-id=\"" + esc(s.id) + "\">" + COLS.map(c => {
    let v = get(s, c[0]);
    if (c[0] === "stage") v = tr(STAGE_LABEL[v] || v);
    else if (c[0] === "status_flags") v = (v || []).map(f => `<span class="tag ${f}">${esc(tr(FLAG_LABEL[f] || f))}</span>`).join(" ");
    else if (c[0] === "assignees") v = esc((v || []).join(", "));
    else if (c[0].startsWith("milestones")) v = fmtD(v);
    else if (c[0] === "planning_m3") v = v ? fmtN(v) : "—";
    else if (c[0] === "customer_name") v = s.url ? `<a href="${esc(s.url)}" target="_blank" onclick="event.stopPropagation()">${esc(v)}</a>` : esc(v);
    else v = esc(v ?? "—");
    return `<td>${v}</td>`; }).join("") + "</tr>").join("");
  $("#cnt-table").textContent = rows.length;
  document.querySelectorAll("#tbl th").forEach(th => th.onclick = () => { const k = th.dataset.k; if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = 1; } render(); });
  document.querySelectorAll("#tbl tbody tr").forEach(tr => tr.onclick = () => openDrawer(tr.dataset.id));
}

function openDrawer(id) {
  const s = ALL.find(x => x.id === id); if (!s) return;
  const ms = s.source !== "TIM" ? ["booked","uplift","delivered","closed"] : ["booked","docs_complete","green_light","at_border_warehouse","docs_to_broker","crossed","at_hub","delivery_scheduled","delivered","closed"];
  const ex = s.extra || {};
  $("#dbody").innerHTML = `<h2>${esc(s.customer_name)}${isDemo(s) ? ' <span class="src demo">DEMO</span>' : ""}</h2>
    <div class="sub">${s.source !== "TIM" ? `<span class="src">${esc(s.source)}</span> ` : ""}${esc(s.agent || "?")}${s.reference_number ? " · " + esc(s.reference_number) : ""}${s.url ? ` · <a href="${esc(s.url)}" target="_blank" style="color:#1967d2">${tr("open in ClickUp ↗")}</a>` : " · " + tr("Moveware job")}</div>
    <div class="meta" style="margin-bottom:14px">${s.status_flags.map(f => `<span class="tag ${f}">${esc(tr(FLAG_LABEL[f] || f))}</span>`).join(" ")}</div>
    <div class="kv">
      <b>${tr("Stage")}</b><span>${esc(STAGE_LABEL[s.stage] || s.stage)}</span>
      <b>${tr("Current step")}</b><span>${s.steps_total ? `${esc(s.current_step || "—")} (${s.steps_done}/${s.steps_total}, ${s.process_format || "?"})` : esc(s.source_status || "—") + (ex.direction ? ` · ${esc(ex.direction)}` : "") + (ex.method ? ` · ${esc(ex.method)}` : "")}</span>
      <b>${tr("Last progress")}</b><span>${s.last_progress_at ? esc(s.last_progress_at) + ` · ${s.days_since_progress} days ago` : "—"}</span>
      <b>${tr("Assigned")}</b><span>${esc((s.assignees || []).join(", ") || "—")}</span>
      <b>${tr("Origin → Dest.")}</b><span>${esc(s.origin || "?")} → ${esc(s.destination || "?")}${s.destination_hub !== "Unknown" ? ` (${esc(s.destination_hub)} hub)` : ""}</span>
      <b>${tr("Volume")}</b><span>${s.lift_vans ? s.lift_vans + " lift van(s) · " : ""}${s.u_boxes ? s.u_boxes + " U-Box(es) · " : ""}${s.volume_m3 ? s.volume_m3 + " m³ · " : ""}${esc(ex.volume_text || "")}${!(s.lift_vans || s.u_boxes || s.volume_m3 || ex.volume_text) ? "—" : ""}</span>
      <b>${tr("Sale value")}</b><span>${money(ex.sale_value)}</span>
      ${s.weight ? `<b>${tr("Weight")}</b><span>${s.weight} kg</span>` : ""}
      ${s.source !== "TIM" ? "" : `<b>Remisiones</b><span>${ex.remisiones_block ? esc(ex.remisiones_block) + (ex.remisiones_week ? " · " + esc(ex.remisiones_week) : "") + (ex.remisiones_status ? "<br>" + esc(ex.remisiones_status) : "") : tr("not on the sheet")}</span>`}
    </div>
    <div class="section" style="margin-top:0">${tr("Milestones")}</div>
    <div class="ms">${ms.map(m => `<div class="${s.milestones && s.milestones[m] ? "done" : "todo"}"><span>${m.replace(/_/g, " ")}</span><span>${s.milestones && s.milestones[m] ? esc(s.milestones[m]) : "—"}</span></div>`).join("")}</div>`;
  $("#drawer").classList.add("open"); $("#overlay").classList.add("open");
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#overlay").classList.remove("open"); }

["#f-source","#f-agent","#f-flag","#f-hub","#f-stage"].forEach(id => $(id).onchange = () => { render(); renderPlan(); });
$("#f-q").oninput = render;
$("#f-closed").onchange = () => load(false);
$("#clear").onclick = () => { ["#f-source","#f-agent","#f-flag","#f-hub","#f-stage"].forEach(id => $(id).value = ""); $("#f-q").value = ""; kpiSel = null; render(); };
$("#view-board").onclick = () => { view = "board"; render(); };
$("#view-table").onclick = () => { view = "table"; render(); };
$("#refresh").onclick = () => load(true);
$("#dclose").onclick = closeDrawer; $("#overlay").onclick = closeDrawer;
$("#lang-en").onclick = () => setLang("en"); $("#lang-es").onclick = () => setLang("es");
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
applyStaticLang(); load(false);
setInterval(() => load(false), 5 * 60 * 1000);
</script>
</body>
</html>
"""
