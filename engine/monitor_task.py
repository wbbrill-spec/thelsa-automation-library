"""
monitor_task.py — Task B: scan the mailbox, classify inbound mail, update state.

SCOPED TO THE CAMPAIGN BY COMPANY DOMAIN: we act on a message when its sender is
one of the agents we emailed OR is anyone at the same company domain as an emailed
agent (so a reply from a colleague at that company still counts). Guardrails:
  - free webmail domains (gmail, outlook, yahoo, ...) are matched by exact address
    only, never by domain — matching all of gmail.com would be absurd;
  - our own mailbox domain (thelsa.com) is excluded from domain matching;
  - a bounce notice comes from a mail-server daemon, so it is detected first and
    tied back to the failed recipient via the bounce body.
Everything else in the inbox is ignored.

Idempotent: every message acted on is logged to activity_log by message_id and
skipped on later runs. Bookings are flagged for human confirmation, never
auto-finalized. The monitor never creates new contacts/leads.
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
        member_emails = {e for e in by_email if e}
        own_domain = (config.MAILBOX or "").split("@")[-1].strip().lower()
        # domain -> representative member, excluding free webmail and our own domain
        by_domain = {}
        for e, m in by_email.items():
            d = classify.domain_of(e)
            if d and d != own_domain and d not in classify.FREE_EMAIL_DOMAINS:
                by_domain.setdefault(d, m)

        since = (today - timedelta(days=since_days)).strftime("%Y-%m-%dT00:00:00Z")
        msgs = mailer.scan_inbox(since) if mailer else []

        for msg in msgs:
            mid = msg.get("message_id", "")
            if not mid or mid in processed:
                continue
            frm = (msg.get("from") or "").lower()
            subj, body = msg.get("subject", ""), msg.get("body", "")

            # Bounce notices come from a mail-server daemon (which may share a
            # campaign domain), so detect them before the scope match.
            if classify.is_bounce(frm):
                target = next((e for e in member_emails
                               if e and e in (body or "").lower()), None)
                if target:
                    sheets.append_row("suppression", {"email_or_domain": target,
                        "reason": "bounce", "date": today.isoformat(),
                        "source_campaign": campaign_id})
                    tm = by_email.get(target)
                    if tm:
                        sheets.update_fields("campaign_members", tm["_row"],
                                             {"stage": "bounced"})
                    sheets.log_activity(campaign_id, target, "bounce", subj, mid)
                    counts["bounces"] += 1
                continue

            # SCOPE: the exact agent we emailed, or anyone at that company's domain.
            member = by_email.get(frm) or by_domain.get(classify.domain_of(frm))
            if member is None:
                continue

            counts["seen"] += 1

            def upd(fields):
                sheets.update_fields("campaign_members", member["_row"], fields)

            cat = classify.classify(subj, body, phrases)

            if cat == "unsubscribe":
                sheets.append_row("suppression", {"email_or_domain": frm,
                    "reason": "unsubscribe", "date": today.isoformat(),
                    "source_campaign": campaign_id})
                upd({"stage": "stopped"})
                sheets.log_activity(campaign_id, frm, "unsubscribe", subj, mid)
                counts["unsubscribes"] += 1

            elif cat == "booking":
                sheets.log_activity(campaign_id, frm, "booking_signal",
                                    f"LIKELY BOOKING (confirm): {subj}", mid)
                counts["bookings_flagged"] += 1  # human confirms; never auto-finalized

            elif cat == "rate_request":
                sheets.append_row("rate_requests", {
                    "request_id": f"rr_{mid[-12:] or today.isoformat()}",
                    "campaign_id": campaign_id,
                    "contact_id": member.get("contact_id", ""),
                    "date": today.isoformat(), "sender_email": frm,
                    "domain": classify.domain_of(frm), "company": "",
                    "subject": subj, "message_id": mid, "lane": "", "status": "new"})
                upd({"stage": "rate_requested"})
                sheets.log_activity(campaign_id, frm, "rate_request", subj, mid)
                counts["rate_requests"] += 1

            else:  # other reply — halt sequence, never auto-follow-up
                upd({"stage": "replied", "notes": "reply received"})
                sheets.log_activity(campaign_id, frm, "replied", subj, mid)
                counts["replies"] += 1

        if run_id:
            sheets.log_run(run_id, campaign_id, "monitor", "ok", str(counts))
        return counts
    except Exception as exc:
        if run_id:
            sheets.log_run(run_id, campaign_id, "monitor", "failed", str(counts), str(exc))
        raise
