"""
mw_schema.py — read-only map of Thelsa's Moveware data, for the new-platform spec.

Hidden admin page (no tile, no link, 404 for everyone except the admins):

  /admin/mw-schema            start a background read (or show the one running)
  /admin/mw-schema?result=1   collect the last finished read as JSON

What it reports: for every endpoint it reads, the field paths, their JSON types
and how often each is filled; whether each sub-resource exists (HTTP status);
the reference lists from /codes and /branches; and value counts ONLY for code
fields (status, type, service, method, branch, currency...). It never returns
customer names, addresses, phones, emails, notes or money amounts.

Read-only by construction: GET only, through mw_live's existing helpers.
"""
from __future__ import annotations

import datetime as dt
import functools
import json
import os
import threading
import urllib.error
from collections import Counter, defaultdict

from flask import Blueprint, abort, jsonify, request, session

import mw_live

schema_bp = Blueprint("mw_schema", __name__)

ADMINS = ("bbrill@thelsa.com", "bill.brill@inflectionpointnow.com")
RESULT_PATH = os.environ.get("MW_SCHEMA_RESULT", "/tmp/mw_schema_result.json")
_LOCK = threading.Lock()
_STATE = {"running": False, "started": None, "progress": ""}

# Value counts are kept only for fields whose LAST path segment is one of these
# code-like names. Everything else is reduced to its type.
CODE_FIELDS = {
    "status", "type", "jobtype", "service", "method", "branch", "branchcode",
    "currency", "calctype", "group", "taxcode", "coststatus", "category",
    "mode", "unit", "units", "measuretype", "role", "entitytype", "clienttype",
    "customertype", "movetype", "servicetype", "included", "optional",
    "selected", "showonquote", "ismovemanager", "isestimator",
}
# Never sample values from these, even if they look code-like.
PII_HINTS = ("name", "address", "street", "phone", "mobile", "email", "note",
             "comment", "description", "contact", "city", "postcode", "zip",
             "firstname", "lastname", "passport", "birth")

JOB_SUBRESOURCES = (
    "roles", "options", "quotations", "invoices", "diary", "inventory",
    "inventory/rooms", "inventory/items", "account", "objects", "dsp",
    "rating", "documents", "notes", "measures", "addresses", "contacts",
    "tasks", "storage", "insurance", "claims", "crew", "vehicles", "events",
)
OTHER_ENDPOINTS = (
    "/codes", "/branches", "/users", "/customers?limit=3", "/suppliers?limit=3",
    "/agents?limit=3", "/companies", "/services", "/methods", "/currencies",
)


def _admin_only(f):
    @functools.wraps(f)
    def wrapped(*a, **kw):
        email = (session.get("user_email") or "").lower()
        allowed = {e.strip().lower() for e in
                   (os.environ.get("MW_SCHEMA_ADMINS") or ",".join(ADMINS)).split(",")
                   if e.strip()}
        if email not in allowed:
            abort(404)
        return f(*a, **kw)
    return wrapped


