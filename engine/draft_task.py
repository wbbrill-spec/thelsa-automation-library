"""
draft_task.py — Task A: reconcile sends, screen, sequence, capped drafting.

Idempotent: a member with draft_created=TRUE is never re-drafted for the current
step; sends are reconciled from the mailbox Sent Items so a manual send is never
re-drafted; the daily cap is reconciled against drafts already created today.
"""
from datetime import date, timedelta

from . import config, sheets, planning


def _templates_by_step(campaign_id):
    out = {}
    for t in sheets.read_tab("templates"):
        if t.get("campaign_id") == campaign_id and str(t.get("active", "")).upper() == "TRUE":
            out.setdefault(t.get("step", ""), t)  # first active variant per step
    return out


def _suppression():
    emails, domains = set(), set()
    for s in sheets.read_tab("suppression"):
        v = (s.get("email_or_domain") or "").strip().lower()
        if not v:
            continue
        (domains if "@" not in v else emails).add(v)
    return emails, domains


def run(campaign_id, mailer, today=None, run_id=None):
    today = today or date.today()
    counts = {"reconciled": 0, "drafted": 0, "skipped": 0, "errors": 0}
    try:
        camp = next((c for c in sheets.read_tab("Campaigns")
                     if c.get("campaign_id") == campaign_id), None)
        if not camp:
            raise RuntimeError(f"campaign {campaign_id} not in registry")
        cap = int(float(camp.get("daily_cap") or 0))
        gaps = config.parse_gaps(camp.get("sequence_gaps"))
        templates = _templates_by_step(campaign_id)
        supp_emails, supp_domains = _suppression()
        members = [m for m in sheets.read_tab("campaign_members")
                   if m.get("campaign_id") == campaign_id]

        # 1) Reconcile sends from the mailbox Sent Items
        since = (today - timedelta(days=21)).strftime("%Y-%m-%dT00:00:00Z")
        sent_addrs = set(mailer.list_sent(since)) if mailer else set()
        for m in members:
            email = (m.get("email") or "").lower()
            if (m.get("draft_created", "").upper() == "TRUE"
                    and (m.get("stage") or "").lower() not in config.SENT_STAGES
                    and email in sent_addrs):
                step = int(float(m.get("step") or 1))
                nxt, nad = planning.next_step_after_send(step, today, gaps)
                fields = {"date_sent": today.isoformat(), "stage": "sent",
                          "draft_created": "FALSE"}
                if nxt:
                    fields.update({"step": nxt, "next_action_date": nad})
                sheets.update_fields("campaign_members", m["_row"], fields)
                sheets.log_activity(campaign_id, email, "sent", f"step {step}")
                counts["reconciled"] += 1
                m.update(fields)  # reflect for the drafting pass below

        # 2) Capped drafting
        drafted_today = sum(1 for m in members
                            if m.get("scheduled_send_date") == today.isoformat()
                            and m.get("draft_created", "").upper() == "TRUE")
        budget = max(0, cap - drafted_today)

        for m in members:
            if budget <= 0:
                break
            if planning.screen_reason(m, supp_emails, supp_domains, config.STOP_STAGES):
                counts["skipped"] += 1
                continue
            if m.get("draft_created", "").upper() == "TRUE":
                continue
            step = int(float(m.get("step") or 1))
            if step > len(gaps):
                continue  # sequence finished
            if not planning.is_due(m.get("next_action_date"), today):
                continue
            tpl = templates.get(str(step))
            if not tpl:
                sheets.log_activity(campaign_id, m.get("email"), "error",
                                    f"no active template for step {step}")
                counts["errors"] += 1
                continue
            cv = f"{campaign_id}_s{step}_{tpl.get('variant_id', 'A')}"
            link = ""
            if not config.DRY_RUN and mailer:
                d = mailer.create_draft(m.get("email"), tpl.get("subject", ""), tpl.get("body", ""))
                link = d.get("webLink", "")
            sheets.update_fields("campaign_members", m["_row"], {
                "draft_created": "TRUE", "draft_link": link, "content_version": cv,
                "scheduled_send_date": today.isoformat(), "stage": "drafted",
                "batch_no": today.isoformat()})
            sheets.log_activity(campaign_id, m.get("email"), "drafted",
                                f"step {step}", "", cv)
            budget -= 1
            counts["drafted"] += 1

        if run_id:
            sheets.log_run(run_id, campaign_id, "draft", "ok", str(counts))
        return counts
    except Exception as exc:
        if run_id:
            sheets.log_run(run_id, campaign_id, "draft", "failed", str(counts), str(exc))
        raise
