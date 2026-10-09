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

from . import engine as E
from . import finance as F

# The new calculation applies from May 2026 (Lupita, 7 Oct 2026). January to
# March were paid on the old calculation; April is still to be confirmed.
NEW_FROM = "2026-05"

# Points to look at, most important first, in the words the reader sees.
LABELS = {
    "credit_note_amount": "Credit note amount looks wrong",
    "dollar_amount_mismatch": "Dollar and peso amounts do not agree",
    "no_dollar_amount": "Dollar invoice with no usable dollar amount",
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
# Long, routine lists: shown folded on the page.
ROUTINE = ("no_cost_posted", "cost_without_sales", "currency_missing")


def rule_for(month: str) -> str:
    if month >= NEW_FROM:
        return "New calculation"
    return "To confirm" if month == "2026-04" else "Old calculation"


def clean_bookings(bookings) -> dict:
    """{month: amount} with only real, positive numbers kept."""
    out = {}
    for m, v in (bookings or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and E._finite(v) and v > 0:
            out[str(m)] = float(v)
    return out


def build(lines, s: E.Settings, bookings=None) -> dict:
    s.check()
    bookings = clean_bookings(bookings)
    by_month = F.commission_lines(lines, s)
    problems = F.problems(lines, s)
    flagged = {}
    for p in problems:
        if p["kind"] not in ROUTINE:
            flagged.setdefault(p["job"], set()).add(p["kind"])
    months, rows, no_budget = [], [], []
    for m in sorted(by_month):
        budget = s.budgets.get(m)
        if not budget:
            no_budget.append(m)
            continue
        known = m in bookings
        res = E.calc_month(m, by_month[m], bookings[m] if known else budget, s)
        waiting = [x for x in res.lines if x.margin_pending]
        cost = sum(x.line.cost for x in res.lines)
        months.append({
            "month": m, "rule": rule_for(m), "new": m >= NEW_FROM, "lines": len(res.lines),
            "billed": res.billed, "cost": cost, "gross_margin": res.billed - cost,
            "margin_pct": (res.billed - cost) / res.billed if abs(res.billed) > 1e-9 else None,
            "budget": budget, "bookings": bookings.get(m), "bookings_known": known, "reach": res.reach,
            "invoicing": sum(x.invoicing for x in res.lines), "margin": sum(x.margin for x in res.lines),
            "discipline": sum(x.discipline for x in res.lines), "total": res.total,
            "payable": res.payable_total, "held": res.held_total,
            "waiting_lines": len(waiting), "waiting_billed": sum(x.line.billed for x in waiting)})
        for x in sorted(res.lines, key=lambda z: z.line.job):
            rows.append({
                "month": m, "job": x.line.job, "billed": x.line.billed, "cost": x.line.cost,
                "gross_margin": x.gross_margin, "margin_pct": x.margin_pct, "cost_posted": x.line.cost_posted,
                "invoicing": x.invoicing, "margin": x.margin, "discipline": x.discipline, "total": x.total,
                "embassy": bool(x.line.note), "credit": x.line.billed < 0,
                "flags": sorted(LABELS.get(k, k) for k in flagged.get(x.line.job, ()))})

    def total(sel):
        keys = ("lines", "billed", "cost", "gross_margin", "invoicing", "margin", "discipline", "total",
                "payable", "held", "waiting_lines", "waiting_billed")
        t = {k: sum(r[k] for r in months if sel(r)) for k in keys}
        t["margin_pct"] = t["gross_margin"] / t["billed"] if abs(t["billed"]) > 1e-9 else None
        return t

    all_, new = total(lambda r: True), total(lambda r: r["new"])
    shares = [(n, "Sales", s.sales_share, p) for n, p in s.sales_people] + \
             [(n, "Adm, MC and Buyer", s.admin_share, p) for n, p in s.admin_people]
    people = [{"name": n, "group": g, "share": p, "if_collected": new["total"] * gs * p,
               "payable": new["payable"] * gs * p} for n, g, gs, p in shares]
    order = list(LABELS) + sorted({p["kind"] for p in problems} - set(LABELS))
    checks = []
    for kind in order:
        items = sorted((p for p in problems if p["kind"] == kind), key=lambda z: (z["job"], z["detail"]))
        if items:
            checks.append({"kind": kind, "label": LABELS.get(kind, kind), "routine": kind in ROUTINE,
                           "items": [{"job": p["job"], "detail": p["detail"],
                                      "amount": p.get("amount_mxn", p.get("understated_mxn"))} for p in items]})
    dates = [ln.date for ln in lines if ln.date]
    return {"months": months, "lines": rows, "total_all": all_, "total_new": new, "people": people,
            "control": sum(p["if_collected"] for p in people) - new["total"],
            "checks": checks, "check_count": sum(len(c["items"]) for c in checks),
            "months_without_budget": no_budget,
            "placeholder_months": [r["month"] for r in months if not r["bookings_known"]],
            "source": {"lines": len(lines), "from": min(dates).isoformat() if dates else None,
                       "to": max(dates).isoformat() if dates else None,
                       "jobs_in_scope": sum(1 for (_, t) in F.jobs(lines, s) if t == F.IN_SCOPE)}}