# ── schema walking ─────────────────────────────────────────────────────────────
def _jtype(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        if len(v) >= 10 and v[4:5] == "-" and v[7:8] == "-":
            return "date-string"
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


class Shape:
    """Accumulates field paths -> types, fill counts, and code-value counts."""

    def __init__(self):
        self.docs = 0
        self.types = defaultdict(Counter)
        self.filled = Counter()
        self.values = defaultdict(Counter)

    def add(self, doc):
        self.docs += 1
        self._walk(doc, "")

    def _walk(self, v, path):
        t = _jtype(v)
        if path:
            self.types[path][t] += 1
            if t not in ("null",) and v != "" and v != [] and v != {}:
                self.filled[path] += 1
        if isinstance(v, dict):
            for k, sub in v.items():
                if k in ("_links", "links"):
                    self.types[(path + "." if path else "") + k]["links"] += 1
                    continue
                self._walk(sub, (path + "." if path else "") + str(k))
        elif isinstance(v, list):
            for item in v[:25]:
                self._walk(item, path + "[]")
        else:
            last = path.split(".")[-1].replace("[]", "").lower()
            if (last in CODE_FIELDS and not any(h in last for h in PII_HINTS)
                    and isinstance(v, (str, bool, int)) and not isinstance(v, float)):
                sval = str(v)
                if len(sval) <= 24:
                    self.values[path][sval] += 1

    def report(self):
        fields = []
        for p in sorted(self.types):
            fields.append({
                "path": p,
                "types": dict(self.types[p]),
                "filled": self.filled.get(p, 0),
            })
        codes = {p: dict(c.most_common(40)) for p, c in sorted(self.values.items())}
        return {"documents": self.docs, "fields": fields, "code_values": codes}


# ── the read ──────────────────────────────────────────────────────────────────
def _get(path, timeout=15):
    return mw_live._get_timed(path, timeout)


def _status_of(exc) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return str(exc.code)
    return type(exc).__name__


def _probe(path, shape: Shape | None, availability: dict, key: str):
    try:
        data = _get(path)
        availability.setdefault(key, Counter())["ok"] += 1
        if shape is not None and data is not None:
            shape.add(data)
        return data
    except Exception as e:  # noqa: BLE001
        availability.setdefault(key, Counter())[_status_of(e)] += 1
        return None


def _list_items(payload, *keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
        data = payload.get("data")
        if isinstance(data, list):
            return data
    return []


def run_read(pages: int = 8, per_combo: int = 3, max_jobs: int = 60) -> dict:
    started = dt.datetime.utcnow()
    shapes: dict[str, Shape] = defaultdict(Shape)
    availability: dict[str, Counter] = {}
    reference: dict[str, object] = {}

    # 1. Sample jobs from the feed, spread across (type, status).
    seen, picked, combos = set(), [], Counter()
    list_shape = shapes["GET /jobs (list row)"]
    for page in range(1, pages + 1):
        _STATE["progress"] = f"reading job list page {page}/{pages}"
        try:
            payload = _get(f"/jobs?limit=100&page={page}&offset={page}")
        except Exception as e:  # noqa: BLE001
            availability.setdefault("GET /jobs", Counter())[_status_of(e)] += 1
            continue
        availability.setdefault("GET /jobs", Counter())["ok"] += 1
        for j in mw_live._page_jobs(payload):
            if not isinstance(j, dict) or j.get("id") in seen:
                continue
            seen.add(j.get("id"))
            list_shape.add(j)
            combo = (str(j.get("type") or j.get("jobType") or "?"), str(j.get("status") or "?"))
            if combos[combo] < per_combo and len(picked) < max_jobs:
                combos[combo] += 1
                picked.append(j.get("id"))

    # 2. Read every sub-resource for each sampled job.
    for n, jid in enumerate(picked, 1):
        _STATE["progress"] = f"reading job {n}/{len(picked)}"
        _probe(f"/jobs/{jid}", shapes["GET /jobs/{id}"], availability, "GET /jobs/{id}")
        for sub in JOB_SUBRESOURCES:
            key = f"GET /jobs/{{id}}/{sub}"
            data = _probe(f"/jobs/{jid}/{sub}", shapes[key], availability, key)
            if sub == "options":
                for opt in _list_items(data, "options")[:2]:
                    oid = opt.get("id") if isinstance(opt, dict) else None
                    if oid is None:
                        continue
                    k2 = "GET /jobs/{id}/options/{optId}/charges"
                    _probe(f"/jobs/{jid}/options/{oid}/charges", shapes[k2], availability, k2)
            if sub == "invoices":
                for inv in _list_items(data, "invoices")[:2]:
                    iid = inv.get("id") if isinstance(inv, dict) else None
                    if iid is None:
                        continue
                    k2 = "GET /jobs/{id}/invoices/{invId}/charges"
                    _probe(f"/jobs/{jid}/invoices/{iid}/charges", shapes[k2], availability, k2)

    # 3. Other endpoints and reference lists.
    for path in OTHER_ENDPOINTS:
        _STATE["progress"] = f"reading {path}"
        key = "GET " + path.split("?")[0]
        data = _probe(path, shapes[key], availability, key)
        if path in ("/codes", "/branches") and data is not None:
            reference[path] = _scrub_reference(data)
    # One user record by code, taken from a sampled job's move manager.
    try:
        code = None
        for jid in picked[:10]:
            roles = _get(f"/jobs/{jid}/roles")
            for r in _list_items(roles, "roles"):
                if isinstance(r, dict) and r.get("type") == "moveManager":
                    ent = r.get("entity") or {}
                    code = ent.get("code") or r.get("code")
                    break
            if code:
                break
        if code:
            _probe(f"/users/{code}", shapes["GET /users/{code}"], availability, "GET /users/{code}")
    except Exception:  # noqa: BLE001
        pass

    return {
        "generated_at": started.isoformat() + "Z",
        "seconds": round((dt.datetime.utcnow() - started).total_seconds()),
        "base_url": mw_live.BASE_URL,
        "jobs_listed": len(seen),
        "jobs_sampled": len(picked),
        "type_status_mix": {f"{t}/{s}": c for (t, s), c in sorted(combos.items())},
        "availability": {k: dict(v) for k, v in sorted(availability.items())},
        "schemas": {k: v.report() for k, v in sorted(shapes.items()) if v.docs},
        "reference": reference,
        "note": "Field names, types, fill counts and code values only. No customer data.",
    }


def _scrub_reference(data):
    """Keep code lists, drop anything that looks like personal data."""
    def scrub(v):
        if isinstance(v, dict):
            return {k: scrub(x) for k, x in v.items()
                    if not any(h in k.lower() for h in ("phone", "email", "address", "contact"))}
        if isinstance(v, list):
            return [scrub(x) for x in v[:500]]
        return v
    return scrub(data)


def _background():
    try:
        result = run_read()
        with open(RESULT_PATH, "w", encoding="utf-8") as fh:
            json.dump(result, fh)
    except Exception as e:  # noqa: BLE001
        with open(RESULT_PATH, "w", encoding="utf-8") as fh:
            json.dump({"error": str(e)[:300]}, fh)
    finally:
        _STATE["running"] = False
        _STATE["progress"] = "finished"


@schema_bp.route("/admin/mw-schema")
@_admin_only
def mw_schema():
    if request.args.get("result"):
        if not os.path.exists(RESULT_PATH):
            return jsonify({"status": "no result yet", **_STATE})
        with open(RESULT_PATH, encoding="utf-8") as fh:
            return jsonify(json.load(fh))
    with _LOCK:
        if not _STATE["running"]:
            _STATE.update(running=True, started=dt.datetime.utcnow().isoformat() + "Z",
                          progress="starting")
            threading.Thread(target=_background, daemon=True).start()
    return jsonify({"status": "running" if _STATE["running"] else "idle", **_STATE,
                    "collect": "/admin/mw-schema?result=1"})
