"""Multi-currency conversion for the audit dashboard.

Every amount is converted to one display currency (AUDIT_DISPLAY_CCY, default USD)
at TODAY's spot rate from Yahoo Finance. Rates are fetched once per UTC day and
cached in memory + on disk (FX_CACHE_PATH, default /var/data/fx_cache.json when the
persistent disk exists). A hard-coded fallback table means a failed fetch never
blanks the dashboard.

NOTE: this is the dashboard's spot-rate path only. The commission report uses the
fixed Finance rate (16.5 MXN/USD) and must NOT use this module's rates.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import unicodedata

# Units of the currency per 1 USD. Used when Yahoo is unreachable.
FALLBACK_PER_USD = {"USD": 1.0, "MXN": 18.3, "EUR": 0.86, "GBP": 0.75, "CAD": 1.38,
                    "BRL": 5.4, "COP": 3900.0, "CHF": 0.80, "JPY": 148.0}

_ALIASES = {
    "US$": "USD", "U$S": "USD", "$": "USD", "DOLAR": "USD", "DOLARES": "USD",
    "DOLAR AMERICANO": "USD", "DOLARES AMERICANOS": "USD", "US DOLLAR": "USD",
    "DOLLAR": "USD", "DLS": "USD", "USD$": "USD",
    "MX$": "MXN", "MN": "MXN", "M.N.": "MXN", "MXP": "MXN", "PESO": "MXN", "PESOS": "MXN",
    "PESO MEXICANO": "MXN", "PESOS MEXICANOS": "MXN",
    "€": "EUR", "EURO": "EUR", "EUROS": "EUR", "£": "GBP", "LIBRA": "GBP",
}

_lock = threading.Lock()
_mem: dict = {}   # {"date": "YYYY-MM-DD", "per_usd": {...}, "source": "yahoo"|"fallback"}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize(code, default: str | None = None) -> str | None:
    """Map ISO codes, symbols and Spanish names to an ISO-4217 code."""
    if code is None:
        return default
    if isinstance(code, dict):
        code = code.get("code") or code.get("isoCode") or code.get("name") or ""
    s = _strip_accents(str(code)).strip().upper()
    if not s:
        return default
    if s in _ALIASES:
        return _ALIASES[s]
    if len(s) == 3 and s.isalpha():
        return s
    return default


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _cache_path() -> str:
    p = os.environ.get("FX_CACHE_PATH")
    if p:
        return p
    return "/var/data/fx_cache.json" if os.path.isdir("/var/data") else "fx_cache.json"


def _fetch_yahoo(codes) -> dict:
    """Return {ccy: units per USD} for the codes Yahoo answered."""
    import requests
    out = {}
    for c in codes:
        if c == "USD":
            continue
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/USD{c}=X",
                             params={"range": "1d", "interval": "1d"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            r.raise_for_status()
            meta = r.json()["chart"]["result"][0]["meta"]
            px = float(meta.get("regularMarketPrice") or meta.get("previousClose"))
            if px > 0:
                out[c] = px
        except Exception:
            continue
    return out


def rates(force: bool = False) -> dict:
    """Today's rates: {"date", "per_usd", "source"}. Fetched at most once per UTC day."""
    today = _today()
    with _lock:
        if not force and _mem.get("date") == today:
            return _mem
        if not force:
            try:
                with open(_cache_path()) as fh:
                    disk = json.load(fh)
                if disk.get("date") == today and disk.get("per_usd"):
                    _mem.clear(); _mem.update(disk)
                    return _mem
            except Exception:
                pass
        per_usd = dict(FALLBACK_PER_USD)
        got = _fetch_yahoo(list(FALLBACK_PER_USD))
        per_usd.update(got)
        data = {"date": today, "per_usd": per_usd,
                "source": "yahoo" if got else "fallback"}
        _mem.clear(); _mem.update(data)
        if got:   # only persist real rates, so a bad day retries tomorrow's fetch
            try:
                with open(_cache_path(), "w") as fh:
                    json.dump(data, fh)
            except Exception:
                pass
        return _mem


def rate(src, dst) -> float:
    """Multiplier converting 1 unit of `src` into `dst`."""
    s = normalize(src, "USD"); d = normalize(dst, "USD")
    if s == d:
        return 1.0
    pu = rates()["per_usd"]
    if s not in pu or d not in pu:
        return 1.0   # unknown currency: leave the number as booked rather than guess
    return pu[d] / pu[s]


