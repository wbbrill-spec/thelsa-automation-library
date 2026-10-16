"""
report.py — the commission report as numbers, ready for a page or a workbook.

One function, `build`, takes the lines read from Finance's margin report, the
settings, and the bookings typed in so far, and returns everything the report
shows: a row per month, totals, the distribution, every commission line and the
list of points to look at. The web page and the Excel workbook both read from
this, so they cannot disagree.

Where bookings for a month are not known yet, the month is calculated with
bookings equal to its budget (sales reach 0.60) and marked as a placeholder.
"""
from __future__ import annotations

import dataclasses
import re
import statistics
from collections import defaultdict

from . import engine as E
from . import finance as F
from . import invoices as I
from . import payments as P
from . import quotes as Q

# The new calculation applies from June 2026. January to May follow the old
# calculation (Lupita, 12 Oct 2026: "April and May are OLD calculation"; her
# note of 7 Oct had said May).
NEW_FROM = "2026-06"

# Points to look at, most important first, in the words the reader sees.
LABELS = {
    "credit_note_amount": "Credit note amount looks wrong",
    "dollar_amount_mismatch": "Dollar and peso amounts do not agree",
    "no_dollar_amount": "Dollar invoice with no usable dollar amount",
    "other_currency": "Invoice in another currency, Finance's pesos used",
    "partly_collected": "Invoice collected in part",
    "moveware_type_differs": "Type in Moveware differs from Finance's",
    "quote_totals": "Moveware Removals Summary: totals and list differ",
    "quote_branch_currency": "Moveware branch and currency do not agree",
    "quote_total_off": "Moveware costing: option total is not the sum of its lines",
    "costing_duplicate": "Moveware costing: the same cost line entered more than once",
    "move_type_unlisted": "Moveware move type not in the settings list",
    "quote_no_date": "Moveware: date won not readable",
    "quote_missing": "Invoiced file with no booked quote in Moveware",
    "quote_no_cost": "Booked quote with no quoted cost",
    "quote_with_tax": "Booked quote loaded with IVA in Moveware",
    "duplicate_line": "Same document entered twice",
    "fx_outlier": "Exchange rate looks mistyped",
    "embassy_billed_in_scope": "Billed to the Embassy, typed CORP-PART",
    "type_conflict": "Same job typed two ways",
    "bad_job_number": "Not a valid job number",
    "unknown_type": "Unknown type",
    "no_date": "No date: the line is in no month",
    "date_out_of_range": "Date in the wrong year",
    "empty_line": "Line with no peso amount",
    "sale_and_cost_on_one_row": "Sale and cost on the same line",
    "negative_cost": "Negative cost on the job",
    "credit_reference_unreadable": "Credit note: cancelled document not readable",
    "cost_not_counted": "Cost not counted: the job's billing nets to zero",
    "no_cost_posted": "Billed, no cost posted yet",
    "cost_without_sales": "Cost posted, nothing billed in the period",
    "currency_missing": "Invoice currency not stated",
}
LABELS_ES = {
    "credit_note_amount": "El importe de la nota de crédito parece incorrecto",
    "dollar_amount_mismatch": "Los importes en dólares y en pesos no coinciden",
    "no_dollar_amount": "Factura en dólares sin importe en dólares utilizable",
    "other_currency": "Factura en otra moneda, se usan los pesos de Finanzas",
    "partly_collected": "Factura cobrada en parte",
    "moveware_type_differs": "El tipo en Moveware difiere del de Finanzas",
    "quote_totals": "Removals Summary de Moveware: totales y lista no coinciden",
    "quote_branch_currency": "Sucursal y moneda de Moveware no coinciden",
    "quote_total_off": "Costeo de Moveware: el total de la opción no es la suma de sus líneas",
    "costing_duplicate": "Costeo de Moveware: la misma línea de costo capturada más de una vez",
    "move_type_unlisted": "Tipo de mudanza de Moveware que no está en la configuración",
    "quote_no_date": "Moveware: fecha de ganado ilegible",
    "quote_missing": "Expediente facturado sin cotización en Moveware",
    "quote_no_cost": "Cotización sin costo cotizado",
    "quote_with_tax": "Cotización cargada con IVA en Moveware",
    "duplicate_line": "El mismo documento capturado dos veces",
    "fx_outlier": "El tipo de cambio parece mal capturado",
    "embassy_billed_in_scope": "Facturado a la Embajada, con tipo CORP-PART",
    "type_conflict": "El mismo expediente con dos tipos",
    "bad_job_number": "Número de expediente no válido",
    "unknown_type": "Tipo desconocido",
    "no_date": "Sin fecha: la línea no cae en ningún mes",
    "date_out_of_range": "Fecha en el año equivocado",
    "empty_line": "Línea sin importe en pesos",
    "sale_and_cost_on_one_row": "Venta y costo en la misma línea",
    "negative_cost": "Costo negativo en el expediente",
    "credit_reference_unreadable": "Nota de crédito: no se puede leer el documento que cancela",
    "cost_not_counted": "Costo no contado: lo facturado del expediente se cancela",
    "no_cost_posted": "Facturado, todavía sin costo",
    "cost_without_sales": "Costo registrado, nada facturado en el periodo",
    "currency_missing": "Moneda de la factura no indicada",
}
RULES_ES = {"New calculation": "Cálculo nuevo", "To confirm": "Por confirmar", "Old calculation": "Cálculo anterior"}
# Roles as Rogelio's workbook names them, and the same in Spanish.
PEOPLE_ES = {"Buyer and Cost Manager (Lupita)": "Compradora y Gerente de Costos (Lupita)",
             "Logistics Supervisor (Sara Reyes)": "Supervisora de Logística (Sara Reyes)",
             "Sales": "Ventas", "Adm, MC and Buyer": "Adm, MC y Compras"}
