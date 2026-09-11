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
  /crossborder/raw?tms=probe       Moveware shape discovery (a few calls; &env=test|prod).
  /crossborder/raw?tms=1&days=30   a bounded TMS walk with diagnostics (&max_details=N).
  /crossborder                     the dashboard page (dashboard.py).
  /crossborder/api/shipments       JSON: normalized shipments + status +
                                   diagnostics (cached 5 min; ?refresh=1 to force).
  /crossborder/api/plan            JSON: the consolidation engine's suggested loads.
  /crossborder/plan/draft  (POST)  create the suggested-load email as a DRAFT in
                                   the Thelsa mailbox (never sends). Also runs
                                   daily at PLAN_EMAIL_HOUR when PLAN_EMAIL_ENABLED=1.
  /crossborder/api/alerts          JSON: per-person outstanding-item alerts as they
                                   would be drafted (preview only — writes nothing).
  /crossborder/alerts/draft (POST) file those alerts as DRAFTS, one per owner.
                                   Also runs daily at CB_ALERTS_HOUR when
                                   CB_ALERTS_ENABLED=1.
"""
from __future__ import annotations

import functools
import logging
import os
import threading
import time

from flask import Blueprint, jsonify, redirect, request, session, url_for

from . import alerts, clickup, engine, remisiones, tim, tms
from .dashboard import DASHBOARD_HTML
from .models import Source

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
        # TMS shipments from Moveware (read-only, narrow walk). A Moveware
        # failure never drops the TIM fleet either.
        if os.environ.get("TMS_ENABLED", "1") in ("1", "true", "yes"):
            tms_ships, tdiag = _tms_cached(prog)
            shipments = shipments + tms_ships
            diag["tms"] = tdiag
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


# Moveware is polled on its own, slower clock (performance guardrail: a few
# scheduled pulls a day, never every page load). The ClickUp refresh reuses the
# last TMS result until it is older than TMS_CACHE_TTL (default 1 h).
_TMS_CACHE: dict = {"at": 0.0, "shipments": [], "diag": {}, "good_at": 0.0,
                    "attempt_at": 0.0, "degraded": False, "reason": None}
_TMS_TTL = int(os.environ.get("TMS_CACHE_TTL", "3600") or 3600)
# While Moveware is refusing, retry on this slower clock instead of on every
# 5-minute ClickUp refresh — a walk is ~20 requests and the provider's
# performance guardrail is explicit.
_TMS_RETRY_S = int(os.environ.get("TMS_RETRY_TTL", "900") or 900)


def _compact_tms_diag(tdiag: dict) -> dict:
    """Drop the per-slice detail but keep how many slices failed and why —
    without this a Moveware outage looks identical to a quiet week."""
    slices = tdiag.get("slices", []) or []
    errs = [s.get("error") for s in slices if isinstance(s, dict) and s.get("error")]
    out = {k: v for k, v in tdiag.items() if k != "slices"}
    out["slices"] = len(slices)
    out["slice_errors"] = len(errs)
    if errs:
        out["slice_error_sample"] = errs[0][:160]
    return out


def _tms_stale(cached: list, cdiag: dict, now: float, reason: str) -> tuple[list, dict]:
    with _LOCK:
        good_at = _TMS_CACHE["good_at"] or _TMS_CACHE["at"]
    return cached, {**cdiag, "stale": True, "count": len(cached),
                    "stale_reason": reason,
                    "stale_since": good_at or None,
                    "stale_age_s": int(now - good_at) if good_at else None}


def _tms_cached(prog: dict):
    """Moveware on its own, slower clock. A failed walk — or one that suddenly
    returns nothing where the last good walk had jobs — keeps serving the last
    good fleet rather than silently emptying half the board."""
    now = time.time()
    with _LOCK:
        age = (now - _TMS_CACHE["at"]) if _TMS_CACHE["at"] else None
        degraded = _TMS_CACHE["degraded"]
        since_attempt = now - (_TMS_CACHE["attempt_at"] or 0.0)
        cached = list(_TMS_CACHE["shipments"])
        cdiag = dict(_TMS_CACHE["diag"])
        reason = _TMS_CACHE["reason"]
    if age is not None and age < _TMS_TTL and not degraded:
        return cached, {**cdiag, "cache_age_s": int(age)}
    if degraded and since_attempt < _TMS_RETRY_S:
        return _tms_stale(cached, cdiag, now, reason or "Moveware unavailable")

    with _LOCK:
        _TMS_CACHE["attempt_at"] = now
    try:
        if not tms.MovewareClient.have_creds():
            return [], {"error": "MW_USERNAME / MW_PASSWORD / MW_COMPANY_ID not set"}
        tprog: dict = {}
        prog["tms"] = tprog
        ships, tdiag = tms.fetch_tms_shipments(progress=tprog)
        tdiag = _compact_tms_diag(tdiag)

        # An empty walk is only believable if the last good one was empty too and
        # no slice errored. Moveware answering 503 on every slice returns zero
        # rows without raising, which is exactly how the board lost all 45 TMS
        # shipments on 2026-09-11 without a single error on the page.
        slice_errs = tdiag.get("slice_errors", 0)
        if not ships and (cached or slice_errs):
            why = (f"Moveware returned no jobs ({slice_errs} of "
                   f"{tdiag.get('slices', 0)} slices failed)")
            log.warning("tms walk returned 0 rows (%d slice errors); keeping %d cached",
                        slice_errs, len(cached))
            with _LOCK:
                _TMS_CACHE.update(degraded=True, reason=why, diag={**tdiag, "degraded": True})
            if cached:
                return _tms_stale(cached, tdiag, now, why)
            # Nothing to fall back on — a restart during an outage starts cold.
            # Say so loudly rather than reporting a confident zero.
            return [], {**tdiag, "error": why, "degraded": True}

        with _LOCK:
            _TMS_CACHE.update(at=now, shipments=ships, diag=tdiag, degraded=False,
                              reason=None, good_at=now if ships else _TMS_CACHE["good_at"])
        return ships, tdiag
    except Exception as exc:  # noqa: BLE001 — never lose the ClickUp fleet over Moveware
        log.exception("tms fetch failed")
        why = f"{type(exc).__name__}: {exc}"
        with _LOCK:
            _TMS_CACHE.update(degraded=True, reason=why)
        return _tms_stale(cached, cdiag, now, why)


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
        if request.args.get("tms"):
            mode = request.args.get("tms")
            client = tms.MovewareClient(env=request.args.get("env"))
            if mode == "probe":
                out["tms"] = tms.probe(client, sample=int(request.args.get("sample", "3") or 3))
            elif mode == "get":
                # Read-only, allowlisted passthrough for shape discovery (never exposes creds).
                path = request.args.get("path", "/jobs?limit=3")
                if not path.startswith(("/jobs", "/codes", "/branches")):
                    out["tms"] = {"error": "path must start with /jobs, /codes or /branches"}
                    return jsonify(out)
                try:
                    body = client.get(path)
                    rows = body.get("jobs") if isinstance(body, dict) else None
                    out["tms"] = {"path": path, "rows": len(rows) if isinstance(rows, list) else None,
                                  "body": body if not isinstance(rows, list) or len(rows) <= 5 else {**body, "jobs": rows[:5]}}
                except tms.MovewareError as exc:
                    out["tms"] = {"path": path, "error": str(exc)}
                out["requests_made"] = client.requests_made
                return jsonify(out)
            else:
                ships, tdiag = tms.fetch_tms_shipments(
                    client, days=int(request.args.get("days", "30") or 30),
                    details=request.args.get("details", "1") != "0",
                    max_details=int(request.args.get("max_details", "10") or 10))
                out["tms"] = tdiag
                out["shipments"] = [x.to_dict() for x in ships]
            return jsonify(out)
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
        out["by_source"] = _count_by(shipments, lambda s: s.source.value)
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
                                    "errors": diag.get("errors"),
                                    "tms": {k: v for k, v in (diag.get("tms") or {}).items()
                                            if k in ("env", "count", "error", "rows_seen", "cross_border", "by_direction",
                                                     "details_fetched", "requests_made", "by_stage",
                                                     "stale", "stale_reason", "stale_since", "stale_age_s",
                                                     "slice_errors", "slice_error_sample", "cache_age_s")}},
                    "shipments": [s.to_dict() for s in shipments]})


@crossborder_bp.route("/crossborder/api/plan")
@_login_required
def api_plan():
    shipments, diag, status = load_shipments()
    try:
        p = engine.plan(shipments)
    except Exception as exc:  # noqa: BLE001
        log.exception("plan failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "loads": []}), 200
    p["status"] = status
    return jsonify(p)


def _plan_recipients() -> list[str]:
    raw = os.environ.get("PLAN_EMAIL_TO", "") or ""
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def create_plan_draft(actor: str = "scheduler") -> dict:
    """Build today's plan and file it as a DRAFT email (Graph). Never sends."""
    shipments, diag, status = load_shipments()
    if not shipments:
        return {"ok": False, "reason": "no shipments loaded yet"}
    p = engine.plan(shipments)
    subject, body = engine.email_body(p)
    to = _plan_recipients() or [os.environ.get("GRAPH_SENDER", "bbrill@thelsa.com")]
    try:
        from engine.mailer import GraphMailer   # the library's app-only Graph adapter (draft-only use here)
        mailer = GraphMailer()
        d = mailer.create_draft(to[0], subject, body, cc=to[1:],
                                folder=os.environ.get("PLAN_EMAIL_DRAFT_FOLDER") or None)
        out = {"ok": True, "to": to, "subject": subject, "draft_id": d.get("id"), "folder": d.get("folder"),
               "webLink": d.get("webLink"), "actor": actor, "trailers": p["summary"]["trailers"]}
    except Exception as exc:  # noqa: BLE001
        log.exception("plan draft failed")
        out = {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "to": to, "subject": subject}
    with _LOCK:
        _PLAN_STATE["last"] = {**out, "at": time.time()}
    return out


