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


def _say(text: str, bad: bool = False) -> None:
    session["commission_msg"] = {"text": text, "bad": bad}


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
        return None, meta, "The stored report could not be read back. Please upload Finance's report again."


def _month_label(m: str) -> str:
    return f"{MONTH_NAMES[int(m[5:7]) - 1]} {m[:4]}"


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
        durable=store.durable(), new_from=_month_label(R.NEW_FROM), email=email))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@commissions_bp.route("/commissions/upload", methods=["POST"])
@_restricted
def commissions_upload(email):
    _check_csrf()
    f = request.files.get("report")
    if f is None or not (f.filename or "").strip():
        _say("No file was chosen.", bad=True)
        return redirect(url_for("commissions.commissions_home"))
    if not f.filename.lower().endswith(".xlsx"):
        _say("The report must be an Excel workbook (.xlsx).", bad=True)
        return redirect(url_for("commissions.commissions_home"))
    data = f.stream.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        _say("The file is larger than 20 MB. Finance's margin report is far smaller; please check the file.", bad=True)
        return redirect(url_for("commissions.commissions_home"))
    try:
        lines = F.load_base(io.BytesIO(data))
    except F.FinanceReportError as e:          # these messages carry no text from the workbook
        _say(f"The report was not loaded: {e}. Nothing was changed.", bad=True)
        return redirect(url_for("commissions.commissions_home"))
    except Exception:                           # not a workbook, damaged, password-protected…
        _say("The file could not be opened as an Excel workbook. Nothing was changed.", bad=True)
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
         f"{src['jobs_in_scope']} Corporate and Private jobs, {rep['check_count']} points to look at.")
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
            bad.append(_month_label(m))
            continue
        out[m] = float(raw.replace(",", ""))
    if bad:
        _say("Bookings were not saved. These months do not hold an amount such as 2,150,000.00: "
             + ", ".join(bad) + ".", bad=True)
    else:
        store.put("bookings", out, by=email)
        _say(f"Bookings saved for {len(out)} month(s). Months left blank use the budget as a placeholder.")
    return redirect(url_for("commissions.commissions_home") + "#bookings")


