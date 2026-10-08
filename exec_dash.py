"""Executive view of the Move-File Audit: YTD and last-12-months only.

Money is kept as a PAIR [mxn_part, usd_part] so the page can show it three ways
without re-asking the server (same methodology as the TMS Executive Dashboard):
  • MXN        = mxn + usd × spot
  • USD · plan = usd + mxn ÷ plan (17.4479, the rate the 2026 budget was struck at)
  • USD · spot = usd + mxn ÷ spot (month-end rate of the latest completed month)
Non-MXN/USD amounts (EUR, GBP…) are folded into the USD part at today's cross rate.
Percentages (margin %, % billed) are computed on pesos so the currency control
can never change the performance read.

Rules that fix the "funky numbers" of the old view:
  • Revenue = NUMBERED, non-cancelled MoveWare invoices, NET of IVA, in the
    invoice's own currency, dated in the period. (The old tile summed IVA-inclusive
    totals of every invoice, cancelled and pro-forma included, and assumed one
    currency per file.)
  • Leads (L) and cancelled jobs (C) are never "ready to invoice".
  • Embassy files are recognised by client name, by Finance's DIPLOM type, or by
    the embassy handler (Edgar Espino) — they bill only after delivery.
  • A quote far larger than any normal move (AUDIT_QUOTE_CHECK_MXN, default
    MXN 1.5M) is listed for checking and kept OUT of the totals, so one mis-keyed
    quote can't swamp the page.
  • Gross margin comes from Finance actuals; a file with no cost posted is
    "cost pending", never 100% margin.
"""
from __future__ import annotations

import datetime as dt
import os

import fx

LEAD, CANCELLED = "L", "C"
EMBASSY_HANDLERS = ("edgarespino", "edgar espino", "edgar.espino")


