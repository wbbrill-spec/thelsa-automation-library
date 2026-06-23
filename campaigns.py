"""
campaigns.py — Email Campaigns dashboard for the Thelsa Automation Library.

Adds a single login-gated route, /campaigns, that reads the master workbook
"Thelsa Agent Outreach" live and renders a multi-campaign overview plus a
per-campaign funnel (Sent → Replied → Rate Request → Booked), pacing, pipeline
value, suppression count, and a "Bookings to confirm" queue.

Registry-driven: every row in the Campaigns tab appears automatically — adding a
new campaign needs NO change here.

Wiring (app.py):
    from campaigns import campaigns_bp, login_required as _lr  # use app's login_required
    app.register_blueprint(campaigns_bp)
The blueprint expects the app to expose `login_required`; we import it lazily to
avoid a circular import (see _login_required below).

Auth to read the Sheet — choose ONE (set in Render env):
  • GOOGLE_SA_B64  : base64 service-account JSON (recommended; share Sheet as Viewer)
  • else falls back to the logged-in user's OAuth token IF it carries the
    spreadsheets.readonly scope (requires adding that scope in app.py GMAIL_SCOPES).

Env:
  OUTREACH_SHEET_ID : workbook id (defaults to the one already created in Drive)
"""
import base64
import json
import os
from collections import defaultdict
from datetime import date, datetime

from flask import Blueprint, redirect, request, session, url_for, render_template_string

campaigns_bp = Blueprint("campaigns", __name__)

SHEET_ID = os.environ.get(
    "OUTREACH_SHEET_ID", "1QhlNpaLndlcEy3EDgnVwsfc7PjNqO26HysaqjLdy6uc"
)
SA_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


# ── auth shim ───────────────────────────────────────────────────────────────────
def _login_required(f):
    """Reuse the app's session-based login gate without importing app at module load."""
    import functools

    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)

    return wrapped


# ── Sheets reader ────────────────────────────────────────────────────────────────
def _sheets_service():
    """Return a Sheets API service. Service account first, else user OAuth token."""
    from googleapiclient.discovery import build

    b64 = os.environ.get("GOOGLE_SA_B64", "").strip()
    if b64:
        from google.oauth2.service_account import Credentials as SACreds
        info = json.loads(base64.b64decode(b64).decode())
        creds = SACreds.from_service_account_info(info, scopes=SA_SCOPES)
        return build("sheets", "v4", credentials=creds, cache_discovery=False)

    # Fallback: logged-in user's stored token (needs spreadsheets.readonly scope).
    from google.oauth2.credentials import Credentials as UserCreds
    email = (session.get("user_email") or "").lower()
    import re
    from pathlib import Path
    safe = re.sub(r"[^a-z0-9]", "_", email)
    tok = Path(__file__).resolve().parent / "data" / "tokens" / f"{safe}.json"
    if not tok.exists():
        raise RuntimeError("No Sheets credential: set GOOGLE_SA_B64 or add the "
                           "spreadsheets.readonly OAuth scope and re-login.")
    creds = UserCreds.from_authorized_user_info(json.loads(tok.read_text()))
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _read_tab(svc, tab):
    """Return list[dict] for a tab using its first row as headers (empty if absent)."""
    try:
        resp = svc.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{tab}'!A1:ZZ").execute()
    except Exception:
        return []
    rows = resp.get("values", [])
    if not rows:
        return []
    hdr = rows[0]
    out = []
    for r in rows[1:]:
        r = r + [""] * (len(hdr) - len(r))
        out.append({hdr[i]: r[i] for i in range(len(hdr))})
    return out


def _num(x):
    try:
        return float(str(x).replace(",", "").replace("$", "").strip() or 0)
    except ValueError:
        return 0.0


SENT_STAGES = {"sent", "replied", "rate_requested", "booked"}
ENGAGED_STAGES = {"replied", "rate_requested", "booked"}
RR_STAGES = {"rate_requested", "booked"}


def _campaign_metrics(members, rrs):
    sent = sum(1 for m in members if (m.get("date_sent") or m.get("stage", "") in SENT_STAGES))
    replied = sum(1 for m in members if m.get("stage", "") in ENGAGED_STAGES)
    rate_req = sum(1 for m in members if m.get("stage", "") in RR_STAGES) or len(rrs)
    booked = sum(1 for m in members if m.get("stage", "") == "booked") \
        or sum(1 for r in rrs if r.get("status", "") == "won")

    def pct(a, b):
        return f"{(100 * a / b):.0f}%" if b else "—"

    by_country = defaultdict(int)
    by_domain = defaultdict(int)
    for m in members:
        by_country[m.get("country") or m.get("_country") or "—"] += 1
        by_domain[(m.get("email", "").split("@")[-1]) or "—"] += 1

    pipeline = sum(_num(r.get("value")) for r in rrs)
    today = str(date.today())
    sent_today = sum(1 for m in members if (m.get("date_sent") or "").startswith(today))
    return {
        "total": len(members), "sent": sent, "replied": replied,
        "rate_req": rate_req, "booked": booked,
        "cv_sent_reply": pct(replied, sent),
        "cv_reply_rate": pct(rate_req, replied),
        "cv_rate_book": pct(booked, rate_req),
        "pipeline": pipeline, "sent_today": sent_today,
        "by_country": dict(sorted(by_country.items(), key=lambda x: -x[1])),
        "by_domain": dict(sorted(by_domain.items(), key=lambda x: -x[1])[:15]),
    }