MONEY = ("billed", "cost", "gross_margin", "invoicing", "margin", "discipline", "total", "payable", "held",
         "waiting_billed")
# Long, routine lists: shown folded on the page.
ROUTINE = ("no_cost_posted", "cost_without_sales", "currency_missing", "quote_missing", "quote_with_tax",
           "quote_no_cost")


def rule_for(month: str) -> str:
    return "New calculation" if month >= NEW_FROM else "Old calculation"


def clean_bookings(bookings) -> dict:
    """{month: amount} with only real, positive numbers kept."""
    out = {}
    for m, v in (bookings or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and E._finite(v) and v > 0:
            out[str(m)] = float(v)
    return out


def month_rates(lines, s: E.Settings) -> tuple:
    """Pesos per dollar "as booked": for the "USD books" view (Bill, 15 Oct).

    Each month uses the middle rate of its dollar invoices in Finance's own
    report (the middle one, so a mistyped rate does not move it); a month with
    no dollar invoice uses the middle of the year. Display only: the commission
    itself is always calculated in pesos with dollar sales at 16.50 (D30).
    Returns ({month: rate}, rate for any other month, whether the rates were
    taken from the report)."""
    by = defaultdict(list)
    for ln in lines:
        if (ln.is_sale and ln.date and ln.currency == "USD" and ln.currency_source == "stated" and ln.fx
                and F.RATE_BAND[0] <= ln.fx <= F.RATE_BAND[1]):
            by[ln.month].append(ln.fx)
    every = [x for v in by.values() for x in v]
    return ({m: statistics.median(v) for m, v in by.items()},
            statistics.median(every) if every else s.usd_mxn, bool(every))


PAYMENT_WORDS = ("Paid", "Pending")
_PAY_KEY = re.compile(r"^(\d{4}-(?:0[1-9]|1[0-2]))\|(\d{5,7}[A-Z]{0,2})$")


def clean_payments(payments) -> dict:
    """{(month, job): True for Paid, False for Pending} from what the page
    stored as {"2026-06|110510": "Paid"}. Anything else is left out."""
    out = {}
    for k, v in (payments or {}).items():
        m = _PAY_KEY.match(str(k))
        if m and v in PAYMENT_WORDS:
            out[(m.group(1), m.group(2))] = v == "Paid"
    return out


# When an invoice counts as collected in full. The payments reports show what the customer paid, IVA included.
COLLECTED_AT = 0.98          # of Finance's amount without IVA, when the invoice's own total is not known
COLLECTED_OF_TOTAL = 0.96    # of the invoice total when it carries IVA: a customer that withholds IVA on freight
#                              pays 1.12 of the subtotal against a total of 1.16, which is 96.6 % of it
COLLECTED_OTHER_CCY = 0.92   # when the payment was made in the other currency, at the rate of the payment day
# Finance's margin report sometimes writes the type where the invoice series goes.
LABEL_SERIES = ("CORP-PART", "AGENTE", "DIPLOM", "?")


def _real(ln, finder) -> tuple:
    """(series, company) of the invoice a line of the margin report shows,
    looked up in the invoice register by folio and date when the line carries
    a type instead of a series, or no company."""
    series, company = ln.series, ln.entity
    found = finder.get((ln.folio, ln.date.isoformat()), {}) if finder else {}
    if series in found:
        return series, company or found[series]["company"]
    if series in LABEL_SERIES and found:
        c = {k: v for k, v in found.items() if not company or not v["company"] or v["company"] == company} or found
        c = {k: v for k, v in c.items() if ln.job in v["jobs"]} or c
        if len(c) == 1:
            (series, d), = c.items()
            company = company or d["company"]
    return series, company


def invoice_map(lines, register=None) -> dict:
    """{(month, job): [invoice, …]} — the invoices behind each commission line:
    the positive sales of Corporate and Private jobs, by invoice month.

    invoice = {"series", "folio", "company", "date", "pesos", "dollars": this
    line's part of it, "whole_pesos", "whole_dollars": the invoice over every
    file it bills (dollars None when not all known), "due": the part of the
    invoice left after credit notes that name it, "total", "subtotal",
    "currency", "rate": the invoice as issued, from the register (None, None, '',
    None without it)}.

    An invoice taken back in full, by credit notes that name it or by a
    negative line under its own number, is left out: it will never be
    collected. `register` = invoices.Invoice rows of Finance's invoice register."""
    finder = I.real_series(register) if register else {}
    numbers = I.by_number(register) if register else {}
    sales = [(ln,) + _real(ln, finder) for ln in lines if ln.is_sale and ln.date]
    issuers = {}                    # two companies can use the same series and folio: an invoice is told by all three
    for ln, series, company in sales:
        issuers.setdefault((series, ln.folio), set()).add(company)

    def number(series, folio, company):
        known = issuers.get((series, folio), set()) - {""}
        return series, folio, company or (next(iter(known)) if len(known) == 1 else "")
    whole, credit = {}, {}
    for ln, series, company in sales:
        w = whole.setdefault(number(series, ln.folio, company), {"pesos": 0.0, "dollars": 0.0, "all": True})
        w["pesos"] += ln.net_sales_mxn
        w["dollars"] += ln.usd_equiv or 0.0
        w["all"] = w["all"] and bool(ln.usd_equiv)
        if ln.net_sales_mxn < 0 and ln.cancels:
            a, _, b = ln.cancels.rpartition(" ")
            credit[number(a, b, company)] = credit.get(number(a, b, company), 0.0) - ln.net_sales_mxn
    out = {}
    for ln, series, company in sales:
        if ln.type != F.IN_SCOPE or ln.net_sales_mxn <= 0:
            continue
        key = number(series, ln.folio, company)
        w, company = whole[key], key[2]
        due = 1.0 - credit.get(key, 0.0) / w["pesos"] if w["pesos"] >= F.NET_ZERO else 0.0
        if due < 0.01:
            continue                                             # taken back
        same = numbers.get((series, ln.folio), [])
        reg = next((d for d in same if d["company"] == company), same[0] if len(same) == 1 and not company else None)
        known = bool(reg) and reg["total"] > 0 and reg["currency"] in ("USD", "MXN")
        out.setdefault((ln.date.strftime("%Y-%m"), ln.job), []).append({
            "series": series, "folio": ln.folio, "company": company, "date": ln.date.isoformat(),
            "pesos": ln.net_sales_mxn, "dollars": ln.usd_equiv if (ln.usd_equiv and ln.usd_equiv > 0) else None,
            "whole_pesos": w["pesos"], "whole_dollars": w["dollars"] if (w["all"] and w["dollars"] > 0) else None,
            "due": min(due, 1.0), "total": reg["total"] if known else None, "subtotal": reg["subtotal"] if known else None,
            "currency": reg["currency"] if known else "", "rate": reg["rate"] if known else None})
    return out


def payment_reach(sources) -> dict:
    """{invoice series: last day covered} from [(payments, info), …]: what the
    payments reports loaded can speak about. A series is covered by a report
    when the report shows a payment of an invoice of that series."""
    out = {}
    for payments, info in sources or []:
        for p in payments:
            out[p.series] = max(out.get(p.series, ""), info.get("to", "") or "")
    return out


def company_reach(sources) -> dict:
    """{company: last day covered} from [(payments, info), …], for the reports
    of companies known by name, named as Finance's margin report names the
    company that issued an invoice. A company's report covers every invoice
    that company issued up to the report's last day."""
    out = {}
    for _payments, info in sources or []:
        issuer = P.ISSUER.get(info.get("key", ""))
        if issuer:
            out[issuer] = max(out.get(issuer, ""), info.get("to", "") or "")
    return out


def _reached(i, c) -> tuple:
    """(share of what is due on the invoice that the payments reach, the share
    from which it counts as collected in full)."""
    usd, mxn, due = c["USD"], c["MXN"], i["due"]
    if i["total"]:
        with_tax = i["total"] > (i["subtotal"] or 0.0) * 1.01
        at = COLLECTED_OF_TOTAL if with_tax else COLLECTED_AT
        if i["currency"] == "USD":
            need = max(i["total"], i["whole_dollars"] or 0.0) * due
            rate = i["rate"] or (i["whole_pesos"] / i["whole_dollars"] if i["whole_dollars"] else None)
            return usd / need + (mxn / (need * rate) if rate else 0.0), COLLECTED_OTHER_CCY if mxn > 0 else at
        need = max(i["total"], i["whole_pesos"]) * due
        return mxn / need + (usd * i["rate"] / need if i["rate"] else 0.0), COLLECTED_OTHER_CCY if usd > 0 else at
    share = mxn / (i["whole_pesos"] * due) + (usd / (i["whole_dollars"] * due) if i["whole_dollars"] else 0.0)
    return share, COLLECTED_AT


def auto_payments(lines, payments, reach=None, register=None, companies=None) -> tuple:
    """(status, problems, summary) from Finance's payments reports.

    status = {(month, job): {"paid": True | False, "date": ISO of the latest
    payment or ''}} for the lines the reports can speak about. A line is Paid
    when every invoice behind it is collected in full; Pending when the reports
    cover its invoices and at least one is not collected in full. A line with
    an invoice the reports do not cover gets no status.

    A report covers an invoice when it is the report of the company that
    issued it (`companies`, from company_reach) and the invoice is dated within
    the report's period. Where the company of the invoice or of the report is
    not known, `reach` (payment_reach) decides: the invoice's series appears in
    a report.

    The reports show payments with IVA. With the invoice register (`register`)
    an invoice is collected in full when the payments reach its own total (see
    COLLECTED_OF_TOTAL); without it, when they reach Finance's amount without
    IVA (COLLECTED_AT), a weaker test. Either way the whole invoice is
    compared, not the part of it that belongs to one file."""
    everything = P.collected(payments)
    by_issuer = {}
    reach = dict(reach) if reach is not None else {series: "" for (series, _folio) in everything}
    loose = {p.series for p in payments or [] if p.company not in P.ISSUER}     # reports of companies not known by name
    inv = invoice_map(lines, register)
    known = {(i["series"], i["folio"]) for invs in inv.values() for i in invs}
    status, problems = {}, []
    counts = {"invoices": 0, "covered": 0, "full": 0, "part": 0, "open": 0}
    per = {}
    for (month, job), invs in sorted(inv.items()):
        states, last = [], ""
        for i in invs:
            co = i["company"]
            if companies is not None and co:
                if co in companies:
                    in_reach = not companies[co] or i["date"] <= companies[co]
                else:
                    in_reach = i["series"] in loose and (not reach.get(i["series"]) or i["date"] <= reach[i["series"]])
                if co not in by_issuer:
                    by_issuer[co] = P.collected(payments, co)
                c = by_issuer[co].get((i["series"], i["folio"]))
            else:
                in_reach = i["series"] in reach and (not reach[i["series"]] or i["date"] <= reach[i["series"]])
                c = everything.get((i["series"], i["folio"]))
            tally = per.setdefault(co or "?", {"invoices": 0, "covered": 0, "full": 0, "part": 0, "open": 0})
            for t in (counts, tally):
                t["invoices"] += 1
                t["covered"] += bool(c) or in_reach
            if not c:
                states.append("open" if in_reach else "unknown")
                counts["open"] += in_reach
                tally["open"] += in_reach
                continue
            share, at = _reached(i, c)
            if share >= at:
                states.append("full")
                counts["full"] += 1
                tally["full"] += 1
                last = max(last, c["last"])
            else:
                states.append("part")
                counts["part"] += 1
                tally["part"] += 1
                problems.append({
                    "kind": "partly_collected", "job": job,
                    "detail": f"invoice {i['series']} {i['folio']} of {i['date']}: payments reach {share:.0%} of the "
                              "invoiced amount; the line is not payable until it is collected in full",
                    "detail_es": f"factura {i['series']} {i['folio']} del {i['date']}: los pagos llegan al {share:.0%} "
                                 "del importe facturado; la línea no es pagadera hasta cobrarse completa",
                    "amount_mxn": round(i["pesos"], 2)})
        if "unknown" in states:
            continue
        status[(month, job)] = {"paid": all(x == "full" for x in states), "date": last if all(x == "full" for x in states) else ""}
    stray = sorted(k for k in everything if k not in known)
    summary = dict(counts, not_in_report=[f"{a} {b}" for a, b in stray], series=sorted(reach),
                   register=bool(register),
                   companies=[dict(v, company=k, report=(k in (companies or {})), to=(companies or {}).get(k, ""))
                              for k, v in sorted(per.items())])
    return status, problems, summary


def _quote_months(by_month, quotes) -> tuple:
    """(first, per_month). `first` = the month each job is first invoiced: its
    booked quote is shown and counted there, once. per_month = how many of those
    files have a quote, which do not, and the sale prices found."""
    first = {}
    for m in sorted(by_month):
        for x in by_month[m]:
            if x.billed > 0:
                first.setdefault(x.job, m)
    per = {}
    for job, m in sorted(first.items()):
        d = per.setdefault(m, {"files": 0, "found": 0, "missing": [], "bookings": 0.0})
        d["files"] += 1
        q = quotes.get(job)
        if q and q["sale"] is not None:
            d["found"] += 1
            d["bookings"] += q["sale"]
        else:
            d["missing"].append(job)
    return first, per


def _quote_problems(by_month, first, quotes, s: E.Settings | None = None) -> list:
    """Points about the quotes of the files in the report."""
    out = []
    s = s or E.Settings()
    agent = E.agent_types(s)
    kinds = dict(s.move_types)
    for job, m in sorted(first.items()):
        q = quotes.get(job)
        mt = (q or {}).get("move_type") or "?"
        if q and mt != "?" and kinds.get(mt) in (None, "unused"):
            out.append({"kind": "move_type_unlisted", "job": job,
                        "detail": f"move type {mt} is {'marked not in use' if mt in kinds else 'not in the list'} "
                                  "in the settings: say what it is (agent, TMS booking, mixed)",
                        "detail_es": f"el tipo de mudanza {mt} {'está marcado sin uso' if mt in kinds else 'no está en la lista'} "
                                     "de la configuración: indique qué es (agente, reserva TMS, mixto)"})
        if not q or q["sale"] is None:
            out.append({"kind": "quote_missing", "job": job,
                        "detail": f"first invoiced in {m}: no booked quote in the Moveware reports loaded",
                        "detail_es": f"facturado por primera vez en {m}: sin cotización en los reportes de Moveware cargados"})
            continue
        if q["with_tax"]:
            out.append({"kind": "quote_with_tax", "job": job,
                        "detail": "loaded in Moveware with IVA; the sale price used is the value excluding IVA, the "
                                  "quoted cost is as loaded",
                        "detail_es": "cargado en Moveware con IVA; el precio de venta usado es el valor sin IVA, el "
                                     "costo cotizado es el cargado", "amount_mxn": round(q["sale"], 2)})
        if q["cost"] is None:
            out.append({"kind": "quote_no_cost", "job": job,
                        "detail": "sale price found, but no cost option with status W in the Move Costing Sheet",
                        "detail_es": "hay precio de venta, pero ninguna opción de costo con estatus W en el Move Costing Sheet",
                        "amount_mxn": round(q["sale"], 2)})
        if q["move_type"] in agent or q["customer_type"] in ("Diplomatic", "Agent"):
            out.append({"kind": "moveware_type_differs", "job": job,
                        "detail": f"Corporate or Private in Finance's report, but move type {q['move_type']} and "
                                  f"customer type {q['customer_type']} in Moveware",
                        "detail_es": f"corporativo o particular en el reporte de Finanzas, pero tipo de mudanza "
                                     f"{q['move_type']} y tipo de cliente {q['customer_type']} en Moveware"})
    return out


def book_quotes(lines, removals, control, costings, s: E.Settings) -> tuple:
    """(quotes, problems) from what the two Moveware readers kept, checked
    against Finance's invoices for the currency of each job."""
    billed = {job: j["billed_fin_mxn"] for (job, t), j in F.jobs(lines, s).items() if t == F.IN_SCOPE}
    pesos = Q.looks_like_pesos(removals, billed)
    return Q.book(removals, costings, s, pesos=pesos), Q.problems(removals, control, costings, pesos=pesos)


def build(lines, s: E.Settings, bookings=None, quotes=None, quote_problems=(), payments=None,
          collected=None, collected_reach=None, register=None, collected_companies=None) -> dict:
    """`quotes` = quotes.book(...): the booked quote of each job, or None when
    the Moveware reports have not been loaded. A month's bookings are, in this
    order: the figure typed on the page; the booked quotes (when every file of
    the month has one); the budget, as a placeholder.

    `payments` = the payment status marked on the page per line (file and
    invoice month). `collected` = the payments read from Finance's payments
    reports (payments.Payment), `collected_reach` and `collected_companies` what
    they cover (payment_reach, company_reach). `register` = Finance's invoice
    register (invoices.Invoice), which gives each invoice its real series, its
    company and its total.
    Commission is payable only on lines that are Paid: by a mark on the page,
    or, where there is no mark, because the reports show every invoice of the
    line collected in full."""
    s.check()
    bookings = clean_bookings(bookings)
    pay = clean_payments(payments)
    auto, auto_problems, collection = ({}, [], None)
    if collected is not None:
        auto, auto_problems, collection = auto_payments(lines, collected, collected_reach, register,
                                                        collected_companies)
    elif register is not None:                    # no payments yet: only which company's report each invoice needs
        auto, auto_problems, collection = auto_payments(lines, [], {}, register, {})

    def paid(m, job):
        if (m, job) in pay:
            return pay[(m, job)]
        return auto[(m, job)]["paid"] if (m, job) in auto else None
    by_month = {m: [dataclasses.replace(x, paid=paid(m, x.job)) for x in ls]
                for m, ls in F.commission_lines(lines, s).items()}
    problems = F.problems(lines, s) + auto_problems
    first, qmonths, won = {}, {}, {}
    if quotes is not None:
        first, qmonths = _quote_months(by_month, quotes)
        in_scope = {job for (job, t) in F.jobs(lines, s) if t == F.IN_SCOPE}
        won = Q.won_by_month(quotes, also=in_scope, agent=E.agent_types(s))
        problems = problems + list(quote_problems) + _quote_problems(by_month, first, quotes, s)
    flagged = {}
    for p in problems:
        if p["kind"] not in ROUTINE:
            flagged.setdefault(p["job"], set()).add(p["kind"])
    rates, rate_other, rates_found = month_rates(lines, s)
    months, rows, no_budget = [], [], []
    for m in sorted(by_month):
        budget = s.budgets.get(m)
        if not budget:
            no_budget.append(m)
            continue
        qd = qmonths.get(m)
        if m in bookings:
            source, booked = "entered", bookings[m]
        elif s.bookings_basis == "won" and won.get(m, 0) > 0:
            source, booked = "quotes", won[m]
        elif s.bookings_basis == "invoiced" and qd and qd["files"] and not qd["missing"]:
            source, booked = "quotes", qd["bookings"]
        else:
            source, booked = "placeholder", budget
        known = source != "placeholder"
        res = E.calc_month(m, by_month[m], booked, s)
        waiting = [x for x in res.lines if x.margin_pending]
        cost = sum(x.line.cost for x in res.lines)
        rate = rates.get(m, rate_other)
        months.append({
            "month": m, "rule": rule_for(m), "rule_es": RULES_ES[rule_for(m)], "new": m >= NEW_FROM,
            "rate": rate, "lines": len(res.lines),
            "billed": res.billed, "cost": cost, "gross_margin": res.billed - cost,
            "margin_pct": (res.billed - cost) / res.billed if abs(res.billed) > 1e-9 else None,
            "budget": budget, "bookings": booked if known else None, "bookings_known": known,
            "bookings_source": source, "reach": res.reach,
            "quote": None if quotes is None else {
                "files": qd["files"] if qd else 0, "found": qd["found"] if qd else 0,
                "missing": list(qd["missing"]) if qd else [], "bookings": qd["bookings"] if qd else 0.0,
                "won": won.get(m, 0.0)},
            "invoicing": sum(x.invoicing for x in res.lines), "margin": sum(x.margin for x in res.lines),
            "discipline": sum(x.discipline for x in res.lines), "total": res.total,
            "payable": res.payable_total, "held": res.held_total,
            "paid_lines": sum(1 for x in res.lines if x.line.paid is True),
            "pending_lines": sum(1 for x in res.lines if x.line.paid is False),
            "waiting_lines": len(waiting), "waiting_billed": sum(x.line.billed for x in waiting)})
        mrow = months[-1]
        mrow["usd"] = {k: mrow[k] / rate for k in MONEY}
        mrow["usd"]["budget"] = budget / rate
        mrow["usd"]["bookings"] = booked / rate if known else None
        for x in sorted(res.lines, key=lambda z: z.line.job):
            q = quotes.get(x.line.job) if quotes is not None else None
            here = bool(q) and first.get(x.line.job) == m          # the quote is shown once, where first invoiced
            rows.append({
                "status": "Ganado" if (q and q["status"] == "W") else "",
                "sale_price": q["sale"] if here else None, "quote_cost": q["cost"] if here else None,
                "quote_month": first.get(x.line.job) if q else None,
                "payment": {True: "Paid", False: "Pending"}.get(x.line.paid, ""),
                "payment_source": "marked" if (m, x.line.job) in pay else ("file" if (m, x.line.job) in auto else ""),
                "payment_mark": {True: "Paid", False: "Pending"}.get(pay.get((m, x.line.job)), ""),
                "paid_on": auto[(m, x.line.job)]["date"] if ((m, x.line.job) in auto and (m, x.line.job) not in pay) else "",
                "payable": x.total if x.line.paid is True else 0.0,
                "month": m, "job": x.line.job, "billed": x.line.billed, "cost": x.line.cost,
                "gross_margin": x.gross_margin, "margin_pct": x.margin_pct, "cost_posted": x.line.cost_posted,
                "invoicing": x.invoicing, "margin": x.margin, "discipline": x.discipline, "total": x.total,
                "embassy": bool(x.line.note), "credit": x.line.billed < 0,
                "usd": {"billed": x.line.billed / rate, "cost": x.line.cost / rate,
                        "gross_margin": x.gross_margin / rate, "invoicing": x.invoicing / rate,
                        "margin": x.margin / rate, "discipline": x.discipline / rate, "total": x.total / rate},
                "flags": [{"en": LABELS.get(k, k), "es": LABELS_ES.get(k, LABELS.get(k, k))}
                          for k in sorted(flagged.get(x.line.job, ()), key=lambda k: LABELS.get(k, k))]})

    def total(sel):
        keys = ("lines", "billed", "cost", "gross_margin", "invoicing", "margin", "discipline", "total",
                "payable", "held", "waiting_lines", "waiting_billed")
        t = {k: sum(r[k] for r in months if sel(r)) for k in keys}
        t["margin_pct"] = t["gross_margin"] / t["billed"] if abs(t["billed"]) > 1e-9 else None
        t["usd"] = {k: sum(r["usd"][k] for r in months if sel(r)) for k in MONEY}
        return t

    all_, new = total(lambda r: True), total(lambda r: r["new"])
    shares = [(n, "Sales", s.sales_share, p) for n, p in s.sales_people] + \
             [(n, "Adm, MC and Buyer", s.admin_share, p) for n, p in s.admin_people]
    people = [{"name": n, "name_es": PEOPLE_ES.get(n, n), "group": g, "group_es": PEOPLE_ES.get(g, g), "share": p,
               "if_collected": new["total"] * gs * p, "payable": new["payable"] * gs * p,
               "usd": {"if_collected": new["usd"]["total"] * gs * p, "payable": new["usd"]["payable"] * gs * p},
               # the statement: each new-calculation month, what this person gets
               "months": [{"month": r["month"], "if_collected": r["total"] * gs * p, "payable": r["payable"] * gs * p,
                           "held": r["held"] * gs * p,
                           "usd": {"if_collected": r["usd"]["total"] * gs * p, "payable": r["usd"]["payable"] * gs * p,
                                   "held": r["usd"]["held"] * gs * p}}
                          for r in months if r["new"]]}
              for n, g, gs, p in shares]
    order = list(LABELS) + sorted({p["kind"] for p in problems} - set(LABELS))
    checks = []
    for kind in order:
        items = sorted((p for p in problems if p["kind"] == kind), key=lambda z: (z["job"], z["detail"]))
        if items:
            checks.append({"kind": kind, "label": LABELS.get(kind, kind),
                           "label_es": LABELS_ES.get(kind, LABELS.get(kind, kind)), "routine": kind in ROUTINE,
                           "items": [{"job": p["job"], "detail": p["detail"], "detail_es": p["detail_es"],
                                      "amount": p.get("amount_mxn", p.get("understated_mxn"))} for p in items]})
    dates = [ln.date for ln in lines if ln.date]
    return {"months": months, "lines": rows, "total_all": all_, "total_new": new, "people": people,
            "control": sum(p["if_collected"] for p in people) - new["total"],
            "checks": checks, "check_count": sum(len(c["items"]) for c in checks),
            "months_without_budget": no_budget,
            "rates": {"by_month": {r["month"]: r["rate"] for r in months}, "other": rate_other,
                      "from_report": rates_found},
            "placeholder_months": [r["month"] for r in months if not r["bookings_known"]],
            "quotes_loaded": quotes is not None, "bookings_basis": s.bookings_basis,
            "paid_lines": sum(r["paid_lines"] for r in months), "pending_lines": sum(r["pending_lines"] for r in months),
            "collection": collection, "register_loaded": register is not None,
            "payments_unused": sorted(f"{m}|{j}" for (m, j) in pay
                                      if not any(x.job == j for x in by_month.get(m, ()))),
            "source": {"lines": len(lines), "from": min(dates).isoformat() if dates else None,
                       "to": max(dates).isoformat() if dates else None,
                       "jobs_in_scope": sum(1 for (_, t) in F.jobs(lines, s) if t == F.IN_SCOPE)}}
