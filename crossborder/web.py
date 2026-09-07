"""
web.py — Flask blueprint for the Cross-Border Shipment Dashboard.

Login-gated under /crossborder (reuses the library's Google session, same as
audit_web.py / faim_web.py).

Step-1 routes (data validation — the dashboard UI comes next):
  /crossborder/raw            JSON: config check, ClickUp discovery (team/space/
                              list ids) when CLICKUP_LIST_ID is unset, else the
                              normalized shipments + diagnostics (field map,
                              unmapped statuses, unknown hubs).
  /crossborder/raw?discover=1 force the hierarchy walk even when a list is set.
  /crossborder/api/shipments  JSON: normalized shipments (cached 5 min).
"""
from __future__ import annotations

import functools
import logging
import os
import threading
import time

from flask import Blueprint, jsonify, redirect, request, session, url_for

from . import clickup

log = logging.getLogger(__name__)

crossborder_bp = Blueprint("crossborder", __name__)

_CACHE: dict = {"at": 0.0, "shipments": None, "diag": None}
_CACHE_TTL = 300
_LOCK = threading.Lock()


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
        "CLICKUP_LIST_ID": os.environ.get("CLICKUP_LIST_ID", "") or None,
        "CLICKUP_TEAM_ID": os.environ.get("CLICKUP_TEAM_ID", "") or None,
        "CLICKUP_WEBHOOK_SECRET": bool(os.environ.get("CLICKUP_WEBHOOK_SECRET")),
        "MW_CREDS": all(os.environ.get(k) for k in ("MW_USERNAME", "MW_PASSWORD", "MW_COMPANY_ID")),
    }


def load_shipments(force: bool = False, include_closed: bool = False):
    """Cached pull of every TIM shipment (Moveware/TMS joins in step 2)."""
    with _LOCK:
        fresh = _CACHE["shipments"] is not None and time.time() - _CACHE["at"] < _CACHE_TTL
        if fresh and not force:
            return _CACHE["shipments"], _CACHE["diag"]
        shipments, diag = clickup.fetch_shipments(include_closed=include_closed)
        _CACHE.update(at=time.time(), shipments=shipments, diag=diag)
        return shipments, diag


@crossborder_bp.route("/crossborder/raw")
@_login_required
def raw():
    cfg = _config_status()
    out: dict = {"config": cfg}
    if not cfg["CLICKUP_TOKEN"]:
        out["next_step"] = "Set CLICKUP_TOKEN in Render (Admin personal API token, pk_…)."
        return jsonify(out)
    try:
        client = clickup.ClickUpClient()
        if not cfg["CLICKUP_LIST_ID"] or request.args.get("discover"):
            out["hierarchy"] = client.hierarchy()
            out["next_step"] = ("Pick the shipments list below and set CLICKUP_LIST_ID "
                                "(and CLICKUP_TEAM_ID) in Render.")
        if cfg["CLICKUP_LIST_ID"]:
            shipments, diag = clickup.fetch_shipments(
                client, include_closed=bool(request.args.get("closed")))
            out["diagnostics"] = diag
            out["count"] = len(shipments)
            out["shipments"] = [s.to_dict() for s in shipments]
    except clickup.ClickUpError as exc:
        out["error"] = str(exc)
    except Exception as exc:  # never 500 the validation page
        log.exception("crossborder/raw failed")
        out["error"] = f"{type(exc).__name__}: {exc}"
    return jsonify(out)


@crossborder_bp.route("/crossborder/api/shipments")
@_login_required
def api_shipments():
    try:
        shipments, diag = load_shipments(force=bool(request.args.get("refresh")))
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "shipments": []}), 200
    return jsonify({"count": len(shipments),
                    "shipments": [s.to_dict() for s in shipments],
                    "cached_at": _CACHE["at"]})


@crossborder_bp.route("/crossborder")
@_login_required
def index():
    """Placeholder until the dashboard UI lands (step 2+)."""
    return redirect(url_for("crossborder.raw"))
