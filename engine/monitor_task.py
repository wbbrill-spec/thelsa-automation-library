"""
monitor_task.py — Task B: scan the mailbox, classify inbound mail, update state.

Idempotent: every message acted on is logged to activity_log by message_id and
skipped on later runs. Bookings are flagged for human confirmation, never
auto-finalized.
"""
from datetime import date, timedelta

from . import config, sheets, classify


def run(campaign_id, mailer, today=None, run_id=None, since_days=7):
    today = today or date.today()
    counts = {"rate_requests": 0, "bookings_flagged": 0, "unsubscribes": 0,
              "bounces": 0, "replies": 0, "leads": 0, "seen": 0}
    try:
        phrases = classify.phrases_by_category(sheets.read_tab("signal_phrases"))
        processed = sheets.processed_message_ids()
        members = [m for m in sheets.read_tab("campaign_members")
                   if m.get("campaign_id") == campaign_id]
        by_email = {(m.get("email") or "").lower(): m for m in members}
        contact_emails = {(c.get("email") or "").lower()
                          for c in sheets.read_tab("contacts")}

        since = (today - timedelta(days=since_days)).strftime("%Y-%m-%dT00:00:00Z")
        msgs = mailer.scan_inbox(since) if mailer else []

        for msg in msgs:
            mid = msg.get("message_id", "")
            if not mid or mid in processed:
                continue
            frm = (msg.get("from") or "").lower()
            subj, body = msg.get("subject", ""), msg.get("body", "")
            member = by_email.get(frm)

            def upd(fields):
                if member:
                    sheets.update_fields("campaign_members", member["_row"], fields)

            if classify.is_bounce(frm):
                sheets.append_row("suppression", {"email_or_domain": frm, "reason": "bounce",
                    "date": today.isoformat(), "source_campaign": campaign_id})
                upd({"stage": "bounced"})
                sheets.log_activity(campaign_id, frm, "bounce", subj, mid)
                counts["bounces"] += 1
                continue

            cat = classify.classify(subj, body, phrases)

            if cat == "unsubscribe":
                sheets.append_row("suppression", {"email_or_domain": frm, "reason": "unsubscribe",
                    "date": today.isoformat(), "source_campaign": campaign_id})
                upd({"stage": "stopped"})
                sheets.log_activity(campaign_id, frm, "unsubscribe", subj, mid)
                counts["unsubscribes"] += 1

            elif cat == "booking":
                sheets.log_activity(campaign_id, frm, "booking_signal",
                                    f"LIKELY BOOKING (confirm): {subj}", mid)
                counts["bookings_flagged"] += 1  # human confirms; never auto-finalized

            elif cat == "rate_request" and classify.is_original_thread(subj):
                sheets.append_row("rate_requests", {
                    "request_id": f"rr_{mid[-12:] or today.isoformat()}",
                    "campaign_id": campaign_id if member else "",
                    "contact_id": member.get("contact_id", "") if member else "",
                    "date": today.isoformat(), "sender_email": frm,
                    "domain": classify.domain_of(frm), "company": "",
                    "subject": subj, "message_id": mid, "lane": "", "status": "new"})
                upd({"stage": "rate_requested"})
                sheets.log_activity(campaign_id, frm, "rate_request", subj, mid)
                counts["rate_requests"] += 1
                if not member and frm not in contact_emails:  # unknown-domain lead
                    sheets.append_row("contacts", {
                        "contact_id": f"in_{mid[-10:]}", "email": frm,
                        "domain": classify.domain_of(frm), "source": "inbound",
                        "status": "active", "notes": "LEAD (inbound rate request)"})
                    counts["leads"] += 1

            else:  # other reply — halt sequence, never auto-follow-up
                if member:
                    upd({"stage": "replied", "notes": "reply received"})
                    sheets.log_activity(campaign_id, frm, "replied", subj, mid)
                    counts["replies"] += 1
                else:
                    continue  # unrelated inbound, ignore (not logged)
            counts["seen"] += 1

        if run_id:
            sheets.log_run(run_id, campaign_id, "monitor", "ok", str(counts))
        return counts
    except Exception as exc:
        if run_id:
            sheets.log_run(run_id, campaign_id, "monitor", "failed", str(counts), str(exc))
        raise