@commissions_bp.route("/commissions/workbook")
@_restricted
def commissions_workbook(email):
    lines, _meta, error = _current()
    if not lines:
        _say(error or "Upload Finance's margin report first.", bad=True)
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
    return v.astimezone(dt.timezone(dt.timedelta(hours=-6))).strftime("%d %b %Y, %H:%M") + " (Mexico City)"


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TMS Commission Report</title>
<style>
:root{--ink:#1b2430;--soft:#5b6675;--line:#dfe3e8;--bg:#f5f6f8;--card:#fff;--navy:#1f3864;--warn:#8a3b12;--warnbg:#fdf0e6;
--ok:#1d5c3a;--okbg:#e8f4ec;--bad:#8c1d18;--badbg:#fbe9e7;--hl:#fff8d6}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,Arial,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 2px}h2{font-size:16px;margin:0 0 10px}.sub{color:var(--soft);margin:0 0 16px}
a{color:var(--navy)}.top{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;margin:14px 0}
.note{border-radius:8px;padding:10px 14px;margin:12px 0;border:1px solid}
.note.warn{background:var(--warnbg);border-color:#f1cfb4;color:var(--warn)}
.note.ok{background:var(--okbg);border-color:#bfe0cb;color:var(--ok)}.note.bad{background:var(--badbg);border-color:#f2c1bd;color:var(--bad)}
.note ul{margin:6px 0 0 18px;padding:0}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{background:var(--navy);color:#fff;font-weight:600;font-size:12px;white-space:normal;vertical-align:bottom}
th:first-child,td:first-child,td.l,th.l{text-align:left}td.wrapc{white-space:normal;text-align:left;min-width:260px}
tr.tot td{font-weight:700;background:#eef1f6;border-top:2px solid var(--navy)}tr.old td{color:var(--soft)}
td.ph{background:var(--hl)}table.sum th,table.sum td{padding:6px 5px;font-size:13px}table.sum th{font-size:11.5px}
table.chk td:first-child{width:110px}table.chk td:last-child{width:150px}.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;background:#eef1f6;color:var(--soft)}
.tag.w{background:var(--warnbg);color:var(--warn)}
.big{display:flex;flex-wrap:wrap;gap:12px}.big div{flex:1 1 180px;background:#f8f9fb;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.big b{display:block;font-size:20px}.big span{color:var(--soft);font-size:12px}
details{margin:8px 0}summary{cursor:pointer;font-weight:600;padding:4px 0}
input[type=text]{width:150px;padding:5px 7px;border:1px solid #b9c0ca;border-radius:5px;text-align:right;font:inherit}
input[type=file]{font:inherit}button,.btn{background:var(--navy);color:#fff;border:0;border-radius:6px;padding:8px 14px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
.btn.ghost{background:#fff;color:var(--navy);border:1px solid var(--navy)}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.small{font-size:12px;color:var(--soft)}
</style></head><body><div class="wrap">
<div class="top"><div><h1>TMS Commission Report</h1>
<p class="sub">Corporate accounts and Private customers. US Embassy and agent business are not included.</p></div>
<div class="small">{{ email }} · <a href="/">Automation Library</a></div></div>

{% if msg %}<div class="note {{ 'bad' if msg.bad else 'ok' }}">{{ msg.text }}</div>{% endif %}
{% if error %}<div class="note bad">{{ error }}</div>{% endif %}
{% if not durable %}<div class="note warn">This service has no database connected, so an uploaded report is lost when the service is rebuilt.</div>{% endif %}

{% if rep %}
<div class="note warn"><b>Trial. Not for payment.</b>
<ul>
{% if rep.placeholder_months %}<li>Bookings are not entered for {{ rep.placeholder_months|length }} month(s). Those months use the budget as a placeholder, which gives a sales reach of 0.60 (yellow cells).</li>{% endif %}
<li>No invoice is marked as collected yet, so nothing is payable.</li>
<li>Costs arrive about two months after the invoice. Recent months show too much margin until their costs are posted.</li>
<li>The margin part follows the sample workbook (file 111000 pays 765.00). To be confirmed.</li>
</ul></div>

<div class="card"><h2>{{ new_from }} onwards: new calculation</h2>
<div class="big">
<div><b>{{ rep.total_new.billed|cm_money }}</b><span>Billed (MXN)</span></div>
<div><b>{{ rep.total_new.margin_pct|cm_pct }}</b><span>Gross margin on cost posted so far</span></div>
<div><b>{{ rep.total_new.total|cm_money(2) }}</b><span>Commission calculated</span></div>
<div><b>{{ rep.total_new.payable|cm_money(2) }}</b><span>Payable now (collected)</span></div>
<div><b>{{ rep.total_new.waiting_lines }}</b><span>Lines waiting for cost ({{ rep.total_new.waiting_billed|cm_money }} billed)</span></div>
</div></div>

<div class="card"><h2>By invoice month</h2><div class="scroll"><table class="sum">
<tr><th>Month</th><th class="l">Rules</th><th>Lines</th><th>Billed</th><th>Actual cost</th><th>Gross margin</th><th>% margin</th>
<th>Bookings</th><th>Sales reach</th><th>Invoicing part</th><th>Margin part</th><th>Discipline part</th><th>Total calculated</th><th>Payable</th><th>Waiting for cost</th></tr>
{% for r in rep.months %}<tr class="{{ '' if r.new else 'old' }}">
<td>{{ month_label(r.month) }}</td><td class="l">{{ r.rule }}</td><td>{{ r.lines }}</td><td>{{ r.billed|cm_money }}</td><td>{{ r.cost|cm_money }}</td>
<td>{{ r.gross_margin|cm_money }}</td><td>{{ r.margin_pct|cm_pct }}</td>
<td class="{{ '' if r.bookings_known else 'ph' }}">{% if r.bookings_known %}{{ r.bookings|cm_money }}{% else %}= budget{% endif %}</td>
<td class="{{ '' if r.bookings_known else 'ph' }}">{{ '%.4f'|format(r.reach) }}</td>
<td>{{ r.invoicing|cm_money }}</td><td>{{ r.margin|cm_money }}</td><td>{{ r.discipline|cm_money }}</td><td>{{ r.total|cm_money(2) }}</td>
<td>{{ r.payable|cm_money(2) }}</td><td>{% if r.waiting_lines %}{{ r.waiting_lines }} ({{ r.waiting_billed|cm_money }}){% else %}–{% endif %}</td></tr>{% endfor %}
{% for label, t in (('All months', rep.total_all), (new_from ~ ' onwards', rep.total_new)) %}<tr class="tot">
<td colspan="2">{{ label }}</td><td>{{ t.lines }}</td><td>{{ t.billed|cm_money }}</td><td>{{ t.cost|cm_money }}</td><td>{{ t.gross_margin|cm_money }}</td>
<td>{{ t.margin_pct|cm_pct }}</td><td></td><td></td><td>{{ t.invoicing|cm_money }}</td><td>{{ t.margin|cm_money }}</td><td>{{ t.discipline|cm_money }}</td>
<td>{{ t.total|cm_money(2) }}</td><td>{{ t.payable|cm_money(2) }}</td><td>{{ t.waiting_lines }} ({{ t.waiting_billed|cm_money }})</td></tr>{% endfor %}
</table></div>
{% if rep.months_without_budget %}<p class="small">No budget is set for {{ rep.months_without_budget|join(', ') }}; those months are not calculated.</p>{% endif %}
<p class="small">{% if s.sales_fx == 'internal' %}US-dollar invoices at {{ '%.2f'|format(s.usd_mxn) }}. Costs as Finance booked them, at the rate of the day of each cost invoice.{% else %}Sales and costs in dollars are in pesos as Finance booked them, each at the rate of its own invoice day.{% endif %}</p></div>

<div class="card"><h2>Distribution, {{ new_from }} onwards</h2><div class="scroll"><table>
<tr><th>Role and name</th><th class="l">Group</th><th>Share of the group</th><th>If every invoice were collected</th><th>Payable now</th></tr>
{% for p in rep.people %}<tr><td>{{ p.name }}</td><td class="l">{{ p.group }}</td><td>{{ p.share|cm_pct }}</td><td>{{ p.if_collected|cm_money(2) }}</td><td>{{ p.payable|cm_money(2) }}</td></tr>{% endfor %}
<tr class="tot"><td colspan="3">Total</td><td>{{ rep.total_new.total|cm_money(2) }}</td><td>{{ rep.total_new.payable|cm_money(2) }}</td></tr>
</table></div><p class="small">Sales two thirds, Adm, MC and Buyer one third. Control (total paid out minus total calculated): {{ rep.control|cm_money(2) }}.</p></div>

<div class="card" id="checks"><h2>Points to look at in Finance's report ({{ rep.check_count }})</h2>
<p class="small">File numbers, document numbers and amounts only. Row numbers are rows of the sheet "Base".</p>
{% for c in rep.checks %}<details {{ '' if c.routine else 'open' }}><summary>{{ c.label }} ({{ c['items']|length }})</summary>
<div class="scroll"><table class="chk"><tr><th>File</th><th class="l">Detail</th><th>Amount (MXN)</th></tr>
{% for i in c['items'] %}<tr><td>{{ i.job }}</td><td class="wrapc">{{ i.detail }}</td><td>{{ i.amount|cm_money(2) if i.amount is not none else '' }}</td></tr>{% endfor %}
</table></div></details>{% endfor %}</div>
{% endif %}

<div class="card" id="upload"><h2>Finance's margin report</h2>
{% if meta %}<p>In use: uploaded {{ meta.at|cm_when }} by {{ meta.by }}{% if rep %} · {{ '{:,}'.format(rep.source.lines) }} lines dated {{ rep.source['from'] }} to {{ rep.source['to'] }} · {{ rep.source.jobs_in_scope }} Corporate and Private jobs{% endif %}.</p>
{% else %}<p>No report has been uploaded yet.</p>{% endif %}
<form method="post" action="/commissions/upload" enctype="multipart/form-data" class="row">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="file" name="report" accept=".xlsx" required>
<button type="submit">Upload report</button>
{% if rep %}<a class="btn ghost" href="/commissions/workbook">Download as Excel</a>{% endif %}</form>
<p class="small">Upload the workbook "Margen x Expediente" with its sheet "Base", the whole year to date each time. It replaces the report in use.
The workbook is read and not kept; only file numbers, document numbers, dates and amounts are stored, never customer names.
If a number cannot be read, nothing is loaded and the page says which row.</p></div>

<div class="card" id="bookings"><h2>Bookings per month</h2>
<p class="small">Sale price of the Corporate and Private files booked in each month, in pesos. Sales reach = bookings ÷ budget × {{ (s.w_invoicing*100)|round|int }} %.
Leave a month blank if it is not known yet.{% if bmeta %} Last saved {{ bmeta.at|cm_when }} by {{ bmeta.by }}.{% endif %}</p>
<form method="post" action="/commissions/bookings"><input type="hidden" name="csrf" value="{{ csrf }}">
<div class="scroll"><table><tr><th>Month</th><th>Budget (MXN)</th><th>Bookings (MXN)</th><th>Budget achievement</th></tr>
{% for m in months %}<tr><td>{{ month_label(m) }}</td><td>{{ s.budgets[m]|cm_money(2) }}</td>
<td><input type="text" name="b_{{ m }}" inputmode="decimal" value="{{ '{:,.2f}'.format(bookings[m]) if m in bookings else '' }}" aria-label="Bookings {{ month_label(m) }}"></td>
<td>{{ (bookings[m] / s.budgets[m])|cm_pct if m in bookings else '' }}</td></tr>{% endfor %}
</table></div><p><button type="submit">Save bookings</button></p></form></div>

{% if rep %}<div class="card" id="lines"><h2>Commission lines ({{ rep.lines|length }})</h2>
<p class="small">One line per file per invoice month, net of that month's credit notes.</p>
{% for r in rep.months %}<details><summary>{{ month_label(r.month) }}: {{ r.lines }} lines, {{ r.total|cm_money(2) }}</summary>
<div class="scroll"><table><tr><th>File</th><th>Billed</th><th>Actual cost</th><th>Gross margin</th><th>% margin</th>
<th>Invoicing part</th><th>Margin part</th><th>Discipline part</th><th>Total</th><th class="l">Notes</th></tr>
{% for x in rep.lines if x.month == r.month %}<tr><td>{{ x.job }}</td><td>{{ x.billed|cm_money(2) }}</td><td>{{ x.cost|cm_money(2) }}</td>
<td>{{ x.gross_margin|cm_money(2) }}</td><td>{{ x.margin_pct|cm_pct }}</td><td>{{ x.invoicing|cm_money(2) }}</td>
<td>{% if x.cost_posted %}{{ x.margin|cm_money(2) }}{% else %}<span class="tag w">waits for cost</span>{% endif %}</td>
<td>{{ x.discipline|cm_money(2) }}</td><td>{{ x.total|cm_money(2) }}</td>
<td class="wrapc">{% if x.credit %}<span class="tag">credit notes exceed invoices</span> {% endif %}{% for f in x.flags %}<span class="tag w">{{ f }}</span> {% endfor %}</td></tr>{% endfor %}
</table></div></details>{% endfor %}</div>{% endif %}
</div></body></html>
"""
