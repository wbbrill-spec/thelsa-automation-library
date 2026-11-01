"""
commission_feed.py — AuditTrack reads the reports the team already uploads to
the Commission Report (Bill, 31 Oct 2026: "when the team uploads their reports to
the Commission Report, we pull the same reports into AuditTrack").

Both apps share one database (commissions.store), so nothing is uploaded twice:

  Invoice register ("FACTURADO ACUMULADO YTD")   which files Finance really invoiced
                                                 → ready-to-invoice never lists them
  Payments reports (CONTPAQ, one per company)    which invoices are still unpaid
                                                 → "invoices still open"
  Removals Summary (Moveware)                    the booked sale price of each file
                                                 → flags a Moveware price that differs
  Move Costing Sheet (Moveware)                  line-level supplier costs; read again
                                                 by costing_sheet.py at upload time
                                                 (commissions/web.py) for the supplier checks

Read-only. Customer names are never in these records. Every function returns an
empty result (never raises) when a report has not been uploaded yet.
"""
from __future__ import annotations

import datetime as dt
import time

_CACHE: dict = {}
_TTL = 600          # the commission page is uploaded monthly; 10 minutes is plenty

# Same thresholds as the commission report (commissions/report.py), so the two
# apps agree on what "collected in full" means.
COLLECTED_OF_TOTAL = 0.96
COLLECTED_OTHER_CCY = 0.92


def base(job: str) -> str:
    s = str(job or "").strip().upper()
    while s and not s[-1].isdigit():
        s = s[:-1]
    return s


def _cached(name, fn):
    hit = _CACHE.get(name)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = None
    _CACHE[name] = (time.time(), val)
    return val


def clear_cache() -> None:
    _CACHE.clear()


def _cancelled(invoices) -> set:
    """{(series, folio-without-leading-zeros)} of invoices another one cancels."""
    import re
    out = set()
    for x in invoices or []:
        m = re.match(r"^([A-Z][A-Z\-]{0,11}?)[ -]?0*(\d{1,9})$", (x.cancels or "").replace(" ", ""))
        if m:
            out.add((m.group(1).rstrip("-"), m.group(2)))
    return out


def _is_cancelled(series, folio, cancelled) -> bool:
    return (series, str(folio).lstrip("0") or "0") in cancelled


def _meta_at(meta):
    at = (meta or {}).get("at") if isinstance(meta, dict) else None
    return at.date().isoformat() if hasattr(at, "date") else (str(at)[:10] if at else "")


# ── loading what the commission page stored ──────────────────────────────────
def _register():
    from commissions import invoices as I
    from commissions import store
    doc, meta = store.get("invoice_register")
    if not doc:
        return None
    invs, info = I.from_records(doc.get("records") or {})
    return {"invoices": invs, "info": info, "loaded": _meta_at(meta)}


def register():
    return _cached("register", _register)


def _payments():
    from commissions import payments as P
    from commissions import store
    doc, _ = store.get("payment_reports")
    out = []
    for _name, src in sorted(((doc or {}).get("sources") or {}).items()):
        ps, info = P.from_records(src.get("records") or {})
        out.append((ps, info, str(src.get("at") or "")[:10]))
    return out or None


def payment_sources():
    return _cached("payments", _payments)


def _removals():
    from commissions import quotes as Q
    from commissions import store
    doc, meta = store.get("removals_summary")
    if not doc:
        return None
    jobs, control = Q.removals_from_records(doc.get("records") or {})
    return {"jobs": jobs, "loaded": _meta_at(meta)}


def removals():
    return _cached("removals", _removals)


# ── what AuditTrack uses ─────────────────────────────────────────────────────
def invoiced_files() -> dict:
    """{'files': base file numbers with an invoice in Finance's register (not
    cancelled), 'to': last invoice date in the register}. Empty when the
    register has not been uploaded."""
    reg = register()
    if not reg:
        return {"files": set(), "to": ""}
    cancelled = _cancelled(reg["invoices"])
    files = set()
    for x in reg["invoices"]:
        if not x.job or (x.total or 0) <= 0:
            continue
        if _is_cancelled(x.series, x.folio, cancelled):
            continue
        files.add(base(x.job))
    return {"files": files, "to": str(reg["info"].get("to") or "")}


