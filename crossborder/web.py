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
  /crossborder/api/plan            JSON: the consolidation engine's suggested loads,
                                   in two stages (border crossing, then onward),
                                   plus consolidations a coordinator already made.
  /crossborder/api/plan-history    JSON: Plan de Viajes services that vanished or
                                   changed date since the last republication.
  /crossborder/api/rules           JSON: which operational rules are live and what
                                   they are currently excluding (open this in training).
  /crossborder/api/metrics         JSON: the consolidation scoreboard and its history —
                                   utilisation, files per load, solo trucks, trucks avoided.
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

import datetime as dt
import functools
import logging
import os
import threading
import time

from flask import Blueprint, jsonify, redirect, request, session, url_for

from . import (alerts, census, clickup, demo, engine, fx, grouping, metrics,
               models, notices, plan_history, remisiones, rules, sit, tim, tms)
from .dashboard import DASHBOARD_HTML
from .models import Source

log = logging.getLogger(__name__)

crossborder_bp = Blueprint("crossborder", __name__)

_CACHE: dict = {"at": 0.0, "shipments": None, "diag": None, "completed": False,
                "refreshing": False, "progress": {}, "error": None, "started_at": 0.0}
# 10 minutes, not 5. Reading the consolidation notes made a full walk take
# two to four minutes; a cache shorter than the walk that fills it means the
# board is permanently mid-refresh. Shipment data does not change minute to
# minute, and ?refresh=1 is there when somebody wants it now.
_CACHE_TTL = int(os.environ.get("CROSSBORDER_CACHE_TTL", "600") or 600)
_LOCK = threading.Lock()


# The board-level exclusion rules live in rules.py, where the team's decisions
# are written down together. Kept here as a name because the rest of this module
# and its tests call it.
exclude_us_diplomatic = rules.exclude_us_diplomatic


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
            tms_ships, excluded = exclude_us_diplomatic(tms_ships)
            shipments = shipments + tms_ships
            diag["tms"] = {**tdiag, "excluded_us_diplomatic": excluded}
        # Everything else the team asked to keep off the board — today that is
        # commercial / new-furniture freight (Edgar, 22 Sep). Applied to both
        # sources at once, and always reported so a drop in the count is never
        # a mystery.
        shipments, excl = rules.board_exclusions(shipments)
        diag["excluded"] = excl
        # Link each consolidation note to the files it names. This has to run
        # after every source has been merged: Fernanda writes one note naming
        # seven other customers, and some of those are Moveware files, so the
        # names can only be resolved once TIM and TMS are on the same board.
        try:
            diag["consolidation_groups"] = grouping.resolve_groups(shipments)
        except Exception as exc:  # noqa: BLE001 — never lose the board over a note
            log.exception("consolidation grouping failed")
            diag["consolidation_groups"] = {"error": f"{type(exc).__name__}: {exc}"}
        # SIT "Plan de Viajes" — the Mexican onward leg (truck, driver, dates)
        # and the real trucks the engine can offer. A SIT failure must never
        # cost us the board, same rule as Moveware and Remisiones.
        if os.environ.get("SIT_ENABLED", "1") in ("1", "true", "yes"):
            try:
                diag["sit"] = _merge_sit(shipments)
            except Exception as exc:  # noqa: BLE001
                log.exception("sit merge failed")
                diag["sit"] = {"error": f"{type(exc).__name__}: {exc}"}
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
            return [], {"error": "MW_USERNAME / MW_PASSWORD not set"}
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


# The Plan de Viajes workbook is republished ~3x a day, so it is pulled on its
# own slow clock and the parsed trips are kept for the engine to plan against.
_SIT_CACHE: dict = {"at": 0.0, "trips": [], "fleet": {}, "diag": {}}
_SIT_TTL = int(os.environ.get("SIT_CACHE_TTL", "7200") or 7200)


