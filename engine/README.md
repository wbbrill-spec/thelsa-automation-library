# Outreach Engine

Generic, config-driven, idempotent engine that runs every campaign in the
`Campaigns` registry. Adding a campaign = adding rows to the workbook; no code
change. Built but **not yet scheduled or connected** — see "Status / gated" below.

## Modules
| File | Role |
|------|------|
| `config.py` | env + registry constants, `parse_gaps`, `graph_configured` |
| `sheets.py` | service-account read/write over the workbook (+ activity_log/runs writers) |
| `mailer.py` | Microsoft Graph adapter for bbrill@ — `create_draft`, `list_sent`, `scan_inbox` |
| `classify.py` | `signal_phrases`-driven inbound classifier (pure) |
| `planning.py` | business-day scheduling + suppression/stage screening (pure) |
| `draft_task.py` | **Task A** — reconcile sends → screen → sequence → capped drafting (idempotent) |
| `monitor_task.py` | **Task B** — classify inbound → rate_request / booking-flag / unsubscribe / bounce / reply / lead |
| `alerts.py` | failure surfacing (runs row + activity_log; email needs Mail.Send, not granted) |
| `run.py` / `draft.py` / `monitor.py` | entrypoints for all active campaigns |
| `test_logic.py` | unit tests for the pure logic (all passing) |

## Runtime (recommended: Render Cron Jobs)
Two cron jobs on the Render service (shares this repo + env):
- **Drafting** — `python -m engine.draft` — Campaign #1: weekly, Sunday 19:00 America/Chicago.
- **Monitoring** — `python -m engine.monitor` — daily.

The drafting job stages the week's drafts as daily batches of ≤ `daily_cap`,
reconciled against the mailbox Sent Items; you send each day's batch manually.

## Environment variables
```
OUTREACH_SHEET_ID    = 1QhlNpaLndlcEy3EDgnVwsfc7PjNqO26HysaqjLdy6uc
GOOGLE_SA_B64        = <service-account JSON, base64>   # already set for the dashboard
GRAPH_TENANT_ID      = <from IT>      # Microsoft Graph app registration
GRAPH_CLIENT_ID      = <from IT>
GRAPH_CLIENT_SECRET  = <from IT>
MAILBOX              = bbrill@thelsa.com
ALERT_EMAIL          = bbrill@thelsa.com
DRY_RUN              = 1   # optional: run the full path but create no drafts
```
Requirements: `google-api-python-client`, `google-auth`, `requests` (first two
already in the library's requirements; add `requests` if not present).

## Safety / idempotency
- **Never drafts** to anyone in `suppression` (email or domain) or whose stage is
  replied/rate_requested/booked/stopped/bounced/suppressed.
- **Idempotent**: `draft_created` guards per-step re-drafts; sends reconciled from
  Sent Items; the daily cap is reconciled against drafts already created today;
  every inbound message acted on is logged by `message_id` and skipped thereafter.
- **Drafts only** — the engine never sends (no `Mail.Send`); you review and send.
- **Bookings are flagged for human confirmation**, never auto-finalized.
- `DRY_RUN=1` exercises everything without creating drafts.

## Status / gated (do not skip)
- ✅ Code built; pure logic unit-tested (`python -m engine.test_logic`).
- ⏳ **Microsoft Graph credential** (Mail.ReadWrite for bbrill@) — pending IT.
- 🛑 **Deliverability** (SPF/DKIM/DMARC on thelsa.com) confirmed before any send.
- 🛑 **First test draft** to a test address, reviewed by Bill.
- 🛑 **Scheduling the crons** and the **first real batch** — only after Bill's approval.
