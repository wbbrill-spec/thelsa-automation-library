"""
web.py — the /commissions pages.

    /commissions            the report: months, distribution, points to look at, lines
    /commissions/upload     Finance uploads the monthly margin report (POST)
    /commissions/bookings   bookings per month, typed in (POST)
    /commissions/workbook   the same report as an Excel workbook

Who may open it: only the addresses in COMMISSION_USERS (comma separated, set in
Render). Without that setting it is Lupita, Mario, Rogelio and Bill. Anyone else
who is signed in to the library gets "not found", not "forbidden": the page is
not advertised.

The uploaded workbook is read in memory and thrown away. What is kept is the
list of lines the reader makes from it, which holds no customer names.

Wiring (app.py):
    from commissions.web import commissions_bp
    app.register_blueprint(commissions_bp)
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import io
import os
import re
import secrets

from flask import (Blueprint, abort, make_response, redirect, render_template_string, request,
                   send_file, session, url_for)

from . import engine as E
from . import finance as F
from . import report as R
from . import store

commissions_bp = Blueprint("commissions", __name__)

DEFAULT_USERS = ("maria.gonzalez@thelsa.com", "mariotorres@thelsa.com", "rogeliofranco@thelsa.com",
                 "bbrill@thelsa.com", "bill.brill@inflectionpointnow.com")
MAX_UPLOAD = 20 * 1024 * 1024
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_AMOUNT = re.compile(r"^(\d{1,3}(,\d{3})+|\d+)(\.\d{1,2})?$")
MONTH_NAMES = ("January February March April May June July August September October November December").split()


def allowed_users() -> set:
    raw = os.environ.get("COMMISSION_USERS", "")
    listed = {e.strip().lower() for e in raw.replace(";", ",").split(",") if e.strip()}
    return listed or set(DEFAULT_USERS)


def _restricted(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        email = (session.get("user_email") or "").strip().lower()
        if not email:
            return redirect(url_for("login", next=request.url))
        if email not in allowed_users():
            abort(404)
        return f(email, *args, **kwargs)
    return wrapped


def _csrf() -> str:
    if "commission_csrf" not in session:
        session["commission_csrf"] = secrets.token_urlsafe(24)
    return session["commission_csrf"]


def _check_csrf() -> None:
    sent = request.form.get("csrf") or ""
    if not sent or not secrets.compare_digest(sent, session.get("commission_csrf", "")):
        abort(400, "The form expired. Reload the page and try again.")


def _say(en: str, es: str, bad: bool = False) -> None:
    session["commission_msg"] = {"en": en, "es": es, "bad": bad}


def settings() -> E.Settings:
    return E.Settings(budgets=dict(E.BUDGET_2026)).check()


def _current():
    """(lines, meta, error). Lines are None when nothing usable is stored."""
    doc, meta = store.get("finance_report")
    if not doc:
        return None, None, None
    try:
        return F.from_records(doc.get("records") or []), meta, None
    except F.FinanceReportError:
        return None, meta, ("The stored report could not be read back. Please upload Finance's report again.",
                            "No se pudo leer el reporte guardado. Suba de nuevo el reporte de Finanzas.")


MONTH_NAMES_ES = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()


def _month_label(m: str) -> str:
    return f"{MONTH_NAMES[int(m[5:7]) - 1]} {m[:4]}"


def _month_label_es(m: str) -> str:
    return f"{MONTH_NAMES_ES[int(m[5:7]) - 1]} {m[:4]}"


# ── pages ────────────────────────────────────────────────────────────────────
@commissions_bp.route("/commissions")
@_restricted
def commissions_home(email):
    lines, meta, error = _current()
    s = settings()
    bookings, bmeta = store.get("bookings", {})
    rep = R.build(lines, s, bookings) if lines else None
    months = sorted(s.budgets)
    resp = make_response(render_template_string(
        PAGE, rep=rep, meta=meta, error=error, msg=session.pop("commission_msg", None), csrf=_csrf(),
        s=s, bookings=R.clean_bookings(bookings), bmeta=bmeta, months=months, month_label=_month_label,
        month_label_es=_month_label_es, durable=store.durable(), new_from=_month_label(R.NEW_FROM),
        new_from_es=_month_label_es(R.NEW_FROM), email=email))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@commissions_bp.route("/commissions/upload", methods=["POST"])
@_restricted
def commissions_upload(email):
    _check_csrf()
    f = request.files.get("report")
    if f is None or not (f.filename or "").strip():
        _say("No file was chosen.", "No se eligió ningún archivo.", bad=True)
        return redirect(url_for("commissions.commissions_home"))
    if not f.filename.lower().endswith(".xlsx"):
        _say("The report must be an Excel workbook (.xlsx).", "El reporte debe ser un libro de Excel (.xlsx).", bad=True)
        return redirect(url_for("commissions.commissions_home"))
    data = f.stream.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        _say("The file is larger than 20 MB. Finance's margin report is far smaller; please check the file.",
             "El archivo pesa más de 20 MB. El reporte de margen de Finanzas es mucho más pequeño; revise el archivo.",
             bad=True)
        return redirect(url_for("commissions.commissions_home"))
    try:
        lines = F.load_base(io.BytesIO(data))
    except F.FinanceReportError as e:          # these messages carry no text from the workbook
        _say(f"The report was not loaded: {e}. Nothing was changed.",
             f"El reporte no se cargó: {e.es}. No se cambió nada.", bad=True)
        return redirect(url_for("commissions.commissions_home"))
    except Exception:                           # not a workbook, damaged, password-protected…
        _say("The file could not be opened as an Excel workbook. Nothing was changed.",
             "El archivo no se pudo abrir como libro de Excel. No se cambió nada.", bad=True)
        return redirect(url_for("commissions.commissions_home"))
    rep = R.build(lines, settings(), store.get("bookings", {})[0])
    previous, _ = store.get("finance_report")
    if previous:
        store.put("finance_report_previous", previous, by=email)
    digest = hashlib.sha256(data).hexdigest()
    store.put("finance_report", {"records": F.to_records(lines), "sha256": digest}, by=email)
    log, _ = store.get("uploads", [])
    log.append({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "by": email,
                "lines": len(lines), "sha256": digest[:16]})
    store.put("uploads", log[-50:], by=email)
    src = rep["source"]
    _say(f"Report loaded: {src['lines']:,} lines dated {src['from']} to {src['to']}, "
         f"{src['jobs_in_scope']} Corporate and Private jobs, {rep['check_count']} points to look at.",
         f"Reporte cargado: {src['lines']:,} líneas con fecha del {src['from']} al {src['to']}, "
         f"{src['jobs_in_scope']} expedientes corporativos y particulares, {rep['check_count']} puntos por revisar.")
    return redirect(url_for("commissions.commissions_home"))


@commissions_bp.route("/commissions/bookings", methods=["POST"])
@_restricted
def commissions_bookings(email):
    _check_csrf()
    s = settings()
    out, bad = {}, []
    for m in sorted(s.budgets):
        raw = (request.form.get(f"b_{m}") or "").replace("$", "").replace(" ", "")
        if not raw:
            continue                                   # blank = not known yet
        if not _AMOUNT.match(raw) or float(raw.replace(",", "")) <= 0:
            bad.append(m)
            continue
        out[m] = float(raw.replace(",", ""))
    if bad:
        _say("Bookings were not saved. These months do not hold an amount such as 2,150,000.00: "
             + ", ".join(_month_label(m) for m in bad) + ".",
             "No se guardaron las ventas contratadas. Estos meses no tienen un importe como 2,150,000.00: "
             + ", ".join(_month_label_es(m) for m in bad) + ".", bad=True)
    else:
        store.put("bookings", out, by=email)
        _say(f"Bookings saved for {len(out)} month(s). Months left blank use the budget as a placeholder.",
             f"Ventas contratadas guardadas para {len(out)} mes(es). Los meses en blanco usan el presupuesto "
             "como valor provisional.")
    return redirect(url_for("commissions.commissions_home") + "#bookings")


@commissions_bp.route("/commissions/workbook")
@_restricted
def commissions_workbook(email):
    lines, _meta, error = _current()
    if not lines:
        en, es = error or ("Upload Finance's margin report first.", "Primero suba el reporte de margen de Finanzas.")
        _say(en, es, bad=True)
        return redirect(url_for("commissions.commissions_home"))
    from . import trial_workbook
    buf = io.BytesIO()
    trial_workbook.build(None, buf, lines=lines, bookings=store.get("bookings", {})[0])
    buf.seek(0)
    last = max((ln.date for ln in lines if ln.date), default=dt.date.today())
    resp = send_file(buf, as_attachment=True, download_name=f"TMS commission report to {last.isoformat()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── formatting ───────────────────────────────────────────────────────────────
@commissions_bp.app_template_filter("cm_money")
def _money(v, decimals=0):
    if v is None:
        return ""
    if abs(v) < 0.5 * 10 ** -decimals:
        return "–"
    t = f"{abs(v):,.{decimals}f}"
    return f"({t})" if v < 0 else t


@commissions_bp.app_template_filter("cm_pct")
def _pct(v):
    return "" if v is None else (f"({abs(v) * 100:.1f}%)" if v < 0 else f"{v * 100:.1f}%")


@commissions_bp.app_template_filter("cm_when")
def _when(v):
    if not v:
        return ""
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v.astimezone(dt.timezone(dt.timedelta(hours=-6))).strftime("%Y-%m-%d %H:%M")


PAGE = r"""<!doctype html>
{%- macro t(en, es) -%}<span class="en">{{ en }}</span><span class="es">{{ es }}</span>{%- endmacro -%}
{%- macro M(v, u, d=0) -%}{% if v is none %}{% else %}<span class="m" data-x="{{ '%.6f'|format(v) }}" data-u="{{ '%.6f'|format(u) if u is not none else '' }}" data-d="{{ d }}">{{ v|cm_money(d) }}</span>{% endif %}{%- endmacro -%}
{%- macro mon(m) -%}{{ t(month_label(m), month_label_es(m)) }}{%- endmacro -%}
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TMS Commission Report</title>
<style>
:root{--ink:#1b2430;--soft:#5b6675;--line:#dfe3e8;--bg:#f5f6f8;--card:#fff;--navy:#1f3864;--warn:#8a3b12;--warnbg:#fdf0e6;
--ok:#1d5c3a;--okbg:#e8f4ec;--bad:#8c1d18;--badbg:#fbe9e7;--hl:#fff8d6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,Arial,sans-serif}
span.es{display:none}body.es span.en{display:none}body.es span.es{display:inline}
.usdnote{display:none}body.usd .usdnote{display:block}
.wrap{max-width:1240px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 2px}h2{font-size:16px;margin:0 0 10px}.sub{color:var(--soft);margin:0 0 16px}
a{color:var(--navy)}.top{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px}
.ctl{display:flex;gap:10px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
.seg{display:inline-flex;border:1px solid var(--navy);border-radius:7px;overflow:hidden}
.seg button{background:#fff;color:var(--navy);border:0;border-radius:0;padding:5px 12px;font:inherit;font-weight:600;cursor:pointer}
.seg button.on{background:var(--navy);color:#fff}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;margin:14px 0}
.note{border-radius:8px;padding:10px 14px;margin:12px 0;border:1px solid}
.note.warn{background:var(--warnbg);border-color:#f1cfb4;color:var(--warn)}
.note.ok{background:var(--okbg);border-color:#bfe0cb;color:var(--ok)}.note.bad{background:var(--badbg);border-color:#f2c1bd;color:var(--bad)}
.note.info{background:#eef3fb;border-color:#c9d8ef;color:var(--navy)}
.note ul{margin:6px 0 0 18px;padding:0}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{background:var(--navy);color:#fff;font-weight:600;font-size:12px;white-space:normal;vertical-align:bottom}
th:first-child,td:first-child,td.l,th.l{text-align:left}td.wrapc{white-space:normal;text-align:left;min-width:260px}
tr.tot td{font-weight:700;background:#eef1f6;border-top:2px solid var(--navy)}tr.old td{color:var(--soft)}
td.ph{background:var(--hl)}table.sum th,table.sum td{padding:6px 5px;font-size:13px}table.sum th{font-size:11.5px}
table.chk td:first-child{width:110px}table.chk td:last-child{width:150px}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;background:#eef1f6;color:var(--soft)}
.tag.w{background:var(--warnbg);color:var(--warn)}
.big{display:flex;flex-wrap:wrap;gap:12px}.big div{flex:1 1 180px;background:#f8f9fb;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.big b{display:block;font-size:20px}.big div>span{color:var(--soft);font-size:12px}
details{margin:8px 0}summary{cursor:pointer;font-weight:600;padding:4px 0}
input[type=text]{width:150px;padding:5px 7px;border:1px solid #b9c0ca;border-radius:5px;text-align:right;font:inherit}
input[type=file]{font:inherit}button.go,.btn{background:var(--navy);color:#fff;border:0;border-radius:6px;padding:8px 14px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
.btn.ghost{background:#fff;color:var(--navy);border:1px solid var(--navy)}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.small{font-size:12px;color:var(--soft)}
</style></head><body><div class="wrap">
<div class="top"><div><h1>{{ t('TMS Commission Report', 'Reporte de comisiones TMS') }}</h1>
<p class="sub">{{ t('Corporate accounts and Private customers. US Embassy and agent business are not included.',
'Cuentas corporativas y clientes particulares. No incluye la Embajada de EE. UU. ni el negocio de agentes.') }}</p></div>
<div><div class="ctl">
<div class="seg" id="ccySeg" title="MXN / USD"><button type="button" data-c="mxn">MXN</button><button type="button" data-c="usd">USD</button></div>
<div class="seg" id="langSeg" title="English / Español"><button type="button" data-l="en">EN</button><button type="button" data-l="es">ES</button></div>
</div><div class="small" style="text-align:right;margin-top:6px">{{ email }} · <a href="/">{{ t('Automation Library', 'Biblioteca de automatizaciones') }}</a></div></div></div>

{% if msg %}<div class="note {{ 'bad' if msg.bad else 'ok' }}">{{ t(msg.en, msg.es) }}</div>{% endif %}
{% if error %}<div class="note bad">{{ t(error[0], error[1]) }}</div>{% endif %}
{% if not durable %}<div class="note warn">{{ t('This service has no database connected, so an uploaded report is lost when the service is rebuilt.',
'Este servicio no tiene una base de datos conectada, así que el reporte subido se pierde cuando el servicio se reconstruye.') }}</div>{% endif %}

{% if rep %}
<div class="note info usdnote">{{ t('Shown in US dollars: the pesos of each month divided by the middle exchange rate of that month\'s dollar invoices in Finance\'s report',
'Se muestra en dólares: los pesos de cada mes entre el tipo de cambio central de las facturas en dólares de ese mes en el reporte de Finanzas') }}
({% for r in rep.months %}{{ mon(r.month) }} {{ '%.2f'|format(r.rate) }}{% if not loop.last %} · {% endif %}{% endfor %}).
{{ t('The commission is calculated in pesos; percentages do not change with the currency shown. The check list and the bookings table stay in pesos.',
'La comisión se calcula en pesos; los porcentajes no cambian con la moneda mostrada. La lista de puntos por revisar y la tabla de ventas contratadas siguen en pesos.') }}</div>

<div class="note warn"><b>{{ t('Trial. Not for payment.', 'Prueba. No usar para pago.') }}</b>
<ul>
{% if rep.placeholder_months %}<li>{{ t('Bookings are not entered for ' ~ rep.placeholder_months|length ~ ' month(s). Those months use the budget as a placeholder, which gives a sales reach of 0.60 (yellow cells).',
'Faltan las ventas contratadas de ' ~ rep.placeholder_months|length ~ ' mes(es). Esos meses usan el presupuesto como valor provisional, lo que da un alcance de ventas de 0.60 (celdas amarillas).') }}</li>{% endif %}
<li>{{ t('No invoice is marked as collected yet, so nothing is payable.', 'Ninguna factura está marcada como cobrada todavía, así que no hay nada por pagar.') }}</li>
<li>{{ t('Costs arrive about two months after the invoice. Recent months show too much margin until their costs are posted.',
'Los costos llegan unos dos meses después de la factura. Los meses recientes muestran margen de más hasta que se registran sus costos.') }}</li>
<li>{{ t('The margin part follows the sample workbook (file 111000 pays 765.00). To be confirmed.',
'La parte de margen sigue el libro de ejemplo (el expediente 111000 paga 765.00). Por confirmar.') }}</li>
</ul></div>

<div class="card"><h2>{{ t(new_from ~ ' onwards: new calculation', 'A partir de ' ~ new_from_es ~ ': cálculo nuevo') }}</h2>
<div class="big">
<div><b>{{ M(rep.total_new.billed, rep.total_new.usd.billed) }}</b><span>{{ t('Billed', 'Facturado') }} (<span class="cur">MXN</span>)</span></div>
<div><b>{{ rep.total_new.margin_pct|cm_pct }}</b><span>{{ t('Gross margin on cost posted so far', 'Margen bruto con el costo registrado hasta hoy') }}</span></div>
<div><b>{{ M(rep.total_new.total, rep.total_new.usd.total, 2) }}</b><span>{{ t('Commission calculated', 'Comisión calculada') }} (<span class="cur">MXN</span>)</span></div>
<div><b>{{ M(rep.total_new.payable, rep.total_new.usd.payable, 2) }}</b><span>{{ t('Payable now (collected)', 'Por pagar hoy (cobrado)') }}</span></div>
<div><b>{{ rep.total_new.waiting_lines }}</b><span>{{ t('Lines waiting for cost', 'Líneas en espera de costo') }} ({{ M(rep.total_new.waiting_billed, rep.total_new.usd.waiting_billed) }} {{ t('billed', 'facturado') }})</span></div>
</div></div>

<div class="card"><h2>{{ t('By invoice month', 'Por mes de factura') }} (<span class="cur">MXN</span>)</h2><div class="scroll"><table class="sum">
<tr><th>{{ t('Month', 'Mes') }}</th><th class="l">{{ t('Rules', 'Reglas') }}</th><th>{{ t('Lines', 'Líneas') }}</th><th>{{ t('Billed', 'Facturado') }}</th><th>{{ t('Actual cost', 'Costo real') }}</th><th>{{ t('Gross margin', 'Margen bruto') }}</th><th>{{ t('% margin', '% margen') }}</th>
<th>{{ t('Bookings', 'Ventas contratadas') }}</th><th>{{ t('Sales reach', 'Alcance de ventas') }}</th><th>{{ t('Invoicing part', 'Parte facturación') }}</th><th>{{ t('Margin part', 'Parte margen') }}</th><th>{{ t('Discipline part', 'Parte disciplina') }}</th><th>{{ t('Total calculated', 'Total calculado') }}</th><th>{{ t('Payable', 'Por pagar') }}</th><th>{{ t('Waiting for cost', 'En espera de costo') }}</th></tr>
{% for r in rep.months %}<tr class="{{ '' if r.new else 'old' }}">
<td>{{ mon(r.month) }}</td><td class="l">{{ t(r.rule, r.rule_es) }}</td><td>{{ r.lines }}</td><td>{{ M(r.billed, r.usd.billed) }}</td><td>{{ M(r.cost, r.usd.cost) }}</td>
<td>{{ M(r.gross_margin, r.usd.gross_margin) }}</td><td>{{ r.margin_pct|cm_pct }}</td>
<td class="{{ '' if r.bookings_known else 'ph' }}">{% if r.bookings_known %}{{ M(r.bookings, r.usd.bookings) }}{% else %}{{ t('= budget', '= presupuesto') }}{% endif %}</td>
<td class="{{ '' if r.bookings_known else 'ph' }}">{{ '%.4f'|format(r.reach) }}</td>
<td>{{ M(r.invoicing, r.usd.invoicing) }}</td><td>{{ M(r.margin, r.usd.margin) }}</td><td>{{ M(r.discipline, r.usd.discipline) }}</td><td>{{ M(r.total, r.usd.total, 2) }}</td>
<td>{{ M(r.payable, r.usd.payable, 2) }}</td><td>{% if r.waiting_lines %}{{ r.waiting_lines }} ({{ M(r.waiting_billed, r.usd.waiting_billed) }}){% else %}–{% endif %}</td></tr>{% endfor %}
{% for label, label_es, tt in (('All months', 'Todos los meses', rep.total_all), (new_from ~ ' onwards', 'A partir de ' ~ new_from_es, rep.total_new)) %}<tr class="tot">
<td colspan="2">{{ t(label, label_es) }}</td><td>{{ tt.lines }}</td><td>{{ M(tt.billed, tt.usd.billed) }}</td><td>{{ M(tt.cost, tt.usd.cost) }}</td><td>{{ M(tt.gross_margin, tt.usd.gross_margin) }}</td>
<td>{{ tt.margin_pct|cm_pct }}</td><td></td><td></td><td>{{ M(tt.invoicing, tt.usd.invoicing) }}</td><td>{{ M(tt.margin, tt.usd.margin) }}</td><td>{{ M(tt.discipline, tt.usd.discipline) }}</td>
<td>{{ M(tt.total, tt.usd.total, 2) }}</td><td>{{ M(tt.payable, tt.usd.payable, 2) }}</td><td>{{ tt.waiting_lines }} ({{ M(tt.waiting_billed, tt.usd.waiting_billed) }})</td></tr>{% endfor %}
</table></div>
{% if rep.months_without_budget %}<p class="small">{{ t('No budget is set for ' ~ rep.months_without_budget|join(', ') ~ '; those months are not calculated.',
'No hay presupuesto para ' ~ rep.months_without_budget|join(', ') ~ '; esos meses no se calculan.') }}</p>{% endif %}
<p class="small">{% if s.sales_fx == 'internal' %}{{ t('US-dollar invoices at ' ~ '%.2f'|format(s.usd_mxn) ~ '. Costs as Finance booked them, at the rate of the day of each cost invoice.',
'Facturas en dólares a ' ~ '%.2f'|format(s.usd_mxn) ~ '. Costos como los registró Finanzas, al tipo de cambio del día de cada factura de costo.') }}{% else %}{{ t('Sales and costs in dollars are in pesos as Finance booked them, each at the rate of its own invoice day.',
'Las ventas y los costos en dólares están en pesos como los registró Finanzas, cada uno al tipo de cambio del día de su factura.') }}{% endif %}</p></div>

<div class="card"><h2>{{ t('Distribution, ' ~ new_from ~ ' onwards', 'Distribución, a partir de ' ~ new_from_es) }} (<span class="cur">MXN</span>)</h2><div class="scroll"><table>
<tr><th>{{ t('Role and name', 'Puesto y nombre') }}</th><th class="l">{{ t('Group', 'Grupo') }}</th><th>{{ t('Share of the group', 'Parte del grupo') }}</th><th>{{ t('If every invoice were collected', 'Si se cobraran todas las facturas') }}</th><th>{{ t('Payable now', 'Por pagar hoy') }}</th></tr>
{% for p in rep.people %}<tr><td>{{ t(p.name, p.name_es) }}</td><td class="l">{{ t(p.group, p.group_es) }}</td><td>{{ p.share|cm_pct }}</td><td>{{ M(p.if_collected, p.usd.if_collected, 2) }}</td><td>{{ M(p.payable, p.usd.payable, 2) }}</td></tr>{% endfor %}
<tr class="tot"><td colspan="3">Total</td><td>{{ M(rep.total_new.total, rep.total_new.usd.total, 2) }}</td><td>{{ M(rep.total_new.payable, rep.total_new.usd.payable, 2) }}</td></tr>
</table></div><p class="small">{{ t('Sales two thirds, Adm, MC and Buyer one third. Control (total paid out minus total calculated):',
'Ventas dos tercios; Adm, MC y Compras un tercio. Control (total repartido menos total calculado):') }} {{ rep.control|cm_money(2) }}.</p></div>

<div class="card" id="checks"><h2>{{ t('Points to look at in Finance\'s report', 'Puntos por revisar en el reporte de Finanzas') }} ({{ rep.check_count }})</h2>
<p class="small">{{ t('File numbers, document numbers and amounts only. Row numbers are rows of the sheet "Base". Amounts here are always in pesos.',
'Solo números de expediente, de documento e importes. Los números de fila son filas de la hoja "Base". Aquí los importes siempre están en pesos.') }}</p>
{% for c in rep.checks %}<details {{ '' if c.routine else 'open' }}><summary>{{ t(c.label, c.label_es) }} ({{ c['items']|length }})</summary>
<div class="scroll"><table class="chk"><tr><th>{{ t('File', 'Expediente') }}</th><th class="l">{{ t('Detail', 'Detalle') }}</th><th>{{ t('Amount (MXN)', 'Importe (MXN)') }}</th></tr>
{% for i in c['items'] %}<tr><td>{{ i.job }}</td><td class="wrapc">{{ t(i.detail, i.detail_es) }}</td><td>{{ i.amount|cm_money(2) if i.amount is not none else '' }}</td></tr>{% endfor %}
</table></div></details>{% endfor %}</div>
{% endif %}

<div class="card" id="upload"><h2>{{ t('Finance\'s margin report', 'Reporte de margen de Finanzas') }}</h2>
{% if meta %}<p>{{ t('In use: uploaded', 'En uso: subido el') }} {{ meta.at|cm_when }} ({{ t('Mexico City time', 'hora de Ciudad de México') }}) {{ t('by', 'por') }} {{ meta.by }}{% if rep %} · {{ '{:,}'.format(rep.source.lines) }} {{ t('lines dated', 'líneas con fecha del') }} {{ rep.source['from'] }} {{ t('to', 'al') }} {{ rep.source['to'] }} · {{ rep.source.jobs_in_scope }} {{ t('Corporate and Private jobs', 'expedientes corporativos y particulares') }}{% endif %}.</p>
{% else %}<p>{{ t('No report has been uploaded yet.', 'Todavía no se ha subido ningún reporte.') }}</p>{% endif %}
<form method="post" action="/commissions/upload" enctype="multipart/form-data" class="row">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="file" name="report" accept=".xlsx" required>
<button type="submit" class="go">{{ t('Upload report', 'Subir reporte') }}</button>
{% if rep %}<a class="btn ghost" href="/commissions/workbook">{{ t('Download as Excel', 'Descargar en Excel') }}</a>{% endif %}</form>
<p class="small">{{ t('Upload the workbook "Margen x Expediente" with its sheet "Base", the whole year to date each time. It replaces the report in use. The workbook is read and not kept; only file numbers, document numbers, dates and amounts are stored, never customer names. If a number cannot be read, nothing is loaded and the page says which row. The Excel download is in English and in pesos.',
'Suba el libro "Margen x Expediente" con su hoja "Base", siempre con todo el año a la fecha. Sustituye al reporte en uso. El libro se lee y no se guarda; solo se almacenan números de expediente, de documento, fechas e importes, nunca nombres de clientes. Si un número no se puede leer, no se carga nada y la página indica la fila. La descarga en Excel está en inglés y en pesos.') }}</p></div>

<div class="card" id="bookings"><h2>{{ t('Bookings per month', 'Ventas contratadas por mes') }} (MXN)</h2>
<p class="small">{{ t('Sale price of the Corporate and Private files booked in each month, in pesos. Sales reach = bookings ÷ budget × ' ~ (s.w_invoicing*100)|round|int ~ ' %. Leave a month blank if it is not known yet.',
'Precio de venta de los expedientes corporativos y particulares contratados en cada mes, en pesos. Alcance de ventas = ventas contratadas ÷ presupuesto × ' ~ (s.w_invoicing*100)|round|int ~ ' %. Deje el mes en blanco si todavía no se conoce.') }}{% if bmeta %} {{ t('Last saved', 'Guardado por última vez el') }} {{ bmeta.at|cm_when }} {{ t('by', 'por') }} {{ bmeta.by }}.{% endif %}</p>
<form method="post" action="/commissions/bookings"><input type="hidden" name="csrf" value="{{ csrf }}">
<div class="scroll"><table><tr><th>{{ t('Month', 'Mes') }}</th><th>{{ t('Budget (MXN)', 'Presupuesto (MXN)') }}</th><th>{{ t('Bookings (MXN)', 'Ventas contratadas (MXN)') }}</th><th>{{ t('Budget achievement', 'Cumplimiento del presupuesto') }}</th></tr>
{% for m in months %}<tr><td>{{ mon(m) }}</td><td>{{ s.budgets[m]|cm_money(2) }}</td>
<td><input type="text" name="b_{{ m }}" inputmode="decimal" value="{{ '{:,.2f}'.format(bookings[m]) if m in bookings else '' }}" aria-label="{{ month_label(m) }}"></td>
<td>{{ (bookings[m] / s.budgets[m])|cm_pct if m in bookings else '' }}</td></tr>{% endfor %}
</table></div><p><button type="submit" class="go">{{ t('Save bookings', 'Guardar ventas contratadas') }}</button></p></form></div>

{% if rep %}<div class="card" id="lines"><h2>{{ t('Commission lines', 'Líneas de comisión') }} ({{ rep.lines|length }}) (<span class="cur">MXN</span>)</h2>
<p class="small">{{ t('One line per file per invoice month, net of that month\'s credit notes.', 'Una línea por expediente por mes de factura, neta de las notas de crédito de ese mes.') }}</p>
{% for r in rep.months %}<details><summary>{{ mon(r.month) }}: {{ r.lines }} {{ t('lines', 'líneas') }}, {{ M(r.total, r.usd.total, 2) }}</summary>
<div class="scroll"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Billed', 'Facturado') }}</th><th>{{ t('Actual cost', 'Costo real') }}</th><th>{{ t('Gross margin', 'Margen bruto') }}</th><th>{{ t('% margin', '% margen') }}</th>
<th>{{ t('Invoicing part', 'Parte facturación') }}</th><th>{{ t('Margin part', 'Parte margen') }}</th><th>{{ t('Discipline part', 'Parte disciplina') }}</th><th>Total</th><th class="l">{{ t('Notes', 'Notas') }}</th></tr>
{% for x in rep.lines if x.month == r.month %}<tr><td>{{ x.job }}</td><td>{{ M(x.billed, x.usd.billed, 2) }}</td><td>{{ M(x.cost, x.usd.cost, 2) }}</td>
<td>{{ M(x.gross_margin, x.usd.gross_margin, 2) }}</td><td>{{ x.margin_pct|cm_pct }}</td><td>{{ M(x.invoicing, x.usd.invoicing, 2) }}</td>
<td>{% if x.cost_posted %}{{ M(x.margin, x.usd.margin, 2) }}{% else %}<span class="tag w">{{ t('waits for cost', 'espera costo') }}</span>{% endif %}</td>
<td>{{ M(x.discipline, x.usd.discipline, 2) }}</td><td>{{ M(x.total, x.usd.total, 2) }}</td>
<td class="wrapc">{% if x.credit %}<span class="tag">{{ t('credit notes exceed invoices', 'las notas de crédito superan a las facturas') }}</span> {% endif %}{% for f in x.flags %}<span class="tag w">{{ t(f.en, f.es) }}</span> {% endfor %}</td></tr>{% endfor %}
</table></div></details>{% endfor %}</div>{% endif %}
</div>
<script>
(function(){
  function get(k, d){ try { return localStorage.getItem(k) || d; } catch(e){ return d; } }
  function put(k, v){ try { localStorage.setItem(k, v); } catch(e){} }
  var dl = (navigator.language || '').toLowerCase().indexOf('es') === 0 ? 'es' : 'en';
  var S = {lang: get('audit.lang', dl), ccy: get('commission.ccy', 'mxn')};
  if (['en','es'].indexOf(S.lang) < 0) S.lang = 'en';
  if (['mxn','usd'].indexOf(S.ccy) < 0) S.ccy = 'mxn';
  function fmt(v, d){
    if (Math.abs(v) < 0.5 * Math.pow(10, -d)) return '–';
    var s = Math.abs(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
    return v < 0 ? '(' + s + ')' : s;
  }
  function render(){
    document.body.classList.toggle('es', S.lang === 'es');
    document.body.classList.toggle('usd', S.ccy === 'usd');
    document.documentElement.lang = S.lang;
    document.querySelectorAll('.m').forEach(function(el){
      var a = el.getAttribute(S.ccy === 'usd' ? 'data-u' : 'data-x');
      if (a === null || a === '') a = el.getAttribute('data-x');
      el.textContent = fmt(+a, +el.getAttribute('data-d') || 0);
    });
    document.querySelectorAll('.cur').forEach(function(el){ el.textContent = S.ccy === 'usd' ? 'USD' : 'MXN'; });
    document.querySelectorAll('#ccySeg button').forEach(function(b){ b.classList.toggle('on', b.getAttribute('data-c') === S.ccy); });
    document.querySelectorAll('#langSeg button').forEach(function(b){ b.classList.toggle('on', b.getAttribute('data-l') === S.lang); });
  }
  document.querySelectorAll('#ccySeg button').forEach(function(b){
    b.addEventListener('click', function(){ S.ccy = b.getAttribute('data-c'); put('commission.ccy', S.ccy); render(); }); });
  document.querySelectorAll('#langSeg button').forEach(function(b){
    b.addEventListener('click', function(){ S.lang = b.getAttribute('data-l'); put('audit.lang', S.lang); render(); }); });
  render();
})();
</script>
</body></html>
"""
