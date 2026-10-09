"""
moveware.py — what the commission report takes from Moveware.

It reads nothing itself. It works on the job records the library's background
reader (mw_live) has already collected, so it adds no load on Moveware and no
second way of reading it (Moveware Data Capture Guide: one adapter).

Two things are needed from Moveware and nowhere else:

  1. Bookings per month — the sale price of the jobs won in the month, which
     drives sales reach. Finance's margin report has invoices, not bookings.
  2. Which jobs are Corporate/Private at the moment they are booked, before
     Finance has typed them on an invoice.

How Moveware marks an agent job, a corporate one, a private one and a
diplomatic one is NOT assumed here. `type_crosstab` measures it: it lines up
Finance's type for every job in the margin report against the three things
Moveware records (customer type, booking agent, corporate account) and counts
the combinations. The rule is then written into Settings from that table and
confirmed with Lupita. Until then `bookings_by_month` only counts jobs Finance
has already typed.

Job numbers keep their lot letter everywhere: a lot is its own job, with its
own type, value and date won.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter, defaultdict

from . import engine as E
from . import finance as F

WON = "W"


def _job(f: dict) -> str:
    """The job number as staff write it: '110771B'. A number that arrives as
    110800.0, ' 110800 a' or '110800-A' is the same job."""
    v = f.get("number") or f.get("job") or ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    t = re.sub(r"[\s\-/]", "", str(v)).upper()
    return re.sub(r"\.0+$", "", t)


def _price(v):
    """A sale price as a number, or None when it is not one."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    t = str(v).strip().replace("$", "").replace(" ", "")
    if re.fullmatch(r"(\d{1,3}(,\d{3})+|\d+)(\.\d+)?", t):
        return float(t.replace(",", ""))
    return None


_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _month(v) -> str:
    """'2026-06' from a date or an ISO date string. Anything else is not a
    month: a date typed as text ("10/06/2026") must never become a month key."""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.strftime("%Y-%m")
    m = _ISO.match(str(v).strip()) if v else None
    if not m:
        return ""
    try:
        dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return ""
    return f"{m.group(1)}-{m.group(2)}"


def signature(f: dict) -> tuple:
    """How Moveware classes one job: (customer type, booking agent?, corporate account?)."""
    def yn(v):
        return "?" if v is None else ("yes" if v else "no")
    return (str(f.get("customer_type") or "").strip() or "(blank)",
            yn(f.get("has_booking_agent")), yn(f.get("has_corporate_account")))


def type_crosstab(fin_lines, mw_files) -> dict:
    """Finance's type against Moveware's own marks, job by job (lot letter kept).

    Returns the count of every (Finance type, Moveware signature) pair, the
    Finance jobs Moveware's reader has not seen, and, for each signature, how
    cleanly it points at one Finance type. Job numbers only."""
    fin_type = {}
    for (job, typ), _ in F.jobs(fin_lines).items():
        fin_type.setdefault(job, set()).add(typ)
    mw = {}
    for f in sorted((f for f in mw_files if _job(f)), key=lambda f: (_job(f), signature(f))):
        mw.setdefault(_job(f), f)                      # same answer whatever the order
    table, missing, ambiguous = Counter(), [], []
    by_sig = defaultdict(Counter)
    for job, types in sorted(fin_type.items()):
        if len(types) > 1:
            ambiguous.append(job)
            continue
        typ = next(iter(types))
        if job not in mw:
            missing.append(job)
            continue
        sig = signature(mw[job])
        table[(typ, sig)] += 1
        by_sig[sig][typ] += 1
    purity = {}
    for sig, c in by_sig.items():
        top, n = c.most_common(1)[0]
        purity[sig] = {"points_to": top, "jobs": sum(c.values()), "agree": n,
                       "exceptions": sum(c.values()) - n}
    return {"pairs": dict(table), "by_signature": purity,
            "finance_jobs": len(fin_type), "matched": sum(table.values()),
            "not_in_moveware_reader": missing, "typed_two_ways_by_finance": ambiguous}


def bookings_by_month(mw_files, in_scope, s: E.Settings) -> dict:
    """Sale price of the jobs won in each month, in pesos, for jobs `in_scope`
    accepts.

    Returns {"months": {month: {"bookings": …, "jobs": n}}, "skipped": {why:
    [job numbers]}, "won_without_number": n}. The result does not depend on the
    order of the records. A job the reader holds twice is counted once when the
    copies agree and not at all when they differ (there is no telling which is
    right). Every won, in-scope job that could not be counted is under
    "skipped"."""
    out = defaultdict(lambda: {"bookings": 0.0, "jobs": 0})
    skipped = defaultdict(list)
    held = defaultdict(list)
    no_number = 0
    for f in mw_files:
        if str(f.get("status") or "").strip().upper() != WON:
            continue
        job = _job(f)
        if not job:
            no_number += 1
            continue
        if in_scope(f):
            held[job].append(f)
    for job in sorted(held):
        copies = held[job]
        facts = {(_month(f.get("booked")), _price(f.get("sell")), str(f.get("currency") or "").strip().upper())
                 for f in copies}
        if len(facts) > 1:
            skipped["held twice by the reader with different details, not counted"].append(job)
            continue
        if len(copies) > 1:
            skipped["held twice by the reader, counted once"].append(job)
        f = copies[0]
        booked = f.get("booked")
        month = _month(booked)
        if not month:
            skipped["won, date won not readable" if booked else "won, no date won"].append(job)
            continue
        raw = f.get("sell")
        value = _price(raw)
        if value is None and raw not in (None, ""):
            skipped["won, sale price is not a number"].append(job)
            continue
        if not value or value <= 0:
            skipped["won, no sale price"].append(job)
            continue
        ccy = E.currency_code(f.get("currency"))
        if ccy not in ("MXN", "USD"):
            skipped[f"currency {ccy or 'not stated or not known'}"].append(job)
            continue
        out[month]["bookings"] += E.to_mxn(value, ccy, s)
        out[month]["jobs"] += 1
    return {"months": {m: v for m, v in sorted(out.items())},
            "skipped": {k: sorted(set(v)) for k, v in sorted(skipped.items())},
            "won_without_number": no_number}


def typed_by_finance(fin_lines, type_: str = F.IN_SCOPE):
    """An `in_scope` test for bookings_by_month that uses Finance's own typing.

    Jobs Finance has not invoiced yet are not counted, so recent months read low
    until the rule from `type_crosstab` replaces this.

    A job Finance typed two ways (re-classified during the year) takes the type
    on its most recent line. If the most recent date carries both types there
    is no telling, the job is left out, and it is listed on the returned
    function as `.undecided`. `.two_ways` lists every such job."""
    latest = {}
    for ln in fin_lines:
        d = ln.date or dt.date.min
        cur = latest.get(ln.job)
        if cur is None or d > cur[0]:
            latest[ln.job] = (d, {ln.type})
        elif d == cur[0]:
            cur[1].add(ln.type)
    types = defaultdict(set)
    for ln in fin_lines:
        types[ln.job].add(ln.type)
    ok = {job for job, (_, ts) in latest.items() if ts == {type_}}

    def in_scope(f):
        return _job(f) in ok

    in_scope.two_ways = sorted(j for j, ts in types.items() if len(ts) > 1)
    in_scope.undecided = sorted(j for j, (_, ts) in latest.items() if len(ts) > 1)
    return in_scope
