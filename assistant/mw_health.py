"""
Moveware data health for the admin page: is the team entering what the app needs?

Counts and coordinator names only (no customers, no money):
  * missing / wrong key dates per coordinator (same rows as the daily email);
  * booked files with gaps and no coordinator;
  * planned Load / Delivery passed and still open, per coordinator;
  * job types on files opened in the last RECENT_DAYS, and any type that does not
    appear on older files: a setup change in Moveware (e.g. the unused TIM job
    types that were deactivated once and came back) shows up here first;
  * whether the reminder email is actually switched on.
"""
from __future__ import annotations

import datetime as _dt
import os

RECENT_DAYS = int(os.environ.get("ASSISTANT_HEALTH_RECENT_DAYS", "30"))


def _d(v):
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def job_types(files, today=None) -> dict:
    """{'recent': [(type, n)], 'new': [type]} — types on files created in the last
    RECENT_DAYS, and those never seen on an older file in the book."""
    today = today or _dt.date.today()
    cut = today - _dt.timedelta(days=RECENT_DAYS)
    recent, older = {}, set()
    for f in files or []:
        t = str(f.get("job_type") or "").strip().upper() or "(none)"
        c = _d(f.get("created"))
        if c and c >= cut:
            recent[t] = recent.get(t, 0) + 1
        elif c:
            older.add(t)
    new = sorted(t for t in recent if older and t not in older)
    return {"recent": sorted(recent.items(), key=lambda kv: (-kv[1], kv[0])), "new": new}


def gaps_by_coordinator(gaps) -> list:
    by = {}
    for g in gaps or []:
        name = g.get("coord") or "Unassigned"
        if not (g.get("coord_email") or "").strip():
            name = "Unassigned" if name == "Unassigned" else f"{name} (no email)"
        r = by.setdefault(name, {"coord": name, "files": 0, "no_dates": 0, "wrong_year": 0})
        r["files"] += 1
        r["no_dates"] += 1 if g.get("no_dates") else 0
        r["wrong_year"] += 1 if g.get("wrong_year") else 0
    return sorted(by.values(), key=lambda r: (-r["files"], r["coord"].lower()))


def open_confirms(files, today=None) -> dict:
    """Planned Load/Delivery passed and still open, per coordinator (from the
    diaries already read; unread files are counted separately)."""
    from . import mw_confirm
    by, unread = {}, 0
    coord = {str(f.get("job") or ""): (f.get("coordinator") or f.get("mw_coordinator") or "Unassigned")
             for f in files or []}
    for job, _jid, ms, _day, _t in mw_confirm.due(files, today):
        c = mw_confirm._confirmed(job)
        if c is None:
            unread += 1
            continue
        if mw_confirm._done(c, ms):
            continue
        name = coord.get(job) or "Unassigned"
        by[name] = by.get(name, 0) + 1
    return {"rows": sorted(by.items(), key=lambda kv: (-kv[1], kv[0].lower())), "unread": unread}


def alerts_state() -> dict:
    try:
        import date_gap_alerts as dga
        return {"enabled": dga.enabled(), "send": dga.send_enabled(),
                "auto": os.environ.get("DATE_ALERTS_AUTO", "1") == "1",
                "hour": os.environ.get("DATE_ALERTS_HOUR", "8"),
                "cc": ", ".join(dga._list("DATE_ALERT_CC", dga.DEFAULT_CC))}
    except Exception:
        return {"enabled": False, "send": False, "auto": False, "hour": "8", "cc": ""}


def summary() -> dict:
    """Everything for the admin panel; never raises."""
    out = {"live": False, "complete": False, "gaps": [], "gap_total": 0, "unassigned": 0,
           "confirms": {"rows": [], "unread": 0}, "types": {"recent": [], "new": []},
           "alerts": alerts_state()}
    try:
        import mw_live
        if not mw_live.have_creds():
            return out
        files = mw_live.audited_in_window() or []
    except Exception:
        return out
    from . import moveware
    gaps, complete = moveware._date_gaps()
    out["live"], out["complete"] = gaps is not None, complete
    out["gaps"] = gaps_by_coordinator(gaps)
    out["gap_total"] = len(gaps or [])
    out["unassigned"] = sum(1 for g in gaps or [] if not (g.get("coord_email") or "").strip())
    try:
        out["confirms"] = open_confirms(files)
    except Exception:
        pass
    out["types"] = job_types(files)
    return out
