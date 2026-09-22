"""
plan_history.py — remember what the Plan de Viajes said, so nobody needs a screenshot.

The problem (training, 21 Sep 2026)
-----------------------------------
Sara and Fernanda accept a service in the Plan de Viajes and later find it gone,
or moved to another day. Their defence today is to screenshot the workbook every
time they accept something, and keep the screenshots as proof. Edgar's reading
on 22 Sep is that some of this is people editing their own downloaded copy
rather than the shared file — but Sara still sees it happen, so the dashboard
should simply keep the receipts itself.

How it works
------------
The workbook is republished about three times a day and `sit.py` already fetches
the newest one. Every time it does, this module takes a fingerprint of Thelsa's
rows and compares it with the previous fingerprint:

    vanished       a trip that was in the plan and is no longer in it
    date_changed   same trip, different load day
    unit_changed   same trip, different truck
    new            a trip that was not there before

Changes accumulate in a small JSON file with a timestamp each, so the board can
answer "this service was on the plan at 09:14 for the 24th, and at 15:02 it had
moved to the 26th" — which is what the screenshots were for.

It is a record, not an alarm: nothing here emails anyone or changes the plan.
Only Thelsa's own rows (EJECUTIVO `TIM/…` or `TMS/…`) are tracked, which keeps
the file small and the noise out.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from typing import Optional

from .models import norm_text

__all__ = ["snapshot", "diff", "record", "history", "reset", "MAX_EVENTS"]

MAX_EVENTS = int(os.environ.get("CB_PLAN_HISTORY_MAX", "500") or 500)
_LOCK = threading.Lock()


def _state_path() -> str:
    override = (os.environ.get("CB_PLAN_HISTORY_PATH") or "").strip()
    if override:
        return override
    for c in ("/var/data", "/data"):
        if os.path.isdir(c) and os.access(c, os.W_OK):
            return os.path.join(c, "crossborder_plan_history.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "crossborder_plan_history.json")


def _load() -> dict:
    try:
        with open(_state_path()) as fh:
            data = json.load(fh) or {}
    except Exception:  # noqa: BLE001 — a missing or corrupt file just starts fresh
        return {"snapshot": {}, "events": [], "taken_at": None}
    data.setdefault("snapshot", {})
    data.setdefault("events", [])
    data.setdefault("taken_at", None)
    return data


def _save(data: dict) -> None:
    try:
        p = _state_path()
        with open(p + ".tmp", "w") as fh:
            json.dump(data, fh)
        os.replace(p + ".tmp", p)
    except Exception:  # noqa: BLE001 — history is a convenience, never load-bearing
        pass


def trip_key(t) -> str:
    """A stable identity for a trip across republications of the workbook.

    The Moveware job number is exact when it is there. TIM rows carry no
    reference at all, so they fall back to customer + route, which is what a
    coordinator would use to say "that is the same service".
    """
    if getattr(t, "job_no", ""):
        return f"job:{t.job_no}"
    parts = [norm_text(getattr(t, "customer", "")), norm_text(getattr(t, "origin", "")),
             norm_text(getattr(t, "destination", ""))]
    if not any(parts):
        return ""
    return "route:" + "|".join(parts)


def snapshot(trips: list) -> dict:
    """Fingerprint Thelsa's rows in the current plan: {key: facts}."""
    out: dict = {}
    for t in trips or []:
        if not getattr(t, "is_thelsa", False):
            continue
        key = trip_key(t)
        if not key:
            continue
        cur = {
            "customer": getattr(t, "customer", ""),
            "source": t.source.value if getattr(t, "source", None) else None,
            "coordinator": getattr(t, "coordinator", ""),
            "job_no": getattr(t, "job_no", ""),
            "origin": getattr(t, "origin", ""),
            "destination": getattr(t, "destination", ""),
            "tipo": getattr(t, "tipo", ""),
            "unit": getattr(t, "unit", ""),
            "load_date": t.load_date.isoformat() if getattr(t, "load_date", None) else None,
            "unload_date": t.unload_date.isoformat() if getattr(t, "unload_date", None) else None,
            "m3": getattr(t, "m3", 0.0),
        }
        prev = out.get(key)
        # A job split across two units shows as several rows. Keep the earliest
        # scheduled one — that is the date the coordinator is holding us to.
        if prev and (prev.get("load_date") or "9999") <= (cur.get("load_date") or "9999"):
            continue
        out[key] = cur
    return out


