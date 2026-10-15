"""
web.py — the /commissions pages.

    /commissions            the report: months, distribution, points to look at, lines
    /commissions/upload     Finance uploads the monthly margin report (POST)
    /commissions/bookings   bookings per month, typed in (POST)
    /commissions/payments   payment status per line, Paid or Pending (POST)
    /commissions/upload-payments   Finance's report of customer payments, one per company (POST)
    /commissions/upload-invoices   Finance's register of invoices issued (POST)
    /commissions/upload-removals, /commissions/upload-costing
                            the two Moveware reports that hold each file's booked quote (POST)
    /commissions/workbook   the same report as an Excel workbook, one sheet per month in Finance's own layout
    /commissions/signoff    a step of a pay run: submit (Lupita), approve or return (Mario), paid (Rogelio) (POST)
    /commissions/signoff/<n>/instruction   the payment instruction of pay run n, as Excel

Who may open it: only the addresses in COMMISSION_USERS (comma separated, set in
Render). Without that setting it is Lupita, Mario, Rogelio and Bill. Anyone else
who is signed in to the library gets "not found", not "forbidden". The home
page shows its tile (Management Reports) only to those same addresses:
home_tile() cuts the tile out of the page for everyone else.

Every uploaded workbook is read in memory and thrown away. What is kept is the
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
from . import invoices as I
from . import payments as P
from . import quotes as Q
from . import report as R
from . import signoff as SO
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


_TILE = re.compile(r"<!-- COMMISSION_TILE_START.*?<!-- COMMISSION_TILE_END -->\n?", re.S)


def home_tile(html: str, email: str) -> str:
    """The library home page with the commission tile left in only for someone
    who may open the report. For anyone else the tile is cut out."""
    if (email or "").strip().lower() in allowed_users():
        return html
    return _TILE.sub("", html)


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


def _moveware():
    """What is stored from the two Moveware reports:
    {"removals": (jobs, control, meta) | None, "costing": (jobs, period, meta) | None, "error": (en, es) | None}."""
    out = {"removals": None, "costing": None, "error": None}
    for key, name, back in (("removals", "removals_summary", Q.removals_from_records),
                            ("costing", "costing_sheet", Q.costing_from_records)):
        doc, meta = store.get(name)
        if not doc:
            continue
        try:
            a, b = back(doc.get("records") or {})
            out[key] = (a, b, meta)
        except Q.QuoteReportError as e:
            out["error"] = (f"{e}. Please upload it again.", f"{e.es}. Súbalo de nuevo.")
    return out


def _collections() -> dict:
    """The payments reports stored, one per company:
    {"sources": [(payments, info, {"at", "by"})], "error": (en, es) | None}."""
    doc, _ = store.get("payment_reports")
    out = {"sources": [], "error": None}
    for name, src in sorted(((doc or {}).get("sources") or {}).items()):
        try:
            ps, info = P.from_records(src.get("records") or {})
            at = src.get("at")
            out["sources"].append((ps, info, {"at": dt.datetime.fromisoformat(at) if at else None, "by": src.get("by", "")}))
        except (P.PaymentReportError, ValueError, AttributeError):
            out["error"] = ("A stored payments report could not be read back. Please upload it again.",
                            "No se pudo leer un reporte de pagos guardado. Súbalo de nuevo.")
    return out


def _register() -> dict:
    """Finance's invoice register as stored: {"invoices": [...] | None, "info", "meta", "error": (en, es) | None}."""
    doc, meta = store.get("invoice_register")
    out = {"invoices": None, "info": None, "meta": meta, "error": None}
    if doc:
        try:
            out["invoices"], out["info"] = I.from_records(doc.get("records") or {})
        except I.InvoiceRegisterError:
            out["error"] = ("The stored invoice register could not be read back. Please upload it again.",
                            "No se pudo leer el registro de facturas guardado. Súbalo de nuevo.")
    return out


def _report(lines, s: E.Settings, mw=None) -> dict:
    """The report as the page and the Excel download show it: Finance's lines,
    the bookings typed in, and the booked quotes when the Removals Summary is loaded."""
    mw = mw or _moveware()
    bookings = store.get("bookings", {})[0]
    col = _collections()["sources"]
    more = dict(payments=store.get("payments", {})[0], register=_register()["invoices"])
    if col:
        src = [(ps, info) for ps, info, _m in col]
        more.update(collected=[p for ps, _i, _m in col for p in ps], collected_reach=R.payment_reach(src),
                    collected_companies=R.company_reach(src))
    if not mw["removals"]:
        return R.build(lines, s, bookings, **more)
    rem, control, _ = mw["removals"]
    cst = mw["costing"][0] if mw["costing"] else []
    quotes, qproblems = R.book_quotes(lines, rem, control, cst, s)
    return R.build(lines, s, bookings, quotes=quotes, quote_problems=qproblems, **more)


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
    pmeta = store.get("payments", {})[1]
    mw = _moveware()
    col = _collections()
    reg = _register()
    rep = _report(lines, s, mw) if lines else None
    months = sorted(s.budgets)
    runs = _runs()
    pay_draft = SO.draft(rep, runs, s, R.NEW_FROM) if rep else None
    resp = make_response(render_template_string(
        PAGE, rep=rep, runs=list(reversed(runs)), pay_draft=pay_draft, so=SO, my_roles=SO.roles_of(email),
        open_run=next((r for r in runs if r["state"] == SO.OPEN), None), meta=meta, error=error or mw["error"] or col["error"] or reg["error"], mw=mw, col=col["sources"], reg=reg, pmeta=pmeta, msg=session.pop("commission_msg", None), csrf=_csrf(),
        s=s, bookings=R.clean_bookings(bookings), bmeta=bmeta, months=months, month_label=_month_label,
        month_label_es=_month_label_es, durable=store.durable(), new_from=_month_label(R.NEW_FROM),
        new_from_es=_month_label_es(R.NEW_FROM), email=email))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _uploaded():
    """The bytes of the uploaded workbook, or None after telling the user why not."""
    f = request.files.get("report")
    if f is None or not (f.filename or "").strip():
        _say("No file was chosen.", "No se eligió ningún archivo.", bad=True)
        return None
    if not f.filename.lower().endswith(".xlsx"):
        _say("The report must be an Excel workbook (.xlsx).", "El reporte debe ser un libro de Excel (.xlsx).", bad=True)
        return None
    data = f.stream.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        _say("The file is larger than 20 MB. These reports are far smaller; please check the file.",
             "El archivo pesa más de 20 MB. Estos reportes son mucho más pequeños; revise el archivo.", bad=True)
        return None
    return data


