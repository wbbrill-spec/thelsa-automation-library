"""
"Did it happen?" — the day after a planned Load or Delivery, ask the coordinator
to confirm it in Moveware.

Since the 16 Oct 2026 training, Load/Pack and Delivery are auto-created with a
PLANNED date, and Elizabeth asked not to type dates twice. Jose's answer, backed
by Bill: enter the planned date, then mark the action completed (correcting the
date if it moved) when it happens. A planned date that has passed and is still
open in Moveware is therefore unfinished data entry: either the move happened and
nobody closed the action, or it moved and nobody changed the date. Both make the
reports wrong, so it goes on the coordinator's dashboard until it is fixed.

  confirm_done   planned Load (key action Uplift; Pack stands in only when the
                 file has no Load action) or Delivery between 1 and LOOKBACK days
                 ago, file booked (W), action not marked completed in Moveware.

Job-type rules as everywhere else (job_rules): no Delivery on EXA, no Pack/Load
on IMA. Read-only: the diary is read from GET /jobs/{id}/activities in a
background thread, at most MAX_FETCH files per pass, each re-read after TTL. A
file whose diary has not been read yet produces NO item — never accuse on a guess.
"""
from __future__ import annotations

import datetime as _dt
import os
import threading
import time

LOOKBACK = int(os.environ.get("ASSISTANT_CONFIRM_DAYS", "14"))
TTL = int(os.environ.get("ASSISTANT_CONFIRM_TTL_S", str(6 * 3600)))
MAX_FETCH = int(os.environ.get("ASSISTANT_CONFIRM_MAX", "200"))

_CACHE: dict = {}          # job → {"load","pack","delivery","has_load","at"}
_STATE = {"running": False, "last": None, "read": 0, "errors": 0}
_LOCK = threading.Lock()


def _as_date(v):
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, str) and v:
        try:
            return _dt.date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def due(files, today=None) -> list:
    """(job, job_id, milestone, planned_date, job_type) whose planned day has
    passed within LOOKBACK days, on booked files, where the date is expected."""
    import job_rules
    today = today or _dt.date.today()
    lo = today - _dt.timedelta(days=LOOKBACK)
    out = []
    for f in files or []:
        if str(f.get("status") or "").upper() != "W":
            continue
        job = str(f.get("job") or "")
        if not job:
            continue
        jt = f.get("job_type") or ""
        for ms, field in (("load", "pack"), ("delivery", "delivery")):
            if not job_rules.expected("pack" if ms == "load" else ms, jt):
                continue
            d = _as_date(f.get(field))
            if d and lo <= d < today:
                out.append((job, f.get("job_id"), ms, d, jt))
    return out


def _confirmed(job):
    c = _CACHE.get(job)
    if c is None:
        try:                                   # the audit reads the same diaries
            import audit_web
            c = audit_web._CONFIRMED["by_job"].get(job)
        except Exception:
            c = None
    if c and time.time() - c.get("at", 0) <= TTL * 2:
        return c
    return None


def _done(c, ms) -> bool:
    if ms == "load":
        # The LOAD date counts (Bill, 16 Oct). A completed Pack stands in only
        # when the file has no Load action at all.
        return bool(c.get("load") or (c.get("pack") and not c.get("has_load")))
    return bool(c.get("delivery"))


def items(files, today=None) -> list:
    """Dashboard items for these files (one coordinator's). Queues a background
    read for any due file whose diary is unknown or stale."""
    today = today or _dt.date.today()
    out, need = [], []
    for job, jid, ms, d, jt in due(files, today):
        c = _confirmed(job)
        if c is None or time.time() - c.get("at", 0) > TTL:
            if jid:
                need.append((job, jid))
        if c is None or _done(c, ms):
            continue
        days = (today - d).days
        meta = {"job": job, "milestone": ms, "date": d.isoformat(), "days": days, "type": jt}
        item = {"kind": "confirm_done", "external_id": f"{job}:confirm:{ms}", "from_name": "Moveware",
                "from_addr": None, "url": None, "received_at": None, "meta": meta}
        from . import i18n
        item["subject"], item["snippet"] = i18n.item_text(item, "en")
        out.append(item)
    if need:
        refresh(need)
    return out


def _fetch(pairs):
    import job_rules
    import mw_live
    read = errs = 0
    try:
        for job, jid in pairs[:MAX_FETCH]:
            try:
                body = mw_live._get_timed(f"/jobs/{jid}/activities", 15) or {}
                acts = body.get("activities") or []
                _CACHE[job] = {**job_rules.confirmed_dates(acts), "at": time.time()}
                read += 1
            except Exception:
                errs += 1
    finally:
        _STATE.update(running=False, last=time.time(), read=read, errors=errs)


def refresh(pairs, wait=False):
    """Read these files' diaries in the background (one pass at a time)."""
    seen, uniq = set(), []
    for job, jid in pairs:
        if job not in seen:
            seen.add(job)
            uniq.append((job, jid))
    with _LOCK:
        if _STATE["running"]:
            return False
        _STATE["running"] = True
    t = threading.Thread(target=_fetch, args=(uniq,), daemon=True, name="asst-confirm")
    t.start()
    if wait:
        t.join()
    return True


def status() -> dict:
    return {"cached": len(_CACHE), **_STATE}


def prewarm() -> int:
    """Before a scheduled run: read the diaries of every due file in the book
    (not just one coordinator's), so every dashboard in the run is current.
    Blocks; bounded by MAX_FETCH reads. Returns how many were queued."""
    try:
        import mw_live
        if not mw_live.have_creds():
            return 0
        files = mw_live.audited_in_window() or []
    except Exception:
        return 0
    need = [(job, jid) for job, jid, *_ in due(files)
            if jid and (job not in _CACHE or time.time() - _CACHE[job].get("at", 0) > TTL)]
    if need:
        refresh(need, wait=True)
    return len(need)