def _sit_cached():
    now = time.time()
    with _LOCK:
        age = (now - _SIT_CACHE["at"]) if _SIT_CACHE["at"] else None
        if age is not None and age < _SIT_TTL:
            return list(_SIT_CACHE["trips"]), dict(_SIT_CACHE["fleet"]), {**_SIT_CACHE["diag"], "cache_age_s": int(age)}
    trips, fleet, info = sit.load_plan()
    if trips:
        # Remember what this republication of the plan said, so the board can
        # show a service that later vanishes or moves — the thing the team
        # currently keeps screenshots to prove (training, 21 Sep).
        try:
            info["history"] = plan_history.record(trips, workbook=str(info.get("file") or ""))
        except Exception as exc:  # noqa: BLE001 — a record-keeping nicety, never load-bearing
            log.warning("plan history failed: %s", exc)
            info["history"] = {"error": f"{type(exc).__name__}: {exc}"}
    with _LOCK:
        # Keep the last good plan rather than blanking the onward leg on a blip.
        if trips or not _SIT_CACHE["trips"]:
            _SIT_CACHE.update(at=now, trips=trips, fleet=fleet, diag=info)
            return trips, fleet, info
        return (list(_SIT_CACHE["trips"]), dict(_SIT_CACHE["fleet"]),
                {**_SIT_CACHE["diag"], "stale": True, "stale_reason": info.get("error") or "no trips returned"})


def _merge_sit(shipments: list) -> dict:
    """Hang each shipment's Mexican leg on it, and remember the trucks."""
    trips, fleet, info = _sit_cached()
    if not trips:
        return info
    plans = sit.job_plans(trips)
    m = sit.match_plans_to_shipments(plans, shipments)
    by_id = {s.id: s for s in shipments}
    for sid, jp in m["matches"].items():
        if sid in by_id:
            sit.enrich_shipment(by_id[sid], jp)
    trucks = sit.truck_loads(trips, fleet)
    with _LOCK:
        _SIT_CACHE["trucks"] = trucks
    return {**info, **m["diag"], "trips": len(trips), "fleet": len(fleet),
            "trucks": len(trucks), "spare_by_hub": sit.spare_by_hub(trucks)}


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
        "MW_CREDS": all(os.environ.get(k) for k in ("MW_USERNAME", "MW_PASSWORD")),
        "MW_BASE_URL": tms.BASE_URLS.get(os.environ.get("TMS_MW_ENV", "prod").lower(), ""),
    }


def load_shipments(force: bool = False, include_completed: bool = False):
    """Cached shipments (may be empty while the first refresh runs)."""
    status = ensure_fresh(force=force, include_completed=include_completed)
    with _LOCK:
        return list(_CACHE["shipments"] or []), dict(_CACHE["diag"] or {}), status


def _with_demo(shipments: list, diag: dict, args=None):
    """Mix the simulated fleet in for THIS REQUEST only.

    Demo rows are added at the edge and never written back into _CACHE, so
    nothing that can reach a mailbox — the alert drafter, the suggested-load
    email, the daily scheduler — can ever see one. See demo.py.
    """
    mode = demo.demo_mode(args)
    if mode == "off":
        return shipments, diag
    fake = demo.demo_shipments()
    out = list(fake) if mode == "only" else list(shipments) + list(fake)
    return out, {**diag, "demo": {**demo.diagnostics(fake), "mode": mode}}


def _demo_blocks_drafting() -> "str | None":
    """Drafting is refused outright while the environment is in demo mode —
    simulated shipments must never turn into an email a coordinator acts on."""
    if demo.demo_mode(None) != "off":
        return ("Demo mode is on (CROSSBORDER_DEMO). Email drafting is disabled "
                "so simulated shipments can never reach an inbox — unset it to "
                "draft against live data.")
    return None


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
        shipments, diag = _with_demo(shipments, diag, request.args)
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
    shipments, diag = _with_demo(shipments, diag, request.args)
    return jsonify({"count": len(shipments), "status": status, "fx": fx.describe(),
                    "diagnostics": {"remisiones": diag.get("remisiones"), "requests_made": diag.get("requests_made"),
                                    "errors": diag.get("errors"), "demo": diag.get("demo"), "sit": diag.get("sit"),
                                    "excluded": diag.get("excluded"),
                                    "consolidation_notes": diag.get("consolidation_notes"),
                                    "consolidation_groups": diag.get("consolidation_groups"),
                                    "tms": {k: v for k, v in (diag.get("tms") or {}).items()
                                            if k in ("env", "count", "error", "rows_seen", "cross_border", "by_direction",
                                                     "details_fetched", "requests_made", "by_stage", "excluded_us_diplomatic",
                                                     "stale", "stale_reason", "stale_since", "stale_age_s",
                                                     "slice_errors", "slice_error_sample", "cache_age_s")}},
                    "shipments": [s.to_dict() for s in shipments]})


