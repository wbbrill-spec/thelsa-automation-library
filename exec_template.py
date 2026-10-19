"""Jinja template for the live /audit page (executive view).

Bilingual: every visible string is rendered in both languages with
{{ t('English', 'Español') }}; the EN/ES toggle just flips a class on <body>.
Money: {{ M(pair) }} renders a span carrying [mxn, usd]; the currency control
(MXN · USD plan · USD spot) formats it in the browser. Period: today, week,
month, quarter and year to date and the last 12 months are all rendered; the
period control shows one.
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
  .per{display:none} body.p-day .per-day, body.p-wtd .per-wtd, body.p-mtd .per-mtd, body.p-qtd .per-qtd,
  body.p-ytd .per-ytd, body.p-l12m .per-l12m{display:block}
  #perSeg{flex-wrap:wrap}
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
    <div class="seg" id="perSeg"><button data-p="day">{{ t('Today', 'Hoy') }}</button><button data-p="wtd">{{ t('Week', 'Semana') }}</button><button data-p="mtd">{{ t('Month', 'Mes') }}</button><button data-p="qtd">{{ t('Quarter', 'Trimestre') }}</button><button data-p="ytd">{{ t('YTD', 'Año') }}</button><button data-p="l12m">{{ t('12 months', '12 meses') }}</button></div>
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
    {% if ex.pc %}<a class="tab-btn" data-tab="checks" href="#checks">{{ t('Supplier cost checks', 'Revisión de costos') }}{% if ex.pc.rows %} ({{ ex.pc.rows|length }}){% endif %}</a>{% endif %}
    {% if ex.sc %}<a class="tab-btn" data-tab="costs" href="#costs">{{ t('Supplier costs', 'Costos de proveedores') }}{% if ex.sc.n_open %} ({{ ex.sc.n_open }}){% endif %}</a>{% endif %}
  </nav>

  <section class="tab" data-tab="overview">
  {% for key in ['day', 'wtd', 'mtd', 'qtd', 'ytd', 'l12m'] %}{% set P = ex.periods[key] %}{% set fin_gap = ex.fin_ledger_to and ex.fin_ledger_to < P['to'] %}{% set fin_none = ex.fin_ledger_to and ex.fin_ledger_to < P['from'] %}
  <div class="per per-{{ key }}">
    <p class="meta" style="margin:8px 0 0">{{ t('Period', 'Periodo') }}: <b>{{ P['from'] }}</b> → <b>{{ P['to'] }}</b></p>

    <h2>{{ t('Revenue & margin', 'Ingresos y margen') }}</h2>
    <div class="grid">
      <div class="tile"><div class="label">{{ t('Invoiced — MoveWare', 'Facturado — MoveWare') }}</div>
        <div class="value">{{ M(P.inv_num) }}</div>
        <div class="sub">{{ N(P.inv_files) }} {{ t('files · numbered invoices dated in the period, net of IVA, cancelled excluded', 'expedientes · facturas con número y fecha en el periodo, sin IVA, sin canceladas') }}</div></div>
      <div class="tile"><div class="label">{{ t('Invoiced — Finance', 'Facturado — Finanzas') }}</div>
        <div class="value">{% if fin_none %}—{% else %}{{ M(P.fin_sales_all) }}{% endif %}</div>
        <div class="sub">{% if fin_none %}{{ t("Finance's ledger runs through", 'El libro de Finanzas llega hasta') }} {{ ex.fin_ledger_to }} {{ t('— this period is not in it yet. MoveWare invoicing (left) is live.', '— este periodo aún no está. La facturación de MoveWare (izquierda) está al día.') }}{% else %}{{ t("Finance's ledger, net of credit notes · files first invoiced in the period", 'Libro de Finanzas, neto de notas de crédito · expedientes con primera factura en el periodo') }}{% if fin_gap %} · {{ t('ledger through', 'datos hasta') }} {{ ex.fin_ledger_to }}{% elif P.fin_last_invoice %} · {{ t('ledger through', 'datos hasta') }} {{ P.fin_last_invoice }}{% endif %}{% endif %}</div></div>
      <div class="tile"><div class="label">{{ t('Gross margin — Finance actuals', 'Margen bruto — real de Finanzas') }}</div>
        <div class="value {{ 'good' if (P.fin_margin_pct or 0) >= 0 else 'bad' }}">{% if fin_none %}—{% else %}{{ M(P.fin_margin) }}{% endif %}</div>
        <div class="sub">{% if fin_none %}{{ t('needs Finance\'s ledger for this period (through', 'requiere el libro de Finanzas de este periodo (hasta') }} {{ ex.fin_ledger_to }})
        {% elif P.fin_margin_pct is not none %}<b>{{ P.fin_margin_pct }}%</b> {{ t('on', 'sobre') }} {{ N(P.fin_costed_n) }} {{ t('costed files', 'expedientes con costo') }}{% if P.fin_neg_n %} · <span class="bad">{{ P.fin_neg_n }} {{ t('below cost', 'bajo costo') }}</span>{% endif %}{% if P.fin_sheet_n %} · {{ P.fin_sheet_n }} {{ t('costed from the MoveWare costing sheet until Finance posts them', 'con costo de la hoja de costeo de MoveWare hasta que Finanzas los registre') }}{% endif %}{% else %}{{ t('no costed files in this period yet', 'aún no hay expedientes con costo en este periodo') }}{% endif %}</div></div>
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
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Client', 'Cliente') }}</th><th>{{ t('Coordinator', 'Coordinador') }}</th><th>{{ t('Loaded', 'Carga') }}</th><th>{{ t('Delivered', 'Entrega') }}</th><th class="r">{{ t('Quote', 'Cotización') }}</th></tr>
    {% for r in P.ready[:40] %}<tr><td class="num">{{ r.job }}{% if r.embassy %} <span class="pill">EMB</span>{% endif %}</td>
      <td class="wrap">{{ r.client }}</td><td>{{ r.coordinator or '—' }}</td><td>{% if r.pack_na and not r.pack %}<span class="meta">{{ t('n/a', 'n/a') }} ({{ r.job_type }})</span>{% else %}{{ r.pack or '—' }}{% endif %}{% if r.pack_src %} <span class="pill" title="Date from SIT Plan de Viajes; not yet in MoveWare / Fecha del Plan de Viajes de SIT; aún no en MoveWare">SIT</span>{% elif r.pack_planned %} <span class="pill" title="Planned date only: not marked completed in MoveWare / Solo fecha planeada: no marcada como completada en MoveWare">{{ t('planned', 'planeada') }}</span>{% endif %}</td><td>{% if r.delivery_na and not r.delivery %}<span class="meta">{{ t('n/a', 'n/a') }} ({{ r.job_type }})</span>{% else %}{{ r.delivery or '—' }}{% endif %}{% if r.delivery_src %} <span class="pill" title="Date from SIT Plan de Viajes; not yet in MoveWare / Fecha del Plan de Viajes de SIT; aún no en MoveWare">SIT</span>{% elif r.delivery_planned %} <span class="pill" title="Planned date only: not marked completed in MoveWare / Solo fecha planeada: no marcada como completada en MoveWare">{{ t('planned', 'planeada') }}</span>{% endif %}</td>
      <td class="r num">{{ M(r.value) }}{% if r.ccy %}<span class="ccy">· {{ t('quoted in', 'cotizado en') }} {{ r.ccy }}</span>{% endif %}</td></tr>{% endfor %}</table></div>
    {% if P.ready|length > 40 %}<p class="meta">{{ t('Showing top 40 of', 'Se muestran 40 de') }} {{ P.ready|length }}.</p>{% endif %}
    {% if P.ready_planned_n %}<p class="meta"><b>{{ P.ready_planned_n }}</b> {{ t('of these have a pack or delivery date that is only planned: nobody has marked it completed in MoveWare. Check the move really happened before invoicing.', 'de estos tienen una fecha de empaque o entrega solo planeada: nadie la marcó como completada en MoveWare. Confirmar que la mudanza ocurrió antes de facturar.') }}</p>{% endif %}
    {% if P.ready_unread_n %}<p class="meta">{{ t('Checking completed diary dates for', 'Revisando fechas completadas del diario de') }} {{ P.ready_unread_n }} {{ t('files — reload in a few minutes.', 'expedientes — recargue en unos minutos.') }}</p>{% endif %}
    <p class="meta">{{ t('Agent exports (EXA) have no delivery date and agent imports (IMA) no pack date, by design.', 'Las exportaciones de agente (EXA) no llevan fecha de entrega y las importaciones de agente (IMA) no llevan fecha de empaque, por diseño.') }}</p>
    {% endif %}

    {% if P.checks %}
    <h2>{{ t('Quotes to check before billing', 'Cotizaciones a revisar antes de facturar') }}</h2>
    <p class="meta" style="margin-top:-4px">{{ t('The accepted quote on these files is larger than any normal move (over MXN', 'La cotización aceptada es mayor que cualquier mudanza normal (más de MXN') }} {{ '{:,.0f}'.format(ex.quote_check_mxn) }}) — {{ t('likely keyed in the wrong currency or with a typo. Kept out of the totals above.', 'probablemente capturada en otra moneda o con error. Excluidas de los totales.') }}</p>
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Client', 'Cliente') }}</th><th>{{ t('Coordinator', 'Coordinador') }}</th><th>{{ t('Status', 'Estatus') }}</th><th>{{ t('Loaded', 'Carga') }}</th><th class="r">{{ t('Quote in MoveWare', 'Cotización en MoveWare') }}</th></tr>
    {% for r in P.checks %}<tr><td class="num">{{ r.job }}{% if r.embassy %} <span class="pill">EMB</span>{% endif %}</td><td class="wrap">{{ r.client }}</td><td>{{ r.coordinator or '—' }}</td><td>{{ r.status or '—' }}</td><td>{{ r.pack or '—' }}</td>
      <td class="r num bad">{{ M(r.value) }}{% if r.ccy %}<span class="ccy">· {{ t('quoted in', 'cotizado en') }} {{ r.ccy }}</span>{% else %}<span class="ccy">{{ t('no currency', 'sin moneda') }}</span>{% endif %}</td></tr>{% endfor %}</table></div>
    {% endif %}

    {% if P.emb %}
    <h2>{{ t('Embassy files in transit', 'Expedientes de embajada en tránsito') }}</h2>
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Client', 'Cliente') }}</th><th>{{ t('Loaded', 'Carga') }}</th><th class="r">{{ t('Quote', 'Cotización') }}</th></tr>
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

  {% if ex.pc %}{% set C = ex.pc %}
  <section class="tab" data-tab="checks">
    <h2>{{ t('Supplier cost checks — MoveWare Move Costing Sheet', 'Revisión de costos de proveedores — Move Costing Sheet de MoveWare') }}</h2>
    <p class="meta">{{ t('Cost options with status W only. Sheet in use:', 'Solo opciones de costo con estatus W. Hoja en uso:') }} {{ C.period.won_from or '?' }} → {{ C.period.won_to or '?' }}{% if C.loaded_at %} · {{ t('loaded', 'cargada') }} {{ C.loaded_at[:10] }}{% endif %}{% if C.source %} · {{ C.source }}{% endif %}.
      {{ t('Until MoveWare returns cost through its API, this sheet is also the interim cost for files Finance has not costed yet.', 'Hasta que MoveWare entregue costos por su API, esta hoja también es el costo provisional de los expedientes que Finanzas aún no costea.') }}</p>
    <div class="grid">
      {% for k, lab in C.labels.items() %}<div class="tile"><div class="label">{{ t(lab[0], lab[1]) }}</div><div class="value {{ 'warn' if C.counts.get(k) else 'good' }}">{{ N(C.counts.get(k, 0)) }}</div></div>{% endfor %}
    </div>
    {% if C.rows %}
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Check', 'Revisión') }}</th><th>{{ t('Line', 'Concepto') }}</th><th>{{ t('Supplier', 'Proveedor') }}</th><th>{{ t('Approval no.', 'No. aprobación') }}</th><th class="r">{{ t('Quoted', 'Cotizado') }}</th><th class="r">{{ t('Actual', 'Real') }}</th><th>{{ t('Note', 'Nota') }}</th></tr>
    {% for r in C.rows[:200] %}<tr><td class="num">{{ r.job }}</td><td>{{ t(C.labels[r.check][0], C.labels[r.check][1]) }}</td><td class="wrap">{{ r.line }}</td><td class="wrap">{{ r.creditor or '—' }}</td><td class="num">{{ r.po or '—' }}</td>
      <td class="r num">{{ '{:,.2f}'.format(r.quoted) if r.quoted is not none else '—' }}</td><td class="r num">{{ '{:,.2f}'.format(r.actual) if r.actual is not none else '—' }}</td>
      <td class="wrap">{% if r.pct %}+{{ r.pct }}%{% elif r.other_files %}{{ t('also on', 'también en') }} {{ r.other_files|join(', ') }}{% elif r.times %}{{ t('entered', 'cargado') }} {{ r.times }}× {% if r.pos %}({{ r.pos|join(', ') }}){% endif %} — {{ t('correct the original line in MoveWare, don\'t add a new one', 'corregir la línea original en MoveWare, no abrir otra') }}{% elif r.days %}{{ r.days }} {{ t('days since delivery', 'días desde la entrega') }}{% endif %}</td></tr>{% endfor %}</table></div>
    {% else %}<p class="meta good">{{ t('No findings.', 'Sin hallazgos.') }}</p>{% endif %}
    <h2>{{ t('Costing sheet vs Finance — cost per file', 'Hoja de costeo vs Finanzas — costo por expediente') }}</h2>
    <p class="meta">{{ t('Actual cost loaded in MoveWare against the total cost (supplier + intercompany) in Finance\'s margin report, in pesos. Dollar files at the rate Finance booked on the file\'s invoices. Finance source: ' ~ C.finance_source ~ '.', 'Costo real cargado en MoveWare contra el costo total (proveedor + intercompañía) del reporte de margen de Finanzas, en pesos. Expedientes en dólares al tipo de cambio de sus facturas. Fuente de Finanzas: ' ~ C.finance_source ~ '.') }}</p>
    <div class="grid">{% for k, lab in C.recon_labels.items() %}{% if C.recon_counts.get(k) %}<div class="tile"><div class="label">{{ t(lab[0], lab[1]) }}</div><div class="value {{ 'good' if k == 'match' else 'warn' }}">{{ N(C.recon_counts.get(k)) }}</div></div>{% endif %}{% endfor %}</div>
    {% if C.recon_rows %}
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Finding', 'Hallazgo') }}</th><th>{{ t('Cost lines loaded', 'Líneas con costo') }}</th><th class="r">{{ t('MoveWare (MXN)', 'MoveWare (MXN)') }}</th><th class="r">{{ t('Finance (MXN)', 'Finanzas (MXN)') }}</th><th class="r">{{ t('Difference', 'Diferencia') }}</th></tr>
    {% for r in C.recon_rows %}<tr><td class="num">{{ r.job }}</td><td>{{ t(C.recon_labels[r.finding][0], C.recon_labels[r.finding][1]) }}</td><td>{{ r.lines }}</td>
      <td class="r num">{{ '{:,.0f}'.format(r.act_mxn) if r.act_mxn is not none else '—' }}</td><td class="r num">{{ '{:,.0f}'.format(r.finance_cost_mxn) if r.finance_cost_mxn is not none else '—' }}</td>
      <td class="r num">{{ '{:,.0f}'.format(r.difference_mxn) if r.difference_mxn is not none else '—' }}</td></tr>{% endfor %}</table></div>
    {% endif %}
    {% if C.ms and C.ms.steps %}
    <h2>{{ t('Pack & delivery in the MoveWare diary — what the automation would change (dry run)', 'Empaque y entrega en el diario de MoveWare — lo que la automatización cambiaría (simulación)') }}</h2>
    <p class="meta">{{ t('From SIT\'s Plan de Viajes, moves already done only. Nothing is written to MoveWare until Bill approves a test. Dates a coordinator completed are never overwritten — they are listed for review.', 'Del Plan de Viajes de SIT, solo mudanzas ya realizadas. Nada se escribe en MoveWare hasta que Bill apruebe una prueba. Las fechas que un coordinador completó nunca se sobrescriben — se listan para revisión.') }}
      {{ C.ms.summary.create or 0 }} {{ t('to create', 'por crear') }} · {{ C.ms.summary.update or 0 }} {{ t('to update', 'por actualizar') }} · {{ C.ms.summary.review or 0 }} {{ t('to review', 'por revisar') }}.</p>
    <div class="tbl"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Coordinator', 'Coordinador') }}</th><th>{{ t('Action', 'Acción') }}</th><th>{{ t('Would do', 'Haría') }}</th><th>{{ t('Plan de Viajes', 'Plan de Viajes') }}</th><th>{{ t('MoveWare today', 'MoveWare hoy') }}</th><th>{{ t('Why', 'Motivo') }}</th></tr>
    {% for r in C.ms.steps %}<tr><td class="num">{{ r.job }}</td><td>{{ r.coordinator|title }}</td><td>{{ r.code }}</td><td><span class="pill">{{ {'create': t('create', 'crear'), 'update': t('update', 'actualizar'), 'review': t('review', 'revisar')}[r.action] }}</span></td>
      <td>{{ r.plan_date or '—' }}</td><td>{{ r.mw_date or '—' }}{% if r.mw_completed == 'Y' %} ✓{% endif %}</td><td class="wrap">{{ r.why }}</td></tr>{% endfor %}</table></div>
    {% endif %}
    <h2>{{ t('Load a new Move Costing Sheet', 'Cargar un Move Costing Sheet nuevo') }}</h2>
    <form method="post" action="/audit/upload-costing" enctype="multipart/form-data" class="row">
      <input type="file" name="file" accept=".xlsx" required> <button type="submit">{{ t('Upload', 'Subir') }}</button></form>
    <p class="meta">{{ t('The workbook is read and not kept; customer names are never stored.', 'El archivo se lee y no se guarda; los nombres de clientes nunca se almacenan.') }}</p>
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
  if (['day','wtd','mtd','qtd','ytd','l12m'].indexOf(S.per) < 0) S.per = 'ytd';

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
