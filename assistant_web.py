"""
Personal AI Assistant — multi-tenant Phase 1 (on-demand, nothing persisted).

When a signed-in team member opens /assistant, this scans THEIR OWN mailbox live
via Microsoft Graph using the access token held in their server-side session
(put there at login), triages recent mail with simple rules, and renders a personal
dashboard. No mailbox data and no long-lived token are stored anywhere — the page is
derived fresh on each load and the access token lives only in the user's session.

Later phases add: persisted status/comment edits (Postgres), Moveware to-dos per
coordinator, WhatsApp (browser extension), and a background refresh.
"""

import datetime
import html as _html

import requests
from flask import Blueprint, session, redirect, url_for, request

assistant_bp = Blueprint("assistant", __name__)

GRAPH = "https://graph.microsoft.com/v1.0"
LOOKBACK_DAYS = 7
MAX_MESSAGES = 50

# Senders that are almost never something a person needs to reply to.
_AUTOMATED_HINTS = (
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
    "notifications", "notification", "notify", "mailer", "mailer-daemon",
    "postmaster", "bounce", "bounces", "automated", "auto-confirm",
    "alerts", "alert@", "updates@", "news@", "newsletter", "marketing@",
    "billing@", "receipts@", "support@microsoft", "account-security-noreply",
)

# Palette / branding — matches the library login page.
_CSS = """
  *{box-sizing:border-box}
  body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#f5f6f8;color:#1a1a2e;}
  .wrap{max-width:860px;margin:0 auto;padding:28px 20px 60px;}
  .top{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:22px;}
  .top img{height:34px}
  .top a{color:#1967d2;text-decoration:none;font-size:13px;font-weight:600}
  h1{font-size:24px;margin:0 0 2px}
  .sub{color:#666;font-size:14px;margin:0 0 22px}
  .kpis{display:flex;gap:12px;margin-bottom:26px;flex-wrap:wrap}
  .kpi{background:#fff;border:1px solid #e6e8ec;border-radius:12px;padding:14px 18px;min-width:120px;flex:1}
  .kpi .n{font-size:26px;font-weight:700;color:#1a1a2e;line-height:1}
  .kpi .l{font-size:12px;color:#777;margin-top:6px;text-transform:uppercase;letter-spacing:.5px}
  .kpi.attn .n{color:#c0392b}
  .section-label{font-size:11px;font-weight:700;color:#999;letter-spacing:1px;text-transform:uppercase;margin:26px 0 12px}
  .card{background:#fff;border:1px solid #e6e8ec;border-radius:12px;padding:14px 16px;margin-bottom:10px;
        display:flex;gap:14px;align-items:flex-start}
  .card .who{font-weight:600;font-size:14px}
  .card .subj{font-size:14px;margin:2px 0 4px}
  .card .snip{font-size:13px;color:#666;line-height:1.45;
        display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .card .when{font-size:12px;color:#999;white-space:nowrap}
  .card .body{flex:1;min-width:0}
  .card a.open{font-size:13px;color:#1967d2;text-decoration:none;font-weight:600;white-space:nowrap}
  .pill{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;margin-left:8px;vertical-align:middle}
  .pill.flag{background:#fef3cd;color:#8a6d00}
  .empty{color:#888;font-size:14px;background:#fff;border:1px dashed #dfe3e8;border-radius:12px;padding:20px;text-align:center}
  .foot{margin-top:30px;font-size:12px;color:#999;line-height:1.5}
  .btn{display:inline-block;background:#1a1a2e;color:#fff;text-decoration:none;font-weight:600;
       font-size:14px;padding:10px 18px;border-radius:10px;margin-top:6px}
"""


def _connect_page(reason: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>My Assistant — Thelsa</title><style>{_CSS}
  .box{{background:#fff;max-width:440px;margin:12vh auto 0;border-radius:14px;padding:36px 32px;
        text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.08)}}
  .box img{{height:40px;margin-bottom:16px}}
