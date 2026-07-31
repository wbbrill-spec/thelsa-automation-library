"""
mw_live.py — Live MoveWare data pull for the audit dashboard.

Reads jobs, quotes, and invoices from the MoveWare REST API using the
mw-username / mw-password / mw-company-id credentials stored in Render env vars,
and maps them to the same dict shape the dashboard's reconcile()/compute_metrics()
already expect.

Safety:
- Activates ONLY when all three credentials are present in the environment.
- Every network path is wrapped so a failure returns None and the caller falls
  back to the demo dataset — the /audit page can never break.
- Results are cached in-memory (TTL) so page loads don't hammer MoveWare.

The one thing still being confirmed from real data is which charge `type` codes
mean estimated-cost vs sell-price. `_classify_charge()` centralises that so it's
a one-line change once the live structure is inspected via /audit/raw.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request

_CACHE = {"at": 0.0, "data": None}
_CACHE_TTL = 600  # seconds
_MAX_JOBS = 25    # cap the first live pull for speed

# Env-driven base URL; defaults to UAT for safety.
BASE_URL = os.environ.get(
    "MOVEWARE_URL", "https://rest.moveconnect.com/movewareUAT/v1"
).rstrip("/")


def have_creds() -> bool:
    return all(os.environ.get(k) for k in ("MW_USERNAME", "MW_PASSWORD", "MW_COMPANY_ID"))


def _headers() -> dict:
    return {
        "mw-username": os.environ.get("MW_USERNAME", ""),
        "mw-password": os.environ.get("MW_PASSWORD", ""),
        "mw-company-id": os.environ.get("MW_COMPANY_ID", ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _get(path: str):
    url = f"{BASE_URL}/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _first(d: dict, *keys, default=None):
    if not isinstance(d, dict):
        return default
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
        for actual in d:
            if actual.lower() == k.lower() and d[actual] not in (None, ""):
                return d[actual]
    return default


def _num(v) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