def diff(before: dict, after: dict) -> dict:
    """What changed between two fingerprints of the plan."""
    out: dict = {"vanished": [], "date_changed": [], "unit_changed": [], "new": []}
    for key, was in (before or {}).items():
        now = (after or {}).get(key)
        if now is None:
            out["vanished"].append({"key": key, "was": was})
            continue
        if was.get("load_date") != now.get("load_date"):
            out["date_changed"].append({"key": key, "from": was.get("load_date"),
                                        "to": now.get("load_date"), "trip": now})
        if was.get("unit") != now.get("unit") and (was.get("unit") or now.get("unit")):
            out["unit_changed"].append({"key": key, "from": was.get("unit"),
                                        "to": now.get("unit"), "trip": now})
    for key, now in (after or {}).items():
        if key not in (before or {}):
            out["new"].append({"key": key, "trip": now})
    return out


def _describe(kind: str, change: dict) -> str:
    t = change.get("trip") or change.get("was") or {}
    who = t.get("customer") or t.get("job_no") or change.get("key", "")
    route = " → ".join(x for x in (t.get("origin"), t.get("destination")) if x)
    if kind == "vanished":
        when = t.get("load_date") or "no date"
        return f"{who} ({route}) was on the plan for {when} and is no longer in it"
    if kind == "date_changed":
        return f"{who} ({route}) moved from {change.get('from') or 'no date'} to {change.get('to') or 'no date'}"
    if kind == "unit_changed":
        return f"{who} ({route}) changed truck from {change.get('from') or 'unassigned'} to {change.get('to') or 'unassigned'}"
    return f"{who} ({route}) added to the plan for {t.get('load_date') or 'no date'}"


def record(trips: list, when: Optional[dt.datetime] = None,
           workbook: str = "", track_new: bool = False) -> dict:
    """Compare this plan with the last one seen and file what changed.

    `track_new` is off by default: new services appear constantly and are not
    what anyone is worried about. Set CB_PLAN_HISTORY_TRACK_NEW=1 to keep them.
    """
    when = when or dt.datetime.now()
    track_new = track_new or (os.environ.get("CB_PLAN_HISTORY_TRACK_NEW") or "") in ("1", "true", "yes")
    cur = snapshot(trips)
    with _LOCK:
        data = _load()
        before = data.get("snapshot") or {}
        first_run = not before
        d = diff(before, cur) if before else {"vanished": [], "date_changed": [],
                                              "unit_changed": [], "new": []}
        events = data.get("events") or []
        added = []
        kinds = ["vanished", "date_changed", "unit_changed"] + (["new"] if track_new else [])
        for kind in kinds:
            for change in d.get(kind, []):
                added.append({"at": when.isoformat(timespec="seconds"), "kind": kind,
                              "workbook": workbook, "detail": _describe(kind, change),
                              **change})
        events = (events + added)[-MAX_EVENTS:]
        data.update(snapshot=cur, events=events,
                    taken_at=when.isoformat(timespec="seconds"), workbook=workbook)
        _save(data)
    return {"tracked": len(cur), "first_run": first_run,
            "changes": {k: len(d.get(k, [])) for k in ("vanished", "date_changed", "unit_changed", "new")},
            "recorded": len(added), "events": added}


def history(limit: int = 50, kinds: Optional[list] = None) -> dict:
    """The change log, newest first — what the screenshots used to prove."""
    with _LOCK:
        data = _load()
    events = list(reversed(data.get("events") or []))
    if kinds:
        events = [e for e in events if e.get("kind") in kinds]
    return {"taken_at": data.get("taken_at"), "workbook": data.get("workbook", ""),
            "tracked": len(data.get("snapshot") or {}),
            "count": len(events), "events": events[:max(1, limit)]}


def reset() -> None:
    """Forget everything (tests, or a deliberate fresh start)."""
    with _LOCK:
        _save({"snapshot": {}, "events": [], "taken_at": None})