@crossborder_bp.route("/crossborder/api/plan")
@_login_required
def api_plan():
    shipments, diag, status = load_shipments()
    shipments, diag = _with_demo(shipments, diag, request.args)
    try:
        p = engine.plan(shipments)
    except Exception as exc:  # noqa: BLE001
        log.exception("plan failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "loads": []}), 200
    p["status"] = status
    p["demo"] = diag.get("demo")
    # Name the real SIT trucks that could carry each suggested load, and the
    # empty space already heading to each hub. Advisory only — nothing books.
    with _LOCK:
        trucks = list(_SIT_CACHE.get("trucks") or [])
    if trucks and not diag.get("demo"):
        try:
            p["loads"] = sit.offer_trucks(p.get("loads") or [], trucks)
            p["spare_by_hub"] = sit.spare_by_hub(trucks)
            p["trucks_considered"] = len(trucks)
        except Exception as exc:  # noqa: BLE001
            log.exception("truck offers failed")
            p["trucks_error"] = f"{type(exc).__name__}: {exc}"
    # The consolidation scoreboard, and one point a day on the curve. Demo rows
    # must never reach the history — it is the record the programme is judged on.
    try:
        p["metrics"] = metrics.summarise(p)
        if not diag.get("demo"):
            metrics.record(p["metrics"])
        p["metrics"]["history"] = metrics.history()
    except Exception as exc:  # noqa: BLE001
        log.exception("metrics failed")
        p["metrics"] = {"error": f"{type(exc).__name__}: {exc}"}
    # Which border each import crosses (D1/D17, 23 Sep). Policy is now McAllen
    # for everything, and this is how we find out whether that actually
    # happened rather than assuming it did. "recorded" counts only the files
    # where somebody wrote it down; TIM's McAllen is policy, not evidence.
    try:
        # Land imports only. Sea freight has no land port of entry — it comes
        # through Veracruz by definition — so counting it here inflated
        # "not recorded" from 10 to 25 and made the McAllen switch look worse
        # than it is. The two ports are different questions.
        imports = [s for s in shipments
                   if (s.extra or {}).get("direction") != "export" and not s.is_sea]
        ports: dict = {}
        recorded = 0
        for s in imports:
            port = s.port_of_entry or "not recorded"
            ports[port] = ports.get(port, 0) + 1
            if (s.extra or {}).get("port_of_entry"):
                recorded += 1
        p["ports"] = {"by_port": ports, "recorded": recorded, "imports": len(imports),
                      "policy": "McAllen"}
    except Exception as exc:  # noqa: BLE001
        log.exception("port split failed")
    return jsonify(p)


@crossborder_bp.route("/crossborder/api/trucks")
@_login_required
def api_trucks():
    """SIT trucks in the horizon with their booked and spare m³."""
    load_shipments()                      # make sure a refresh has run at least once
    with _LOCK:
        trucks = list(_SIT_CACHE.get("trucks") or [])
        diag = dict(_SIT_CACHE.get("diag") or {})
    return jsonify({"count": len(trucks), "spare_by_hub": sit.spare_by_hub(trucks),
                    "diagnostics": diag, "trucks": trucks})


@crossborder_bp.route("/crossborder/api/metrics")
@_login_required
def api_metrics():
    """Is consolidation improving? Today's scoreboard plus the curve."""
    try:
        limit = max(2, min(int(request.args.get("days", "90") or 90), 400))
    except ValueError:
        limit = 90
    return jsonify({"history": metrics.history(limit=limit),
                    "truck_cost_mxn": metrics.truck_cost(),
                    "small_lot_mxn": metrics.small_lot_cost(),
                    "confirmed": notices.recent(limit=200),
                    "capacity_m3": models.TRUCK_53_M3})


# ── "these travel together" (Bill, consolidation meeting 23 Sep — D32) ───────
@crossborder_bp.route("/crossborder/api/consolidation/notice", methods=["POST"])
@_login_required
def api_consolidation_notice():
    """Tick the shipments that are going on one truck; get a notice to send.

    Drafts only. A consolidation notice asks another company to hold space on
    a truck — it is precisely the kind of message that must not leave without
    a person having read it.
    """
    payload = request.get_json(silent=True) or {}
    ids = [str(x) for x in (payload.get("ids") or []) if x]
    if len(ids) < 2:
        return jsonify({"error": "pick at least two shipments to consolidate"}), 400
    wanted = set(ids)
    picked, lane = [], str(payload.get("lane") or "")
    shipments, diag, _ = load_shipments()
    shipments, diag = _with_demo(shipments, diag, request.args)
    try:
        current = engine.plan(shipments)
    except Exception as exc:  # noqa: BLE001
        log.exception("plan failed while drafting a consolidation notice")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500
    seen: set = set()

    def _take(it: dict, from_lane: str):
        nonlocal lane
        if it.get("id") in wanted and it.get("id") not in seen:
            seen.add(it["id"])
            picked.append(dict(it))
            lane = lane or from_lane

    for ld in (current or {}).get("loads") or []:
        for it in ld.get("shipments") or []:
            _take(it, ld.get("lane", ""))
    # Also the trucks a coordinator is ALREADY filling, and the files that
    # could still join one. Without this the notice worked on suggestions but
    # not on the consolidation Fernanda had actually made — which is the one
    # place a coordinator most wants to add a file and tell people about it.
    for g in (current or {}).get("groups") or []:
        for it in (g.get("members") or []) + (g.get("could_join") or []):
            _take(it, g.get("lane", ""))
    found = {p.get("id") for p in picked}
    missing = [i for i in ids if i not in found]
    if len(picked) < 2:
        return jsonify({"error": "those shipments are not on the current plan",
                        "missing": missing}), 409
    draft = notices.build(picked, lane, actor=session.get("user_email", "user"),
                          note=str(payload.get("note") or ""))
    if payload.get("record", True):
        draft["recorded"] = notices.record(draft)
    draft["missing"] = missing
    return jsonify(draft)


@crossborder_bp.route("/crossborder/api/census")
@_login_required
def api_census():
    """How many files TIM opened, by month. Counts finished files too.

    Deliberately on demand only: it costs one ClickUp request per shipment
    list, which is well over the per-minute ceiling and takes a couple of
    minutes behind the rate limiter. Never called from a page load.
    """
    try:
        year = int(request.args.get("year", "") or dt.date.today().year)
    except ValueError:
        year = dt.date.today().year
    try:
        return jsonify(census.census(year=year))
    except Exception as exc:  # noqa: BLE001
        log.exception("census failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500


@crossborder_bp.route("/crossborder/api/consolidation/notices")
@_login_required
def api_consolidation_notices():
    """What the team has actually confirmed — the scorecard's honest half."""
    try:
        days = max(1, min(int(request.args.get("days", "30") or 30), 365))
    except ValueError:
        days = 30
    return jsonify(notices.recent(days=days))


@crossborder_bp.route("/crossborder/api/plan-history")
@_login_required
def api_plan_history():
    """What changed in the Plan de Viajes since the last republication.

    Services that vanish or move date are what the team screenshots today.
    ?kind=vanished,date_changed narrows it; ?limit=N caps the list.
    """
    kinds = [k.strip() for k in (request.args.get("kind") or "").split(",") if k.strip()]
    try:
        limit = max(1, min(int(request.args.get("limit", "50") or 50), 500))
    except ValueError:
        limit = 50
    try:
        out = plan_history.history(limit=limit, kinds=kinds or None)
    except Exception as exc:  # noqa: BLE001
        log.exception("plan history read failed")
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "events": []}), 200
    return jsonify(out)


