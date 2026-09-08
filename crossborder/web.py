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
  /crossborder/api/shipments       JSON: normalized shipments (cached 5 min;
                                   ?refresh=1 to force).
"""
from __future__ import annotations

import functools
import logging
import os
import threading
import time

from flask import Blueprint, jsonify, redirect, request, session, url_for

from . import clickup, tim

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
        "CLICKUP_TEAM_ID": os.environ.get("CLICKUP_TEAM_ID", "") or None,
        "CLICKUP_ACTIVE_SPACE": os.environ.get("CLICKUP_ACTIVE_SPACE", "Logistics Coordination"),
        "CLICKUP_WEBHOOK_SECRET": bool(os.environ.get("CLICKUP_WEBHOOK_SECRET")),
        "MW_CREDS": all(os.environ.get(k) for k in ("MW_USERNAME", "MW_PASSWORD", "MW_COMPANY_ID")),
    }


def load_shipments(force: bool = False, include_completed: bool = False):
    """Cached pull of every TIM shipment (Moveware/TMS joins in a later step)."""
    with _LOCK:
        fresh = _CACHE["shipments"] is not None and time.time() - _CACHE["at"] < _CACHE_TTL
        if fresh and not force:
            return _CACHE["shipments"], _CACHE["diag"]
        shipments, diag = tim.fetch_tim_shipments(include_completed=include_completed)
        _CACHE.update(at=time.time(), shipments=shipments, diag=diag)
        return shipments, diag


@crossborder_bp.route("/crossborder/raw")
@_login_required
def raw():
    cfg = _config_status()
    out: dict = {"config": cfg}
    if not cfg["CLICKUP_TOKEN"]:
        out["next_step"] = "Set CLICKUP_TOKEN in Render (personal API token, pk_…)."
        return jsonify(out)
    try:
        client = clickup.ClickUpClient()
        if request.args.get("list"):
            ids = [x.strip() for x in request.args["list"].split(",") if x.strip()]
            out["inspected"] = [client.inspect_list(i) for i in ids]
            out["requests_made"] = client.requests_made
            return jsonify(out)
        if request.args.get("discover"):
            out["hierarchy"] = client.hierarchy()
            out["requests_made"] = client.requests_made
            return jsonify(out)
        shipments, diag = tim.fetch_tim_shipments(
            client, include_completed=bool(request.args.get("completed")))
        out["diagnostics"] = diag
        out["count"] = len(shipments)
        out["by_stage"] = _count_by(shipments, lambda s: s.stage.value)
        out["by_agent"] = _count_by(shipments, lambda s: s.agent or "?")
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
        shipments, diag = load_shipments(force=bool(request.args.get("refresh")),
                                         include_completed=bool(request.args.get("completed")))
    except Exception as exc:
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "shipments": []}), 200
    return jsonify({"count": len(shipments),
                    "shipments": [s.to_dict() for s in shipments],
                    "cached_at": _CACHE["at"]})


@crossborder_bp.route("/crossborder")
@_login_required
def index():
    """Placeholder until the dashboard UI lands (next step)."""
    return redirect(url_for("crossborder.raw"))