def convert(amount, src, dst) -> float:
    try:
        return float(amount or 0) * rate(src, dst)
    except (TypeError, ValueError):
        return 0.0


# ── Dashboard bases (same methodology as the TMS Executive Dashboard) ────────────
# Plan  = the rate the 2026 budget was struck at (TMS dashboard data/finance.json).
# Spot  = the month-end USD/MXN of the most recent COMPLETED month (Bill, 21 Sep 2026):
#         Banxico FIX (needs BANXICO_TOKEN) → ECB via frankfurter.app → today's Yahoo
#         spot as a last resort, flagged stale.
PLAN_MXN_PER_USD = 17.4479
_me_lock = threading.Lock()
_me: dict = {}


def plan_rate() -> float:
    try:
        return float(os.environ.get("AUDIT_FX_PLAN") or PLAN_MXN_PER_USD)
    except ValueError:
        return PLAN_MXN_PER_USD


def _last_day(y: int, m: int) -> _dt.date:      # m 1-based
    nxt = _dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return nxt - _dt.timedelta(days=1)


def _prev_month(today: _dt.date):
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def _banxico(y, m):
    tok = os.environ.get("BANXICO_TOKEN")
    if not tok:
        return None
    import requests
    a, b = f"{y}-{m:02d}-01", _last_day(y, m).isoformat()
    r = requests.get(f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/{a}/{b}",
                     headers={"Bmx-Token": tok, "Accept": "application/json"}, timeout=15)
    r.raise_for_status()
    obs = (((r.json().get("bmx") or {}).get("series") or [{}])[0].get("datos") or [])
    good = [o for o in obs if o.get("dato") and o["dato"].replace(",", "").replace(".", "", 1).isdigit()]
    if not good:
        return None
    dd, mm, yy = good[-1]["fecha"].split("/")
    return {"rate": float(good[-1]["dato"].replace(",", "")), "date": f"{yy}-{mm}-{dd}",
            "source": "Banxico FIX (DOF)"}


def _ecb(y, m):
    import requests
    r = requests.get(f"https://api.frankfurter.app/{_last_day(y, m).isoformat()}",
                     params={"from": "USD", "to": "MXN"}, timeout=15)
    r.raise_for_status()
    j = r.json()
    if not j.get("rates", {}).get("MXN") or not str(j.get("date", "")).startswith(f"{y}-{m:02d}"):
        return None
    return {"rate": float(j["rates"]["MXN"]), "date": j["date"], "source": "ECB reference rate"}


def month_end_spot(today: _dt.date | None = None) -> dict:
    """{"rate","asOf","month","source","stale"} — MXN per USD at the end of the latest
    completed month. Fetched at most once per UTC day; cached to disk."""
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    y, m = _prev_month(today)
    want = f"{y}-{m:02d}"
    with _me_lock:
        if _me.get("checked") == today.isoformat() and _me.get("rate"):
            return dict(_me)
        path = _cache_path().replace("fx_cache", "fx_monthend")
        try:
            with open(path) as fh:
                disk = json.load(fh)
            if disk.get("month") == want and disk.get("rate"):
                _me.clear(); _me.update(disk, checked=today.isoformat(), stale=False)
                return dict(_me)
        except Exception:
            pass
        got = None
        for fn in (_banxico, _ecb):
            try:
                got = fn(y, m)
            except Exception:
                got = None
            if got:
                break
        if got:
            data = {"rate": round(got["rate"], 4), "asOf": got["date"], "month": want,
                    "source": got["source"], "stale": False}
            try:
                with open(path, "w") as fh:
                    json.dump(data, fh)
            except Exception:
                pass
        else:
            pu = rates()["per_usd"]
            data = {"rate": round(pu.get("MXN", FALLBACK_PER_USD["MXN"]), 4), "asOf": today.isoformat(),
                    "month": None, "source": rates().get("source"), "stale": True}
        _me.clear(); _me.update(data, checked=today.isoformat())
        return dict(_me)


def display_ccy() -> str:
    return normalize(os.environ.get("AUDIT_DISPLAY_CCY"), "USD") or "USD"


def default_ccy() -> str:
    return normalize(os.environ.get("AUDIT_DEFAULT_CCY"), "MXN") or "MXN"