_PLAN_STATE: dict = {"last": None, "thread": None}
_ALERT_STATE: dict = {"last": None}


def create_alert_drafts(actor: str = "scheduler", respect_state: bool = True) -> dict:
    """One DRAFT per responsible person listing their shipments that need
    attention. Never sends; never writes to ClickUp or Moveware."""
    shipments, diag, status = load_shipments()
    if not shipments:
        return {"ok": False, "reason": "no shipments loaded yet", "actor": actor}
    try:
        out = alerts.create_drafts(shipments, actor=actor, respect_state=respect_state)
        out["ok"] = not any(d.get("ok") is False for d in out.get("drafts", []))
    except Exception as exc:  # noqa: BLE001
        log.exception("alert drafts failed")
        out = {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "actor": actor}
    with _LOCK:
        _ALERT_STATE["last"] = {**out, "at": time.time()}
    return out


def _daily_scheduler():
    """Once a day file the suggested-load draft (PLAN_EMAIL_HOUR, default 07:00,
    when PLAN_EMAIL_ENABLED=1) and the per-person alert drafts (CB_ALERTS_HOUR,
    default 08:00, when CB_ALERTS_ENABLED=1). Both create drafts only."""
    import datetime as _dt
    last_plan_day = None
    last_alert_day = None
    while True:
        try:
            now = _dt.datetime.now()
            if os.environ.get("PLAN_EMAIL_ENABLED") == "1":
                hour = int(os.environ.get("PLAN_EMAIL_HOUR", "7") or 7)
                if now.hour >= hour and last_plan_day != now.date():
                    ensure_fresh()
                    time.sleep(90)            # let the refresh land
                    create_plan_draft(actor="scheduler")
                    last_plan_day = now.date()
        except Exception:  # noqa: BLE001
            log.exception("plan scheduler")
        try:
            now = _dt.datetime.now()
            if alerts.alerts_enabled():
                hour = int(os.environ.get("CB_ALERTS_HOUR", "8") or 8)
                if now.hour >= hour and last_alert_day != now.date():
                    ensure_fresh()
                    time.sleep(90)
                    create_alert_drafts(actor="scheduler")
                    last_alert_day = now.date()
        except Exception:  # noqa: BLE001
            log.exception("alert scheduler")
        time.sleep(300)


