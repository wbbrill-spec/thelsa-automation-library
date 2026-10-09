"""Jinja template for the live /audit page (executive view).

Bilingual: every visible string is rendered in both languages with
{{ t('English', 'Español') }}; the EN/ES toggle just flips a class on <body>.
Money: {{ M(pair) }} renders a span carrying [mxn, usd]; the currency control
(MXN · USD plan · USD spot) formats it in the browser. Period: both YTD and
last-12-months blocks are rendered; the period control shows one.
"""

EXEC_TEMPLATE = r"""<!DOCTYPE html>
{%- macro t(en, es) -%}<span class="en">{{ en }}</span><span class="es">{{ es }}</span>{%- endmacro -%}
{%- macro M(p, cls='') -%}<span class="m {{ cls }}" data-x="{{ p[0] }}" data-u="{{ p[1] }}">—</span>{%- endmacro -%}
{%- macro N(n) -%}<span class="n" data-n="{{ n }}">{{ n }}</span>{%- endmacro -%}
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Move-File Audit</title>
<style>
  :root{--rust:#b0472f;--rust-dark:#8f3a26;--ink:#2b2b2b;--muted:#7d7d78;
        --line:#eae7e3;--bg:#f4f3f1;--card:#fff;--green:#2e7d32;--amber:#a86b12;--red:#c0392b;--tint:#faf3f0;}
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
       background:var(--bg);color:var(--ink);font-size:14px;line-height:1.45}
  body:not(.es) .es, body.es .en{display:none}
  .per{display:none} body.p-ytd .per-ytd, body.p-l12m .per-l12m{display:block}
  header{background:var(--card);border-bottom:1px solid var(--line);padding:18px 30px;
         display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
  header h1{font-size:21px;margin:0;font-weight:800;letter-spacing:-.2px}
  header h1 .accent{color:var(--rust)}
  .meta{font-size:12px;color:var(--muted);margin-top:3px}
  .back{font-size:13px;color:var(--rust);text-decoration:none;font-weight:600}
  .controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;background:var(--card)}
  .seg button{appearance:none;border:none;background:none;padding:7px 12px;font-size:12px;font-weight:700;
              color:var(--muted);cursor:pointer;border-right:1px solid var(--line)}
  .seg button:last-child{border-right:none}
  .seg button.on{background:var(--rust);color:#fff}
  main{padding:20px 30px;max-width:1200px;margin:0 auto}
  .note{background:var(--tint);border:1px solid var(--line);border-radius:12px;padding:10px 14px;
        font-size:12.5px;color:var(--muted);margin-bottom:8px}
  .note b{color:var(--ink)} .note.warn{background:#fff6e5;border-color:#f0dcb8;color:var(--amber)}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin:24px 0 10px;font-weight:700}
  .grid{display:grid;gap:14px;grid-template-columns:repeat(4,1fr)}
  @media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:520px){.grid{grid-template-columns:1fr} main,header{padding-left:16px;padding-right:16px}}
  .tile{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 17px}
  .tile .label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
  .tile .value{font-size:25px;font-weight:800;margin-top:5px;letter-spacing:-.4px;font-variant-numeric:tabular-nums}
  .tile .sub{font-size:12px;color:var(--muted);margin-top:3px}
  .good{color:var(--green)}.warn{color:var(--amber)}.bad{color:var(--red)}
  .tbl{overflow-x:auto;margin-top:10px}
  table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
  th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);font-size:13px;white-space:nowrap}
  td.wrap{white-space:normal}
  th{background:var(--tint);color:var(--rust-dark);font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  td.r,th.r{text-align:right} tr:last-child td{border-bottom:none}
  .num{font-variant-numeric:tabular-nums}
  .pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:10.5px;font-weight:700;background:#fbf0dd;color:var(--amber)}
  .ccy{font-size:10.5px;color:var(--muted);margin-left:4px}
  .tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:10px 0 4px;flex-wrap:wrap}
  .tab-btn{appearance:none;background:none;border:none;cursor:pointer;padding:10px 16px;font-size:13px;font-weight:700;
           color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;text-decoration:none}
  .tab-btn.active{color:var(--rust);border-bottom-color:var(--rust)}
  .tab[hidden]{display:none!important}
  .bar{display:flex;align-items:center;gap:9px;font-size:12px;margin:5px 0}
  .bar .track{flex:1;height:8px;background:var(--line);border-radius:6px;overflow:hidden}
  .bar .fill{height:100%;background:var(--rust)}
  footer{color:var(--muted);font-size:11.5px;text-align:center;padding:22px}
  a.link{color:var(--rust);font-weight:700}
</style></head>
<body class="p-ytd">
<header>
  <div>
    <h1>{{ t('Move-File', 'Auditoría de') }} <span class="accent">{{ t('Audit', 'Expedientes') }}</span></h1>
    <div class="meta"><a class="back" href="/">← {{ t('Automation Library', 'Biblioteca de automatizaciones') }}</a>
      · MoveWare + {{ t('Finance', 'Finanzas') }} · {{ ex.as_of }}</div>
  </div>
  <div class="controls">
    <div class="seg" id="perSeg"><button data-p="ytd">{{ t('YTD', 'Año a la fecha') }}</button><button data-p="l12m">{{ t('Last 12 months', 'Últimos 12 meses') }}</button></div>
    <div class="seg" id="fxSeg"><button data-b="mxn">{{ t('Pesos', 'Pesos') }}</button><button data-b="plan">{{ t('USD · plan', 'USD · plan') }}</button><button data-b="spot">{{ t('USD · spot', 'USD · spot') }}</button></div>
    <div class="seg" id="langSeg"><button data-l="en">EN</button><button data-l="es">ES</button></div>
  </div>
</header>
<main>
  <div class="note" id="fxNote"></div>
  {% if ex.stale_records %}
  <div class="note warn">{{ t('Re-reading', 'Releyendo') }} <b>{{ ex.stale_records }}</b> {{ t('files from MoveWare to pick up per-invoice detail (numbers, dates, currency) — revenue and billing figures complete in a few minutes; reload then.', 'expedientes de MoveWare para obtener el detalle por factura (número, fecha, moneda) — las cifras de ingresos y facturación se completan en unos minutos; recargue entonces.') }}
    {% if ex.remap and ex.remap.running %}({{ ex.remap.done }}/{{ ex.remap.total }}){% endif %}</div>
  {% endif %}

  <nav class="tabs">
    <a class="tab-btn" data-tab="overview" href="#overview">{{ t('Overview', 'Resumen') }}</a>
    <a class="tab-btn" data-tab="underbilling" href="#underbilling">{{ t('Under-billing', 'Facturación incompleta') }}</a>
    {% if ex.sc %}<a class="tab-btn" data-tab="costs" href="#costs">{{ t('Supplier costs', 'Costos de proveedores') }}{% if ex.sc.n_open %} ({{ ex.sc.n_open }}){% endif %}</a>{% endif %}
  </nav>

  <section class="tab" data-tab="overview">
  {% for key in ['ytd', 'l12m'] %}{% set P = ex.periods[key] %}
  <div class="per per-{{ key }}">
    <p class="meta" style="margin:8px 0 0">{{ t('Period', 'Periodo') }}: <b>{{ P['from'] }}</b> → <b>{{ P['to'] }}</b></p>

    <h2>{{ t('Revenue & margin', 'Ingresos y margen') }}</h2>
    <div class="grid">
      <div class="tile"><div class="label">{{ t('Invoiced — MoveWare', 'Facturado — MoveWare') }}</div>
        <div class="value">{{ M(P.inv_num) }}</div>
        <div class="sub">{{ N(P.inv_files) }} {{ t('files · numbered invoices dated in the period, net of IVA, cancelled excluded', 'expedientes · facturas con número y fecha en el periodo, sin IVA, sin canceladas') }}</div></div>
      <div class="tile"><div class="label">{{ t('Invoiced — Finance', 'Facturado — Finanzas') }}</div>
        <div class="value">{{ M(P.fin_sales_all) }}</div>
        <div class="sub">{{ t("Finance's ledger, net of credit notes · files first invoiced in the period", 'Libro de Finanzas, neto de notas de crédito · expedientes con primera factura en el periodo') }}{% if P.fin_last_invoice %} · {{ t('ledger through', 'datos hasta') }} {{ P.fin_last_invoice }}{% endif %}</div></div>
      <div class="tile"><div class="label">{{ t('Gross margin — Finance actuals', 'Margen bruto — real de Finanzas') }}</div>
        <div class="value {{ 'good' if (P.fin_margin_pct or 0) >= 0 else 'bad' }}">{{ M(P.fin_margin) }}</div>
        <div class="sub">{% if P.fin_margin_pct is not none %}<b>{{ P.fin_margin_pct }}%</b> {{ t('on', 'sobre') }} {{ N(P.fin_costed_n) }} {{ t('costed files', 'expedientes con costo') }}{% if P.fin_neg_n %} · <span class="bad">{{ P.fin_neg_n }} {{ t('below cost', 'bajo costo') }}</span>{% endif %}{% else %}{{ t('no costed files in this period yet', 'aún no hay expedientes con costo en este periodo') }}{% endif %}</div></div>
      <div class="tile"><div class="label">{{ t('Cost pending', 'Costo pendiente') }}</div>
        <div class="value warn">{{ N(P.fin_pending_n) }}</div>
        <div class="sub">{{ t('invoiced files with no cost posted yet — left out of the margin, never shown as 100%', 'expedientes facturados sin costo registrado — fuera del margen, nunca como 100%') }}</div></div>
    </div>

    <h2>{{ t('Billing', 'Facturación') }}</h2>
    <div class="grid">
      <div class="tile"><div class="label">{{ t('Ready to invoice', 'Listos para facturar') }}</div>
        <div class="value {{ 'warn' if P.ready_n else 'good' }}">{{ N(P.ready_n) }}</div>
        <div class="sub">{{ M(P.ready_val) }} {{ t('· packed or delivered, no invoice yet (accepted quote)', '· empacados o entregados, sin factura (cotización aceptada)') }}</div></div>
      <div class="tile"><div class="label">{{ t('Percent billed', 'Porcentaje facturado') }}</div>
        <div class="value {{ 'good' if (P.pct_billed or 0) >= 80 else 'warn' }}">{% if P.pct_billed is not none %}{{ P.pct_billed }}%{% else %}—{% endif %}</div>
        <div class="sub">{{ N(P.billed_n) }} {{ t('of', 'de') }} {{ N(P.billed_n + P.ready_n + P.checks_n) }} {{ t('moves that have happened (leads & cancelled excluded)', 'mudanzas realizadas (sin prospectos ni canceladas)') }}</div></div>
      <div class="tile"><div class="label">{{ t('Embassy in transit', 'Embajada en tránsito') }}</div>
        <div class="value">{{ N(P.emb_n) }}</div>
        <div class="sub">{{ M(P.emb_val) }} {{ t('· packed, bills after delivery', '· empacados, se facturan al entregar') }}</div></div>
      <div class="tile"><div class="label">{{ t('Invoices with no number in MoveWare', 'Facturas sin número en MoveWare') }}</div>
        <div class="value {{ 'warn' if P.unnum_files else 'good' }}">{{ N(P.unnum_files) }}</div>
        <div class="sub">{{ M(P.inv_unnum) }} {{ t('· not counted as revenue until numbered', '· no cuentan como ingreso hasta tener número') }}</div></div>
    </div>

    <h2>{{ t('Files with move activity', 'Expedientes con actividad') }}</h2>
    <div class="grid">
      <div class="tile"><div class="label">{{ t('Booked', 'Confirmados') }} (W)</div><div class="value">{{ N(P.status_n.W) }}</div></div>
      <div class="tile"><div class="label">{{ t('Pending', 'Pendientes') }} (P)</div><div class="value">{{ N(P.status_n.P) }}</div></div>
      <div class="tile"><div class="label">{{ t('Leads', 'Prospectos') }} (L)</div><div class="value">{{ N(P.status_n.L) }}</div></div>
      <div class="tile"><div class="label">{{ t('Cancelled', 'Cancelados') }} (C)</div><div class="value">{{ N(P.status_n.C) }}</div>
        {% if P.status_n.other %}<div class="sub">+ {{ P.status_n.other }} {{ t('other status', 'otro estatus') }}</div>{% endif %}</div>
    </div>

    {% if P.ready %}
    <h2>{{ t('Ready to invoice — by value', 'Listos para facturar — por valor') }}</h2>
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Client', 'Cliente') }}</th><th>{{ t('Coordinator', 'Coordinador') }}</th><th>{{ t('Packed', 'Empaque') }}</th><th>{{ t('Delivered', 'Entrega') }}</th><th class="r">{{ t('Quote', 'Cotización') }}</th></tr>
    {% for r in P.ready[:40] %}<tr><td class="num">{{ r.job }}{% if r.embassy %} <span class="pill">EMB</span>{% endif %}</td>
      <td class="wrap">{{ r.client }}</td><td>{{ r.coordinator or '—' }}</td><td>{{ r.pack or '—' }}</td><td>{{ r.delivery or '—' }}</td>
      <td class="r num">{{ M(r.value) }}{% if r.ccy %}<span class="ccy">· {{ t('quoted in', 'cotizado en') }} {{ r.ccy }}</span>{% endif %}</td></tr>{% endfor %}</table></div>
    {% if P.ready|length > 40 %}<p class="meta">{{ t('Showing top 40 of', 'Se muestran 40 de') }} {{ P.ready|length }}.</p>{% endif %}
    {% endif %}

    {% if P.checks %}
    <h2>{{ t('Quotes to check before billing', 'Cotizaciones a revisar antes de facturar') }}</h2>
    <p class="meta" style="margin-top:-4px">{{ t('The accepted quote on these files is larger than any normal move (over MXN', 'La cotización aceptada es mayor que cualquier mudanza normal (más de MXN') }} {{ '{:,.0f}'.format(ex.quote_check_mxn) }}) — {{ t('likely keyed in the wrong currency or with a typo. Kept out of the totals above.', 'probablemente capturada en otra moneda o con error. Excluidas de los totales.') }}</p>
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Client', 'Cliente') }}</th><th>{{ t('Coordinator', 'Coordinador') }}</th><th>{{ t('Status', 'Estatus') }}</th><th>{{ t('Packed', 'Empaque') }}</th><th class="r">{{ t('Quote in MoveWare', 'Cotización en MoveWare') }}</th></tr>
    {% for r in P.checks %}<tr><td class="num">{{ r.job }}{% if r.embassy %} <span class="pill">EMB</span>{% endif %}</td><td class="wrap">{{ r.client }}</td><td>{{ r.coordinator or '—' }}</td><td>{{ r.status or '—' }}</td><td>{{ r.pack or '—' }}</td>
      <td class="r num bad">{{ M(r.value) }}{% if r.ccy %}<span class="ccy">· {{ t('quoted in', 'cotizado en') }} {{ r.ccy }}</span>{% else %}<span class="ccy">{{ t('no currency', 'sin moneda') }}</span>{% endif %}</td></tr>{% endfor %}</table></div>
    {% endif %}

    {% if P.emb %}
    <h2>{{ t('Embassy files in transit', 'Expedientes de embajada en tránsito') }}</h2>
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Client', 'Cliente') }}</th><th>{{ t('Packed', 'Empaque') }}</th><th class="r">{{ t('Quote', 'Cotización') }}</th></tr>
    {% for r in P.emb %}<tr><td class="num">{{ r.job }}</td><td class="wrap">{{ r.client }}</td><td>{{ r.pack or '—' }}</td><td class="r num">{{ M(r.value) }}</td></tr>{% endfor %}</table></div>
    {% endif %}
  </div>
  {% endfor %}

  <h2>{{ t('Finance vs MoveWare', 'Finanzas vs MoveWare') }}</h2>
  <div class="note">{{ t('File-by-file differences between Finance and MoveWare, with a question for each, are in the', 'Las diferencias expediente por expediente entre Finanzas y MoveWare, con una pregunta para cada una, están en la') }}
    <a class="link" href="{{ ex.query_sheet }}" target="_blank" rel="noopener">{{ t('audit query sheet', 'hoja de consultas de auditoría') }}</a>.</div>
  </section>

  <section class="tab" data-tab="underbilling">
    <h2>{{ t('Approved in the quote, not yet invoiced (MoveWare)', 'Aprobado en la cotización, aún no facturado (MoveWare)') }}</h2>
    <p class="meta">{{ t('Each invoiced file’s accepted quote vs what was invoiced. Only gaps over 2% of the accepted value are shown.', 'Cotización aceptada vs lo facturado en cada expediente facturado. Solo diferencias mayores al 2% del valor aceptado.') }}</p>
    <div class="grid">
      <div class="tile"><div class="label">{{ t('Not yet invoiced', 'Sin facturar') }}</div><div class="value warn">{{ M(m.total_disc_pair) }}</div></div>
      <div class="tile"><div class="label">{{ t('Files', 'Expedientes') }}</div><div class="value">{{ N(m.disc_files) }}</div></div>
      <div class="tile"><div class="label">{{ t('Coordinators', 'Coordinadores') }}</div><div class="value">{{ N(m.coords_affected) }}</div></div>
      <div class="tile"><div class="label">{{ t('Email detector', 'Detector de correo') }}</div><div class="value" style="font-size:15px">{% if m.ub_have_creds %}{{ t('connected', 'conectado') }}{% else %}{{ t('waiting on mailbox access', 'esperando acceso al buzón') }}{% endif %}</div></div>
    </div>
    {% if m.by_coordinator_disc %}
    <div class="tile" style="margin-top:12px">{% set mx = (m.by_coordinator_disc[0].value or 1) %}
      {% for c in m.by_coordinator_disc %}<div class="bar"><span style="width:160px">{{ c.coordinator }}</span><span class="track"><span class="fill" style="width:{{ (c.value/mx*100)|round(0) }}%"></span></span><span class="num" style="width:150px;text-align:right">{{ M(c.pair) }} · {{ c.files }}</span></div>{% endfor %}
    </div>{% endif %}
    {% if m.disc_worklist %}
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Client', 'Cliente') }}</th><th>{{ t('Coordinator', 'Coordinador') }}</th><th class="r">{{ t('Amount', 'Monto') }}</th></tr>
    {% for r in m.disc_worklist %}<tr><td class="num">{{ r.job }}</td><td class="wrap">{{ r.client }}</td><td>{{ r.coordinator }}</td><td class="r num warn">{{ M(r.pair) }}</td></tr>{% endfor %}</table></div>
    {% endif %}
  </section>

  {% if ex.sc %}{% set S = ex.sc %}
  <section class="tab" data-tab="costs">
    <h2>{{ t('Supplier costs found in email — to post in MoveWare', 'Costos de proveedores encontrados en correo — por registrar en MoveWare') }}</h2>
    <p class="meta">{{ t('Read from the TMS coordinators’ mail: supplier invoices (CFDI XML) and extra costs quoted by agents, carriers and suppliers (demurrage, storage, inspections, re-booking…). Each line is matched to its MoveWare file.', 'Leído del correo de los coordinadores TMS: facturas de proveedores (XML CFDI) y costos extra cotizados por agentes, navieras y proveedores (demoras, almacenajes, inspecciones, roll de buque…). Cada línea se liga a su expediente en MoveWare.') }}
      <b>{{ t('MoveWare does not yet accept cost through its API, so these are posted by hand for now; they will post automatically once MoveWare opens that endpoint.', 'MoveWare aún no acepta costos por su API, así que por ahora se registran a mano; se registrarán solos cuando MoveWare habilite esa función.') }}</b></p>
    {% if not S.have_creds and not S.rows %}
    <div class="note warn">{{ t('Waiting on mailbox access — the reader is built and tested and starts on its own once the Microsoft Graph app secret is set on the server (same credential as the under-billing detector).', 'Esperando acceso a los buzones — el lector está construido y probado y arranca solo cuando se configure en el servidor el secreto de la app de Microsoft Graph (la misma credencial del detector de facturación incompleta).') }}</div>
    {% else %}
    {% if S.error %}<div class="note warn">{{ S.error }}</div>{% endif %}
    {% if S.diag %}<p class="meta">{{ t('Last scan', 'Última revisión') }}: {{ S.diag.ok }}/{{ S.diag.mailboxes }} {{ t('mailboxes read', 'buzones leídos') }} · {{ S.diag.messages_read }} {{ t('messages', 'mensajes') }} · {{ S.diag.with_xml }} {{ t('with an invoice XML', 'con XML de factura') }} · {{ S.n_messages }} {{ t('with a cost', 'con un costo') }}</p>{% endif %}
    <div class="grid">
      <div class="tile"><div class="label">{{ t('To post', 'Por registrar') }}</div><div class="value warn">{{ N(S.n_to_post) }}</div>
        <div class="sub">{% for c, v in S.open_by_ccy.items() %}{{ c }} {{ '{:,.0f}'.format(v) }}{% if not loop.last %} · {% endif %}{% endfor %}</div></div>
      <div class="tile"><div class="label">{{ t('To confirm', 'Por confirmar') }}</div><div class="value">{{ N(S.n_to_confirm) }}</div><div class="sub">{{ t('cost mentioned by a coordinator — confirm with the supplier', 'costo mencionado por un coordinador — confirmar con el proveedor') }}</div></div>
      <div class="tile"><div class="label">{{ t('File not identified', 'Expediente no identificado') }}</div><div class="value">{{ N(S.n_no_file) }}</div></div>
      <div class="tile"><div class="label">{{ t('Files affected', 'Expedientes') }}</div><div class="value">{{ N(S.n_files) }}</div></div>
    </div>
    {% if S.open %}
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Date', 'Fecha') }}</th><th>{{ t('Source', 'Origen') }}</th><th>{{ t('Supplier', 'Proveedor') }}</th><th>{{ t('Concept', 'Concepto') }}</th><th class="r">{{ t('Amount', 'Monto') }}</th><th>{{ t('Status', 'Estatus') }}</th></tr>
    {% for r in S.open[:150] %}<tr><td class="num">{{ r.file or '—' }}</td><td>{{ r.date }}</td><td>{{ r.source }}{% if r.ref %} · {{ r.ref }}{% endif %}</td><td class="wrap">{{ r.supplier or '—' }}</td>
      <td class="wrap">{{ r.concept }}{% if r.fee_pct %} <span class="ccy">(+{{ r.fee_pct|round(0)|int }}% {{ r.fee_label or '' }})</span>{% endif %}</td>
      <td class="r num">{{ r.currency or '' }} {{ '{:,.2f}'.format(r.amount or 0) }}</td><td><span class="pill">{{ r.status }}</span></td></tr>{% endfor %}</table></div>
    {% else %}<p class="meta good">{{ t('Nothing waiting to be posted.', 'Nada pendiente por registrar.') }}</p>{% endif %}
    {% endif %}
  </section>
  {% endif %}

  <footer>{{ t('Thelsa Automation Library · the audit runs on imperfect data and flags it.', 'Biblioteca de automatizaciones Thelsa · la auditoría trabaja con datos imperfectos y los señala.') }}</footer>
</main>
<script>
(function(){
  var FX = {plan: {{ ex.fx.plan }}, spot: {{ ex.fx.spot.rate }}, spotAsOf: "{{ ex.fx.spot.asOf or '' }}",
            spotMonth: "{{ ex.fx.spot.month or '' }}", spotSrc: "{{ ex.fx.spot.source or '' }}", spotStale: {{ 'true' if ex.fx.spot.stale else 'false' }}};
  var NOTE = {
    en: {mxn: 'Pesos. Dollar-billed amounts converted at the spot rate <b>'+FX.spot+'</b>.',
         plan: 'USD at the plan rate <b>'+FX.plan+'</b> — the rate the 2026 budget was struck at. Dollar-billed amounts shown as billed.',
         spot: FX.spotStale ? 'USD at <b>'+FX.spot+'</b> as of '+FX.spotAsOf+' (<b>could not fetch the latest month-end rate</b> — today\'s rate shown).'
                            : 'USD at the month-end rate <b>'+FX.spot+'</b> for <b>'+FX.spotMonth+'</b>, the latest completed month ('+FX.spotSrc+'). Updates itself when each month closes.',
         pct: ' Percentages are computed in pesos, so they never move with the exchange rate.'},
    es: {mxn: 'Pesos. Montos facturados en dólares convertidos al tipo de cambio spot <b>'+FX.spot+'</b>.',
         plan: 'USD al tipo de cambio del plan <b>'+FX.plan+'</b> — el tipo con el que se hizo el presupuesto 2026. Montos en dólares tal como se facturaron.',
         spot: FX.spotStale ? 'USD a <b>'+FX.spot+'</b> al '+FX.spotAsOf+' (<b>no se pudo obtener el tipo de cierre de mes</b> — se muestra el de hoy).'
                            : 'USD al tipo de cierre de mes <b>'+FX.spot+'</b> de <b>'+FX.spotMonth+'</b>, el último mes cerrado ('+FX.spotSrc+'). Se actualiza solo al cerrar cada mes.',
         pct: ' Los porcentajes se calculan en pesos, así que no cambian con el tipo de cambio.'}
  };
  function get(k, d){ try { return localStorage.getItem(k) || d; } catch(e){ return d; } }
  function put(k, v){ try { localStorage.setItem(k, v); } catch(e){} }
  var dl = (navigator.language || '').toLowerCase().indexOf('es') === 0 ? 'es' : 'en';
  var S = {lang: get('audit.lang', dl), basis: get('audit.basis', 'mxn'), per: get('audit.period', 'ytd')};
  if (['en','es'].indexOf(S.lang) < 0) S.lang = 'en';
  if (['mxn','plan','spot'].indexOf(S.basis) < 0) S.basis = 'mxn';
  if (['ytd','l12m'].indexOf(S.per) < 0) S.per = 'ytd';

  function render(){
    var loc = S.lang === 'es' ? 'es-MX' : 'en-US';
    document.body.className = 'p-' + S.per + (S.lang === 'es' ? ' es' : '');
    document.documentElement.lang = S.lang;
    var f0 = new Intl.NumberFormat(loc, {maximumFractionDigits: 0});
    document.querySelectorAll('.m').forEach(function(el){
      var x = +el.getAttribute('data-x') || 0, u = +el.getAttribute('data-u') || 0, v;
      if (S.basis === 'mxn') v = x + u * FX.spot;
      else if (S.basis === 'plan') v = u + x / FX.plan;
      else v = u + x / FX.spot;
      el.textContent = (v < 0 ? '−' : '') + '$' + f0.format(Math.abs(Math.round(v))) + (S.basis === 'mxn' ? ' MXN' : ' USD');
    });
    document.querySelectorAll('.n').forEach(function(el){ el.textContent = f0.format(+el.getAttribute('data-n') || 0); });
    document.getElementById('fxNote').innerHTML = NOTE[S.lang][S.basis] + NOTE[S.lang].pct;
    [['perSeg','p','per'],['fxSeg','b','basis'],['langSeg','l','lang']].forEach(function(c){
      document.querySelectorAll('#'+c[0]+' button').forEach(function(b){ b.classList.toggle('on', b.getAttribute('data-'+c[1]) === S[c[2]]); });
    });
  }
  [['perSeg','p','per','audit.period'],['fxSeg','b','basis','audit.basis'],['langSeg','l','lang','audit.lang']].forEach(function(c){
    document.querySelectorAll('#'+c[0]+' button').forEach(function(b){
      b.addEventListener('click', function(){ S[c[2]] = b.getAttribute('data-'+c[1]); put(c[3], S[c[2]]); render(); });
    });
  });
  render();

  var tabs = [].slice.call(document.querySelectorAll('.tab')), btns = [].slice.call(document.querySelectorAll('.tab-btn'));
  function show(n){ if (!tabs.some(function(t){return t.getAttribute('data-tab')===n;})) n = 'overview';
    tabs.forEach(function(t){ t.hidden = t.getAttribute('data-tab') !== n; });
    btns.forEach(function(b){ b.classList.toggle('active', b.getAttribute('data-tab') === n); }); }
  btns.forEach(function(b){ b.addEventListener('click', function(e){ e.preventDefault(); var n=b.getAttribute('data-tab');
    if (history.replaceState) history.replaceState(null,'','#'+n); show(n); }); });
  show((location.hash || '').slice(1));
})();
</script>
</body></html>
"""