def open_invoices(today: dt.date | None = None) -> dict:
    """Invoices Finance issued that the payments reports do not show as paid in
    full. Only invoices of a company whose payments report is loaded, and dated
    on or before that report's last day, can be judged; the rest are counted as
    'not covered' (no report yet), never called unpaid.

    {'open': [{company, series, folio, date, currency, total, paid_share, jobs,
               days}], 'not_covered': {company: n}, 'companies': {company: report to},
     'register_to': ISO}"""
    from commissions import invoices as I
    from commissions import payments as P
    today = today or dt.date.today()
    reg = register()
    srcs = payment_sources() or []
    if not reg:
        return {"open": [], "not_covered": {}, "companies": {}, "register_to": "", "ready": False}
    reach = {}
    for _ps, info, _at in srcs:
        issuer = P.ISSUER.get(info.get("key", ""))
        if issuer:
            reach[issuer] = max(reach.get(issuer, ""), info.get("to", "") or "")
    all_payments = [p for ps, _info, _at in srcs for p in ps]
    by_issuer = {co: P.collected(all_payments, co) for co in reach}
    cancelled = _cancelled(reg["invoices"])
    out, not_covered = [], {}
    for (series, folio), same in I.by_number(reg["invoices"]).items():
        if _is_cancelled(series, folio, cancelled):
            continue
        for i in same:
            if (i["total"] or 0) <= 0:
                continue                        # credit notes / zero lines
            co = i["company"] or "?"
            if co not in reach or (reach[co] and i["date"] > reach[co]):
                not_covered[co] = not_covered.get(co, 0) + 1
                continue
            c = by_issuer[co].get((series, folio)) or {"MXN": 0.0, "USD": 0.0, "last": ""}
            if i["currency"] == "USD":
                share = c["USD"] / i["total"] + (c["MXN"] / (i["total"] * i["rate"]) if i["rate"] else 0.0)
                need = COLLECTED_OTHER_CCY if c["MXN"] > 0 else COLLECTED_OF_TOTAL
            elif i["currency"] == "MXN":
                share = c["MXN"] / i["total"] + (c["USD"] * i["rate"] / i["total"] if i["rate"] else 0.0)
                need = COLLECTED_OTHER_CCY if c["USD"] > 0 else COLLECTED_OF_TOTAL
            else:
                continue                        # EUR etc.: the payments reports are MXN/USD only
            if share >= need:
                continue
            try:
                days = (today - dt.date.fromisoformat(i["date"])).days
            except ValueError:
                days = None
            out.append({"company": co, "series": series, "folio": folio, "date": i["date"],
                        "currency": i["currency"], "total": round(i["total"], 2),
                        "paid_share": round(max(share, 0.0), 3),
                        "jobs": [base(j) for j in i["jobs"]], "days": days})
    out.sort(key=lambda r: (-(r["days"] or 0), r["company"]))
    return {"open": out, "not_covered": not_covered, "companies": reach,
            "register_to": str(reg["info"].get("to") or ""), "ready": True}


PRICE_TOLERANCE = 0.05     # 5 %
PRICE_MIN_DIFF = 500.0     # …and at least this much, in the file's currency


def booked_prices() -> dict:
    """{base_job: {'value': sale price without IVA, 'currency': 'USD'|'MXN'|''}}
    from Moveware's Removals Summary (lettered lots added to their file)."""
    from commissions import quotes as Q
    rem = removals()
    if not rem:
        return {}
    out = {}
    for r in rem["jobs"]:
        b = base(r.job)
        d = out.setdefault(b, {"value": 0.0, "currency": Q.currency_of(r.job, r.branch)})
        d["value"] += r.value_ex or 0.0
    return out


def price_differences(files) -> list:
    """Files whose Moveware sale price (as the audit reads it) differs from the
    Removals Summary's booked price by more than 5 % and 500 — e.g. 111154
    (Moveware 17,880 vs 9,790.74 booked). `files` = audit records with job,
    sell, currency. Same currency only."""
    prices = booked_prices()
    out = []
    for f in files or []:
        b = base(f.get("job"))
        p = prices.get(b)
        if not p or not p["value"]:
            continue
        sell = float(f.get("sell") or 0)
        ccy = str(f.get("currency_src") or f.get("currency") or "")
        if not sell or (p["currency"] and ccy and p["currency"] != ccy):
            continue
        diff = sell - p["value"]
        if abs(diff) > max(PRICE_MIN_DIFF, p["value"] * PRICE_TOLERANCE):
            out.append({"job": b, "moveware": round(sell, 2), "booked": round(p["value"], 2),
                        "currency": p["currency"] or ccy, "difference": round(diff, 2),
                        "coordinator": f.get("coordinator") or "Unassigned"})
    out.sort(key=lambda r: -abs(r["difference"]))
    return out


def status() -> dict:
    """Which commission-page reports AuditTrack is reading, and how recent."""
    reg, rem, srcs = register(), removals(), payment_sources() or []
    try:
        import costing_sheet as cs
        period = (cs.load().get("period") or {})
    except Exception:
        period = {}
    return {
        "invoice_register": {"loaded": bool(reg), "to": (reg or {}).get("info", {}).get("to", "") if reg else "",
                             "uploaded": (reg or {}).get("loaded", "")},
        "payments": [{"company": info.get("company") or info.get("key"), "to": info.get("to", ""),
                      "uploaded": at} for _ps, info, at in srcs],
        "removals_summary": {"loaded": bool(rem), "jobs": len((rem or {}).get("jobs") or []),
                             "uploaded": (rem or {}).get("loaded", "")},
        "costing_sheet": {"won_to": period.get("won_to", "")},
    }
