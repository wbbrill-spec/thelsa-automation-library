"""
ClickUp (TIM — Thelsa International Movers) to-dos for move coordinators.

TIM's ClickUp workspace has ONE LIST PER SHIPMENT; its top-level tasks are the
numbered process steps (13 for DA, 17 for DTD Impo). The library's existing
reader (crossborder.tim) already walks the "Logistics Coordination" space with a
shared rate limiter and turns every list into a Shipment with its current step,
stage and days since the last completed step. We reuse it — one walk, cached
for ASSISTANT_TIM_CACHE_S (default 10 min), shared by every user.

Who sees which shipment (assignees in TIM's ClickUp are mostly blank):
  users.tim_scope = "all"       every active TIM shipment (e.g. the TIM owner)
  users.tim_scope = "assigned"  only shipments with a task assigned to them
                                (matched on email or name)        [default]
  users.tim_scope = "none"      no ClickUp items

Each active shipment becomes one item, "Next step: <current step>". Kinds:
  tim_docs     stage = docs pending (request / chase documents)
  tim_stalled  no step completed for CLICKUP_STALLED_DAYS (default 7)
  tim_step     anything else — the next step to move the file forward
"""

import datetime as _dt
import logging
import os
import threading
import time
import unicodedata

log = logging.getLogger("assistant.clickup")
_CACHE = {"at": 0.0, "shipments": None, "error": None}
_LOCK = threading.Lock()
TTL = int(os.environ.get("ASSISTANT_TIM_CACHE_S", "600"))

STAGE_LABEL = {
    "booked": "Booked", "docs_pending": "Documents pending", "green_light": "Green light",
    "in_transit_to_border": "To border", "customs_clearance": "Customs",
    "at_hub": "At Monterrey hub", "onward_leg": "Onward leg",
    "out_for_delivery": "Out for delivery", "delivered": "Delivered", "closed": "Closed",
    "unknown": "In progress",
}


def enabled() -> bool:
    return bool(os.environ.get("CLICKUP_TOKEN"))


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def shipments():
    """Active TIM shipments (cached). None if ClickUp isn't configured/reachable."""
    if not enabled():
        return None
    with _LOCK:
        fresh = _CACHE["shipments"] is not None and time.time() - _CACHE["at"] < TTL
        if fresh:
            return _CACHE["shipments"]
    try:
        from crossborder import tim
        ships, _diag = tim.fetch_tim_shipments(include_completed=False)
        with _LOCK:
            _CACHE.update(at=time.time(), shipments=ships, error=None)
        return ships
    except Exception as exc:  # keep last good walk if there is one
        log.warning("TIM ClickUp walk failed: %s", exc)
        with _LOCK:
            _CACHE["error"] = str(exc)
            return _CACHE["shipments"]


def _stage(s):
    st = getattr(s, "stage", "")
    return getattr(st, "value", st) or "unknown"


def _mine(s, user) -> bool:
    scope = (user.get("tim_scope") or "assigned").lower()
    if scope == "all":
        return True
    if scope == "none":
        return False
    keys = {_norm(user.get("email")), _norm((user.get("email") or "").split("@")[0]),
            _norm(user.get("name"))}
    keys.discard("")
    return any(_norm(a) in keys for a in (getattr(s, "assignees", None) or []))


def tasks_for_shipments(ships, user, stalled_days=None) -> list:
    stalled_days = stalled_days or int(os.environ.get("CLICKUP_STALLED_DAYS", "7") or 7)
    out = []
    for s in ships:
        stage = _stage(s)
        if stage in ("delivered", "closed") or not _mine(s, user):
            continue
        step = getattr(s, "current_step", "") or "Next step"
        days = getattr(s, "days_since_progress", None)
        flags = set(getattr(s, "status_flags", None) or [])
        if stage == "docs_pending":
            kind = "tim_docs"
        elif "stalled" in flags or (days is not None and days >= stalled_days):
            kind = "tim_stalled"
        else:
            kind = "tim_step"
        ref = getattr(s, "reference_number", "") or ""
        cust = getattr(s, "customer_name", "") or "Customer"
        progress = f"{getattr(s, 'steps_done', 0)}/{getattr(s, 'steps_total', 0)} steps"
        idle = f" · no progress for {days} day(s)" if days is not None else ""
        last = getattr(s, "last_progress_at", None)
        out.append({
            "kind": kind,
            "external_id": f"tim:{getattr(s, 'source_ref', '') or ref or cust}",
            "from_name": cust + (f" · {s.agent}" if getattr(s, "agent", "") else ""),
            "from_addr": None,
            "subject": f"{step} — {cust}{f' ({ref})' if ref else ''}",
            "snippet": f"{STAGE_LABEL.get(stage, stage)} · {progress}{idle}",
            "url": getattr(s, "url", None),
            "received_at": (_dt.datetime(last.year, last.month, last.day, 12,
                                         tzinfo=_dt.timezone.utc) if last else None),
            "meta": {"days": days, "stage": stage, "ref": ref, "step": step},
        })
    return out


def tasks_for_user(user):
    """None = ClickUp not available (caller keeps previous items)."""
    if (user.get("tim_scope") or "assigned").lower() == "none":
        return []
    ships = shipments()
    if ships is None:
        return None
    return tasks_for_shipments(ships, user)
