"""
engine_web.py — thin HTTP triggers for the outreach engine, registered on the
library Flask app.

  GET /admin/graph-test   login-gated: create ONE test draft (connectivity check)
  GET /cron/draft?token=  token-gated: run the drafting task for active campaigns
  GET /cron/monitor?token= token-gated: run the monitoring task

The /cron/* routes are meant to be hit on a schedule by an external trigger
(Render Cron Job / GitHub Actions / uptime pinger) using the CRON_TOKEN secret.
Engine imports are lazy so the app still boots if the engine isn't configured.
"""
import functools
import os

from flask import Blueprint, request, session, redirect, url_for, jsonify

engine_bp = Blueprint("engine_web", __name__)


def _login_required(f):
    @functools.wraps(f)
    def wrapped(*a, **k):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.url))
        return f(*a, **k)
    return wrapped


def _token_ok():
    tok = os.environ.get("CRON_TOKEN", "")
    return bool(tok) and request.args.get("token", "") == tok


@engine_bp.route("/admin/graph-test")
@_login_required
def graph_test():
    """Create one harmless test draft in the mailbox to verify Graph access."""
    try:
        from engine.mailer import GraphMailer
        to = request.args.get("to") or os.environ.get("MAILBOX", "bbrill@thelsa.com")
        d = GraphMailer().create_draft(
            to, "[TEST] Thelsa outreach engine — connectivity check",
            "This is a test draft created by the agent-outreach engine to confirm "
            "Microsoft Graph access (Mail.ReadWrite). If you can see this in Outlook "
            "Drafts, the connection works. You can delete it. No campaign mail was sent.")
        return jsonify({"ok": True, "draft": d})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@engine_bp.route("/cron/draft")
def cron_draft():
    if not _token_ok():
        return ("forbidden", 403)
    from engine.run import main
    return jsonify(main("draft"))


@engine_bp.route("/cron/monitor")
def cron_monitor():
    if not _token_ok():
        return ("forbidden", 403)
    from engine.run import main
    return jsonify(main("monitor"))


# ── server-side scheduler ─────────────────────────────────────────────────────
# Runs the engine on a schedule inside this always-on web service, so drafting and
# monitoring happen in the cloud — no external trigger, no dependency on any local
# machine. A file lock ensures only ONE gunicorn worker runs the scheduler, and the
# engine's own idempotency (draft_created flag, daily cap, processed-message ledger)
# makes a stray double-run harmless anyway. Set ENABLE_SCHEDULER=0 to disable.
_sched_lock = None
_scheduler = None


def _sched_draft():
    try:
        from engine.run import main
        print("[scheduler] draft:", main("draft"), flush=True)
    except Exception as exc:
        print("[scheduler] draft error:", exc, flush=True)


def _sched_monitor():
    try:
        from engine.run import main
        print("[scheduler] monitor:", main("monitor"), flush=True)
    except Exception as exc:
        print("[scheduler] monitor error:", exc, flush=True)


def _start_scheduler():
    global _sched_lock
    if os.environ.get("ENABLE_SCHEDULER", "1") != "1":
        return None
    try:  # single-process gate across gunicorn workers
        import fcntl
        _sched_lock = open("/tmp/thelsa_scheduler.lock", "w")
        fcntl.flock(_sched_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        return None  # another worker already owns the scheduler
    try:
        import pytz
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        tz = pytz.timezone("America/Chicago")
        sched = BackgroundScheduler(timezone=tz, daemon=True)
        sched.add_job(_sched_draft,
                      CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone=tz),
                      id="draft", replace_existing=True, misfire_grace_time=3600, coalesce=True)
        sched.add_job(_sched_monitor,
                      CronTrigger(hour="8,16", minute=0, timezone=tz),
                      id="monitor", replace_existing=True, misfire_grace_time=3600, coalesce=True)
        sched.start()
        print("[scheduler] started: draft Mon-Fri 07:30 CT; monitor 08:00 & 16:00 CT",
              flush=True)
        return sched
    except Exception as exc:
        print("[scheduler] failed to start:", exc, flush=True)
        return None


_scheduler = _start_scheduler()
