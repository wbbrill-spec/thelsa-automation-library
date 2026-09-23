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
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-bottom: 8px; }
.metric { background: #fff; border: 1px solid #e8e8e8; border-radius: 12px; padding: 12px 14px; }
.metric .ml { font-size: 11px; color: #666; font-weight: 700; text-transform: uppercase; letter-spacing: .02em; }
.metric .mv { font-size: 26px; font-weight: 800; margin: 4px 0 2px; display: flex; align-items: baseline; gap: 8px; }
.metric .ms { font-size: 11px; color: #888; line-height: 1.4; }
.metric .delta { font-size: 12px; font-weight: 700; }
.metric .delta.up { color: #1e7e34; }
.metric .delta.down { color: #c0392b; }
.tag.no_delivery_date, .tag.no_uplift_date { background: #fdf2f8; color: #9d174d; }
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
/* "These travel together" — Bill, consolidation meeting 23 Sep (D32). */
.load .row .pick { margin: 2px 2px 0 0; cursor: pointer; flex: 0 0 auto; width: 15px; height: 15px; accent-color: #c0392b; }
.load .row.picked { background: #fff6f4; }
#picker { position: fixed; left: 50%; transform: translateX(-50%); bottom: -80px; z-index: 22;
          background: #24292f; color: #fff; border-radius: 999px; padding: 10px 14px 10px 18px;
          display: flex; align-items: center; gap: 12px; font-size: 13px;
          box-shadow: 0 8px 28px rgba(0,0,0,.28); transition: bottom .18s; }
#picker.on { bottom: 18px; }
#picker button { border: 0; border-radius: 999px; padding: 7px 14px; font-size: 13px; font-weight: 700; cursor: pointer; }
#picker .go { background: #c0392b; color: #fff; }
#picker .clr { background: transparent; color: #bbb; font-weight: 600; }
#notice { position: fixed; inset: 0; z-index: 30; display: none; align-items: center; justify-content: center;
          background: rgba(0,0,0,.45); padding: 20px; }
#notice.on { display: flex; }
#notice .card { background: #fff; border-radius: 12px; max-width: 760px; width: 100%; max-height: 88vh;
                overflow-y: auto; padding: 22px; }
#notice h3 { font-size: 17px; font-weight: 800; margin-bottom: 4px; }
#notice .sub { color: #666; font-size: 12.5px; margin-bottom: 12px; }
#notice pre { white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
              font-size: 12px; background: #f6f7f9; border-radius: 8px; padding: 12px; line-height: 1.5; }
#notice .to { font-size: 12.5px; color: #333; margin-bottom: 10px; }
#notice .acts { display: flex; gap: 8px; margin-top: 14px; align-items: center; }
#notice .warn { background: #fff8ec; border-radius: 8px; padding: 9px 12px; font-size: 12.5px; color: #92400e; margin-bottom: 12px; }
.load .row .who { flex: 1; min-width: 0; }
.load .row .m3 { white-space: nowrap; color: #333; font-weight: 600; }
.load .adv { font-size: 12px; color: #92400e; background: #fff8ec; border-radius: 8px; padding: 8px 10px; margin-top: 8px; line-height: 1.45; }
.load .adv.info, .adv.info { color: #1e4d7b; background: #eef5fc; border-radius: 8px; padding: 8px 10px; font-size: 12px; line-height: 1.45; }
.card .cons { font-size: 10px; font-weight: 700; color: #1e7e34; background: #e6f4ea; border-radius: 6px; padding: 3px 6px; margin-top: 4px; }
.leghead { grid-column: 1 / -1; font-size: 12px; font-weight: 800; letter-spacing: .02em; color: #333; text-transform: uppercase; margin: 6px 0 -2px; display: flex; align-items: baseline; gap: 10px; }
.leghead .sub { font-weight: 500; text-transform: none; letter-spacing: 0; color: #777; font-size: 11px; }
.load.grouped { border-color: #1e7e34; box-shadow: 0 0 0 1px #e6f4ea inset; }
.tag.leg { background: #eef1f5; color: #333; }
.tag.alone { background: #e8f0fe; color: #1967d2; }
.tag.detour { background: #f3e8ff; color: #7b1fa2; }
.tag.hired { background: #fff4e5; color: #b45309; }
.tag.grp { background: #e6f4ea; color: #1e7e34; }
.planchg { font-size: 12px; padding: 6px 0; border-bottom: 1px solid #f0f0f0; display: flex; gap: 10px; }
.planchg:last-child { border-bottom: 0; }
.planchg .k { width: 110px; font-weight: 700; flex: none; }
.planchg .k.vanished { color: #c0392b; }
.planchg .k.date_changed { color: #b45309; }
.planchg .k.unit_changed { color: #1967d2; }
.planchg .at { color: #999; white-space: nowrap; }
.load .rs { color: #999; font-size: 11px; }
.trucks { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; margin-top: 10px; }
.truck { background: #fff; border: 1px solid #e8e8e8; border-radius: 10px; padding: 10px 12px; font-size: 12px; }
.truck b { font-size: 13px; }
.truck .sp { color: #1e7e34; font-weight: 700; }
.truck .bar { height: 8px; background: #f0f1f4; border-radius: 4px; overflow: hidden; margin: 6px 0 4px; }
.truck .bar i { display: block; height: 100%; background: #1967d2; }
.tag.truck { background: #e8f0fe; color: #1967d2; }
.tag.truckfit { background: #e6f4ea; color: #1e7e34; }
.tag.awaiting_truck { background: #fff4e5; color: #b45309; }
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
.curtog button { padding: 3px 10px; }
.fxnote { font-size: 11px; color: #8a8f98; margin: 0 0 10px; }
.fxnote b { color: #555; font-weight: 600; }
.money { font-variant-numeric: tabular-nums; white-space: nowrap; }
.money .cur { color: #8a8f98; font-size: .85em; margin-left: 2px; }
.money.asis { color: #8a6d3b; }
.prov { color: #8a6d3b; border-bottom: 1px dotted #c9a227; cursor: help; }
.corp { display: inline-block; background: #eef2ff; color: #3b4cca; border-radius: 9px;
        padding: 1px 7px; font-size: 11px; font-weight: 600; }
.tag.lv { background: #fff4e5; color: #9a5b00; }
.corp.unnamed { background: #f3f4f6; color: #8a8f98; font-style: italic; }
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
    <span id="src-trs" class="pill" style="display:none">TRS · —</span>
    <span id="src-sit" class="pill" style="display:none">Plan de Viajes · —</span>
    <span id="asof">—</span>
    <span class="langtog"><button id="lang-en" class="on">EN</button><button id="lang-es">ES</button></span>
    <span class="langtog curtog"><button id="cur-mxn">Pesos</button><button id="cur-usd" class="on">USD · books</button></span>
    <a href="/" id="lib-link">← Library</a>
  </div>
</header>
<div id="demobar"><span id="demotext"></span><a class="x" id="demo-off" href="?demo=0">turn demo data off</a></div>
<main>
  <div class="toolbar">
    <select id="f-source"><option value="">All sources</option><option value="TIM">TIM (ClickUp)</option><option value="TMS">TMS (Moveware)</option><option value="TRS">TRS (domestic)</option></select>
    <select id="f-agent"><option value="">All agents</option></select>
    <select id="f-flag"><option value="">All flags</option></select>
    <select id="f-hub"><option value="">All hubs</option></select>
    <select id="f-stage"><option value="">All stages</option></select>
    <input id="f-q" type="search" placeholder="Search customer / reference / destination…">
    <button class="btn" id="clear">Clear</button>
    <span class="spacer"></span>
    <label style="font-size:12px;color:#666;display:flex;align-items:center;gap:6px;white-space:nowrap"><input type="checkbox" id="f-closed"> include completed</label>
    <button class="btn" id="view-ship">Shipments</button>
    <button class="btn" id="view-cons">Consolidation</button>
    <button class="btn" id="view-table">Table</button>
    <button class="btn primary" id="refresh">↻ Refresh</button>
  </div>

  <div id="ship-view">
  <div class="kpis" id="kpis"></div>
    <div class="section"><span data-i18n="Pipeline">Pipeline</span> <span class="cnt" id="cnt-board">0</span></div>
    <div class="board" id="board"></div>

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

  <div id="cons-view" style="display:none">
    <div class="section"><span data-i18n="Is consolidation improving?">Is consolidation improving?</span></div>
    <div class="metrics" id="metrics"></div>
    <div class="note" id="metrics-note"></div>

    <div class="plan-head">
      <div class="section"><span data-i18n="Suggested loads">Suggested loads</span> <span class="cnt" id="cnt-loads">0</span></div>
      <span class="plan-stats" id="plan-stats"></span>
      <div class="fxnote" id="fxnote"></div>
      <span class="spacer"></span>
      <button class="btn" id="plan-draft">✉ Draft today's load email</button>
      <span class="plan-stats" id="plan-msg"></span>
    </div>
    <div id="grouphead" class="plan-head" style="display:none">
      <div class="section"><span data-i18n="Already consolidated">Already consolidated</span> <span class="cnt" id="cnt-groups">0</span></div>
      <span class="plan-stats" data-i18n="Trucks a coordinator is already filling — add freight to these before booking another.">Trucks a coordinator is already filling — add freight to these before booking another.</span>
    </div>
    <div class="loads" id="groups"></div>
    <div class="loads" id="loads"></div>
    <div id="coming" class="note" style="display:none"></div>
    <div id="planchangehead" class="plan-head" style="display:none">
      <div class="section"><span data-i18n="Plan de Viajes changes">Plan de Viajes changes</span> <span class="cnt" id="cnt-planchanges">0</span></div>
      <span class="plan-stats" data-i18n="Services that disappeared or moved date since the last republication.">Services that disappeared or moved date since the last republication.</span>
    </div>
    <div id="planchanges" class="panel" style="display:none"></div>
  <div id="sparehead" class="plan-head" style="display:none">
    <div class="section"><span data-i18n="Trucks with space">Trucks with space</span> <span class="cnt" id="cnt-trucks">0</span></div>
    <span class="plan-stats" id="spare-stats"></span>
  </div>
  <div class="trucks" id="trucks"></div>

  </div>

  <div id="table-view" style="display:none">
    <div class="section"><span data-i18n="All shipments">All shipments</span> <span class="cnt" id="cnt-table">0</span></div>
    <div class="tblwrap"><table id="tbl"><thead></thead><tbody></tbody></table></div>
  </div>
</main>

<div id="overlay"></div>
<div id="drawer"><button class="close" id="dclose">×</button><div id="dbody"></div></div>

<!-- "These travel together" (D32, 23 Sep). Tick the boxes on a load, get the
     message that tells Sara and TRS. Drafts only — a person sends it. -->
<div id="picker">
  <span id="pick-count">0</span>
  <button class="go" id="pick-go" data-i18n="Prepare consolidation notice">Prepare consolidation notice</button>
  <button class="clr" id="pick-clear" data-i18n="Clear">Clear</button>
</div>
<div id="notice">
  <div class="card">
    <h3 data-i18n="These travel together">These travel together</h3>
    <div class="sub" id="notice-sub"></div>
    <div class="warn" data-i18n="Nothing has been sent. Read it, then send it yourself — this message asks TRS to hold space on a truck.">Nothing has been sent. Read it, then send it yourself — this message asks TRS to hold space on a truck.</div>
    <div class="to" id="notice-to"></div>
    <pre id="notice-body"></pre>
    <div class="note" id="notice-save" style="margin-top:8px"></div>
    <div class="acts">
      <button class="btn" id="notice-copy" data-i18n="Copy">Copy</button>
      <button class="btn" id="notice-close" data-i18n="Close">Close</button>
    </div>
  </div>
</div>

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
  awaiting_booking:"Awaiting booking", awaiting_truck:"No truck assigned yet",
  no_delivery_date:"No delivery date in Moveware", no_uplift_date:"No pack/load date in Moveware"};
const FLAG_ICON = {stalled:"⏳", docs_incomplete:"📄", on_hold:"⛔", payment_pending:"💳", in_storage:"🏬", certificate_pending:"📝", in_progress:"▶", window_risk:"⚠️", unresponsive:"📵", docs_pending:"📄", visa_pending:"🛂", awaiting_green_light:"🟢", awaiting_booking:"📅", awaiting_truck:"🚚", no_delivery_date:"📆", no_uplift_date:"📆"};
const ALERT_FLAGS = ["on_hold","payment_pending","window_risk","unresponsive","docs_incomplete","docs_pending","certificate_pending","visa_pending","no_delivery_date","no_uplift_date","stalled"];
const HUBS = ["Monterrey","Mexico City","Guadalajara","Querétaro","Mérida","Torreón","Unknown"];
const TRUCK_LV = 13;
const TRUCK_M3 = 88;   // 53' trailer ≈ 20,000 lb HHG at 6.5 lb/cuft ≈ 3,077 cuft ≈ 88 m³ (Bill, 2026-09-09)
const pm3 = s => s.planning_m3 || 0;

let ALL = [], STATUS = {}, DIAG = {}, view = "ship", sortKey = "customer_name", sortDir = 1, kpiSel = null;
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
  // ── U-Box ──
  "U-Box job — number of boxes not on record yet": "Servicio U-Box — aún no se registra cuántos contenedores",
  "Each: 7.3 m³ / 257 cu ft usable · max 2,000 lb contents · 96 × 60 × 90 in outside":
    "Cada uno: 7.3 m³ / 257 pies³ útiles · máx. 2,000 lb de contenido · 96 × 60 × 90 pulg. exterior",
  // ── lift vans ──
  "lift vans": "lift vans", "gross": "bruto", "positions free": "posiciones libres",
  "US Embassy / Consulate — ships in lift vans; Moveware volume is gross":
    "Embajada / Consulado de EE. UU. — viaja en lift vans; el volumen en Moveware es bruto",
  // ── money / FX ──
  "Pesos": "Pesos", "USD · books": "USD · libros",
  "Revenue": "Ingresos", "revenue": "ingresos", "Invoiced": "Facturado",
  "invoiced": "facturado", "outstanding": "pendiente de cobro",
  "yes": "sí", "not yet": "aún no", "show invoice": "ver factura",
  "Invoiced in Moveware": "Facturado en Moveware",
  "Payment status is not held in Moveware.": "El estado de pago no se lleva en Moveware.",
  "checking…": "consultando…", "no invoice raised yet": "aún no se ha emitido factura",
  "no invoice date": "sin fecha de factura",
  "Client": "Cliente", "private / consumer": "particular", "private": "particular",
  "Corporate account": "Cuenta corporativa",
  "— placeholder, no account named in Moveware": "— genérico, sin cuenta nombrada en Moveware",
  "Agents": "Agentes", "booking": "reserva", "origin": "origen", "destination": "destino",
  "Booked": "Registrado", "As booked": "Tal como se registró", "rate": "tipo",
  "provisional rate": "tipo provisional", "no month on file": "sin mes registrado",
  "pack / load date": "fecha de carga", "delivery date": "fecha de entrega",
  "booked date": "fecha de alta",
  "No currency on this file — shown as booked": "Sin moneda en el expediente — se muestra tal cual",
  "Mixed months — open the load to see each file": "Meses distintos — abre la carga para ver cada expediente",
  "Rates on file through": "Tipos registrados hasta",
  "Pesos as booked. USD files converted at each month's accounting rate.":
    "Pesos tal como se registraron. Los expedientes en USD se convierten al tipo contable de cada mes.",
  "USD at each month's own accounting rate. Peso files converted; USD files shown as booked.":
    "USD al tipo contable de cada mes. Los expedientes en pesos se convierten; los de USD se muestran tal cual.",
  "TIM + TMS": "TIM + TMS", "TIM (ClickUp)": "TIM (ClickUp)", "TMS (Moveware)": "TMS (Moveware)",
  "All agents": "Todos los agentes", "All flags": "Todas las alertas", "All hubs": "Todos los hubs",
  "All stages": "Todas las etapas",
  "Search customer / reference / destination…": "Buscar cliente / referencia / destino…",
  "Clear": "Limpiar", "include completed": "incluir completados",
  "Board": "Tablero", "Table": "Tabla", "↻ Refresh": "↻ Actualizar",
  "Pipeline": "Flujo", "Suggested loads": "Cargas sugeridas",
  // Two-stage imports, detours, exports and existing consolidations
  // (Edgar / Fernanda, 22 Sep 2026).
  "Stage 1 — border crossing (McAllen → Monterrey)": "Etapa 1 — cruce fronterizo (McAllen → Monterrey)",
  "Stage 2 — onward from Monterrey": "Etapa 2 — distribución desde Monterrey",
  "Export": "Exportación", "Domestic Mexico": "Nacional México",
  "trailer": "tráiler", "trailers": "tráileres", "avg fill": "llenado prom.",
  "Ships on its own": "Sale por su cuenta", "drops at": "baja en",
  // "These travel together" (D32) and the port of entry (D1/D17), 23 Sep.
  "These travel together": "Estos viajan juntos",
  "shipment selected": "embarque seleccionado", "shipments selected": "embarques seleccionados",
  "Prepare consolidation notice": "Preparar aviso de consolidación",
  "Clear": "Limpiar", "Preparing…": "Preparando…", "Copy": "Copiar", "Copied": "Copiado",
  "Close": "Cerrar", "To": "Para", "Subject": "Asunto",
  "Select the text and copy it": "Selecciona el texto y cópialo",
  "Could not prepare the notice.": "No se pudo preparar el aviso.",
  "of a 53 ft trailer": "de un tráiler de 53'", "free": "libres",
  "Separately": "Por separado", "together": "juntos", "saved": "ahorro",
  "Nothing has been sent. Read it, then send it yourself — this message asks TRS to hold space on a truck.":
    "No se ha enviado nada. Léelo y envíalo tú — este mensaje pide a TRS que reserve espacio en un camión.",
  "port of entry": "puerto de entrada", "assumed": "supuesto",
  "no port recorded": "sin puerto registrado",
  "Crossing at McAllen": "Cruzando por McAllen", "still at Laredo": "aún por Laredo",
  "Laredo": "Laredo", "of": "de", "recorded on the file": "registrados en el expediente",
  "needs a hired 53' trailer": "requiere tráiler de 53' contratado",
  "Already consolidated": "Ya consolidado",
  "Trucks a coordinator is already filling — add freight to these before booking another.":
    "Camiones que una coordinadora ya está llenando — súmale carga antes de contratar otro.",
  "file": "expediente", "files": "expedientes", "consolidated": "consolidados",
  "Could still join this truck": "Aún podría subir a este camión", "note": "nota",
  "Plan de Viajes changes": "Cambios en el Plan de Viajes",
  "Services that disappeared or moved date since the last republication.":
    "Servicios que desaparecieron o cambiaron de fecha desde la última publicación.",
  "disappeared": "desapareció", "date changed": "cambió de fecha", "truck changed": "cambió de unidad",
  // Tabs and the consolidation scoreboard (Bill, 23 Sep)
  "Shipments": "Envíos", "Consolidation": "Consolidación",
  "Is consolidation improving?": "¿Está mejorando la consolidación?",
  "Trailer utilisation": "Uso del tráiler", "across": "en",
  "load": "carga", "loads": "cargas",
  "Shipments per load": "Envíos por carga", "files on": "expedientes en", "trucks": "camiones",
  "Trucks carrying one file": "Camiones con un solo expediente",
  "of the loads that could be shared": "de las cargas que podrían compartirse",
  "exports excluded — they ship alone by rule":
    "exportaciones excluidas — salen solas por regla",
  "Trucks avoided": "Camiones evitados", "assumed saving": "ahorro estimado",
  "every load is carrying a single file": "cada carga lleva un solo expediente",
  "Paid-for empty space": "Espacio vacío ya pagado",
  "already heading to Mexico with room on board": "ya en ruta a México con espacio disponible",
  "Savings assume": "El ahorro supone", "Measured since": "Medido desde", "days": "días",
  "First day of measurement": "Primer día de medición",
  "the trend appears once there are two days on record.":
    "la tendencia aparece cuando haya dos días registrados.",
  "No loads planned today, so there is nothing to measure.":
    "Hoy no hay cargas planeadas, así que no hay nada que medir.",
  "No delivery date in Moveware": "Falta fecha de entrega en Moveware",
  "No pack/load date in Moveware": "Falta fecha de carga/empaque en Moveware",
  "with": "con", "the note is on this file": "la nota está en este expediente",
  "Not for consolidation": "No consolidar", "Moving with": "Se mueve con",
  "Named in the note but not found on the board": "Mencionados en la nota pero no encontrados en el tablero",
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
  "Trucks with space": "Camiones con espacio", "On a truck already going": "En un camión que ya va",
  "free": "libres", "truck": "camión", "trucks": "camiones",
  "of empty space already heading to Mexico in the next 14 days": "de espacio vacío que ya va a México en los próximos 14 días",
  "Mexican leg": "Tramo en México", "loads": "carga", "unloads": "descarga",
  "No truck assigned yet": "Sin camión asignado",
};
let LANG = (function(){ try { return localStorage.getItem("cb_lang") || "en"; } catch(e){ return "en"; } })();
function tr(s){ if (LANG !== "es" || s == null) return s; return (s in ES) ? ES[s] : s; }
function applyStaticLang(){
  document.documentElement.lang = LANG;
  $("#lang-en").classList.toggle("on", LANG === "en");
  $("#lang-es").classList.toggle("on", LANG === "es");
  $("#cur-mxn").textContent = tr("Pesos");
  $("#cur-usd").textContent = tr("USD · books");
  if (typeof applyCurrency === "function") applyCurrency();
  const H = $(".brand h1"); if (H) H.innerHTML = LANG === "es" ? 'Envíos <span>Transfronterizos</span>' : 'Cross-Border <span>Shipments</span>';
  const lib = $("#lib-link"); if (lib) lib.textContent = tr("← Library");
  $("#f-agent").querySelector('option[value=""]') && ($("#f-agent").querySelector('option[value=""]').textContent = tr("All agents"));
  $("#f-flag").querySelector('option[value=""]') && ($("#f-flag").querySelector('option[value=""]').textContent = tr("All flags"));
  $("#f-hub").querySelector('option[value=""]') && ($("#f-hub").querySelector('option[value=""]').textContent = tr("All hubs"));
  $("#f-stage").querySelector('option[value=""]') && ($("#f-stage").querySelector('option[value=""]').textContent = tr("All stages"));
  $("#f-q").placeholder = tr("Search customer / reference / destination…");
  $("#clear").textContent = tr("Clear");
  const inc = document.querySelector('label input#f-closed'); if (inc && inc.parentNode) inc.parentNode.lastChild.textContent = " " + tr("include completed");
  $("#view-ship").textContent = tr("Shipments"); $("#view-cons").textContent = tr("Consolidation");
  $("#view-table").textContent = tr("Table");
  $("#refresh").textContent = tr("↻ Refresh");
  $("#plan-draft").textContent = tr("✉ Draft today's load email");
  if ($("#alert-draft")) $("#alert-draft").textContent = tr("✉ Draft owner alerts");
  document.querySelectorAll("[data-i18n]").forEach(el => el.textContent = tr(el.getAttribute("data-i18n")));
}
function setLang(l){ LANG = l; try { localStorage.setItem("cb_lang", l); } catch(e){}
  applyStaticLang(); buildFilters(); render(); renderPlan(); renderDemoBar(DIAG.demo); }


// ── money ────────────────────────────────────────────────────────────────────
// Thelsa books some files in MXN and some in USD. Nothing here ever adds two
// currencies together without converting first, and the conversion uses the
// books rate for the month the file earned in — pack/load date on exports,
// delivery date on imports (Bill, 2026-09-16), falling back to the booked date
// when neither is filled. FX comes from the server (crossborder/fx.py) so the
// page and the API can never disagree about a rate.
const BASIS_LABEL = { pack: "pack / load date", delivery: "delivery date", booked: "booked date" };
let CUR = (function(){ try { return localStorage.getItem("cb_cur") || "USD"; } catch(e){ return "USD"; } })();
let FX = { table: {}, base: "MXN", current_rate: null, last_month: "", provisional: false };

function fxRate(month){
  const months = Object.keys(FX.table || {}).sort();
  if (!months.length) return null;
  if (month && FX.table[month] != null) return FX.table[month];
  if (!month) return FX.table[months[months.length - 1]];
  if (month > months[months.length - 1]) return FX.table[months[months.length - 1]];
  if (month < months[0]) return FX.table[months[0]];
  const earlier = months.filter(m => m < month);
  return FX.table[earlier.length ? earlier[earlier.length - 1] : months[0]];
}
// True when we had to carry a rate forward rather than use the month's own.
function fxProvisional(month){
  const months = Object.keys(FX.table || {}).sort();
  return !months.length || !month || FX.table[month] == null;
}
function convert(amount, from, month){
  if (amount == null || amount === "" || !from) return null;
  if (from === CUR) return Number(amount);
  const rate = fxRate(month);
  if (rate == null) return null;
  return from === "USD" ? Number(amount) * rate : Number(amount) / rate;
}
function fmtMoney(amount, currency, month, opts){
  opts = opts || {};
  if (amount == null || amount === "") return opts.dash === false ? "" : "—";
  // No currency on the record means we must not convert it — showing an
  // unconverted figure is honest, quietly treating MXN as USD is not.
  if (!currency) {
    return '<span class="money asis" title="' + tr("No currency on this file — shown as booked") + '">'
      + Math.round(Number(amount)).toLocaleString() + '</span>';
  }
  const value = convert(amount, currency, month);
  if (value == null) return '<span class="money asis">' + Math.round(Number(amount)).toLocaleString()
      + '<span class="cur">' + currency + '</span></span>';
  const text = "$" + Math.round(value).toLocaleString() + (CUR === "MXN" ? " MXN" : "");
  const converted = currency !== CUR;
  const prov = converted && fxProvisional(month);
  const title = converted
    ? (tr("Booked") + " " + Math.round(Number(amount)).toLocaleString() + " " + currency
       + " · " + (month ? month : tr("no month on file")) + " @ " + (fxRate(month) || "—")
       + (prov ? " · " + tr("provisional rate") : ""))
    : tr("As booked");
  return '<span class="money' + (prov ? " prov" : "") + '" title="' + title.replace(/"/g, "&quot;") + '">' + text + '</span>';
}
// Several currencies on one load: convert each bucket at its own month's rate.
function fmtMoneyBuckets(buckets){
  if (!buckets || !buckets.length) return "";
  let total = 0, anyProv = false, ok = true, parts = [];
  buckets.forEach(b => {
    parts.push(Math.round(b.amount).toLocaleString() + " " + b.currency);
    const months = b.months && b.months.length ? b.months : [b.month];
    if (b.currency !== CUR && months.length > 1) { ok = false; return; }
    const v = convert(b.amount, b.currency, b.month || months[0] || "");
    if (v == null) { ok = false; return; }
    if (b.currency !== CUR && fxProvisional(b.month || months[0] || "")) anyProv = true;
    total += v;
  });
  if (!ok) return '<span class="money asis" title="' + tr("Mixed months — open the load to see each file") + '">'
    + parts.join(" + ") + "</span>";
  return '<span class="money' + (anyProv ? " prov" : "") + '" title="' + parts.join(" + ").replace(/"/g, "&quot;") + '">'
    + "$" + Math.round(total).toLocaleString() + (CUR === "MXN" ? " MXN" : "") + "</span>";
}
function applyCurrency(){
  $("#cur-mxn").classList.toggle("on", CUR === "MXN");
  $("#cur-usd").classList.toggle("on", CUR === "USD");
  const note = $("#fxnote");
  if (note) {
    note.innerHTML = CUR === "MXN"
      ? tr("Pesos as booked. USD files converted at each month's accounting rate.")
      : tr("USD at each month's own accounting rate. Peso files converted; USD files shown as booked.")
        + (FX.last_month ? " <b>" + tr("Rates on file through") + " " + FX.last_month + "</b>" : "");
  }
}
function setCur(c){ CUR = c; try { localStorage.setItem("cb_cur", c); } catch(e){}
  applyCurrency(); render(); renderPlan(); }


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
  if (j.fx && j.fx.table) { FX = j.fx; applyCurrency(); }
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
  if (trsN) { $("#src-trs").style.display = ""; $("#src-trs").textContent = `TRS · ${trsN}`; $("#src-trs").className = "pill ok"; }
  else $("#src-trs").style.display = "none";
  const sd = DIAG.sit || {};
  if (sd.trips) { $("#src-sit").style.display = ""; $("#src-sit").textContent = `Plan de Viajes · ${sd.matched || 0}/${sd.plans || 0}`;
    $("#src-sit").className = "pill ok"; $("#src-sit").title = `${sd.trips} trips, ${sd.fleet} units, ${sd.trucks} trucks in the horizon`; }
  else if (sd.error) { $("#src-sit").style.display = ""; $("#src-sit").textContent = "Plan de Viajes · error"; $("#src-sit").className = "pill warn"; $("#src-sit").title = sd.error; }
  else $("#src-sit").style.display = "none";
  const rem = DIAG.remisiones || {};
  if (rem.error) { $("#src-rem").textContent = "Remisiones · no access"; $("#src-rem").className = "pill warn"; $("#src-rem").title = rem.error; }
  else if (rem.matched != null) { $("#src-rem").textContent = `Remisiones · ${rem.matched} matched (${rem.week || "latest"})`; $("#src-rem").className = "pill ok"; }
  renderDemoBar(DIAG.demo);
  buildFilters(); render();
  loadPlan();
  loadPlanChanges();
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
    ? `⚠ DATOS DE DEMOSTRACIÓN — ${dg.count} envíos simulados (${dg.tms} TMS, ${dg.trs} TRS domésticos)${dg.mode === "only" ? "; los datos reales están ocultos" : ` junto a ${real} reales`}. No son de ClickUp, Moveware ni TRS. Los borradores de correo están desactivados.`
    : `⚠ DEMO DATA — ${dg.count} simulated shipments (${dg.tms} TMS, ${dg.trs} domestic TRS)${dg.mode === "only" ? "; live data hidden" : `, alongside ${real} real ones`}. Not from ClickUp, Moveware or TRS. Email drafting is disabled.`;
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
  const legStats = Object.fromEntries((p.legs || []).map(g => [g.leg, g]));
  let lastLeg = null;
  $("#loads").innerHTML = loads.length ? loads.map((l, n) => {
    const pct = Math.min(100, l.fill_pct);
    const cls = l.fill_pct >= 85 ? "ok" : (l.light ? "lt" : "");
    const opp = oppByLane[l.lane];
    // An import is planned twice — the crossing, then the truck out of the hub.
    // The heading is what stops the two being read as one list of trailers.
    let head = "";
    if (l.leg && l.leg !== lastLeg) {
      lastLeg = l.leg;
      const g = legStats[l.leg];
      head = `<div class="leghead">${esc(tr(l.leg_label || l.leg))}${g ? `<span class="sub">${g.trailers} ${g.trailers === 1 ? tr("trailer") : tr("trailers")} · ${g.shipments} ${tr("shpt")} · ${tr("avg fill")} ${g.avg_fill_pct}%</span>` : ""}</div>`;
    }
    return `${head}<div class="load ${l.light ? "light" : ""}">
      <h4>${esc(l.lane)} ${l.fill_pct >= 85 ? `<span class="tag full">${tr("Full")}</span>` : ""}${l.light && !l.ships_alone ? `<span class="tag light">${tr("Running light")}</span>` : ""}${l.ships_alone ? `<span class="tag alone">${tr("Ships on its own")}</span>` : ""}${(l.detour_stops || []).length ? `<span class="tag detour">${tr("drops at")} ${esc(l.detour_stops.join(", "))}</span>` : ""}${l.u_boxes && !l.thelsa_truck_ok ? `<span class="tag hired">${tr("needs a hired 53' trailer")}</span>` : ""}${l.cross_silo ? '<span class="tag xs">TIM + TMS</span>' : ""}${l.window_risk.length ? `<span class="tag risk">${l.window_risk.length} ${tr("window risk")}</span>` : ""}${l.anchors > 1 ? `<span class="tag anchor">${tr("2 FTL jobs share")}</span>` : ""}</h4>
      <div class="fillbar"><i class="${cls}" style="width:${pct}%"></i></div>
      <div class="fl"><span>${l.lift_vans
          ? `<b>${l.lift_vans} / ${l.lift_van_positions} ${tr("lift vans")}</b> · ${l.m3} m³ ${tr("gross")} · ${l.fill_pct}%${l.free_lift_van_positions ? ` · ${l.free_lift_van_positions} ${tr("positions free")}` : ""}`
          : l.u_boxes
          ? `<b>${l.u_boxes} / ${l.u_box_positions} U-Box</b> · ${l.m3} m³ · ${l.fill_pct}%${l.free_u_box_positions ? ` · ${l.free_u_box_positions} ${tr("positions free")}` : ""}`
          : `${l.m3} / ${l.truck_m3} m³ · ${l.fill_pct}%`}${l.kg ? ` · ${fmtN(l.kg)} kg` : ""}${
        (l.revenue || []).length ? ` · ${tr("revenue")} ${fmtMoneyBuckets(l.revenue)}` : ""}</span><span>${l.depart_by ? tr("depart by") + " " + fmtD(l.depart_by) : ""}</span></div>
      ${l.shipments.map(it => `<div class="row" data-id="${esc(it.id)}"><input type="checkbox" class="pick" data-id="${esc(it.id)}" data-lane="${esc(l.lane)}" title="${tr("These travel together")}"><div class="who"><b>${esc(it.customer)}</b>${it.anchor ? ' <span class="tag anchor">anchor</span>' : ""}${it.corporate_account ? ` <span class="corp">${esc(it.corporate_account)}</span>` : ""}${it.ubox ? ` <span class="tag lv">U-Box${it.u_boxes ? " ×" + it.u_boxes : ""}</span>` : ""}${it.us_diplomatic ? ` <span class="tag lv" title="${tr("US Embassy / Consulate — ships in lift vans; Moveware volume is gross")}">${tr("lift vans")}</span>` : ""}${l.window_risk.includes(it.id) ? ' <span class="tag risk">by ' + fmtD(it.deadline) + '</span>' : ""}<br><span class="rs">${esc(it.source)} · ${esc(it.agent || "")}${it.reference ? " · " + esc(it.reference) : ""} · → ${esc(it.destination || "?")}${it.service ? " · " + esc(it.service) : ""}</span></div><div class="m3">${it.lift_vans ? `<b>${it.lift_vans} LV</b><br><span class="rs">${it.m3} m³ ${tr("gross")}</span>` : `${it.m3} m³`}${it.revenue != null ? `<br><span class="rs">${fmtMoney(it.revenue, it.revenue_currency, it.revenue_month)}</span>` : ""}</div></div>`).join("")}
      ${(l.trucks||[]).length ? `<div class="fl" style="margin-top:6px"><span>${tr("On a truck already going")}: ${l.trucks.map(t => `<span class="tag ${t.fits?"truckfit":"truck"}" title="${esc(t.driver||"")}">${esc(t.unit)} · ${fmtD(t.date)} · ${t.spare_m3} m³ ${tr("free")}</span>`).join(" ")}</span></div>` : ""}
      ${l.pairing_advice ? `<div class="adv info">${esc(l.pairing_advice)}</div>` : ""}
      ${opp && opp.advice && !l.ships_alone ? `<div class="adv">${esc(opp.advice)}</div>` : ""}
    </div>`; }).join("") : `<div class="empty">${p.error ? "" : tr("No consolidatable shipments are ready right now.")}</div>`;
  renderGroups(p);
  renderMetrics(p);
  document.querySelectorAll("#loads .row, #groups .row").forEach(el => el.onclick = () => openDrawer(el.dataset.id));
  wirePicker();
  const cb = p.coming_by_lane || {}; const lanes = Object.keys(cb).filter(k => !src || cb[k].some(i => i.source === src));
  const ub = p.unsized_by_lane || {}; const ulanes = Object.keys(ub).filter(k => !src || ub[k].some(i => i.source === src));
  const parts = [];
  if (lanes.length) parts.push(("<b>" + tr("Coming (not yet ready):") + "</b> ") + lanes.map(k => `${esc(k)}: ` + cb[k].filter(i => !src || i.source === src).map(i => `${esc(i.customer)} (${i.m3} m³${i.ready_date ? ", " + fmtD(i.ready_date) : ""})`).join(", ")).join(" · "));
  if (ulanes.length) parts.push(`<b>Not plannable — no volume on record (${p.unsized}):</b> ` + ulanes.map(k => `${esc(k)}: ` + ub[k].filter(i => !src || i.source === src).map(i => esc(i.customer)).join(", ")).join(" · "));
  $("#coming").style.display = parts.length ? "" : "none";
  $("#coming").innerHTML = parts.join("<br><br>");
  renderTrucks(p);
}

// Which border the imports actually cross (D1/D17, 23 Sep). Policy is McAllen
// for everything now. This tile is how we find out whether the policy took,
// rather than assuming it did — so it counts what somebody WROTE DOWN, and
// says plainly how much of the board is still silent on the question.
function portTile(p) {
  const po = p.ports;
  if (!po || !po.imports) return "";
  const by = po.by_port || {};
  const mca = by["McAllen"] || 0, lar = by["Laredo"] || 0;
  const sub = lar
    ? `${lar} ${tr("still at Laredo")} · ${po.recorded} ${tr("of")} ${po.imports} ${tr("recorded on the file")}`
    : `${po.recorded} ${tr("of")} ${po.imports} ${tr("recorded on the file")}`;
  return `<div class="metric">
      <div class="ml">${tr("Crossing at McAllen")}</div>
      <div class="mv">${mca}${lar ? `<span class="delta down">▼ ${lar} ${tr("Laredo")}</span>` : ""}</div>
      <div class="ms">${esc(sub)}</div>
    </div>`;
}

// "These travel together" — Bill, consolidation meeting 23 Sep (D32).
//
// Until now a consolidation lived in somebody's head until they wrote a note
// or made a call: Sara cannot add freight to a truck she has not been told
// about, and TRS cannot hold space nobody asked for. Ticking the boxes drafts
// the message that tells them, and records the pick so the scoreboard can
// count decisions the team actually made rather than suggestions it ignored.
//
// It drafts. It does not send. A consolidation notice asks another company to
// hold space on a truck — that does not leave without a person reading it.
const PICKED = new Set();

function wirePicker() {
  document.querySelectorAll("#loads .pick, #groups .pick").forEach(cb => {
    cb.checked = PICKED.has(cb.dataset.id);
    cb.closest(".row").classList.toggle("picked", cb.checked);
    cb.onclick = (ev) => {
      ev.stopPropagation();              // the row itself opens the drawer
      const id = cb.dataset.id;
      if (cb.checked) { PICKED.add(id); PICK_LANE = cb.dataset.lane || PICK_LANE; }
      else PICKED.delete(id);
      cb.closest(".row").classList.toggle("picked", cb.checked);
      renderPicker();
    };
  });
  renderPicker();
}

let PICK_LANE = "";

function renderPicker() {
  const bar = $("#picker");
  if (!bar) return;
  const n = PICKED.size;
  bar.classList.toggle("on", n > 0);
  $("#pick-count").textContent = n === 1
    ? "1 " + tr("shipment selected")
    : n + " " + tr("shipments selected");
  $("#pick-go").disabled = n < 2;
  $("#pick-go").style.opacity = n < 2 ? .5 : 1;
}

function clearPicks() {
  PICKED.clear();
  document.querySelectorAll(".pick").forEach(cb => {
    cb.checked = false; cb.closest(".row").classList.remove("picked");
  });
  renderPicker();
}

async function draftNotice() {
  const go = $("#pick-go");
  const was = go.textContent;
  go.textContent = tr("Preparing…"); go.disabled = true;
  try {
    const r = await fetch("/crossborder/api/consolidation/notice", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ids: [...PICKED], lane: PICK_LANE}),
    });
    const d = await r.json();
    if (!r.ok) { alert(d.error || tr("Could not prepare the notice.")); return; }
    showNotice(d);
  } catch (e) {
    alert(tr("Could not prepare the notice.") + " " + e);
  } finally {
    go.textContent = was; renderPicker();
  }
}

function showNotice(d) {
  const s = d.summary || {};
  const sv = s.saving;
  $("#notice-sub").textContent =
    `${s.files} ${tr("shipments")} · ${s.m3} m³ · ${s.fill_pct}% ${tr("of a 53 ft trailer")}`
    + (s.spare_m3 ? ` · ${s.spare_m3} m³ ${tr("free")}` : "");
  $("#notice-to").innerHTML = `<b>${tr("To")}:</b> ${esc((d.to || []).join(", "))}<br><b>${tr("Subject")}:</b> ${esc(d.subject_en)}`;
  $("#notice-body").textContent = d.body || "";
  $("#notice-save").innerHTML = sv
    ? `${tr("Separately")}: ~${fmtN(sv.alone_mxn)} MXN · ${tr("together")}: ~${fmtN(sv.together_mxn)} MXN · <b>${tr("saved")} ~${fmtN(sv.saved_mxn)} MXN</b>`
    : "";
  $("#notice").classList.add("on");
  $("#notice-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(d.subject_en + "\n\n" + d.body);
          $("#notice-copy").textContent = tr("Copied"); }
    catch (e) { $("#notice-copy").textContent = tr("Select the text and copy it"); }
  };
}

function closeNotice() { $("#notice").classList.remove("on"); }

// The consolidation scoreboard. The programme's whole case is that filling
// trucks saves money; until now the board could not say whether the team was
// filling them any better than last month. Utilisation and files-per-load are
// the numbers to quote; trucks-avoided is the direction of travel, and the
// peso figure rests on one lane's price, so it says so.
function renderMetrics(p) {
  const m = p.metrics || {};
  if (m.error || !m.loads) {
    $("#metrics").innerHTML = `<div class="empty">${tr("No loads planned today, so there is nothing to measure.")}</div>`;
    return;
  }
  const h = (m.history && m.history.change) || null;
  const arrow = (v, goodUp) => {
    if (!v) return "";
    const good = goodUp ? v > 0 : v < 0;
    return `<span class="delta ${good ? "up" : "down"}">${v > 0 ? "▲" : "▼"} ${Math.abs(v)}</span>`;
  };
  const tile = (label, value, sub, delta) => `<div class="metric">
      <div class="ml">${esc(label)}</div>
      <div class="mv">${value}${delta || ""}</div>
      <div class="ms">${esc(sub || "")}</div>
    </div>`;
  const sv = m.savings;
  $("#metrics").innerHTML = [
    tile(tr("Trailer utilisation"), m.utilisation_pct + "%",
         `${fmtN(m.space_m3)} / ${fmtN(m.capacity_m3)} m³ ${tr("across")} ${m.loads} ${m.loads === 1 ? tr("load") : tr("loads")}`,
         h ? arrow(h.utilisation_pct, true) : ""),
    tile(tr("Shipments per load"), m.files_per_load,
         `${m.files_on_trucks} ${tr("files on")} ${m.loads} ${tr("trucks")}`,
         h ? arrow(h.files_per_load, true) : ""),
    tile(tr("Trucks carrying one file"), m.solo_loads,
         m.solo_pct + "% " + tr("of the loads that could be shared")
           + (m.alone_by_policy ? ` · ${m.alone_by_policy} ${tr("exports excluded — they ship alone by rule")}` : ""),
         h ? arrow(h.solo_pct, false) : ""),
    sv ? tile(tr("Trucks avoided"), sv.trucks_avoided,
              tr("assumed saving") + " ~" + fmtN(sv.assumed_mxn) + " MXN")
       : tile(tr("Trucks avoided"), "0", tr("every load is carrying a single file")),
    tile(tr("Paid-for empty space"), fmtN(m.paid_spare_m3) + " m³",
         tr("already heading to Mexico with room on board")),
    portTile(p),
  ].filter(Boolean).join("");
  $("#metrics-note").innerHTML = (sv ? esc(tr("Savings assume") + " " + sv.assumption) : "")
    + (m.history && m.history.count > 1
        ? ` <b>${tr("Measured since")} ${esc(m.history.points[0].as_of)}</b> (${m.history.count} ${tr("days")}).`
        : ` <b>${tr("First day of measurement")}</b> — ${tr("the trend appears once there are two days on record.")}`);
}

// Consolidations a coordinator has already made, read from her note on the
// ClickUp list. These are not suggestions — they are trucks being filled right
// now, and the useful move is to put more freight on one rather than book
// another (decision D6 with Edgar, 22 Sep).
function renderGroups(p) {
  const gs = p.groups || [];
  $("#grouphead").style.display = gs.length ? "" : "none";
  $("#cnt-groups").textContent = gs.length;
  $("#groups").innerHTML = gs.map(g => {
    const pct = Math.min(100, g.fill_pct);
    const cls = g.fill_pct >= 85 ? "ok" : (g.fill_pct < 60 ? "lt" : "");
    return `<div class="load grouped">
      <h4>${esc(g.name || g.lane)} <span class="tag grp">${g.customers} ${g.customers === 1 ? tr("file") : tr("files")} ${tr("consolidated")}</span>${g.third_party ? `<span class="tag detour">${esc(g.third_party)}</span>` : ""}${g.spare_m3 > 0 ? `<span class="tag xs">${g.spare_m3} m³ ${tr("free")}</span>` : ""}</h4>
      <div class="fl"><span>${esc(g.leg_label ? tr(g.leg_label) : g.lane)}</span><span>${g.fill_pct}%</span></div>
      <div class="fillbar"><i class="${cls}" style="width:${pct}%"></i></div>
      ${g.members.map(m => `<div class="row" data-id="${esc(m.id)}"><div class="who"><b>${esc(m.customer)}</b> <span class="tag grp">${esc(m.label)}</span>${m.carrier ? ` <span class="tag xs" title="${tr("the note is on this file")}">${tr("note")}</span>` : ""}<br><span class="rs">→ ${esc(m.destination || "?")}${(m.consolidated_with || []).length ? ` · ${tr("with")} ${esc(m.consolidated_with.join(", "))}` : ""}</span></div><div class="m3">${m.m3 || 0} m³</div></div>`).join("")}
      ${(g.unmatched || []).length ? `<div class="adv">${tr("Named in the note but not found on the board")}: ${esc(g.unmatched.join(", "))}</div>` : ""}
      ${g.could_join && g.could_join.length ? `<div class="adv">${tr("Could still join this truck")}: ${g.could_join.map(c => `${esc(c.customer)} (${c.space_m3} m³)`).join(", ")}</div>` : ""}
      ${g.note ? `<div class="fl" style="margin-top:6px"><span class="rs">${tr("note")}: ${esc(g.note)}</span></div>` : ""}
    </div>`; }).join("");
}

// The Plan de Viajes is republished about three times a day and services
// sometimes vanish or move. The team's defence today is screenshots; this panel
// is the dashboard keeping the receipts instead (training, 21 Sep).
async function loadPlanChanges() {
  let h = null;
  try { h = await fetch(`/crossborder/api/plan-history?limit=12&kind=vanished,date_changed,unit_changed`).then(r => r.json()); }
  catch (e) { return; }
  const ev = (h && h.events) || [];
  $("#planchangehead").style.display = ev.length ? "" : "none";
  $("#planchanges").style.display = ev.length ? "" : "none";
  $("#cnt-planchanges").textContent = ev.length;
  const label = {vanished: tr("disappeared"), date_changed: tr("date changed"), unit_changed: tr("truck changed")};
  $("#planchanges").innerHTML = ev.map(e =>
    `<div class="planchg"><span class="k ${esc(e.kind)}">${esc(label[e.kind] || e.kind)}</span><span>${esc(e.detail || "")}</span><span class="at">${esc((e.at || "").replace("T", " ").slice(0, 16))}</span></div>`).join("");
}

// TRS already runs trucks to these hubs with space left on them. This panel is
// the number the team cannot get anywhere else: paid-for empty space, by hub.
function renderTrucks(p) {
  const spare = p.spare_by_hub || {};
  const hubs = Object.values(spare).filter(h => h.hub !== "Unknown" && h.spare_m3 > 0);
  const head = $("#sparehead");
  if (!hubs.length) { head.style.display = "none"; $("#trucks").innerHTML = ""; return; }
  head.style.display = "";
  const total = hubs.reduce((a, h) => a + h.spare_m3, 0);
  $("#cnt-trucks").textContent = p.trucks_considered || 0;
  $("#spare-stats").textContent = `${fmtN(total)} m³ ${tr("of empty space already heading to Mexico in the next 14 days")}`;
  $("#trucks").innerHTML = hubs.map(h => `<div class="truck">
      <b>${esc(h.hub)}</b>
      <div class="bar"><i style="width:${Math.min(100, Math.round(h.spare_m3 / (TRUCK_M3 * Math.max(h.trucks,1)) * 100))}%"></i></div>
      <div><span class="sp">${fmtN(h.spare_m3)} m³ ${tr("free")}</span> · ${h.trucks} ${h.trucks === 1 ? tr("truck") : tr("trucks")}</div>
      <div class="rs" style="color:#999">${esc((h.units || []).slice(0, 6).join(", "))}</div>
    </div>`).join("");
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
  // Three tabs (Bill, 23 Sep): the shipment tiles first, the consolidation
  // recommendations second, the full table third.
  if (view === "ship") { renderBoard(rows); renderAlerts(rows); renderHubs(rows); }
  else if (view === "table") renderTable(rows);
  $("#ship-view").style.display = view === "ship" ? "" : "none";
  $("#cons-view").style.display = view === "cons" ? "" : "none";
  $("#table-view").style.display = view === "table" ? "" : "none";
  $("#view-ship").className = "btn" + (view === "ship" ? " active" : "");
  $("#view-cons").className = "btn" + (view === "cons" ? " active" : "");
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

// "Already consolidated with TMS - 110719, TIM - 121722" (Bill, 2026-09-22).
// Naming the other files by source and job number is the point: a coordinator
// who sees this has to be able to go and open them.
function consolidatedLine(s) {
  if (!s.is_grouped) return "";
  const w = s.consolidated_with || [];
  return `<div class="cons">${tr("Already consolidated")}${w.length ? ` ${tr("with")} ${esc(w.join(", "))}` : ""}</div>`;
}

function consolidatedBlock(s) {
  const c = s.consolidation || {};
  if (!s.is_grouped && !s.do_not_consolidate) return "";
  const w = s.consolidated_with || [];
  const head = s.is_grouped ? tr("Already consolidated") : tr("Not for consolidation");
  return `<div class="adv info" style="margin-bottom:14px">
    <b>${head}</b>${w.length ? ` — ${tr("with")} ${esc(w.join(", "))}` : ""}
    ${c.third_party ? `<br>${tr("Moving with")} ${esc(c.third_party)}` : ""}
    ${c.note ? `<br><span class="rs">${tr("note")}: ${esc(c.note)}</span>` : ""}
    ${c.source ? `<br><span class="rs">${esc(c.source)}</span>` : ""}
  </div>`;
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
    ${consolidatedLine(s)}
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

const COLS = [["customer_name","Customer"],["source","Src"],["agent","Agent"],["corporate_account","Corporate account"],["reference_number","Reference"],["stage","Stage"],["current_step","Current step"],["days_since_progress","Days idle"],["destination","Destination"],["destination_hub","Hub"],["planning_m3","m³"],["revenue","Revenue"],["status_flags","Flags"],["assignees","Assigned"],["milestones.green_light","Green light"],["milestones.crossed","Crossed"],["milestones.delivered","Delivered"]];
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
    else if (c[0] === "revenue") v = fmtMoney(s.revenue, s.revenue_currency, s.revenue_month);
    else if (c[0] === "corporate_account") v = s.corporate_account
      ? `<span class="corp${s.corporate_account_named ? "" : " unnamed"}">${esc(s.corporate_account)}</span>`
      : (s.customer_type ? esc(s.customer_type) : `<span style="color:#b9bdc4">${tr("private")}</span>`);
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
    ${consolidatedBlock(s)}
    <div class="kv">
      <b>${tr("Stage")}</b><span>${esc(STAGE_LABEL[s.stage] || s.stage)}</span>
      <b>${tr("Current step")}</b><span>${s.steps_total ? `${esc(s.current_step || "—")} (${s.steps_done}/${s.steps_total}, ${s.process_format || "?"})` : esc(s.source_status || "—") + (ex.direction ? ` · ${esc(ex.direction)}` : "") + (ex.method ? ` · ${esc(ex.method)}` : "")}</span>
      <b>${tr("Last progress")}</b><span>${s.last_progress_at ? esc(s.last_progress_at) + ` · ${s.days_since_progress} days ago` : "—"}</span>
      <b>${tr("Assigned")}</b><span>${esc((s.assignees || []).join(", ") || "—")}</span>
      <b>${tr("Origin → Dest.")}</b><span>${esc(s.origin || "?")} → ${esc(s.destination || "?")}${s.destination_hub !== "Unknown" ? ` (${esc(s.destination_hub)} hub)` : ""}</span>
      ${s.is_ubox_job ? `<b>U-Box</b><span>${s.u_boxes_planned
          ? `<b>${s.u_boxes_planned} U-Box${s.u_boxes_planned === 1 ? "" : "es"}</b>`
          : `<span style="color:#9a5b00">${tr("U-Box job — number of boxes not on record yet")}</span>`}
          <br><span style="color:#8a8f98;font-size:11px">${tr("Each: 7.3 m³ / 257 cu ft usable · max 2,000 lb contents · 96 × 60 × 90 in outside")}</span></span>` : ""}
      <b>${tr("Volume")}</b><span>${s.is_us_diplomatic && s.lift_vans_planned ? `<b>${s.lift_vans_planned} ${tr("lift vans")}</b> (${s.volume_m3} m³ ${tr("gross")} ÷ 5.7) · ` : ""}${s.lift_vans && !s.is_us_diplomatic ? s.lift_vans + " lift van(s) · " : ""}${s.u_boxes ? s.u_boxes + " U-Box(es) · " : ""}${s.volume_m3 ? s.volume_m3 + " m³ · " : ""}${esc(ex.volume_text || "")}${!(s.lift_vans || s.u_boxes || s.volume_m3 || ex.volume_text) ? "—" : ""}</span>
      <b>${tr("Revenue")}</b><span>${s.revenue != null
          ? fmtMoney(s.revenue, s.revenue_currency, s.revenue_month)
            + (s.revenue_month ? ` <span style="color:#8a8f98;font-size:11px">· ${esc(s.revenue_month)} ${tr("rate")}`
               + ` (${tr(BASIS_LABEL[s.revenue_month_basis] || s.revenue_month_basis || "")})</span>` : "")
          : (ex.sale_value ? money(ex.sale_value) : "—")}</span>
      ${s.source === "TMS" ? `<b>${tr("Invoiced in Moveware")}</b><span id="dinv">${s.invoice_status === "Y" ? tr("yes") : tr("not yet")} · <a href="#" id="dinv-load" style="color:#1967d2">${tr("show invoice")}</a></span>` : ""}
      <b>${tr("Client")}</b><span>${s.corporate_account
          ? `<span class="corp${s.corporate_account_named ? "" : " unnamed"}">${esc(s.corporate_account)}</span>`
            + (s.corporate_account_named ? "" : ` <span style="color:#8a8f98;font-size:11px">${tr("— placeholder, no account named in Moveware")}</span>`)
          : (s.customer_type ? esc(s.customer_type) : tr("private / consumer"))}</span>
      ${(s.booking_agent || s.origin_agent || s.destination_agent) ? `<b>${tr("Agents")}</b><span>${
          [[tr("booking"), s.booking_agent], [tr("origin"), s.origin_agent], [tr("destination"), s.destination_agent]]
            .filter(p => p[1]).map(p => `${p[0]}: ${esc(p[1])}`).join("<br>")}</span>` : ""}
      ${s.weight ? `<b>${tr("Weight")}</b><span>${s.weight} kg</span>` : ""}
      ${ex.sit_unit ? `<b>${tr("Mexican leg")}</b><span>${esc(ex.sit_unit)}${ex.sit_driver ? " · " + esc(ex.sit_driver) : ""}${ex.sit_load_date ? "<br>" + tr("loads") + " " + fmtD(ex.sit_load_date) : ""}${ex.sit_unload_date ? " → " + tr("unloads") + " " + fmtD(ex.sit_unload_date) : ""}${ex.sit_route ? "<br>" + esc(ex.sit_route) : ""}</span>` : ""}
      ${s.source !== "TIM" ? "" : `<b>Remisiones</b><span>${ex.remisiones_block ? esc(ex.remisiones_block) + (ex.remisiones_week ? " · " + esc(ex.remisiones_week) : "") + (ex.remisiones_status ? "<br>" + esc(ex.remisiones_status) : "") : tr("not on the sheet")}</span>`}
    </div>
    <div class="section" style="margin-top:0">${tr("Milestones")}</div>
    <div class="ms">${ms.map(m => `<div class="${s.milestones && s.milestones[m] ? "done" : "todo"}"><span>${m.replace(/_/g, " ")}</span><span>${s.milestones && s.milestones[m] ? esc(s.milestones[m]) : "—"}</span></div>`).join("")}</div>`;
  // Invoiced-vs-outstanding is one extra Moveware call, so it is fetched only
  // when somebody asks for it rather than on every refresh.
  const invLink = $("#dinv-load");
  if (invLink) invLink.onclick = async (e) => {
    e.preventDefault();
    invLink.textContent = tr("checking…");
    try {
      const r = await fetch(`/crossborder/api/invoices/${encodeURIComponent(s.source_ref)}?v=${Date.now()}${DEMOQ}`);
      const j = await r.json();
      if (j.error) { $("#dinv").innerHTML = `<span style="color:#c0392b">${esc(j.error)}</span>`; return; }
      if (!j.count) { $("#dinv").textContent = tr("no invoice raised yet"); return; }
      // Deliberately NOT labelled "outstanding". Moveware is not Thelsa's
      // accounting system of record (Bill, 2026-09-16) — payments are booked
      // elsewhere, so Moveware shows nearly every invoice as unpaid. Presenting
      // that as an AR figure would invent a receivables crisis that isn't real.
      $("#dinv").innerHTML = `${tr("Invoiced")} ${fmtMoney(j.invoiced, j.currency, s.revenue_month)}`
        + j.invoices.map(i => `<br><span style="color:#8a8f98;font-size:11px">${esc(i.number || i.id)}`
            + `${i.date ? " · " + esc(i.date) : " · " + tr("no invoice date")}</span>`).join("")
        + `<br><span style="color:#8a8f98;font-size:11px">${tr("Payment status is not held in Moveware.")}</span>`;
    } catch (err) {
      $("#dinv").innerHTML = `<span style="color:#c0392b">${esc(String(err))}</span>`;
    }
  };
  $("#drawer").classList.add("open"); $("#overlay").classList.add("open");
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#overlay").classList.remove("open"); }

["#f-source","#f-agent","#f-flag","#f-hub","#f-stage"].forEach(id => $(id).onchange = () => { render(); renderPlan(); });
$("#f-q").oninput = render;
$("#f-closed").onchange = () => load(false);
$("#clear").onclick = () => { ["#f-source","#f-agent","#f-flag","#f-hub","#f-stage"].forEach(id => $(id).value = ""); $("#f-q").value = ""; kpiSel = null; render(); };
$("#view-ship").onclick = () => { view = "ship"; render(); renderPlan(); };
$("#view-cons").onclick = () => { view = "cons"; render(); renderPlan(); };
$("#view-table").onclick = () => { view = "table"; render(); };
$("#refresh").onclick = () => load(true);
$("#dclose").onclick = closeDrawer; $("#overlay").onclick = closeDrawer;
$("#pick-go").onclick = draftNotice;
$("#pick-clear").onclick = clearPicks;
$("#notice-close").onclick = closeNotice;
$("#notice").onclick = (e) => { if (e.target.id === "notice") closeNotice(); };
$("#lang-en").onclick = () => setLang("en"); $("#lang-es").onclick = () => setLang("es");
$("#cur-mxn").onclick = () => setCur("MXN"); $("#cur-usd").onclick = () => setCur("USD");
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
applyStaticLang(); applyCurrency(); load(false);
setInterval(() => load(false), 5 * 60 * 1000);
</script>
</body>
</html>
"""
