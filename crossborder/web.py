"""
web.py — Flask blueprint for the Cross-Border Shipment Dashboard.

Login-gated under /crossborder (reuses the library's Google session, same as
audit_web.py / faim_web.py).

Routes:
  /crossborder/raw                 JSON: config + every TIM shipment normalized
                                   from ClickUp (one list = one shipment) with
                                   diagnostics (folders walked, unmapped steps,
                                   skipped placeholder lists, errors).
  /crossborder/raw?completed=1     also walk the "Completed …" space.
  /crossborder/raw?discover=1      the workspace hierarchy (space/folder/list ids).
  /crossborder/raw?list=<id>,<id>  inspect specific lists in full (every task).
  /crossborder                     the dashboard page (dashboard.py).
  /crossborder/api/shipments       JSON: normalized shipments + status +
                                   diagnostics (cached 5 min; ?refresh=1 to force).
"""
from __future__ import annotations

import functools
import logging
import os
import threading
import time

from flask import Blueprint, jsonify, redirect, request, session, url_for

from . import clickup, remisiones, tim
from .dashboard import DASHBOARD_HTML

log = logging.getLogger(__name__)

crossborder_bp = Blueprint("crossborder", __name__)

_CACHE: dict = {"at": 0.0, "shipments": None, "diag": None, "completed": False,
                "refreshing": False, "progress": {}, "error": None, "started_at": 0.0}
_CACHE_TTL = int(os.environ.get("CROSSBORDER_CACHE_TTL", "300") or 300)
_LOCK = threading.Lock()


def _refresh_worker(include_completed: bool, prog: dict):
    try:
        shipments, diag = tim.fetch_tim_shipments(include_completed=include_completed, progress=prog)
        # Merge the spreadsheet facts (volume / destination / sale) from the
        # latest week of the Remisiones workbook, when it can be fetched.
        try:
            rows, info = remisiones.load_latest_rows()
            if rows:
                m = remisiones.match_rows_to_shipments(rows, shipments)
                for sid, row in m["matches"].items():
                    remisiones.enrich_shipment(next(x for x in shipments if x.id == sid), row)
                info.update(m["diag"])
            diag["remisiones"] = info
        except Exception as exc:  # noqa: BLE001 — never lose the ClickUp fleet over the sheet
            log.exception("remisiones merge failed")
            diag["remisiones"] = {"error": f"{type(exc).__name__}: {exc}"}
        with _LOCK:
            _CACHE.update(at=time.time(), shipments=shipments, diag=diag,
                          completed=include_completed, error=None)
    except Exception as exc:  # noqa: BLE001
        log.exception("crossborder refresh failed")
        with _LOCK:
            _CACHE["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        with _LOCK:
            _CACHE["refreshing"] = False


def ensure_fresh(force: bool = False, include_completed: bool = False) -> dict:
    """Kick off a background refresh when the cache is stale (or forced) and
    return a status snapshot immediately — the ~80-request ClickUp walk must
    never run inside a web request."""
    with _LOCK:
        age = time.time() - _CACHE["at"]
        stale = _CACHE["shipments"] is None or age > _CACHE_TTL
        needs_completed = include_completed and not _CACHE["completed"]
        if (stale or force or needs_completed) and not _CACHE["refreshing"]:
            _CACHE.update(refreshing=True, started_at=time.time(), progress={})
            t = threading.Thread(target=_refresh_worker,
                                 args=(include_completed or _CACHE["completed"], _CACHE["progress"]),
                                 daemon=True, name="crossborder-refresh")
            t.start()
        return {
            "refreshing": _CACHE["refreshing"],
            "cached_at": _CACHE["at"] or None,
            "cache_age_s": int(age) if _CACHE["at"] else None,
            "error": _CACHE["error"],
            "progress": dict(_CACHE["progress"]),
        }


def _login_required(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return wrapped


def _config_status() -> dict:
    return {
        "CLICKUP_TOKEN": bool(os.environ.get("CLICKUP_TOKEN")),
        "CLICKUP_TEAM_ID": os.environ.get("CLICKUP_TEAM_ID", "") or None,
        "CLICKUP_ACTIVE_SPACE": os.environ.get("CLICKUP_ACTIVE_SPACE", "Logistics Coordination"),
        "CLICKUP_WEBHOOK_SECRET": bool(os.environ.get("CLICKUP_WEBHOOK_SECRET")),
        "MW_CREDS": all(os.environ.get(k) for k in ("MW_USERNAME", "MW_PASSWORD", "MW_COMPANY_ID")),
    }


def load_shipments(force: bool = False, include_completed: bool = False):
    """Cached shipments (may be empty while the first refresh runs)."""
    status = ensure_fresh(force=force, include_completed=include_completed)
    with _LOCK:
        return list(_CACHE["shipments"] or []), dict(_CACHE["diag"] or {}), status


@crossborder_bp.route("/crossborder/raw")
@_login_required
def raw():
    cfg = _config_status()
    out: dict = {"config": cfg}
    if not cfg["CLICKUP_TOKEN"]:
        out["next_step"] = "Set CLICKUP_TOKEN in Render (personal API token, pk_…)."
        return jsonify(out)
    try:
        if request.args.get("list") or request.args.get("discover"):
            client = clickup.ClickUpClient()
            if request.args.get("list"):
                ids = [x.strip() for x in request.args["list"].split(",") if x.strip()]
                out["inspected"] = [client.inspect_list(i) for i in ids]
            else:
                out["hierarchy"] = client.hierarchy()
            out["requests_made"] = client.requests_made
            return jsonify(out)
        shipments, diag, status = load_shipments(force=bool(request.args.get("refresh")),
                                                 include_completed=bool(request.args.get("completed")))
        out["status"] = status
        if status["refreshing"] and not shipments:
            out["next_step"] = "First pull is running in the background — reload this page in ~15 s."
        out["diagnostics"] = diag
        out["count"] = len(shipments)
        out["by_stage"] = _count_by(shipments, lambda s: s.stage.value)
        out["by_agent"] = _count_by(shipments, lambda s: s.agent or "?")
        out["by_flag"] = _count_by([f for s in shipments for f in s.status_flags], lambda f: f)
        out["by_hub"] = _count_by([s for s in shipments if s.is_open], lambda s: s.destination_hub.value)
        out["shipments"] = [s.to_dict() for s in shipments]
    except clickup.ClickUpError as exc:
        out["error"] = str(exc)
    except Exception as exc:  # never 500 the validation page
        log.exception("crossborder/raw failed")
        out["error"] = f"{type(exc).__name__}: {exc}"
    return jsonify(out)


def _count_by(items, key) -> dict:
    out: dict = {}
    for it in items:
        k = key(it)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


@crossborder_bp.route("/crossborder/api/shipments")
@_login_required
def api_shipments():
    try:
        shipments, diag, status = load_shipments(force=bool(request.args.get("refresh")),
                                                 include_completed=bool(request.args.get("completed")))
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "shipments": []}), 200
    return jsonify({"count": len(shipments), "status": status,
                    "diagnostics": {"remisiones": diag.get("remisiones"), "requests_made": diag.get("requests_made"),
                                    "errors": diag.get("errors")},
                    "shipments": [s.to_dict() for s in shipments]})


@crossborder_bp.route("/crossborder")
@crossborder_bp.route("/crossborder/")
@_login_required
def index():
    """The dashboard page (renders client-side from /crossborder/api/shipments)."""
    ensure_fresh()   # warm the cache so the page has data by the time it asks
    return DASHBOARD_HTML