@campaigns_bp.route("/campaigns")
@_login_required
def campaigns_view():
    try:
        svc = _sheets_service()
        campaigns = _read_tab(svc, "Campaigns")
        members_all = _read_tab(svc, "campaign_members")
        rrs_all = _read_tab(svc, "rate_requests")
        contacts = {c.get("contact_id"): c for c in _read_tab(svc, "contacts")}
        suppression = _read_tab(svc, "suppression")
    except Exception as exc:
        return render_template_string(_ERROR_HTML, err=str(exc),
                                      name=session.get("user_name", "")), 200

    # enrich members with contact country/company where missing
    for m in members_all:
        c = contacts.get(m.get("contact_id"), {})
        m.setdefault("_country", c.get("country", ""))
        if not m.get("country"):
            m["country"] = c.get("country", "")

    selected = request.args.get("c", "")
    ids = [c.get("campaign_id") for c in campaigns]
    if selected not in ids:
        selected = ids[0] if ids else ""

    cards = []
    for c in campaigns:
        cid = c.get("campaign_id")
        mem = [m for m in members_all if m.get("campaign_id") == cid]
        rr = [r for r in rrs_all if r.get("campaign_id") == cid]
        cards.append({"cfg": c, "m": _campaign_metrics(mem, rr)})

    sel = next((x for x in cards if x["cfg"].get("campaign_id") == selected), None)
    sel_rrs = [r for r in rrs_all if r.get("campaign_id") == selected]
    sel_mem = [m for m in members_all if m.get("campaign_id") == selected]
    bookings_to_confirm = [r for r in sel_rrs if r.get("status", "") in ("", "new")
                           and (r.get("lane") or r.get("subject"))]
    cfg = sel["cfg"] if sel else {}
    cap = int(_num(cfg.get("daily_cap")) or 0)

    return render_template_string(
        _DASH_HTML, name=session.get("user_name", ""), cards=cards,
        selected=selected, sel=sel, cfg=cfg, cap=cap,
        roster=sel_mem, rrs=sel_rrs, bookings=bookings_to_confirm,
        suppression_count=len(suppression),
        gen=datetime.now().strftime("%Y-%m-%d %H:%M"))


# ── templates ────────────────────────────────────────────────────────────────────
_ERROR_HTML = """<!doctype html><meta charset=utf-8><title>Campaigns</title>
<body style="font-family:-apple-system,sans-serif;background:#f5f6f8;padding:40px">
<a href="/" style="color:#c0392b;text-decoration:none">&larr; Library</a>
<h1>Email Campaigns</h1>
<div style="background:#fdecea;border:1px solid #f5c6cb;color:#a33;padding:16px;border-radius:10px;max-width:680px">
<b>Couldn't read the master workbook.</b><br>{{err}}<br><br>
Set <code>GOOGLE_SA_B64</code> (service account, shared as Viewer) or add the
<code>spreadsheets.readonly</code> OAuth scope.</div></body>"""