@crossborder_bp.route("/crossborder/api/rules")
@_login_required
def api_rules():
    """Which operational rules are live right now, and what they are doing.

    This is the page to open in a training session: it says in one place what
    the dashboard leaves out and why, rather than leaving the team to infer it
    from a shipment count.
    """
    shipments, diag, status = load_shipments()
    return jsonify({
        "board_exclusions": diag.get("excluded") or {},
        "labels": rules.EXCLUSION_LABELS,
        "commercial_patterns": rules.commercial_patterns(),
        "consolidation_note_format": grouping.SUGGESTED_FORMAT,
        "settings": {
            "entry_hub": engine.ENTRY_HUB.value,
            "crossing_origin": engine.CROSSING_ORIGIN,
            "detour_stops": {h.value: d["stop"] for h, d in models.DETOUR_STOPS.items()},
            "thelsa_truck_u_boxes": engine.THELSA_TRUCK_U_BOXES,
            "trailer_u_boxes": engine.TRUCK_53_U_BOXES,
            "trailer_lift_vans": engine.TRUCK_53_LIFT_VANS,
            "exports_ship_alone": not engine._hold_exports(),
            "consolidate_door_to_door": os.environ.get("CB_CONSOLIDATE_DTD", "") in ("1", "true", "yes"),
            "show_us_diplomatic": os.environ.get("CROSSBORDER_SHOW_DIPLOMATIC", "") in ("1", "true", "yes"),
        },
        "consolidation_notes": diag.get("consolidation_notes"),
        "consolidation_groups": diag.get("consolidation_groups"),
        "status": status,
    })


