
Mw live · PY
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
        return 0.0
 
 
def _code_text(v) -> str:
    """MoveWare code objects look like {code, text}. Return text (or the value)."""
    if isinstance(v, dict):
        return str(_first(v, "text", "code", default="")).strip()
    return str(v or "").strip()
 
 
def _mode(job: dict) -> str:
    raw = (_code_text(_first(job, "method", "service", default="")) or "").lower()
    if "sea" in raw or "ocean" in raw:
        return "sea"
    if "air" in raw:
        return "air"
    if "road" in raw or "land" in raw or "truck" in raw or "ground" in raw:
        return "road"
    return "sea"  # sensible default; refine from real data
 
 
def _date(v):
    v = _first(v, "date", "value") if isinstance(v, dict) else v
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(str(v)[:19], fmt[: len(str(v)[:19])] if "T" not in str(v) else fmt).date()
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(str(v)[:19]).date()
    except ValueError:
        return None
 
 
def _classify_charge(charge: dict) -> str:
    """Return 'cost' or 'sell' for a quote charge line.
 
    Placeholder heuristic until confirmed from real data via /audit/raw:
    MoveWare quote option charges are the SELL side; cost is inferred from
    rate vs value where available. Adjust here once real charge `type` codes
    are known.
    """
    t = (_code_text(_first(charge, "type", default="")) or "").lower()
    if "cost" in t or "creditor" in t or "supplier" in t or "buy" in t:
        return "cost"
    return "sell"
 
 
def _weight_from_measurements(measurements) -> float | None:
    if not isinstance(measurements, list):
        return None
    for m in measurements:
        t = (_code_text(_first(m, "type", default="")) or "").lower()
        if "weight" in t or "kg" in (str(_first(m, "uom", default="")).lower()):
            return _num(_first(m, "value"))
    return None
 
 
def _map_job(job: dict) -> dict | None:
    job_id = _first(job, "id", "jobId", "jobNumber", "jobFile", "externalId")
    if not job_id:
        return None
    job_id = str(job_id)
 
    client = _code_text(_first(job, "transferee", "customer", default="")) or \
        _first(job, "transfereeName", "customerName", default="")
    if isinstance(_first(job, "transferee"), dict):
        client = _first(job["transferee"], "name", "titleName", default=client)
 
    currency = _first(job, "currency", default="USD")
    pack = _date(_first(job, "pack", "upliftStart", "estimatedMove"))
    delivery = _date(_first(job, "deliveryStart", "estimatedDelivery"))
 
    # Quote (selected option) → sell + estimated cost + weight
    sell = est_cost = 0.0
    declared = None
    weight = None
    try:
        qd = _get(f"/jobs/{job_id}/quotes")
        quotes = _first(qd, "quotes", default=[]) or []
        option = None
        for q in quotes:
            for opt in (_first(q, "options", default=[]) or []):
                if _first(opt, "selected") in (True, "true", 1):
                    option = opt
                    break
            if option:
                break
        if option is None and quotes:
            opts = _first(quotes[0], "options", default=[]) or []
            option = opts[0] if opts else None
        if option:
            weight = _weight_from_measurements(_first(option, "measurements"))
            for ch in (_first(option, "charges", default=[]) or []):
                val = _num(_first(ch, "value"))
                if _classify_charge(ch) == "cost":
                    est_cost += val
                else:
                    sell += val
                desc = (_first(ch, "description", "details", default="") or "").lower()
                if "insur" in desc or "valuation" in desc:
                    declared = _num(_first(ch, "value")) or declared
    except Exception:
        pass
 
    # Invoices → invoiced amount
    invoiced_amt = 0.0
    invoiced = False
    try:
        inv = _get(f"/jobs/{job_id}/invoices")
        invoices = _first(inv, "invoices", default=[]) or []
        for it in invoices:
            invoiced_amt += _num(_first(it, "value", "total", "amount"))
        invoiced = invoiced_amt > 0
    except Exception:
        pass
 
    # Actual cost — best available signal now: estimated cost (refined once
    # /account cost lines are confirmed). Never invents a number.
    actual_cost = est_cost
 
    return {
        "job": job_id,
        "client": client or "",
        "mode": _mode(job),
        "est": round(est_cost, 2),
        "act": round(actual_cost, 2),
        "sell": round(sell, 2),
        "inv_amt": round(invoiced_amt, 2),
        "invoiced": invoiced,
        "declared": declared,
        "ins": None,
        "coordinator": _code_text(_first(job, "moveManager", default="")),
        "agent": None,
        "pack": pack,
        "delivery": delivery,
    }
 
 
def load_live_files():
    """Return mapped live files, or None to signal fallback to demo."""
    if not have_creds():
        return None
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["data"]
    try:
        data = _get("/jobs")
        jobs = _first(data, "jobs", default=[]) or []
        if not isinstance(jobs, list):
            return None
        mapped = []
        for job in jobs[:_MAX_JOBS]:
            # list items may be light; fetch full job if it lacks fields
            jid = _first(job, "id", "jobId", "jobNumber", "jobFile")
            full = job
            if jid and not _first(job, "transferee", "currency"):
                try:
                    full = _get(f"/jobs/{jid}")
                except Exception:
                    full = job
            m = _map_job(full)
            if m:
                mapped.append(m)
        if not mapped:
            return None
        _CACHE["data"] = mapped
        _CACHE["at"] = now
        return mapped
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None
    except Exception:
        return None
 
 
def raw_sample(job_id: str | None = None) -> dict:
    """Debug helper: return raw MoveWare structures to confirm the mapping."""
    out = {"base_url": BASE_URL, "have_creds": have_creds()}
    try:
        jobs = _get("/jobs")
        out["jobs_top_keys"] = list(jobs.keys()) if isinstance(jobs, dict) else "list"
        arr = _first(jobs, "jobs", default=[]) or []
        out["job_count"] = len(arr)
        if arr:
            out["first_job"] = arr[0]
            jid = job_id or str(_first(arr[0], "id", "jobId", "jobNumber", "jobFile"))
            out["sample_job_id"] = jid
            try:
                out["quotes"] = _get(f"/jobs/{jid}/quotes")
            except Exception as e:
                out["quotes_error"] = str(e)
            try:
                out["invoices"] = _get(f"/jobs/{jid}/invoices")
            except Exception as e:
                out["invoices_error"] = str(e)
            try:
                out["account"] = _get(f"/jobs/{jid}/account")
            except Exception as e:
                out["account_error"] = str(e)
    except Exception as e:
        out["error"] = str(e)
    return out
