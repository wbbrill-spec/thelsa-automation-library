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


def display_ccy() -> str:
    return normalize(os.environ.get("AUDIT_DISPLAY_CCY"), "USD") or "USD"


def default_ccy() -> str:
    return normalize(os.environ.get("AUDIT_DEFAULT_CCY"), "MXN") or "MXN"
