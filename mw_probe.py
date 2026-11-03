"""
mw_probe.py — what Moveware's V2 API exposes now, cost endpoints first
(Bill, 3 Nov 2026: "the v2 api now exposes cost endpoints - lots of them... we need
to capture all the line item revenue and cost").

Read-only GETs with the app's Moveware login. It
  1. looks for the API's own endpoint list (OpenAPI / Swagger document) and keeps
     every path whose name points at cost, charges, purchase orders, creditors,
     invoices or accounting;
  2. calls the likely cost paths on a few real booked, delivered files and
     records, for each path, the HTTP status and the SHAPE of the answer: field
     names, numbers, dates and codes. Text is reduced to its length, so no
     customer or supplier name leaves the server (same rule as /faim/check).

The result is kept in memory (/faim/mw-probe) and, once per deploy, emailed to
Bill (MW_PROBE_EMAIL, default bill.brill@inflectionpointnow.com) so it can be read
without anyone signing in. MW_PROBE=0 turns it off.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import threading
import time

import requests

COST_WORDS = re.compile(r"cost|charge|purchase|creditor|supplier|vendor|expense|invoice|"
                        r"accru|payable|ledger|margin|revenue|price|option|account|gl", re.I)
_DATE_LIKE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CODE_LIKE = re.compile(r"^[A-Z0-9]{1,6}$")

SPEC_PATHS = ("swagger/v1/swagger.json", "swagger/v2/swagger.json", "swagger.json",
              "openapi.json", "openapi/v1.json", "v2/swagger.json", "api-docs",
              "swagger", "docs", "")

JOB_PATHS = ("", "/options", "/costs", "/costings", "/costing", "/charges", "/purchaseOrders",
             "/purchaseorders", "/pos", "/creditors", "/supplierInvoices", "/supplierinvoices",
             "/expenses", "/invoices", "/revenue", "/financials", "/financial", "/accounts",
             "/ledger", "/lines", "/items", "/actuals", "/margin", "/profit", "/payments",
             "/receipts", "/accruals")
OPTION_PATHS = ("", "/charges", "/costs", "/costings", "/lines", "/items", "/purchaseOrders")
TOP_PATHS = ("costs", "charges", "purchaseOrders", "purchaseorders", "creditors", "suppliers",
             "invoices", "supplierInvoices", "payments", "receipts", "accounts", "glcodes",
             "chargeCodes", "chargecodes", "costCodes", "costcodes")

_STATE = {"running": False, "at": None, "result": None, "emailed": False, "error": None}
_LOCK = threading.Lock()


def shape(v, depth=0):
    """Field names, numbers, dates, booleans and short codes; other text -> its length."""
    if isinstance(v, dict):
        if depth >= 6:
            return f"object({len(v)} fields)"
        return {k: shape(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return {"count": len(v), "first": [shape(x, depth + 1) for x in v[:2]]}
    if isinstance(v, str):
        if not v:
            return ""
        if _DATE_LIKE.match(v) or _CODE_LIKE.match(v):
            return v
        return f"text({len(v)})"
    return v


def cost_fields(v, prefix="") -> dict:
    """{dotted.key: value} for every NUMBER whose key looks like money (cost,
    amount, price, sell, actual, estimate, total, tax, margin...)."""
    out = {}
    money = re.compile(r"cost|amount|amt|price|sell|actual|estim|total|tax|margin|value|rate|net|gross|qty|quantity", re.I)
    if isinstance(v, dict):
        for k, x in v.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(x, (int, float)) and not isinstance(x, bool) and money.search(k):
                out[key] = x
            elif isinstance(x, (dict, list)):
                out.update(cost_fields(x, key))
    elif isinstance(v, list):
        for i, x in enumerate(v[:3]):
            out.update(cost_fields(x, f"{prefix}[{i}]"))
    return out


def _get(url, timeout=15):
    import mw_live
    try:
        r = requests.get(url, headers=mw_live._headers(), timeout=timeout)
    except Exception as e:                         # noqa: BLE001
        return None, f"{type(e).__name__}"
    try:
        body = r.json()
    except ValueError:
        body = None
    return r.status_code, body if body is not None else f"text({len(r.text or '')}) {r.headers.get('content-type', '')}"


def find_spec() -> dict:
    """Look for the OpenAPI/Swagger document. Returns {tried: [...], paths: [...]}."""
    import mw_live
    host = mw_live.MW_HOST.rstrip("/")
    seg = mw_live.MW_COMPANY_SEGMENT
    bases = [mw_live.BASE_URL, f"{host}/{seg}", host]
    tried, paths, spec_url = [], [], None
    for b in bases:
        for p in SPEC_PATHS:
            url = f"{b}/{p}".rstrip("/")
            code, body = _get(url, 12)
            tried.append({"url": url.replace(host, ""), "status": code,
                          "kind": "json" if isinstance(body, (dict, list)) else str(body)[:60]})
            if isinstance(body, dict) and isinstance(body.get("paths"), dict):
                spec_url = url
                for path, ops in body["paths"].items():
                    methods = sorted(m.upper() for m in (ops or {}) if m.lower() in
                                     ("get", "post", "put", "patch", "delete"))
                    paths.append({"path": path, "methods": methods,
                                  "cost_related": bool(COST_WORDS.search(path))})
                break
        if spec_url:
            break
    return {"spec_url": (spec_url or "").replace(host, ""), "tried": tried[:40],
            "paths": paths, "n_paths": len(paths),
            "cost_paths": [p for p in paths if p["cost_related"]]}


def sample_jobs(n=3) -> list:
    """Internal ids of a few booked, delivered files with a sale price."""
    import mw_live
    today = dt.date.today()
    rows = []
    for f in mw_live.audited_in_window() or []:
        if str(f.get("status") or "").upper() != "W" or not f.get("job_id"):
            continue
        d = f.get("delivery") or f.get("pack")
        if isinstance(d, str):
            try:
                d = dt.date.fromisoformat(d[:10])
            except ValueError:
                d = None
        if d and d <= today and float(f.get("sell") or 0) > 0:
            rows.append((d, f))
    rows.sort(key=lambda x: x[0], reverse=True)
    picked, seen = [], set()
    for d, f in rows[15:]:        # delivered a while ago: costs more likely posted
        if f["job_id"] in seen:
            continue
        seen.add(f["job_id"])
        picked.append({"job_id": f["job_id"], "job": f.get("job"), "type": f.get("job_type")})
        if len(picked) >= n:
            break
    return picked


def probe_job(job_id) -> list:
    import mw_live
    base = mw_live.BASE_URL
    out = []
    opt_ids = []
    for p in JOB_PATHS:
        code, body = _get(f"{base}/jobs/{job_id}{p}")
        entry = {"path": f"/jobs/{{id}}{p}", "status": code}
        if isinstance(body, (dict, list)):
            entry["money_fields"] = cost_fields(body)
            entry["shape"] = shape(body)
            if p == "/options":
                items = body.get("options") if isinstance(body, dict) else body
                for o in (items or [])[:3]:
                    if isinstance(o, dict) and o.get("id"):
                        opt_ids.append(o["id"])
        else:
            entry["body"] = body
        out.append(entry)
    for oid in opt_ids[:2]:
        for p in OPTION_PATHS:
            for url, label in ((f"{base}/jobs/{job_id}/options/{oid}{p}", f"/jobs/{{id}}/options/{{opt}}{p}"),
                               (f"{base}/options/{oid}{p}", f"/options/{{opt}}{p}")):
                code, body = _get(url)
                entry = {"path": label, "status": code}
                if isinstance(body, (dict, list)):
                    entry["money_fields"] = cost_fields(body)
                    entry["shape"] = shape(body)
                else:
                    entry["body"] = body
                out.append(entry)
    return out


def probe_top() -> list:
    import mw_live
    out = []
    for p in TOP_PATHS:
        code, body = _get(f"{mw_live.BASE_URL}/{p}?limit=2")
        entry = {"path": f"/{p}", "status": code}
        if isinstance(body, (dict, list)):
            entry["money_fields"] = cost_fields(body)
            entry["shape"] = shape(body)
        else:
            entry["body"] = body
        out.append(entry)
    return out


def run() -> dict:
    import mw_live
    with _LOCK:
        if _STATE["running"]:
            return _STATE
        _STATE["running"] = True
    try:
        if not mw_live.have_creds():
            raise RuntimeError("no Moveware login on this server")
        jobs = sample_jobs()
        result = {"at": dt.datetime.utcnow().isoformat() + "Z", "base": mw_live.BASE_URL.split("//")[-1],
                  "spec": find_spec(), "top": probe_top(),
                  "jobs": [dict(j, paths=probe_job(j["job_id"])) for j in jobs]}
        with _LOCK:
            _STATE.update(result=result, at=time.time(), error=None)
        return result
    except Exception as e:                         # noqa: BLE001
        with _LOCK:
            _STATE.update(error=f"{type(e).__name__}: {e}"[:300], at=time.time())
        return {"error": _STATE["error"]}
    finally:
        with _LOCK:
            _STATE["running"] = False


def summary_text(result: dict) -> str:
    """A short readable summary, then the full detail."""
    if not result or result.get("error"):
        return f"Probe failed: {(result or {}).get('error')}"
    lines = [f"Moveware API probe — {result['at']} — {result['base']}", ""]
    sp = result["spec"]
    if sp["spec_url"]:
        lines.append(f"Endpoint list found at {sp['spec_url']}: {sp['n_paths']} paths, "
                     f"{len(sp['cost_paths'])} look cost/finance related:")
        lines += [f"  {', '.join(p['methods'])}  {p['path']}" for p in sp["cost_paths"][:150]]
    else:
        lines.append("No endpoint list (OpenAPI/Swagger) found at the usual addresses.")
    lines += ["", "Top-level paths that answered:"]
    lines += [f"  {e['status']}  {e['path']}  money fields: {list(e.get('money_fields', {}))[:12]}"
              for e in result["top"] if e["status"] == 200]
    for j in result["jobs"]:
        lines += ["", f"File {j['job']} ({j['type']}), id {j['job_id']} — paths that answered 200:"]
        for e in j["paths"]:
            if e["status"] == 200:
                mf = e.get("money_fields") or {}
                lines.append(f"  {e['path']}: {len(mf)} money fields"
                             + (f" e.g. {dict(list(mf.items())[:10])}" if mf else ""))
        other = sorted({f"{e['status']} {e['path']}" for e in j["paths"] if e["status"] != 200})
        lines.append("  not available: " + "; ".join(other[:40]))
    return "\n".join(lines)


def email(result: dict) -> bool:
    to = os.environ.get("MW_PROBE_EMAIL", "bill.brill@inflectionpointnow.com")
    try:
        from engine.mailer import GraphMailer
        import coordinator_alerts as ca
        body = ("<pre style='font:12px/1.4 monospace;white-space:pre-wrap'>"
                + html.escape(summary_text(result))
                + "\n\n=== Full detail (shapes only; text reduced to its length) ===\n"
                + html.escape(json.dumps(result, indent=1, default=str)[:150000]) + "</pre>")
        GraphMailer(mailbox=ca._sender()).send_mail(
            to, "[FAIM Audit Assistant] Moveware API probe: cost endpoints", body, html=True)
        with _LOCK:
            _STATE["emailed"] = True
        return True
    except Exception as e:                         # noqa: BLE001
        with _LOCK:
            _STATE["error"] = f"email: {type(e).__name__}: {e}"[:300]
        return False


def run_once_per_deploy() -> None:
    """For the keeper: probe and email once per process, after the file book has
    loaded (it needs real file ids)."""
    if os.environ.get("MW_PROBE", "1") != "1":
        return
    with _LOCK:
        if _STATE["emailed"] or _STATE["running"] or (_STATE["at"] and time.time() - _STATE["at"] < 3600):
            return
    import mw_live
    if not mw_live.audited_in_window():
        return
    def _go():
        res = run()
        email(res)
    threading.Thread(target=_go, daemon=True, name="mw-probe").start()


def state() -> dict:
    with _LOCK:
        return dict(_STATE)
