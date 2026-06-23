"""
alerts.py — surface failures.

The engine has Graph Mail.ReadWrite (drafts/read) but NOT Mail.Send, so it can't
auto-send an alert email. Failures are surfaced two ways instead:
  1. a `runs` row with status=failed (shown on the dashboard), written by each task
  2. an `activity_log` 'alert' entry (audit trail)
If Mail.Send is ever granted, draft_alert() can post a self-addressed draft too.
"""
import logging

logger = logging.getLogger("engine.alerts")


def notify(campaign_id, message):
    logger.error("ALERT [%s] %s", campaign_id, message)
    try:
        from . import sheets
        sheets.log_activity(campaign_id, "", "alert", message[:500])
    except Exception:
        logger.exception("failed to write alert to activity_log")
