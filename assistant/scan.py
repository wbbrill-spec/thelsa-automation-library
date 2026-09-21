"""
Per-user scan + the 5×/day schedule.

The scheduler runs INSIDE the always-on library web service (same pattern as
engine_web's scheduler): it needs the in-process Moveware auditor cache anyway,
and it avoids a separate paid worker. A file lock makes sure only one gunicorn
worker runs it. Set ASSISTANT_SCHEDULER=0 to disable; /assistant/cron?token=…
(CRON_TOKEN) is an external backup trigger.

Times: 06:00, 09:00, 12:00, 15:00, 18:00 America/Mexico_City (override with
ASSISTANT_HOURS="6,9,12,15,18").
"""

import logging
import os
import threading
import time

from . import clickup_tim, db, graph, moveware, triage

log = logging.getLogger("assistant.scan")
TZ = "America/Mexico_City"
HOURS = os.environ.get("ASSISTANT_HOURS", "6,9,12,15,18")

_user_locks = {}
_locks_guard = threading.Lock()


def _lock_for(user_id):
    with _locks_guard:
        return _user_locks.setdefault(user_id, threading.Lock())


def scan_user(user_id: str) -> dict:
    """Scan one user's mailbox + Moveware. Never raises; returns a small report."""
    lock = _lock_for(user_id)
    if not lock.acquire(blocking=False):
        return {"user_id": user_id, "skipped": "already running"}
    report = {"user_id": user_id, "mail": None, "moveware": None, "errors": []}
    try:
        user = db.get_user(user_id)
        if not user or not user["active"]:
            report["skipped"] = "inactive"
            return report
        user = dict(user)

        conn = db.get_connection(user_id, "microsoft")
        if conn:
            try:
                msgs = [triage.normalize_graph(m) for m in graph.fetch_inbox(user_id)]
                found = triage.triage(msgs, conn["account_email"] or user["email"])
                db.replace_items(user_id, "microsoft", found)
                db.mark_connection(user_id, "microsoft", ok=True)
                report["mail"] = len(found)
            except graph.ReconnectNeeded as exc:
                db.mark_connection(user_id, "microsoft", ok=False, error=f"Reconnect needed: {exc}")
                report["errors"].append(f"mail: {exc}")
            except Exception as exc:  # network / Graph hiccup — keep last good items
                db.mark_connection(user_id, "microsoft", ok=False, error=str(exc))
                report["errors"].append(f"mail: {exc}")

        try:
            tasks = moveware.tasks_for_user(user)
            if tasks is not None:
                db.replace_items(user_id, "moveware", tasks)
                report["moveware"] = len(tasks)
        except Exception as exc:
            report["errors"].append(f"moveware: {exc}")
        try:
            tim_tasks = clickup_tim.tasks_for_user(user)
            if tim_tasks is not None:
                db.replace_items(user_id, "clickup", tim_tasks)
                report["clickup"] = len(tim_tasks)
        except Exception as exc:
            report["errors"].append(f"clickup: {exc}")
        return report
    finally:
        lock.release()


def scan_user_async(user_id: str):
    threading.Thread(target=scan_user, args=(user_id,), daemon=True,
                     name=f"asst-scan-{user_id[:8]}").start()


def run_all(trigger: str = "schedule") -> dict:
    run_id = db.start_run(trigger)
    ids = {u["id"] for u in db.users_to_scan("microsoft")}
    ids |= {u["id"] for u in db.list_users() if u["active"] and u["consent_at"]}
    scanned = errors = 0
    notes = []
    for uid in sorted(ids):
        rep = scan_user(uid)
        scanned += 1
        if rep.get("errors"):
            errors += 1
            notes.append(f"{uid[:8]}: {'; '.join(rep['errors'])[:200]}")
        time.sleep(float(os.environ.get("ASSISTANT_STAGGER_SECONDS", "2")))
    db.finish_run(run_id, scanned, errors, "\n".join(notes)[:4000] or None)
    log.info("assistant run %s: %s users, %s errors", trigger, scanned, errors)
    return {"run_id": run_id, "users_scanned": scanned, "errors": errors}


def run_all_async(trigger="manual"):
    threading.Thread(target=run_all, args=(trigger,), daemon=True, name="asst-run-all").start()


_sched = None
_sched_lock_fh = None


def start_scheduler():
    """Start the timezone-aware 5×/day scheduler once per host."""
    global _sched, _sched_lock_fh
    if os.environ.get("ASSISTANT_SCHEDULER", "1") != "1" or _sched is not None:
        return None
    try:
        import fcntl
        _sched_lock_fh = open("/tmp/thelsa_assistant_scheduler.lock", "w")
        fcntl.flock(_sched_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        return None  # another worker owns the scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        _sched = BackgroundScheduler(timezone=TZ)
        _sched.add_job(run_all, "cron", hour=HOURS, minute=0, id="assistant-run-all",
                       max_instances=1, coalesce=True, misfire_grace_time=1800)
        _sched.start()
        log.info("Assistant scheduler started: %s at %s", TZ, HOURS)
        try:  # warm the Moveware cache so the first run has coordinator files
            import mw_live
            mw_live.ensure_auditor()
        except Exception:
            pass
        return _sched
    except Exception as exc:
        log.warning("Assistant scheduler not started: %s", exc)
        return None


def next_runs(n=5):
    if _sched is None:
        return []
    job = _sched.get_job("assistant-run-all")
    return [str(job.next_run_time)] if job and job.next_run_time else []
