"""Currency for the cross-border board.

Thelsa books some files in MXN and some in USD — job 110872 is 268,512.16 MXN
while 111131 is 2,532.84 USD. Adding those numbers together produces a figure
that is wrong by a factor of seventeen, so every amount on the board carries its
own currency and is converted only at display time.

Bill chose the **books** basis (2026-09-16): each month converts at the rate the
accounting pack used for that month, so the dashboard reconciles against the
accounts rather than against a market feed. That mirrors the "USD · books" view
on the TMS Executive Dashboard.

    Jan–Jun 2026   17.36     (the H1 blended rate)
    Jul 2026       16.908
    Aug 2026       17.0

Anything after the last month in the table carries the last known rate forward
and is reported as provisional — see `rate_for()` and the `provisional` flag,
which the UI shows so nobody mistakes a carried-forward rate for a real one.

Override without a deploy:

    CB_FX_BOOKS="2026-01:17.36,2026-07:16.908,2026-08:17.0,2026-09:16.94"

Amounts are never rewritten in place. `convert()` returns a new number and the
caller keeps the original alongside it, so an FX mistake is always reversible
and the booked figure stays visible in the drawer.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re

log = logging.getLogger(__name__)

BASE = "MXN"                      # the currency the rate table is quoted in
SUPPORTED = ("MXN", "USD")

# month (YYYY-MM) → MXN per 1 USD, as used by the accounting pack.
_DEFAULT_BOOKS: dict[str, float] = {
    "2026-01": 17.36, "2026-02": 17.36, "2026-03": 17.36,
    "2026-04": 17.36, "2026-05": 17.36, "2026-06": 17.36,
    "2026-07": 16.908,
    "2026-08": 17.0,
}

_CUR_RE = re.compile(r"\b([A-Z]{3})\b")


def normalize_currency(v) -> str:
    """Moveware writes the currency as `payment: "ACC USD"`, not a bare code.

    Returns "USD" / "MXN", or "" when the value carries no recognisable code —
    never a guess. A blank currency means the caller must not convert.
    """
    s = str(v or "").strip().upper()
    if not s:
        return ""
    for code in _CUR_RE.findall(s):
        if code in SUPPORTED:
            return code
    return ""


def books_table() -> dict[str, float]:
    """The month→rate table, with CB_FX_BOOKS layered on top of the defaults.

    A malformed entry is skipped and logged rather than taking the board down;
    a bad rate would silently corrupt every figure, so it must never be guessed.
    """
    table = dict(_DEFAULT_BOOKS)
    raw = (os.environ.get("CB_FX_BOOKS") or "").strip()
    if not raw:
        return table
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        month, _, rate = part.partition(":")
        month, rate = month.strip(), rate.strip()
        try:
            if not re.fullmatch(r"\d{4}-\d{2}", month):
                raise ValueError(f"month must be YYYY-MM, got {month!r}")
            value = float(rate)
            if value <= 0:
                raise ValueError(f"rate must be positive, got {value}")
        except ValueError as exc:
            log.warning("CB_FX_BOOKS: ignoring %r (%s)", part, exc)
            continue
        table[month] = value
    return table


def month_key(when) -> str:
    """YYYY-MM for a date/datetime/ISO string; "" when there is no usable date."""
    if not when:
        return ""
    if isinstance(when, dt.datetime):
        when = when.date()
    if isinstance(when, dt.date):
        return f"{when.year:04d}-{when.month:02d}"
    s = str(when).strip()
    return s[:7] if re.match(r"^\d{4}-\d{2}", s) else ""


def rate_for(when=None, table: dict[str, float] | None = None) -> tuple[float, str, bool]:
    """(MXN per USD, the month it came from, provisional?).

    A month past the end of the table carries the last known rate forward and is
    flagged provisional — Sept 2026 onward until Bill supplies the real rates.
    A month before the table uses the earliest rate, same flag.
    """
    table = table or books_table()
    if not table:                                  # pragma: no cover - defaults are never empty
        return 1.0, "", True
    months = sorted(table)
    key = month_key(when)
    if key in table:
        return table[key], key, False
    if not key:
        last = months[-1]
        return table[last], last, True
    if key > months[-1]:
        return table[months[-1]], months[-1], True
    if key < months[0]:
        return table[months[0]], months[0], True
    # A hole in the middle of the table: use the most recent month before it.
    earlier = [m for m in months if m < key]
    chosen = earlier[-1] if earlier else months[0]
    return table[chosen], chosen, True


def convert(amount, from_currency: str, to_currency: str, when=None,
            table: dict[str, float] | None = None):
    """Convert between MXN and USD at the books rate for `when`'s month.

    Returns None when the amount is missing or the source currency is unknown —
    an unconvertible figure is shown as-is in its own currency rather than being
    silently treated as if it were already in the target one.
    """
    if amount in (None, ""):
        return None
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    src, dst = normalize_currency(from_currency), normalize_currency(to_currency)
    if not src or not dst:
        return None
    if src == dst:
        return round(value, 2)
    rate, _, _ = rate_for(when, table)
    if src == "USD" and dst == "MXN":
        return round(value * rate, 2)
    if src == "MXN" and dst == "USD":
        return round(value / rate, 2)
    return None                                    # pragma: no cover - SUPPORTED is a 2-set


def describe(table: dict[str, float] | None = None) -> dict:
    """What the UI needs to explain the number it is showing."""
    table = table or books_table()
    months = sorted(table)
    rate_now, month_now, provisional = rate_for(dt.date.today(), table)
    return {
        "basis": "books",
        "base": BASE,
        "supported": list(SUPPORTED),
        "table": table,
        "first_month": months[0] if months else "",
        "last_month": months[-1] if months else "",
        "current_rate": rate_now,
        "current_month": month_now,
        "provisional": provisional,
        "note": ("USD at each month's own accounting rate. "
                 + (f"No rate on file past {months[-1] if months else ''} — later months "
                    f"carry it forward and are marked provisional." if provisional else "")).strip(),
    }
