"""
Urgency scoring — puts every item (mail, Moveware, WhatsApp) on one 0–100 scale
so the dashboard can list everything in order of urgency.

  Urgent  ≥ 75   do first
  Today   50–74
  Soon    < 50
"""

import datetime as _dt
import json

TIERS = (("urgent", 75, "Urgent"), ("today", 50, "Today"), ("soon", 0, "Soon"))


def _meta(item) -> dict:
    m = item.get("meta")
    if isinstance(m, dict):
        return m
    try:
        return json.loads(m or "{}")
    except (TypeError, ValueError):
        return {}


def _age_hours(item, now):
    r = item.get("received_at")
    if not r:
        return 0
    if r.tzinfo is None:
        r = r.replace(tzinfo=_dt.timezone.utc)
    return max(0.0, (now - r).total_seconds() / 3600)


def score(item, now=None) -> int:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    kind, m = item.get("kind"), _meta(item)
    value = float(m.get("value") or 0)
    if kind == "invoice_file":
        s = 60 + min(int(m.get("days") or 0) * 2, 30) + (5 if value >= 5000 else 0)
    elif kind == "invoice_charge":
        s = 62 + (8 if value >= 500 else 0)
    elif kind == "upload_docs":
        s = 55 + min(int(m.get("days") or 0) * 4, 35)
    elif kind == "request_docs":
        s = 35 + max(0, 10 - int(m.get("days_left") or 0)) * 5
    elif kind == "tim_docs":
        s = 50 + min(int(m.get("days") or 0) * 4, 40)
    elif kind == "tim_stalled":
        s = 55 + min(int(m.get("days") or 0) * 2, 35)
    elif kind == "tim_step":
        s = 30 + min(int(m.get("days") or 0) * 4, 25)
    elif kind == "needs_reply":
        s = 45 + min(int(_age_hours(item, now) / 6), 25)
        if m.get("importance") == "high":
            s += 15
    elif kind == "flagged":
        s = 50 + min(int(_age_hours(item, now) / 24) * 3, 20)
    elif kind == "whatsapp":
        s = 50 + min(int(m.get("unread") or 1) * 3, 20)
    else:
        s = 30
    if item.get("seen"):
        s -= 20
    return max(0, min(100, int(s)))


def tier(s: int) -> str:
    for key, floor, _ in TIERS:
        if s >= floor:
            return key
    return "soon"


def rank(items, now=None) -> list:
    """Items as dicts with "score" and "tier", most urgent first."""
    out = []
    for it in items:
        d = dict(it)
        d["meta"] = _meta(it)
        d["score"] = score(d, now)
        d["tier"] = tier(d["score"])
        out.append(d)
    def _when(d):
        r = d.get("received_at")
        if not r:
            return _dt.datetime(2999, 1, 1, tzinfo=_dt.timezone.utc)
        return r if r.tzinfo else r.replace(tzinfo=_dt.timezone.utc)
    return sorted(out, key=lambda d: (-d["score"], _when(d)))
