"""
draft_task.py — Task A: reconcile sends, screen, sequence, capped drafting.

Idempotent: a member with draft_created=TRUE is never re-drafted for the current
step; sends are reconciled from the mailbox Sent Items so a manual send is never
re-drafted; the daily cap is reconciled against drafts already created today.
"""
from datetime import date, timedelta

from . import config, sheets, planning, ai


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


# Role/shared inboxes get "Dear Partner,"; a real person's inbox gets "Hello X,".
ROLE_LOCALPARTS = {
    "rates", "rate", "info", "sales", "admin", "contact", "office", "partners",
    "partner", "hello", "mail", "enquiries", "enquiry", "quote", "quotes", "ops",
    "operations", "booking", "bookings", "reception", "general", "support",
    "accounts", "import", "imports", "export", "exports", "traffic", "moves",
    "moving", "movements", "customerservice", "cs", "team", "hola", "ventas",
    "comercial", "trafico", "logistica", "logistics", "gac", "sales1", "info1",
}

# The AI output must still contain these, or we fall back to the plain template.
REQUIRED_TOKENS = ("rates@thelsa.com", "469-247-3974")


def _greeting(contact):
    email = (contact.get("email") or "").lower()
    local = email.split("@")[0] if "@" in email else ""
    base = "".join(ch for ch in local if ch.isalpha())
    first = (contact.get("first_name") or "").strip()
    if not first or base in ROLE_LOCALPARTS:
        return "Dear Partner,"
    return f"Hello {first.split()[0]},"


def _compose(tpl, contact):
    """Subject + rendered body for one recipient: fill the greeting, convert the
    stored '\\n' markers to real newlines, then optionally AI-personalize with a
    guardrail that falls back to the plain body if anything required is missing."""
    subject = tpl.get("subject", "")
    body = ((tpl.get("body", "") or "")
            .replace("{{greeting}}", _greeting(contact))
            .replace("\\n", "\n"))
    if ai.available():
        try:
            varied = ai.personalize(body, contact)
            if (varied and all(tok in varied for tok in REQUIRED_TOKENS)
                    and "unsubscribe" in varied.lower()):
                body = varied
        except Exception:
            pass  # keep the plain templated body
    return subject, body


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
        contacts_by_email = {(c.get("email") or "").lower(): c
                             for c in sheets.read_tab("contacts")}

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
            contact = contacts_by_email.get((m.get("email") or "").lower(),
                                            {"email": m.get("email")})
            subject, body = _compose(tpl, contact)
            if not config.DRY_RUN and mailer:
                d = mailer.create_draft(m.get("email"), subject, body)
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