def _log_upload(email: str, what: str, count: int, digest: str) -> None:
    log, _ = store.get("uploads", [])
    log.append({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "by": email,
                "what": what, "lines": count, "sha256": digest[:16]})
    store.put("uploads", log[-50:], by=email)


def _upload_moveware(email: str, key: str, load, to_records, name_en: str, name_es: str):
    """Read one Moveware report in memory and keep its names-free records."""
    _check_csrf()
    home = redirect(url_for("commissions.commissions_home") + "#moveware")
    data = _uploaded()
    if data is None:
        return home
    try:
        jobs, extra = load(io.BytesIO(data))
    except Q.QuoteReportError as e:              # these messages carry no text from the workbook
        _say(f"The {name_en} was not loaded: {e}. Nothing was changed.",
             f"El {name_es} no se cargó: {e.es}. No se cambió nada.", bad=True)
        return home
    except Exception:
        _say("The file could not be read. Nothing was changed.", "El archivo no se pudo leer. No se cambió nada.",
             bad=True)
        return home
    digest = hashlib.sha256(data).hexdigest()
    store.put(key, {"records": to_records(jobs, extra), "sha256": digest}, by=email)
    _log_upload(email, key, len(jobs), digest)
    _say(f"{name_en} loaded: {len(jobs):,} jobs. Customer names in it were not kept.",
         f"{name_es} cargado: {len(jobs):,} expedientes. No se guardaron los nombres de clientes.")
    return home


@commissions_bp.route("/commissions/upload-removals", methods=["POST"])
@_restricted
def commissions_upload_removals(email):
    return _upload_moveware(email, "removals_summary", Q.load_removals, Q.removals_to_records,
                            "Removals Summary", "Removals Summary")


@commissions_bp.route("/commissions/upload-costing", methods=["POST"])
@_restricted
def commissions_upload_costing(email):
    return _upload_moveware(email, "costing_sheet", Q.load_costing, Q.costing_to_records,
                            "Move Costing Sheet", "Move Costing Sheet")