_DASH_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Email Campaigns — Thelsa</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f5f6f8;color:#1a1a2e}
header{background:#fff;padding:0 32px;height:64px;display:flex;align-items:center;justify-content:space-between;border-bottom:3px solid #c0392b}
header a{color:#c0392b;text-decoration:none;font-weight:600;font-size:13px}
main{max-width:1100px;margin:28px auto;padding:0 20px}
h1{font-size:24px;margin-bottom:4px}.sub{color:#888;font-size:12px;margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px;margin-bottom:30px}
.kpi{background:#fff;border:1px solid #e8e8e8;border-radius:12px;padding:16px}
.kpi .n{font-size:26px;font-weight:800}.kpi .l{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.5px}
.sel{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
.sel a{padding:7px 14px;border-radius:20px;border:1px solid #e0e0e0;background:#fff;color:#444;font-size:13px;text-decoration:none}
.sel a.on{background:#c0392b;color:#fff;border-color:#c0392b}
.funnel{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 26px}
.step{flex:1;min-width:150px;background:#fff;border:1px solid #e8e8e8;border-radius:12px;padding:16px;text-align:center}
.step .n{font-size:24px;font-weight:800;color:#c0392b}.step .l{font-size:12px;color:#666;margin-top:4px}
.step .cv{font-size:11px;color:#1e7e34;margin-top:6px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.box{background:#fff;border:1px solid #e8e8e8;border-radius:12px;padding:18px;margin-bottom:18px}
.box h3{font-size:13px;text-transform:uppercase;letter-spacing:.5px;color:#999;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid #f0f0f0}
th{color:#888;font-weight:600;font-size:11px;text-transform:uppercase}
.bar{height:8px;background:#eee;border-radius:5px;overflow:hidden;margin-top:8px}
.bar>i{display:block;height:100%;background:#c0392b}
.tag{font-size:10px;padding:2px 8px;border-radius:10px;background:#fef9e7;color:#856404}
@media(max-width:760px){.cols{grid-template-columns:1fr}}
</style></head><body>
<header><a href="/">&larr; Automation Library</a><span style="color:#666;font-size:13px">{{name}}</span></header>
<main>
<h1>Email Campaigns</h1><div class=sub>Live from "Thelsa Agent Outreach" · generated {{gen}}</div>

<!-- all-campaigns overview -->
<div class=grid>
  <div class=kpi><div class=n>{{cards|length}}</div><div class=l>Campaigns</div></div>
  <div class=kpi><div class=n>{{cards|sum(attribute='m.sent')}}</div><div class=l>Total sent</div></div>
  <div class=kpi><div class=n>{{cards|sum(attribute='m.rate_req')}}</div><div class=l>Rate requests</div></div>
  <div class=kpi><div class=n>{{cards|sum(attribute='m.booked')}}</div><div class=l>Booked</div></div>
  <div class=kpi><div class=n>{{suppression_count}}</div><div class=l>Suppressed (global)</div></div>
</div>

<!-- campaign selector (registry-driven) -->
<div class=sel>
  {% for c in cards %}
  <a class="{{ 'on' if c.cfg.campaign_id==selected else '' }}" href="/campaigns?c={{c.cfg.campaign_id}}">
    {{c.cfg.name or c.cfg.campaign_id}}</a>
  {% endfor %}
</div>

{% if sel %}
{% set m = sel.m %}
<h1 style="font-size:19px">{{cfg.name or cfg.campaign_id}}
  <span class=tag>{{cfg.status or 'n/a'}}</span></h1>
<div class=sub>{{cfg.region}} · cap {{cap}}/day · {{cfg.outreach_cadence}}</div>

<div class=funnel>
  <div class=step><div class=n>{{m.sent}}</div><div class=l>Sent</div></div>
  <div class=step><div class=n>{{m.replied}}</div><div class=l>Replied</div><div class=cv>{{m.cv_sent_reply}}</div></div>
  <div class=step><div class=n>{{m.rate_req}}</div><div class=l>Rate Request</div><div class=cv>{{m.cv_reply_rate}}</div></div>
  <div class=step><div class=n>{{m.booked}}</div><div class=l>Booked</div><div class=cv>{{m.cv_rate_book}}</div></div>
</div>

<div class=cols>
  <div>
    <div class=box><h3>Pacing</h3>
      Sent today: <b>{{m.sent_today}}</b> / {{cap}} cap
      <div class=bar><i style="width:{{ (100*m.sent_today/cap)|round if cap else 0 }}%"></i></div>
      <div style="margin-top:12px">Progress: <b>{{m.sent}}</b> / {{m.total}} loaded
      <div class=bar><i style="width:{{ (100*m.sent/m.total)|round if m.total else 0 }}%"></i></div></div>
    </div>
    <div class=box><h3>Pipeline value (rate requests)</h3>
      <div style="font-size:26px;font-weight:800">${{ '{:,.0f}'.format(m.pipeline) }}</div>
      <div style="color:#888;font-size:12px">{{rrs|length}} request(s) logged</div></div>
    <div class=box><h3>By country</h3><table>
      {% for k,v in m.by_country.items() %}<tr><td>{{k}}</td><td style="text-align:right">{{v}}</td></tr>{% endfor %}
    </table></div>
  </div>
  <div>
    <div class=box><h3>Bookings to confirm ({{bookings|length}})</h3>
      {% if bookings %}<table><tr><th>Company</th><th>Lane</th><th>Subject</th></tr>
      {% for b in bookings %}<tr><td>{{b.company}}</td><td>{{b.lane}}</td><td>{{b.subject}}</td></tr>{% endfor %}
      </table>{% else %}<div style="color:#888;font-size:13px">None awaiting confirmation.</div>{% endif %}
    </div>
    <div class=box><h3>By domain (top 15)</h3><table>
      {% for k,v in m.by_domain.items() %}<tr><td>{{k}}</td><td style="text-align:right">{{v}}</td></tr>{% endfor %}
    </table></div>
  </div>
</div>

<div class=box><h3>Roster ({{roster|length}})</h3><table>
  <tr><th>Company</th><th>Country</th><th>Email</th><th>Step</th><th>Stage</th><th>Sent</th></tr>
  {% for r in roster %}<tr>
    <td>{{r.company or ''}}</td><td>{{r.country or ''}}</td><td>{{r.email}}</td>
    <td>{{r.step}}</td><td>{{r.stage}}</td><td>{{r.date_sent}}</td></tr>{% endfor %}
</table></div>
{% else %}
<div class=box>No campaigns in the registry yet. Add a row to the <b>Campaigns</b> tab.</div>
{% endif %}
</main></body></html>"""
