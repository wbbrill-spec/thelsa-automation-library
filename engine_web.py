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