@commissions_bp.route("/commissions/upload-payments", methods=["POST"])
@_restricted
def commissions_upload_payments(email):
    """Finance's report of customer payments for one company. It replaces the
    report stored for that same company; other companies' reports stay."""
    _check_csrf()
    home = redirect(url_for("commissions.commissions_home") + "#collected")
    data = _uploaded()
    if data is None:
        return home
    try:
        ps, info = P.load(io.BytesIO(data))
    except P.PaymentReportError as e:            # these messages carry no text from the workbook
        _say(f"The payments report was not loaded: {e}. Nothing was changed.",
             f"El reporte de pagos no se cargó: {e.es}. No se cambió nada.", bad=True)
        return home
    except Exception:
        _say("The file could not be read. Nothing was changed.", "El archivo no se pudo leer. No se cambió nada.",
             bad=True)
        return home
    doc, _ = store.get("payment_reports")
    doc = doc if isinstance(doc, dict) and isinstance(doc.get("sources"), dict) else {"sources": {}}
    digest = hashlib.sha256(data).hexdigest()
    name = info["key"] or "?"
    doc["sources"][name] = {"records": P.to_records(ps, info), "sha256": digest, "by": email,
                            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    store.put("payment_reports", doc, by=email)
    _log_upload(email, f"payment_report {name}", len(ps), digest)
    who = (name, name) if info["company"] else ("a company this page does not know by name",
                                                "una empresa que esta página no conoce por nombre")
    _say(f"Payments report loaded for {who[0]}: {len(ps):,} payments from {info['from'] or '?'} to {info['to'] or '?'}. "
         "Customer names and bank references in it were not kept.",
         f"Reporte de pagos cargado para {who[1]}: {len(ps):,} pagos del {info['from'] or '?'} al {info['to'] or '?'}. "
         "No se guardaron los nombres de clientes ni las referencias bancarias.")
    return home


@commissions_bp.route("/commissions/upload-invoices", methods=["POST"])
@_restricted
def commissions_upload_invoices(email):
    """Finance's register of invoices issued. It replaces the one stored."""
    _check_csrf()
    home = redirect(url_for("commissions.commissions_home") + "#collected")
    data = _uploaded()
    if data is None:
        return home
    try:
        invoices, info = I.load(io.BytesIO(data))
    except I.InvoiceRegisterError as e:          # these messages carry no text from the workbook
        _say(f"The invoice register was not loaded: {e}. Nothing was changed.",
             f"El registro de facturas no se cargó: {e.es}. No se cambió nada.", bad=True)
        return home
    except Exception:
        _say("The file could not be read. Nothing was changed.", "El archivo no se pudo leer. No se cambió nada.",
             bad=True)
        return home
    digest = hashlib.sha256(data).hexdigest()
    store.put("invoice_register", {"records": I.to_records(invoices), "sha256": digest}, by=email)
    _log_upload(email, "invoice_register", len(invoices), digest)
    _say(f"Invoice register loaded: {info['invoices']:,} invoices from {info['from']} to {info['to']}. "
         "Customer and staff names in it were not kept.",
         f"Registro de facturas cargado: {info['invoices']:,} facturas del {info['from']} al {info['to']}. "
         "No se guardaron los nombres de clientes ni del personal.")
    return home


@commissions_bp.route("/commissions/upload", methods=["POST"])
@_restricted
def commissions_upload(email):
    _check_csrf()
    data = _uploaded()
    if data is None:
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
    rep = _report(lines, settings())
    previous, _ = store.get("finance_report")
    if previous:
        store.put("finance_report_previous", previous, by=email)
    digest = hashlib.sha256(data).hexdigest()
    store.put("finance_report", {"records": F.to_records(lines), "sha256": digest}, by=email)
    _log_upload(email, "finance_report", len(lines), digest)
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
        _say(f"Bookings saved for {len(out)} month(s). Months left blank use the booked quotes, or the budget "
             "as a placeholder.",
             f"Ventas contratadas guardadas para {len(out)} mes(es). Los meses en blanco usan las cotizaciones, o el "
             "presupuesto como valor provisional.")
    return redirect(url_for("commissions.commissions_home") + "#bookings")


_JOB_TOKEN = re.compile(r"\b\d{5,7}[A-Z]{0,2}\b")


@commissions_bp.route("/commissions/payments", methods=["POST"])
@_restricted
def commissions_payments(email):
    """Payment status per line (a file in an invoice month): Paid, Pending or
    not known. Either the selectors of the lines table, or a pasted list of
    file numbers for one month."""
    _check_csrf()
    home = redirect(url_for("commissions.commissions_home") + "#lines")
    lines, _meta, error = _current()
    if not lines:
        en, es = error or ("Upload Finance's margin report first.", "Primero suba el reporte de margen de Finanzas.")
        _say(en, es, bad=True)
        return home
    rep = _report(lines, settings())
    known = {(x["month"], x["job"]) for x in rep["lines"]}
    stored, _ = store.get("payments", {})
    stored = {k: v for k, v in (stored or {}).items() if v in R.PAYMENT_WORDS}
    before = dict(stored)
    pasted = (request.form.get("bulk_jobs") or "").strip()
    if pasted:
        month, status = request.form.get("bulk_month") or "", request.form.get("bulk_status") or ""
        if not _MONTH.match(month) or status not in R.PAYMENT_WORDS:
            _say("Choose the month and the status for the pasted list. Nothing was changed.",
                 "Elija el mes y el estatus para la lista pegada. No se cambió nada.", bad=True)
            return home
        jobs = sorted(set(_JOB_TOKEN.findall(pasted.upper())))
        found = [j for j in jobs if (month, j) in known]
        other = [j for j in jobs if (month, j) not in known]
        for j in found:
            stored[f"{month}|{j}"] = status
        store.put("payments", stored, by=email)
        tail_en = f" Not in {_month_label(month)}, left out: {', '.join(other)}." if other else ""
        tail_es = f" No están en {_month_label_es(month)}, se omitieron: {', '.join(other)}." if other else ""
        _say(f"{len(found)} line(s) of {_month_label(month)} marked {status}.{tail_en}",
             f"{len(found)} línea(s) de {_month_label_es(month)} marcadas como "
             f"{'pagadas' if status == 'Paid' else 'pendientes'}.{tail_es}", bad=not found)
        return home
    for (month, job) in known:                      # only the lines of the report in use can be set from the table
        field = f"p_{month}_{job}"
        if field not in request.form:
            continue
        v = request.form.get(field) or ""
        if v in R.PAYMENT_WORDS:
            stored[f"{month}|{job}"] = v
        elif v == "":
            stored.pop(f"{month}|{job}", None)
    changed = sum(1 for k in set(stored) | set(before) if stored.get(k) != before.get(k))
    store.put("payments", stored, by=email)
    paid = sum(1 for v in stored.values() if v == "Paid")
    _say(f"Payment status saved: {changed} line(s) changed, {paid} marked Paid in all.",
         f"Estatus de pago guardado: {changed} línea(s) cambiadas, {paid} marcadas como pagadas en total.")
    return home


@commissions_bp.route("/commissions/workbook")
@_restricted
def commissions_workbook(email):
    lines, _meta, error = _current()
    if not lines:
        en, es = error or ("Upload Finance's margin report first.", "Primero suba el reporte de margen de Finanzas.")
        _say(en, es, bad=True)
        return redirect(url_for("commissions.commissions_home"))
    from . import template_sheet
    s = settings()
    buf = io.BytesIO()
    template_sheet.build_book(_report(lines, s), s, buf)
    buf.seek(0)
    last = max((ln.date for ln in lines if ln.date), default=dt.date.today())
    resp = send_file(buf, as_attachment=True, download_name=f"TMS commission report to {last.isoformat()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _runs() -> list:
    return store.get("pay_runs", [])[0] or []


@commissions_bp.route("/commissions/signoff", methods=["POST"])
@_restricted
def commissions_signoff(email):
    _check_csrf()
    home = redirect(url_for("commissions.commissions_home") + "#payruns")
    step = request.form.get("step") or ""
    lines, _meta, _error = _current()
    if not lines:
        _say("Upload Finance's margin report first.", "Primero suba el reporte de margen de Finanzas.", bad=True)
        return home
    s = settings()
    runs = _runs()
    current = SO.draft(_report(lines, s), runs, s, R.NEW_FROM)
    try:
        run_id = int(request.form.get("run") or 0)
    except ValueError:
        run_id = 0
    try:
        run = SO.act(runs, step, email, s, current=current, run_id=run_id, note=request.form.get("note") or "",
                     paid_on=request.form.get("paid_on") or "", expect=request.form.get("expect") or "")
    except SO.SignOffError as e:
        _say(str(e), e.es, bad=True)
        return home
    store.put("pay_runs", runs, by=email)
    _say(f"Pay run {run['id']}: step '{step}' done; it is now {run['state']}.",
         f"Corrida {run['id']}: paso «{SO.STEP_ES[step]}» hecho; ahora está {SO.STATE_ES[run['state']]}.")
    return home


@commissions_bp.route("/commissions/signoff/<int:run_id>/instruction")
@_restricted
def commissions_instruction(email, run_id):
    try:
        run = SO.find(_runs(), run_id)
    except SO.SignOffError:
        abort(404)
    buf = io.BytesIO()
    SO.instruction_book(run, buf)
    buf.seek(0)
    resp = send_file(buf, as_attachment=True, download_name=f"Commission pay run {run_id} ({run['state']}).xlsx",
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
.src{font-size:10px;color:#667;border:1px solid #ccd;border-radius:3px;padding:0 3px;white-space:nowrap}span.es{display:none}body.es span.en{display:none}body.es span.es{display:inline}
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
<div class="note info usdnote">{% if rep.rates.from_report %}{{ t('Shown in US dollars: the pesos of each month divided by the middle exchange rate of that month\'s dollar invoices in Finance\'s report',
'Se muestra en dólares: los pesos de cada mes entre el tipo de cambio central de las facturas en dólares de ese mes en el reporte de Finanzas') }}
({% for r in rep.months %}{{ mon(r.month) }} {{ '%.2f'|format(r.rate) }}{% if not loop.last %} · {% endif %}{% endfor %}).{% else %}{{ t('Shown in US dollars at the internal rate of ' ~ '%.2f'|format(rep.rates.other) ~ ' pesos per dollar, the rate the commission uses for dollar invoices.',
'Se muestra en dólares al tipo de cambio interno de ' ~ '%.2f'|format(rep.rates.other) ~ ' pesos por dólar, el que usa la comisión para las facturas en dólares.') }}{% endif %}
{{ t('The commission is calculated in pesos; percentages do not change with the currency shown. The check list and the bookings table stay in pesos.',
'La comisión se calcula en pesos; los porcentajes no cambian con la moneda mostrada. La lista de puntos por revisar y la tabla de ventas contratadas siguen en pesos.') }}</div>

<div class="note warn"><b>{{ t('Trial. Not for payment.', 'Prueba. No usar para pago.') }}</b>
<ul>
{% if rep.placeholder_months %}<li>{{ t('Bookings are not known for ' ~ rep.placeholder_months|length ~ ' month(s). Those months use the budget as a placeholder, which gives a sales reach of 0.60 (yellow cells).',
'No se conocen las ventas contratadas de ' ~ rep.placeholder_months|length ~ ' mes(es). Esos meses usan el presupuesto como valor provisional, lo que da un alcance de ventas de 0.60 (celdas amarillas).') }}</li>{% endif %}
{% if not rep.paid_lines %}<li>{{ t('No line is marked as paid yet, so nothing is payable.', 'Ninguna línea está marcada como pagada todavía, así que no hay nada por pagar.') }}</li>{% else %}<li>{{ rep.paid_lines }} {{ t('line(s) are paid. Only those are payable.', 'línea(s) están pagadas. Solo esas son pagaderas.') }}{% if rep.collection and rep.collection.invoices > rep.collection.covered %} {{ rep.collection.invoices - rep.collection.covered }} {{ t('invoices are not covered by any payments report loaded.', 'facturas no están cubiertas por ningún reporte de pagos cargado.') }}{% endif %}</li>{% endif %}
<li>{{ t('Costs arrive about two months after the invoice. Recent months show too much margin until their costs are posted.',
'Los costos llegan unos dos meses después de la factura. Los meses recientes muestran margen de más hasta que se registran sus costos.') }}</li>
<li>{{ t('January to May follow the old calculation. They are shown here on the new calculation for comparison only.',
'De enero a mayo aplica el cálculo anterior. Aquí se muestran con el cálculo nuevo solo para comparar.') }}</li>
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
<td class="{{ '' if r.bookings_known else 'ph' }}">{% if r.bookings_known %}{{ M(r.bookings, r.usd.bookings) }} <span class="src">{{ t('quotes', 'cotiz.') if r.bookings_source == 'quotes' else t('typed', 'captura') }}</span>{% else %}{{ t('= budget', '= presupuesto') }}{% endif %}</td>
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
</table></div>
{% if rep.people and rep.people[0].months %}{% set pm = rep.people[0].months %}
{% for key, title, title_es in (('payable', 'Statement by person: payable each month (invoices collected)', 'Estado por persona: por pagar cada mes (facturas cobradas)'), ('if_collected', 'Statement by person: calculated each month, if every invoice were collected', 'Estado por persona: calculado cada mes, si se cobraran todas las facturas')) %}
<details {{ 'open' if key == 'payable' else '' }}><summary>{{ t(title, title_es) }}</summary><div class="scroll"><table>
<tr><th>{{ t('Role and name', 'Puesto y nombre') }}</th>{% for x in pm %}<th>{{ mon(x.month) }}</th>{% endfor %}<th>Total</th></tr>
{% for p in rep.people %}<tr><td>{{ t(p.name, p.name_es) }}</td>{% for x in p.months %}<td>{{ M(x[key], x.usd[key], 2) }}</td>{% endfor %}<td><b>{{ M(p[key], p.usd[key], 2) }}</b></td></tr>{% endfor %}
</table></div></details>{% endfor %}
<p class="small">{{ t('The Excel download has the same statement on its sheet "By person". Amounts are not rounded: no rounding rule is set yet.',
'La descarga de Excel tiene el mismo estado en la hoja "By person". Los importes no se redondean: aún no hay regla de redondeo.') }}</p>{% endif %}
<p class="small">{{ t('Sales two thirds, Adm, MC and Buyer one third. Control (total paid out minus total calculated):',
'Ventas dos tercios; Adm, MC y Compras un tercio. Control (total repartido menos total calculado):') }} {{ rep.control|cm_money(2) }}.</p></div>

{% if rep %}<div class="card" id="payruns"><h2>{{ t('Pay runs: review, approval and payment', 'Corridas de pago: revisión, aprobación y pago') }} (MXN)</h2>
<p class="small">{{ t('Lupita reviews and submits a run, Mario approves it (or returns it with a note), Rogelio records that it was paid. A run pays every collected line of the new calculation, less what earlier approved runs already paid on it, so nothing is paid twice and late collections or late margin parts go in the next run.',
'Lupita revisa y envía la corrida, Mario la aprueba (o la devuelve con una nota), Rogelio registra que se pagó. Una corrida paga cada línea cobrada del cálculo nuevo, menos lo ya pagado en corridas aprobadas anteriores; nada se paga dos veces y los cobros o márgenes tardíos entran en la siguiente corrida.') }}
{{ t('Your role:', 'Su papel:') }} <b>{% for r in my_roles %}{{ {'review': t('reviewer', 'revisión'), 'approve': t('approver', 'aprobación'), 'finance': t('Finance', 'Finanzas'), 'admin': 'admin'}[r] }}{% if not loop.last %}, {% endif %}{% else %}{{ t('view only', 'solo consulta') }}{% endfor %}</b>.</p>
{% if open_run %}{% set r = open_run %}
<h3>{{ t('Run ' ~ r.id ~ ' is waiting for approval', 'La corrida ' ~ r.id ~ ' espera aprobación') }}: {{ r.due|cm_money(2) }}</h3>
<p class="small">{{ t('Submitted by', 'Enviada por') }} {{ r.log[0].by }} · {{ r.log[0].at[:16].replace('T', ' ') }} UTC{% if r.log[0].note %} · “{{ r.log[0].note }}”{% endif %} · {{ r.lines|length }} {{ t('lines', 'líneas') }} · {{ t('invoice months', 'meses de factura') }} {{ r.months|join(', ') }}</p>
<div class="scroll"><table><tr><th>{{ t('Person', 'Persona') }}</th><th class="l">{{ t('Group', 'Grupo') }}</th><th>{{ t('Amount', 'Importe') }}</th></tr>
{% for p in r.people %}<tr><td>{{ p.name }}</td><td class="l">{{ p.group }}</td><td>{{ p.amount|cm_money(2) }}</td></tr>{% endfor %}</table></div>
<p><a class="btn ghost" href="/commissions/signoff/{{ r.id }}/instruction">{{ t('Download the run (Excel)', 'Descargar la corrida (Excel)') }}</a></p>
{% if so.may(email, 'approve') %}<form method="post" action="/commissions/signoff" class="row"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="run" value="{{ r.id }}">
<input type="text" name="note" placeholder="{{ 'Note (required to return) / Nota (obligatoria para devolver)' }}" style="min-width:320px">
<button type="submit" name="step" value="approve" class="go">{{ t('Approve run ' ~ r.id, 'Aprobar corrida ' ~ r.id) }}</button>
<button type="submit" name="step" value="return" class="btn ghost">{{ t('Return with note', 'Devolver con nota') }}</button></form>{% endif %}
{% if so.may(email, 'withdraw') %}<form method="post" action="/commissions/signoff" class="row"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="run" value="{{ r.id }}">
<button type="submit" name="step" value="withdraw" class="btn ghost">{{ t('Withdraw (before approval)', 'Retirar (antes de aprobar)') }}</button></form>{% endif %}
{% elif pay_draft %}
<h3>{{ t('Next run (draft, worked out now)', 'Siguiente corrida (borrador, calculado ahora)') }}: {{ pay_draft.due|cm_money(2) }}</h3>
{% if pay_draft.lines %}<p class="small">{{ pay_draft.lines|length }} {{ t('lines', 'líneas') }} · {{ t('invoice months', 'meses de factura') }} {{ pay_draft.months|join(', ') }}{% set adj = pay_draft.lines|selectattr('adjust')|list %}{% if adj %} · {{ adj|length }} {{ t('adjustments to lines paid before', 'ajustes a líneas pagadas antes') }}{% endif %}</p>
<details><summary>{{ t('Lines in this run', 'Líneas de esta corrida') }}</summary><div class="scroll"><table><tr><th>{{ t('Month', 'Mes') }}</th><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Line commission', 'Comisión de la línea') }}</th><th>{{ t('Paid before', 'Pagado antes') }}</th><th>{{ t('In this run', 'En esta corrida') }}</th></tr>
{% for x in pay_draft.lines %}<tr><td>{{ x.month }}</td><td>{{ x.job }}</td><td>{{ x.total|cm_money(2) }}</td><td>{{ x.before|cm_money(2) }}</td><td>{{ x.due|cm_money(2) }}</td></tr>{% endfor %}</table></div></details>
<div class="scroll"><table><tr><th>{{ t('Person', 'Persona') }}</th><th class="l">{{ t('Group', 'Grupo') }}</th><th>{{ t('Amount', 'Importe') }}</th></tr>
{% for p in pay_draft.people %}<tr><td>{{ p.name }}</td><td class="l">{{ p.group }}</td><td>{{ p.amount|cm_money(2) }}</td></tr>{% endfor %}</table></div>
{% if so.may(email, 'submit') %}<form method="post" action="/commissions/signoff" class="row"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="expect" value="{{ pay_draft.fingerprint }}">
<input type="text" name="note" placeholder="{{ 'Note for Mario (optional) / Nota para Mario (opcional)' }}" style="min-width:320px">
<button type="submit" name="step" value="submit" class="go">{{ t('Reviewed: send to Mario for approval', 'Revisado: enviar a Mario para aprobación') }}</button></form>{% endif %}
{% else %}<p>{{ t('Nothing new to pay: no collected line of the new calculation is left unpaid.', 'Nada nuevo por pagar: no queda ninguna línea cobrada del cálculo nuevo sin pagar.') }}</p>{% endif %}
{% for w in pay_draft.warnings %}<p class="note warn">{{ w.month }} · {{ w.job }}: {{ t(w.en, w.es) }} ({{ w.paid|cm_money(2) }}).</p>{% endfor %}
{% endif %}
{% if runs %}<h3>{{ t('All runs', 'Todas las corridas') }}</h3><div class="scroll"><table><tr><th>#</th><th class="l">{{ t('State', 'Estado') }}</th><th>{{ t('Total', 'Total') }}</th><th class="l">{{ t('Months', 'Meses') }}</th><th class="l">{{ t('History', 'Historial') }}</th><th></th></tr>
{% for r in runs %}<tr><td>{{ r.id }}</td><td class="l">{{ t(r.state, so.STATE_ES[r.state]) }}{% if r.paid_on %} {{ r.paid_on }}{% endif %}</td><td>{{ r.due|cm_money(2) }}</td><td class="l">{{ r.months|join(', ') }}</td>
<td class="l wrapc">{% for e in r.log %}{{ t(e.step, so.STEP_ES[e.step]) }}: {{ e.by }}{% if e.as_admin %} (admin){% endif %}, {{ e.at[:16].replace('T', ' ') }}{% if e.note %} “{{ e.note }}”{% endif %}{% if not loop.last %}<br>{% endif %}{% endfor %}
{% if r.changed_at_approval %}<br><span class="small">{{ t('At approval the live figures differed by', 'Al aprobar, las cifras vivas diferían en') }} {{ (r.changed_at_approval.on_these_lines + r.changed_at_approval.new_lines)|cm_money(2) }}; {{ t('the difference goes to the next run.', 'la diferencia pasa a la siguiente corrida.') }}</span>{% endif %}</td>
<td><a href="/commissions/signoff/{{ r.id }}/instruction">Excel</a>
{% if r.state == 'approved' and so.may(email, 'pay') %}<form method="post" action="/commissions/signoff" class="row"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="run" value="{{ r.id }}">
<input type="date" name="paid_on"><button type="submit" name="step" value="pay" class="go">{{ t('Mark paid', 'Marcar pagada') }}</button></form>{% endif %}
{% if r.state == 'approved' and so.may(email, 'cancel') and 'admin' in my_roles %}<form method="post" action="/commissions/signoff" class="row"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="run" value="{{ r.id }}">
<input type="text" name="note" placeholder="Reason / Motivo"><button type="submit" name="step" value="cancel" class="btn ghost">{{ t('Cancel (admin)', 'Cancelar (admin)') }}</button></form>{% endif %}</td></tr>{% endfor %}</table></div>{% endif %}
<p class="small">{{ t('Amounts per person are rounded to the cent (half up); any cent left over shows as the rounding difference in the Excel file.', 'Los importes por persona se redondean al centavo (medio hacia arriba); cualquier centavo sobrante aparece como diferencia por redondeo en el Excel.') }}</p></div>{% endif %}

<div class="card" id="checks"><h2>{{ t('Points to look at', 'Puntos por revisar') }} ({{ rep.check_count }})</h2>
<p class="small">{{ t('From Finance\'s report and from the Moveware reports. File numbers, document numbers and amounts only. Unless a Moveware report is named, row numbers are rows of the sheet "Base". Amounts here are always in pesos.',
'Del reporte de Finanzas y de los reportes de Moveware. Solo números de expediente, de documento e importes. Salvo que se nombre un reporte de Moveware, los números de fila son filas de la hoja "Base". Aquí los importes siempre están en pesos.') }}</p>
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
<p class="small">{{ t('Upload the workbook "Margen x Expediente" with its sheet "Base", the whole year to date each time. It replaces the report in use. The workbook is read and not kept; only file numbers, document numbers, dates and amounts are stored, never customer names. If a number cannot be read, nothing is loaded and the page says which row. The Excel download has one sheet per month in the layout of the Finance commission workbook, in English and in pesos.',
'Suba el libro "Margen x Expediente" con su hoja "Base", siempre con todo el año a la fecha. Sustituye al reporte en uso. El libro se lee y no se guarda; solo se almacenan números de expediente, de documento, fechas e importes, nunca nombres de clientes. Si un número no se puede leer, no se carga nada y la página indica la fila. La descarga en Excel tiene una hoja por mes con el formato del libro de comisiones de Finanzas, en inglés y en pesos.') }}</p></div>

<div class="card" id="collected"><h2>{{ t('Collected invoices', 'Facturas cobradas') }}</h2>
<p class="small">{{ t('Finance\'s report of customer payments (CONTPAQ: "Análisis de IVA de pagos de clientes"), one workbook per company, from 1 January to today. Each upload replaces the report of that same company. A line becomes Paid when every invoice behind it is collected in full, and Pending when the report covers its invoices and one is not; a mark made in the lines table below always wins. The workbook is read and not kept; customer names and bank references in it are never stored.',
'Reporte de pagos de clientes de Finanzas (CONTPAQ: "Análisis de IVA de pagos de clientes"), un libro por empresa, del 1 de enero a hoy. Cada carga sustituye al reporte de esa misma empresa. Una línea pasa a Pagada cuando todas sus facturas están cobradas por completo, y a Pendiente cuando el reporte cubre sus facturas y alguna no lo está; una marca hecha en la tabla de líneas de abajo siempre prevalece. El libro se lee y no se guarda; nunca se almacenan los nombres de clientes ni las referencias bancarias.') }}</p>
{% for ps, info, m in col %}<p>{{ info.company or t('Company not known by name', 'Empresa no conocida por nombre') }}: {{ ps|length }} {{ t('payments from', 'pagos del') }} {{ info['from'] or '?' }} {{ t('to', 'al') }} {{ info['to'] or '?' }} · {{ t('uploaded', 'subido el') }} {{ m.at|cm_when if m.at else '?' }} {{ t('by', 'por') }} {{ m.by }}.</p>{% else %}<p>{{ t('No payments report loaded yet.', 'Aún no se ha cargado ningún reporte de pagos.') }}</p>{% endfor %}
<form method="post" action="/commissions/upload-payments" enctype="multipart/form-data" class="row">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="file" name="report" accept=".xlsx" required aria-label="Payments report">
<button type="submit" class="go">{{ t('Upload payments report', 'Subir reporte de pagos') }}</button></form>
<p style="margin-top:14px">{% if reg.invoices %}{{ t('Invoice register in use:', 'Registro de facturas en uso:') }} {{ reg.info.invoices }} {{ t('invoices from', 'facturas del') }} {{ reg.info['from'] }} {{ t('to', 'al') }} {{ reg.info['to'] }} · {{ t('uploaded', 'subido el') }} {{ reg.meta.at|cm_when }} {{ t('by', 'por') }} {{ reg.meta.by }}.{% else %}{{ t('Invoice register: not loaded yet.', 'Registro de facturas: aún no se ha cargado.') }}{% endif %}</p>
<p class="small">{{ t('Finance\'s register of invoices issued (FACTURADO ACUMULADO YTD) gives each invoice its real series, the company that issued it and its total. With it a payment is matched to the right invoice, and an invoice counts as collected in full when the payments reach its total. Each upload replaces the one before. The workbook is read and not kept; customer and staff names in it are never stored.',
'El registro de facturas emitidas de Finanzas (FACTURADO ACUMULADO YTD) da a cada factura su serie real, la empresa que la emitió y su total. Con él cada pago se liga a la factura correcta, y una factura cuenta como cobrada por completo cuando los pagos alcanzan su total. Cada carga sustituye a la anterior. El libro se lee y no se guarda; nunca se almacenan los nombres de clientes ni del personal.') }}</p>
<form method="post" action="/commissions/upload-invoices" enctype="multipart/form-data" class="row">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="file" name="report" accept=".xlsx" required aria-label="Invoice register">
<button type="submit" class="go">{{ t('Upload invoice register', 'Subir registro de facturas') }}</button></form>
{% if rep and rep.collection %}{% set c = rep.collection %}<p style="margin-top:14px">{{ c.invoices }} {{ t('invoices are behind the commission lines.', 'facturas respaldan las líneas de comisión.') }} {{ c.covered }} {{ t('are covered by the payments reports loaded:', 'están cubiertas por los reportes de pagos cargados:') }} {{ c.full }} {{ t('collected in full,', 'cobradas por completo,') }} {{ c.part }} {{ t('in part,', 'en parte,') }} {{ c.open }} {{ t('not collected.', 'sin cobrar.') }} {{ c.invoices - c.covered }} {{ t('are not covered by any report loaded, so their lines have no status yet.', 'no están cubiertas por ningún reporte cargado, así que sus líneas aún no tienen estatus.') }}</p>
<div class="scroll"><table><tr><th class="l">{{ t('Company that issued the invoice', 'Empresa que emitió la factura') }}</th><th>{{ t('Invoices', 'Facturas') }}</th><th class="l">{{ t('Payments report', 'Reporte de pagos') }}</th><th>{{ t('Collected in full', 'Cobradas por completo') }}</th><th>{{ t('In part', 'En parte') }}</th><th>{{ t('Not collected', 'Sin cobrar') }}</th><th>{{ t('Not covered', 'Sin cubrir') }}</th></tr>
{% for k in c.companies %}<tr><td class="l">{{ k.company if k.company != '?' else t('Not known', 'No conocida') }}</td><td>{{ k.invoices }}</td><td class="l">{% if k.report %}{{ t('loaded, to', 'cargado, al') }} {{ k.to or '?' }}{% else %}<span class="tag w">{{ t('not loaded', 'no cargado') }}</span>{% endif %}</td><td>{{ k.full }}</td><td>{{ k.part }}</td><td>{{ k.open }}</td><td>{{ k.invoices - k.covered }}</td></tr>{% endfor %}</table></div>
{% if c.not_in_report %}<p class="small">{{ c.not_in_report|length }} {{ t('paid invoices are not behind any Corporate or Private line of Finance\'s report and are ignored:', 'facturas pagadas no respaldan ninguna línea corporativa o particular del reporte de Finanzas y se ignoran:') }} {{ c.not_in_report|join(', ') }}.</p>{% endif %}{% endif %}</div>

<div class="card" id="moveware"><h2>{{ t('Booked quotes from Moveware', 'Cotizaciones contratadas de Moveware') }}</h2>
<p class="small">{{ t('Two Moveware reports give each file its booked quote: the Removals Summary (sale price excluding IVA, date won, customer type) and the Move Costing Sheet (quoted cost). Run both from 1 January to today and upload them here; each upload replaces the one before. The workbooks are read and not kept; customer names, addresses and e-mail addresses in them are never stored.',
'Dos reportes de Moveware dan la cotización contratada de cada expediente: el Removals Summary (precio de venta sin IVA, fecha de ganado, tipo de cliente) y el Move Costing Sheet (costo cotizado). Genere ambos del 1 de enero a hoy y súbalos aquí; cada carga sustituye a la anterior. Los libros se leen y no se guardan; nunca se almacenan los nombres, direcciones ni correos de clientes.') }}</p>
<p>{% if mw.removals %}{{ t('Removals Summary in use: uploaded', 'Removals Summary en uso: subido el') }} {{ mw.removals[2].at|cm_when }} {{ t('by', 'por') }} {{ mw.removals[2].by }} · {{ mw.removals[0]|length }} {{ t('jobs', 'expedientes') }}.{% else %}{{ t('Removals Summary: not loaded yet.', 'Removals Summary: aún no se ha cargado.') }}{% endif %}</p>
<form method="post" action="/commissions/upload-removals" enctype="multipart/form-data" class="row">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="file" name="report" accept=".xlsx" required aria-label="Removals Summary">
<button type="submit" class="go">{{ t('Upload Removals Summary', 'Subir Removals Summary') }}</button></form>
<p>{% if mw.costing %}{{ t('Move Costing Sheet in use: uploaded', 'Move Costing Sheet en uso: subido el') }} {{ mw.costing[2].at|cm_when }} {{ t('by', 'por') }} {{ mw.costing[2].by }} · {{ mw.costing[0]|length }} {{ t('jobs', 'expedientes') }}{% if mw.costing[1].won_from %} · {{ t('won', 'ganados del') }} {{ mw.costing[1].won_from }} {{ t('to', 'al') }} {{ mw.costing[1].won_to }}{% endif %}.{% else %}{{ t('Move Costing Sheet: not loaded yet.', 'Move Costing Sheet: aún no se ha cargado.') }}{% endif %}</p>
<form method="post" action="/commissions/upload-costing" enctype="multipart/form-data" class="row">
<input type="hidden" name="csrf" value="{{ csrf }}"><input type="file" name="report" accept=".xlsx" required aria-label="Move Costing Sheet">
<button type="submit" class="go">{{ t('Upload Move Costing Sheet', 'Subir Move Costing Sheet') }}</button></form>
{% if rep and rep.quotes_loaded %}<div class="scroll" style="margin-top:14px"><table><tr><th>{{ t('Invoice month', 'Mes de factura') }}</th><th>{{ t('Files first invoiced', 'Expedientes facturados por primera vez') }}</th><th>{{ t('With a booked quote', 'Con cotización') }}</th><th>{{ t('Sale prices found (MXN)', 'Precios de venta encontrados (MXN)') }}</th><th>{{ t('Budget (MXN)', 'Presupuesto (MXN)') }}</th><th class="l">{{ t('Bookings used', 'Ventas contratadas usadas') }}</th><th class="l">{{ t('Files with no quote', 'Expedientes sin cotización') }}</th></tr>
{% for r in rep.months %}<tr><td>{{ mon(r.month) }}</td><td>{{ r.quote.files }}</td><td>{{ r.quote.found }}</td><td>{{ r.quote.bookings|cm_money(0) }}</td><td>{{ r.budget|cm_money(0) }}</td>
<td class="l">{% if r.bookings_source == 'quotes' %}{{ t('the booked quotes', 'las cotizaciones') }}{% elif r.bookings_source == 'entered' %}{{ t('the figure typed below', 'la cifra capturada abajo') }}{% else %}{{ t('the budget, as a placeholder', 'el presupuesto, como valor provisional') }}{% endif %}</td>
<td class="l wrapc">{{ r.quote.missing|join(', ') if r.quote.missing else '–' }}</td></tr>{% endfor %}</table></div>
<p class="small">{{ t('A file\'s booked quote is counted once, in the month the file is first invoiced. A month uses its booked quotes as bookings when every file first invoiced in it has one; otherwise it keeps the budget as a placeholder, unless a figure is typed below. Dollar quotes at ' ~ '%.2f'|format(s.usd_mxn) ~ '.',
'La cotización de un expediente se cuenta una vez, en el mes en que se factura por primera vez. Un mes usa sus cotizaciones como ventas contratadas cuando todos los expedientes facturados por primera vez en él tienen cotización; si no, conserva el presupuesto como valor provisional, salvo que se capture una cifra abajo. Cotizaciones en dólares a ' ~ '%.2f'|format(s.usd_mxn) ~ '.') }}</p>{% endif %}</div>

<div class="card" id="bookings"><h2>{{ t('Bookings per month', 'Ventas contratadas por mes') }} (MXN)</h2>
<p class="small">{{ t('Bookings normally come from the booked quotes above. A figure typed here replaces them for that month. Sales reach = bookings ÷ budget × ' ~ (s.w_invoicing*100)|round|int ~ ' %. Leave a month blank to use the booked quotes.',
'Las ventas contratadas normalmente salen de las cotizaciones de arriba. Una cifra capturada aquí las sustituye en ese mes. Alcance de ventas = ventas contratadas ÷ presupuesto × ' ~ (s.w_invoicing*100)|round|int ~ ' %. Deje el mes en blanco para usar las cotizaciones.') }}{% if bmeta %} {{ t('Last saved', 'Guardado por última vez el') }} {{ bmeta.at|cm_when }} {{ t('by', 'por') }} {{ bmeta.by }}.{% endif %}</p>
<form method="post" action="/commissions/bookings"><input type="hidden" name="csrf" value="{{ csrf }}">
<div class="scroll"><table><tr><th>{{ t('Month', 'Mes') }}</th><th>{{ t('Budget (MXN)', 'Presupuesto (MXN)') }}</th><th>{{ t('Bookings (MXN)', 'Ventas contratadas (MXN)') }}</th><th>{{ t('Budget achievement', 'Cumplimiento del presupuesto') }}</th></tr>
{% for m in months %}<tr><td>{{ mon(m) }}</td><td>{{ s.budgets[m]|cm_money(2) }}</td>
<td><input type="text" name="b_{{ m }}" inputmode="decimal" value="{{ '{:,.2f}'.format(bookings[m]) if m in bookings else '' }}" aria-label="{{ month_label(m) }}"></td>
<td>{{ (bookings[m] / s.budgets[m])|cm_pct if m in bookings else '' }}</td></tr>{% endfor %}
</table></div><p><button type="submit" class="go">{{ t('Save bookings', 'Guardar ventas contratadas') }}</button></p></form></div>

{% if rep %}<div class="card" id="lines"><h2>{{ t('Commission lines', 'Líneas de comisión') }} ({{ rep.lines|length }}) (<span class="cur">MXN</span>)</h2>
<p class="small">{{ t('One line per file per invoice month, net of that month\'s credit notes. Commission is payable only on Paid lines. The status comes from the payments reports loaded above (shown as a tag); to set or correct a line by hand, choose its status and save, or paste a list of file numbers below.', 'Una línea por expediente por mes de factura, neta de las notas de crédito de ese mes. La comisión solo es pagadera en las líneas pagadas. El estatus sale de los reportes de pagos cargados arriba (se muestra como etiqueta); para fijar o corregir una línea a mano, elija su estatus y guarde, o pegue abajo una lista de expedientes.') }}{% if pmeta %} {{ t('Payment status last saved', 'Estatus de pago guardado por última vez el') }} {{ pmeta.at|cm_when }} {{ t('by', 'por') }} {{ pmeta.by }}.{% endif %}</p>
<form method="post" action="/commissions/payments" class="row" style="align-items:flex-start;margin-bottom:10px"><input type="hidden" name="csrf" value="{{ csrf }}">
<textarea name="bulk_jobs" rows="2" cols="42" aria-label="{{ 'File numbers' }}" placeholder="110510 110584 110611 …"></textarea>
<select name="bulk_month" aria-label="Month">{% for r in rep.months %}<option value="{{ r.month }}"{{ ' selected' if loop.last else '' }}>{{ month_label(r.month) }}</option>{% endfor %}</select>
<select name="bulk_status" aria-label="Status"><option value="Paid">Paid / Pagada</option><option value="Pending">Pending / Pendiente</option></select>
<button type="submit" class="go">{{ t('Mark the pasted files', 'Marcar los expedientes pegados') }}</button></form>
{% if rep.payments_unused %}<p class="small">{{ rep.payments_unused|length }} {{ t('payment mark(s) belong to lines that are not in the report in use and are ignored:', 'marca(s) de pago son de líneas que no están en el reporte en uso y se ignoran:') }} {{ rep.payments_unused|join(', ') }}.</p>{% endif %}
<form method="post" action="/commissions/payments"><input type="hidden" name="csrf" value="{{ csrf }}">
<p><button type="submit" class="go">{{ t('Save payment status', 'Guardar estatus de pago') }}</button></p>
{% for r in rep.months %}<details{{ ' open' if r.paid_lines or r.pending_lines else '' }}><summary>{{ mon(r.month) }}: {{ r.lines }} {{ t('lines', 'líneas') }}, {{ M(r.total, r.usd.total, 2) }}{% if r.paid_lines %} · {{ r.paid_lines }} {{ t('paid', 'pagadas') }}, {{ M(r.payable, r.usd.payable, 2) }}{% endif %}</summary>
<div class="scroll"><table><tr><th>{{ t('File', 'Expediente') }}</th><th>{{ t('Billed', 'Facturado') }}</th><th>{{ t('Actual cost', 'Costo real') }}</th><th>{{ t('Gross margin', 'Margen bruto') }}</th><th>{{ t('% margin', '% margen') }}</th>
<th>{{ t('Invoicing part', 'Parte facturación') }}</th><th>{{ t('Margin part', 'Parte margen') }}</th><th>{{ t('Discipline part', 'Parte disciplina') }}</th><th>Total</th><th class="l">{{ t('Payment', 'Pago') }}</th><th class="l">{{ t('Notes', 'Notas') }}</th></tr>
{% for x in rep.lines if x.month == r.month %}<tr><td>{{ x.job }}</td><td>{{ M(x.billed, x.usd.billed, 2) }}</td><td>{{ M(x.cost, x.usd.cost, 2) }}</td>
<td>{{ M(x.gross_margin, x.usd.gross_margin, 2) }}</td><td>{{ x.margin_pct|cm_pct }}</td><td>{{ M(x.invoicing, x.usd.invoicing, 2) }}</td>
<td>{% if x.cost_posted %}{{ M(x.margin, x.usd.margin, 2) }}{% else %}<span class="tag w">{{ t('waits for cost', 'espera costo') }}</span>{% endif %}</td>
<td>{{ M(x.discipline, x.usd.discipline, 2) }}</td><td>{{ M(x.total, x.usd.total, 2) }}</td>
<td class="l"><select name="p_{{ x.month }}_{{ x.job }}" aria-label="{{ x.job }}"><option value=""{{ ' selected' if not x.payment_mark else '' }}>–</option><option value="Paid"{{ ' selected' if x.payment_mark == 'Paid' else '' }}>Paid / Pagada</option><option value="Pending"{{ ' selected' if x.payment_mark == 'Pending' else '' }}>Pending / Pendiente</option></select>{% if x.payment_source == 'file' %} <span class="tag{{ '' if x.payment == 'Paid' else ' w' }}">{% if x.payment == 'Paid' %}{{ t('paid', 'pagada') }}{% if x.paid_on %} {{ x.paid_on }}{% endif %}{% else %}{{ t('not collected', 'sin cobrar') }}{% endif %}</span>{% endif %}</td>
<td class="wrapc">{% if x.credit %}<span class="tag">{{ t('credit notes exceed invoices', 'las notas de crédito superan a las facturas') }}</span> {% endif %}{% for f in x.flags %}<span class="tag w">{{ t(f.en, f.es) }}</span> {% endfor %}</td></tr>{% endfor %}
</table></div></details>{% endfor %}
<p><button type="submit" class="go">{{ t('Save payment status', 'Guardar estatus de pago') }}</button></p></form></div>{% endif %}
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
