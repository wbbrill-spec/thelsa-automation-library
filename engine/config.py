"""
config.py — environment + campaign-registry config for the outreach engine.

The engine is generic and config-driven: it reads the `Campaigns` tab of the
master workbook and runs each active campaign on its own cadence. Adding a
campaign = adding a registry row (+ templates + members). No code change.

Environment variables (set in the engine's runtime, e.g. Render):
  OUTREACH_SHEET_ID   master workbook id (defaults to the live one)
  GOOGLE_SA_B64       base64 service-account JSON (Sheets read/write)
  GRAPH_TENANT_ID     Microsoft Entra tenant id        (mail, prod)
  GRAPH_CLIENT_ID     app registration client id       (mail, prod)
  GRAPH_CLIENT_SECRET app registration client secret   (mail, prod)
  MAILBOX             the mailbox UPN the engine drafts from (bbrill@thelsa.com)
  ALERT_EMAIL         where failure alerts go (bbrill@thelsa.com)
  DRY_RUN             "1" => never create real drafts; log intended actions only
"""
import os

SHEET_ID = os.environ.get("OUTREACH_SHEET_ID", "1QhlNpaLndlcEy3EDgnVwsfc7PjNqO26HysaqjLdy6uc")
MAILBOX = os.environ.get("MAILBOX", "bbrill@thelsa.com")
# Mailboxes the engine may draft from; lead-gen round-robins across these.
ALLOWED_MAILBOXES = [m.strip().lower() for m in os.environ.get("ALLOWED_MAILBOXES", "bbrill@thelsa.com,armandosilveyra@thelsa.com,gustavogonzalez@thelsa.com").split(",") if m.strip()]
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "bbrill@thelsa.com")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

# Stages that mean "do not draft to this member again"
STOP_STAGES = {"replied", "rate_requested", "booked", "stopped", "bounced", "suppressed"}
# Stages that count as "has been sent at least once" for the funnel
SENT_STAGES = {"sent", "replied", "rate_requested", "booked"}


def parse_gaps(s):
    """'0,5,7' -> [0,5,7]  (business-day gaps before each step)."""
    out = []
    for part in str(s or "").replace(" ", "").split(","):
        if part:
            try:
                out.append(int(part))
            except ValueError:
                pass
    return out or [0, 5, 7]


def graph_configured():
    return all(os.environ.get(k) for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"))