def _d(v):
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def periods(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    return {
        "ytd": (dt.date(today.year, 1, 1), today),
        "l12m": (today - dt.timedelta(days=364), today),
    }


def _in(day, p):
    return bool(day and p[0] <= day <= p[1])


def pair(amount, ccy) -> list:
    """Amount in its own currency → [mxn_part, usd_part]."""
    a = float(amount or 0)
    c = fx.normalize(ccy, None) or fx.default_ccy()
    if c == "MXN":
        return [a, 0.0]
    if c == "USD":
        return [0.0, a]
    return [0.0, fx.convert(a, c, "USD")]


def _add(acc, p):
    acc[0] += p[0]
    acc[1] += p[1]
    return acc


def _r(p):
    return [round(p[0], 2), round(p[1], 2)]


def to_mxn(p, spot):
    return p[0] + p[1] * spot


def quote_check_mxn() -> float:
    try:
        return float(os.environ.get("AUDIT_QUOTE_CHECK_MXN") or 1_500_000)
    except ValueError:
        return 1_500_000.0


def _active_invoices(f):
    """(numbered, unnumbered) non-cancelled invoices with a value."""
    num, unnum = [], []
    for i in f.get("inv_list") or []:
        if (i.get("st") or "").upper() == CANCELLED or not i.get("net"):
            continue
        (num if (i.get("n") or "").strip() else unnum).append(i)
    return num, unnum


def _invoiced(f):
    if "inv_list" in f:
        num, unnum = _active_invoices(f)
        return bool(num or unnum)
    return bool(f.get("invoiced"))


def _is_embassy(f, fin_type):
    if f.get("is_embassy") or fin_type == "DIPLOM":
        return True
    who = f"{f.get('coordinator') or ''} {f.get('coordinator_email') or ''}".lower()
    return any(h in who for h in EMBASSY_HANDLERS)


def _base(job):
    s = str(job or "").strip()
    while s and not s[-1].isdigit():
        s = s[:-1]
    return s


def build(files, finance: dict, today: dt.date | None = None) -> dict:
    """All executive metrics, for both periods. `files` = the auditor's raw
    in-window records (NOT the display-currency copies)."""
    today = today or dt.date.today()
    P = periods(today)
    spot = fx.month_end_spot(today)
    plan = fx.plan_rate()
    s_rate = spot["rate"]
    qmax = quote_check_mxn()

    fin_files = (finance or {}).get("files") or {}
    ledger = (finance or {}).get("ledger") or {}

    def ftype(job):
        b = _base(job)
        return ((fin_files.get(b) or {}).get("type") or (ledger.get(b) or {}).get("type") or "")

    out = {"periods": {}, "fx": {"plan": plan, "spot": spot}, "as_of": today.isoformat()}
    stale = sum(1 for f in files if "inv_list" not in f)

    for key, p in P.items():
        inv_num, inv_unnum = [0.0, 0.0], [0.0, 0.0]
        inv_files, unnum_files = set(), set()
        ready, emb_transit, checks = [], [], []
        ready_val, emb_val = [0.0, 0.0], [0.0, 0.0]
        status_n = {"W": 0, "P": 0, LEAD: 0, CANCELLED: 0, "other": 0}
        billed_n = 0
        active_n = 0

        for f in files:
            st = (f.get("status") or "").upper()
            anchor = _d(f.get("anchor"))
            if _in(anchor, p):
                status_n[st if st in status_n else "other"] += 1

            # Revenue: numbered, non-cancelled invoices dated in the period (net of IVA).
            num, unnum = _active_invoices(f)
            for i in num:
                if _in(_d(i.get("d")), p):
                    _add(inv_num, pair(i["net"], i.get("c")))
                    inv_files.add(f.get("job"))
            for i in unnum:
                # Unnumbered invoices usually have no date either; count them when the
                # move itself falls in the period so they stay visible as data cleanup.
                if _in(_d(i.get("d")) or anchor, p):
                    _add(inv_unnum, pair(i["net"], i.get("c")))
                    unnum_files.add(f.get("job"))

            if st in (LEAD, CANCELLED):
                continue
            pack, deliv = _d(f.get("pack")), _d(f.get("delivery"))
            packed = bool(pack and pack <= today)
            delivered = bool(deliv and deliv <= today)
            milestone = max([x for x in (pack, deliv) if x and x <= today], default=None)
            if not _in(milestone, p):
                continue
            active_n += 1
            emb = _is_embassy(f, ftype(f.get("job")))
            if _invoiced(f):
                billed_n += 1
                continue
            q = pair(f.get("sell"), f.get("currency"))
            row = {"job": f.get("job"), "client": f.get("client") or "",
                   "coordinator": f.get("coordinator") or "",
                   "pack": pack.isoformat() if pack else None,
                   "delivery": deliv.isoformat() if deliv else None,
                   "status": st, "embassy": emb, "value": _r(q),
                   "ccy": fx.normalize(f.get("currency"), None) or "",
                   "value_mxn": round(to_mxn(q, s_rate))}
            if emb and not delivered:
                if packed:
                    emb_transit.append(row)
                    _add(emb_val, q)
                continue
            if to_mxn(q, s_rate) > qmax:
                checks.append(row)
                continue
            ready.append(row)
            _add(ready_val, q)

        # Finance actuals — a file counts in the period of its first invoice.
        sales, sales_mxn, cost, cost_mxn = [0.0, 0.0], 0.0, 0.0, 0.0
        pend_n = costed_n = neg_n = 0
        all_sales = [0.0, 0.0]
        last_inv = None
        for job, e in ledger.items():
            last_inv = max(filter(None, [last_inv, e.get("last")]), default=None)
            if not _in(_d(e.get("first")), p):
                continue
            s_pair = [0.0, 0.0]
            for c, a in (e.get("inv_orig") or {}).items():
                _add(s_pair, pair(a, c))
            _add(all_sales, s_pair)
            if (e.get("inv_mxn") or 0) <= 0:
                continue
            # Lupita's margin workbook is the authority where it has the file (its
            # cost includes intercompany TRS cost); otherwise the ledger's cost.
            wb = fin_files.get(job) or {}
            if wb.get("has_cost") and (wb.get("net_sales") or 0) > 0:
                f_sales, f_cost, f_pair = wb["net_sales"], wb.get("total_cost") or 0, [wb["net_sales"], 0.0]
            elif (e.get("cost_mxn") or 0) > 0:
                f_sales, f_cost, f_pair = e["inv_mxn"], e["cost_mxn"], s_pair
            else:
                pend_n += 1
                continue
            costed_n += 1
            _add(sales, f_pair)
            sales_mxn += f_sales
            cost_mxn += f_cost
            if f_cost > f_sales:
                neg_n += 1
        margin_mxn = sales_mxn - cost_mxn
        # Cost is booked in pesos; the margin pair = sales pair minus cost (MXN part).
        margin = [sales[0] - cost_mxn, sales[1]]

        ready.sort(key=lambda r: -r["value_mxn"])
        checks.sort(key=lambda r: -r["value_mxn"])
        denom = billed_n + len(ready) + len(checks)
        out["periods"][key] = {
            "from": p[0].isoformat(), "to": p[1].isoformat(),
            "inv_num": _r(inv_num), "inv_files": len(inv_files),
            "inv_unnum": _r(inv_unnum), "unnum_files": len(unnum_files),
            "fin_sales_all": _r(all_sales),
            "fin_sales": _r(sales), "fin_margin": _r(margin), "fin_cost_mxn": round(cost_mxn, 2),
            "fin_margin_pct": round(margin_mxn / sales_mxn * 100, 1) if sales_mxn else None,
            "fin_costed_n": costed_n, "fin_pending_n": pend_n, "fin_neg_n": neg_n,
            "fin_last_invoice": last_inv,
            "ready": ready, "ready_n": len(ready), "ready_val": _r(ready_val),
            "checks": checks, "checks_n": len(checks),
            "emb": emb_transit, "emb_n": len(emb_transit), "emb_val": _r(emb_val),
            "active_n": active_n, "billed_n": billed_n,
            "pct_billed": round(billed_n / denom * 100, 1) if denom else None,
            "status_n": status_n, "files_n": sum(status_n.values()),
        }
    out["stale_records"] = stale
    out["quote_check_mxn"] = qmax
    return out