@crossborder_bp.route("/crossborder/api/invoices/<job_id>")
@_login_required
def api_invoices(job_id: str):
    """The invoice(s) raised against one Moveware job, fetched on demand.

    Deliberately NOT part of the refresh: it is one extra call per job, and the
    board only needs it when somebody opens a shipment. Read-only.

    `outstanding` is returned because Moveware sends it, but it is NOT a
    receivables figure: Moveware is not Thelsa's accounting system of record
    (Bill, 2026-09-16), payments are booked elsewhere, and so almost every
    invoice here reads as fully unpaid. The UI shows the invoiced amount and
    says plainly that payment status lives in another system. Do not build an
    AR metric on this field without checking where cash is actually recorded.
    """
    jid = str(job_id or "").strip()
    if not jid.isdigit():
        return jsonify({"error": "job id must be numeric"}), 400
    if jid.startswith("DEMO") or demo.demo_mode(request.args) == "only":
        return jsonify({"job_id": jid, "invoices": [], "demo": True})
    try:
        client = tms.MovewareClient()
        body = client.get(f"/jobs/{jid}/invoices")
    except Exception as exc:  # noqa: BLE001
        log.warning("invoice fetch failed for %s: %s", jid, exc)
        return jsonify({"job_id": jid, "error": f"{type(exc).__name__}: {exc}", "invoices": []}), 200
    rows = body.get("invoices") if isinstance(body, dict) else None
    out, invoiced, outstanding, currency = [], 0.0, 0.0, ""
    for inv in rows or []:
        if not isinstance(inv, dict):
            continue
        cur = fx.normalize_currency(inv.get("currency"))
        currency = currency or cur
        value = tms.to_number(inv.get("valueInclusive")) or 0.0
        owed = tms.to_number(inv.get("outstanding")) or 0.0
        invoiced += value
        outstanding += owed
        out.append({"id": inv.get("id"), "number": _clean(inv.get("number")), "date": inv.get("date"),
                    "status": _clean(inv.get("status")), "currency": cur,
                    "value": round(value, 2), "outstanding": round(owed, 2),
                    "description": _clean(inv.get("description"))[:120]})
    return jsonify({"job_id": jid, "currency": currency, "count": len(out),
                    "invoiced": round(invoiced, 2), "outstanding": round(outstanding, 2),
                    "invoices": out})


def _clean(v) -> str:
    return str(v or "").strip()


def _plan_recipients() -> list[str]:
    raw = os.environ.get("PLAN_EMAIL_TO", "") or ""
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def create_plan_draft(actor: str = "scheduler") -> dict:
    """Build today's plan and file it as a DRAFT email (Graph). Never sends."""
    blocked = _demo_blocks_drafting()
    if blocked:
        return {"ok": False, "reason": blocked, "actor": actor}
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
    blocked = _demo_blocks_drafting()
    if blocked:
        return {"ok": False, "reason": blocked, "skipped_reason": blocked, "actor": actor}
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