</style></head><body>
  <div class="box">
    <img src="/static/thelsa_logo.png" alt="Thelsa">
    <h1 style="font-size:20px">My Assistant</h1>
    <p class="sub">{_html.escape(reason)}</p>
    <a class="btn" href="/login/microsoft?next=/assistant">Sign in with Microsoft</a>
  </div></body></html>"""


def _graph_get(path, params=None):
    tok = session.get("ms_access_token")
    if not tok:
        return None, 401
    try:
        r = requests.get(GRAPH + path,
                         headers={"Authorization": f"Bearer {tok}"},
                         params=params, timeout=25)
        return r, r.status_code
    except requests.RequestException:
        return None, 599


def _is_automated(addr: str) -> bool:
    a = (addr or "").lower()
    return any(h in a for h in _AUTOMATED_HINTS)


def _fmt_when(iso: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        days = (now.date() - dt.date()).days
        t = dt.strftime("%-I:%M %p") if hasattr(dt, "strftime") else ""
        if days == 0:
            return f"Today {t}"
        if days == 1:
            return f"Yesterday {t}"
        return dt.strftime("%b %-d")
    except Exception:
        return (iso or "")[:10]


def _card(m, flagged=False):
    frm = (m.get("from") or {}).get("emailAddress") or {}
    who = _html.escape(frm.get("name") or frm.get("address") or "Unknown")
    subj = _html.escape(m.get("subject") or "(no subject)")
    snip = _html.escape((m.get("bodyPreview") or "").strip())
    when = _html.escape(_fmt_when(m.get("receivedDateTime") or ""))
    link = _html.escape(m.get("webLink") or "#")
    pill = '<span class="pill flag">Flagged</span>' if flagged else ""
    return (f'<div class="card"><div class="body">'
            f'<div class="who">{who}{pill}</div>'
            f'<div class="subj">{subj}</div>'
            f'<div class="snip">{snip}</div></div>'
            f'<div style="text-align:right"><div class="when">{when}</div>'
            f'<a class="open" href="{link}" target="_blank" rel="noopener">Open ↗</a></div></div>')


@assistant_bp.route("/assistant")
def assistant():
    if not session.get("user_email"):
        return redirect(url_for("login", next=request.url))
    if session.get("auth_provider") != "microsoft" or not session.get("ms_access_token"):
        return _connect_page(
            "Your assistant reads your Thelsa (Microsoft) mailbox. "
            "Sign in with Microsoft to view your dashboard."
        )

    since = (datetime.datetime.now(datetime.timezone.utc)
             - datetime.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r, st = _graph_get("/me/mailFolders/inbox/messages", {
        "$select": "subject,from,receivedDateTime,isRead,webLink,bodyPreview,flag,importance",
        "$top": str(MAX_MESSAGES),
        "$orderby": "receivedDateTime desc",
        "$filter": f"receivedDateTime ge {since}",
    })
    if st == 401:
        session.pop("ms_access_token", None)
        return _connect_page("Your session expired. Sign in with Microsoft to refresh your dashboard.")
    if r is None or st >= 400:
        return _connect_page("Couldn't reach your mailbox just now. Please try again in a moment.")

    msgs = (r.json() or {}).get("value", [])
    needs, flagged = [], []
    for m in msgs:
        frm = (m.get("from") or {}).get("emailAddress") or {}
        is_flagged = ((m.get("flag") or {}).get("flagStatus") == "flagged")
        if is_flagged:
            flagged.append(m)
        if (not m.get("isRead")) and not _is_automated(frm.get("address")):
            needs.append(m)

    name = _html.escape((session.get("user_name") or "").split(" ")[0] or "there")
    hour = datetime.datetime.now(datetime.timezone.utc).hour
    greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")
    scanned = datetime.datetime.now(datetime.timezone.utc).strftime("%b %-d, %-I:%M %p UTC")

    def section(label, items, flagged=False):
        if not items:
            return f'<div class="section-label">{label}</div><div class="empty">Nothing here right now. 🎉</div>'
        cards = "".join(_card(m, flagged=flagged) for m in items)
        return f'<div class="section-label">{label} ({len(items)})</div>{cards}'

    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>My Assistant — Thelsa</title><style>{_CSS}</style></head><body>
<div class="wrap">
  <div class="top">
    <img src="/static/thelsa_logo.png" alt="Thelsa">
    <a href="/">← Automation Library</a>
  </div>
  <h1>{greeting}, {name}.</h1>
  <p class="sub">Here's what's waiting in your inbox — scanned live just now.</p>
  <div class="kpis">
    <div class="kpi attn"><div class="n">{len(needs)}</div><div class="l">Need a reply</div></div>
    <div class="kpi"><div class="n">{len(flagged)}</div><div class="l">Flagged</div></div>
    <div class="kpi"><div class="n">{len(msgs)}</div><div class="l">Last {LOOKBACK_DAYS} days</div></div>
  </div>
  {section("Needs your reply", needs)}
  {section("Flagged / follow-up", flagged, flagged=True)}
  <div class="foot">
    Scanned {scanned} directly from your mailbox. Nothing from your email is stored —
    this page is built fresh each time you open it. &nbsp;<a href="/assistant">Refresh</a><br>
    Coming soon: your Moveware document &amp; approval to-dos, and WhatsApp.
  </div>
</div></body></html>"""
    return body