def ensure_plan_scheduler():
    with _LOCK:
        if _PLAN_STATE["thread"] is None:
            t = threading.Thread(target=_daily_scheduler, daemon=True, name="crossborder-scheduler")
            t.start()
            _PLAN_STATE["thread"] = t


@crossborder_bp.route("/crossborder/api/alerts")
@_login_required
def api_alerts():
    """Preview the per-person alerts exactly as they would be drafted. Read-only."""
    shipments, diag, status = load_shipments()
    try:
        built = alerts.build_alerts(shipments)
    except Exception as exc:  # noqa: BLE001
        log.exception("alerts failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "alerts": []}), 200
    return jsonify({
        "as_of": time.strftime("%Y-%m-%d"),
        "status": status,
        "owner_count": len(built),
        "shipment_count": sum(a["shipment_count"] for a in built),
        "unresolved": [a["owner"] for a in built if not a["resolved"]],
        "alerts": built,
    })


@crossborder_bp.route("/crossborder/alerts/draft", methods=["POST"])
@_login_required
def alerts_draft():
    # A human pressed the button, so nothing is suppressed by the repeat window.
    return jsonify(create_alert_drafts(actor=session.get("user_email", "user"),
                                       respect_state=False))


@crossborder_bp.route("/crossborder/alerts/status")
@_login_required
def alerts_status():
    with _LOCK:
        last = _ALERT_STATE["last"]
    return jsonify({"enabled": alerts.alerts_enabled(),
                    "hour": os.environ.get("CB_ALERTS_HOUR", "8"),
                    "repeat_hours": os.environ.get("CB_ALERT_REPEAT_HOURS", "72"),
                    "tim_owner": alerts.owner_name_for_source(Source.TIM),
                    "tms_owner": alerts.owner_name_for_source(Source.TMS),
                    "last": last})


@crossborder_bp.route("/crossborder/plan/draft", methods=["POST"])
@_login_required
def plan_draft():
    return jsonify(create_plan_draft(actor=session.get("user_email", "user")))


@crossborder_bp.route("/crossborder/plan/status")
@_login_required
def plan_status():
    with _LOCK:
        last = _PLAN_STATE["last"]
    return jsonify({"enabled": os.environ.get("PLAN_EMAIL_ENABLED") == "1",
                    "hour": os.environ.get("PLAN_EMAIL_HOUR", "7"), "to": _plan_recipients(), "last": last})


@crossborder_bp.route("/crossborder")
@crossborder_bp.route("/crossborder/")
@_login_required
def index():
    """The dashboard page (renders client-side from /crossborder/api/shipments)."""
    ensure_fresh()   # warm the cache so the page has data by the time it asks
    ensure_plan_scheduler()
    return DASHBOARD_HTML
