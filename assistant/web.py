"""
AI Assistant web routes (Flask blueprint, mounted in the library app).

Isolation rule: every route derives the user from the signed-in session
(session["user_email"]) — never from a URL or form parameter. Item/draft ids in
URLs are always looked up together with that user's id, so guessing another
user's id returns nothing. Admin routes additionally require role == admin and
show connection health only, never anyone's mail.
"""

import datetime as _dt
import functools
import io
import json
import os
import secrets
import zipfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (Blueprint, Response, abort, jsonify, redirect, render_template_string,
                   request, session, url_for)

from . import db, drafting, graph, i18n, priority, scan, vault, wa_triage

bp = Blueprint("assistant", __name__)
MX = ZoneInfo("America/Mexico_City")
EXT_DIR = Path(__file__).resolve().parent / "whatsapp_extension"


def _admins():
    raw = os.environ.get("ASSISTANT_ADMINS",
                         "bbrill@thelsa.com,bill.brill@inflectionpointnow.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


# ── Language ───────────────────────────────────────────────────────────────────
def user_lang(u=None) -> str:
    """Saved choice first, then the browser's language, then English."""
    if u is not None and u.get("lang") in i18n.LANGS:
        return u["lang"]
    try:
        best = request.accept_languages.best_match(["en", "es"])
    except RuntimeError:
        best = None
    return best or "en"


# ── Auth helpers ───────────────────────────────────────────────────────────────
def current_user():
    email = (session.get("user_email") or "").lower().strip()
    if not email:
        return None
    u = db.get_user_by_email(email)
    role = "admin" if email in _admins() else None
    if not u or (role and u["role"] != role):
        u = db.upsert_user(email, session.get("user_name"), role=role)
    return u


def login_required(f):
    @functools.wraps(f)
    def wrapped(*a, **k):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.url))
        u = current_user()
        if not u["active"]:
            lang = user_lang(u)
            return _page(i18n.t(lang, "paused_title"),
                         f"<p class='sub'>{i18n.t(lang, 'paused_body')}</p>", lang=lang), 403
        return f(u, *a, **k)
    return wrapped


def admin_required(f):
    @functools.wraps(f)
    @login_required
    def wrapped(u, *a, **k):
        if u["role"] != "admin":
            abort(403)
        return f(u, *a, **k)
    return wrapped


def _csrf_token():
    if "asst_csrf" not in session:
        session["asst_csrf"] = secrets.token_urlsafe(24)
    return session["asst_csrf"]


def _check_csrf():
    sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
    if not sent or not secrets.compare_digest(sent, session.get("asst_csrf", "")):
        abort(400, "Form expired — reload the page and try again.")


# ── Formatting ─────────────────────────────────────────────────────────────────
def _mx(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(MX)


def _when(dt, lang="en"):
    d = _mx(dt)
    if not d:
        return ""
    es = lang == "es"
    today = _dt.datetime.now(MX).date()
    days = (today - d.date()).days
    t = d.strftime("%-I:%M %p")
    if days == 0:
        return f"{'Hoy' if es else 'Today'} {t}"
    if days == 1:
        return f"{'Ayer' if es else 'Yesterday'} {t}"
    if days == -1:
        return "Mañana" if es else "Tomorrow"
    return f"{d.day} {i18n.month_abbr(d, lang)}" if es else d.strftime("%b %-d")


def _stamp(dt, lang="en"):
    d = _mx(dt)
    if not d:
        return i18n.t(lang, "never")
    if lang == "es":
        return f"{d.day} {i18n.month_abbr(d, lang)}, {d.strftime('%-I:%M %p')}"
    return d.strftime("%b %-d, %-I:%M %p")


# ── Shared page shell ──────────────────────────────────────────────────────────
CSS = """
*{box-sizing:border-box}
:root{--ink:#1a1a2e;--muted:#6b7080;--line:#e6e8ec;--bg:#f5f6f8;--card:#fff;--link:#1967d2;
      --urgent:#c0392b;--today:#d68910;--soon:#7f8c8d;--ok:#1e8449}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--ink)}
a{color:var(--link)}
.wrap{max-width:920px;margin:0 auto;padding:24px 16px 60px}
.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}
.top img{height:32px}.top nav a{font-size:13px;font-weight:600;text-decoration:none;margin-left:14px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:0 0 10px}
.sub{color:var(--muted);font-size:14px;margin:0 0 18px;line-height:1.5}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.kpi .n{font-size:26px;font-weight:700;line-height:1}.kpi .l{font-size:11px;color:var(--muted);margin-top:6px;text-transform:uppercase;letter-spacing:.5px}
.kpi.urgent .n{color:var(--urgent)}.kpi.today .n{color:var(--today)}
.fresh{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:12px;color:var(--muted);margin-bottom:14px}
.fresh b{color:var(--ink);font-weight:600}.fresh .err{color:var(--urgent)}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:16px}
.chip{border:1px solid var(--line);background:#fff;border-radius:20px;padding:6px 12px;font-size:13px;cursor:pointer;font-weight:600;color:var(--ink)}
.chip.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.spacer{flex:1}
.btn{display:inline-block;border:0;background:var(--ink);color:#fff;text-decoration:none;font-weight:600;font-size:13px;padding:8px 14px;border-radius:9px;cursor:pointer;font-family:inherit}
.btn.light{background:#fff;color:var(--ink);border:1px solid var(--line)}
.btn.small{padding:5px 10px;font-size:12px}.btn.danger{background:var(--urgent)}
.tier-h{font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;margin:22px 0 10px;display:flex;align-items:center;gap:8px}
.tier-h .dot{width:9px;height:9px;border-radius:50%}
.card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--soon);border-radius:12px;padding:12px 14px;margin-bottom:9px}
.card.urgent{border-left-color:var(--urgent)}.card.today{border-left-color:var(--today)}.card.seen{opacity:.6}
.card .row{display:flex;gap:12px;align-items:flex-start}.card .body{flex:1;min-width:0}
.tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;margin-right:6px;background:#eef1f5;color:#3d4452}
.tag.moveware{background:#e8f1fb;color:#1a5490}.tag.clickup{background:#f1eafd;color:#5b2a9e}.tag.whatsapp{background:#e6f6ec;color:#1e7a3c}.tag.kind{background:#fff4e0;color:#8a5a00}
.card .title{font-weight:600;font-size:14px;margin:6px 0 2px;overflow-wrap:anywhere}
.card .who{font-size:13px;color:var(--muted)}
.card .snip{font-size:13px;color:#555;line-height:1.45;margin-top:4px;overflow-wrap:anywhere}
.card .side{text-align:right;white-space:nowrap;font-size:12px;color:var(--muted)}
.card .acts{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.card form{display:inline}
textarea{width:100%;min-height:130px;border:1px solid var(--line);border-radius:9px;padding:10px;font:inherit;font-size:13px}
.draft{background:#fafbfc;border:1px dashed #cfd5dd;border-radius:10px;padding:10px;margin-top:10px}
.empty{color:var(--muted);font-size:14px;background:#fff;border:1px dashed #dfe3e8;border-radius:12px;padding:22px;text-align:center}
.banner{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.banner.warn{border-color:#f0c36d;background:#fffaf0}
.box{background:#fff;max-width:560px;margin:6vh auto 0;border-radius:14px;padding:30px 26px;box-shadow:0 8px 30px rgba(0,0,0,.08)}
.box ul{font-size:14px;line-height:1.6;padding-left:18px}
label.ck{display:flex;gap:10px;font-size:14px;margin:12px 0;align-items:flex-start}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#fafbfc;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted)}
.ok{color:var(--ok);font-weight:600}.bad{color:var(--urgent);font-weight:600}
input[type=text],input[type=email]{border:1px solid var(--line);border-radius:8px;padding:7px 9px;font:inherit;font-size:13px}
.foot{margin-top:28px;font-size:12px;color:var(--muted);line-height:1.6}
.lang{display:inline-flex;border:1px solid var(--line);border-radius:20px;overflow:hidden;margin-left:14px;vertical-align:middle}
.lang a{margin:0!important;padding:4px 10px;font-size:12px;color:var(--ink)!important}
.lang a.on{background:var(--ink);color:#fff!important}
code{background:#eef1f5;padding:2px 6px;border-radius:5px;font-size:12px;overflow-wrap:anywhere}
@media(max-width:600px){.card .row{flex-direction:column}.card .side{text-align:left}}
"""

SHELL = """<!doctype html><html lang="{{ lang }}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} — {{ t('title_suffix') }}</title><style>{{ css|safe }}</style></head><body>
<div class="wrap">
  <div class="top"><img src="/static/thelsa_logo.png" alt="Thelsa">
    <nav>{% if admin %}<a href="/assistant/admin">{{ t('nav_admin') }}</a>{% endif %}
      <a href="/assistant">{{ t('nav_dashboard') }}</a><a href="/">{{ t('nav_library') }}</a>
      <span class="lang" role="group" aria-label="Language / Idioma">
        <a href="/assistant/lang/en?next={{ here }}" class="{{ 'on' if lang == 'en' else '' }}">EN</a>
        <a href="/assistant/lang/es?next={{ here }}" class="{{ 'on' if lang == 'es' else '' }}">ES</a></span></nav></div>
  {{ body|safe }}
</div></body></html>"""


def _here():
    try:
        p = request.path
    except RuntimeError:
        return "/assistant"
    return p if p.startswith("/assistant") and not p.startswith("/assistant/lang") else "/assistant"


def _page(title, body, admin=False, lang="en"):
    return render_template_string(SHELL, title=title, css=CSS, body=body, admin=admin, lang=lang,
                                  here=_here(), t=lambda k, **kw: i18n.t(lang, k, **kw))


def _render(title, tpl, u, lang=None, **ctx):
    lang = lang or user_lang(u)
    body = render_template_string(tpl, csrf=_csrf_token(), u=u, lang=lang,
                                  t=lambda k, **kw: i18n.t(lang, k, **kw), **ctx)
    return _page(title, body, admin=(u and u["role"] == "admin"), lang=lang)


@bp.route("/assistant/lang/<code>")
@login_required
def set_language(u, code):
    if code in i18n.LANGS:
        db.set_lang(u["id"], code)
    nxt = request.args.get("next") or "/assistant"
    if not nxt.startswith("/assistant") or nxt.startswith("//"):
        nxt = "/assistant"
    return redirect(nxt)


# ── Consent ────────────────────────────────────────────────────────────────────
CONSENT_TPL = """
<div class="box">
  <h1 style="font-size:21px">{{ t('consent_title') }}</h1>
  <p class="sub">{{ t('consent_sub') }}</p>
  <ul>
    <li>{{ t('consent_reads')|safe }}</li>
    <li>{{ t('consent_often')|safe }}</li>
    <li>{{ t('consent_stores')|safe }}</li>
    <li>{{ t('consent_drafts')|safe }}</li>
    <li>{{ t('consent_privacy')|safe }}</li>
    <li>{{ t('consent_stop')|safe }}</li>
  </ul>
  <form method="post" action="/assistant/consent">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label class="ck"><input type="checkbox" name="agree" required>
      <span>{{ t('consent_agree') }}</span></label>
    <label class="ck"><input type="checkbox" name="whatsapp">
      <span>{{ t('consent_whatsapp')|safe }}</span></label>
    <button class="btn" type="submit">{{ t('continue') }}</button>
  </form>
</div>"""


@bp.route("/assistant/consent", methods=["POST"])
@login_required
def consent(u):
    _check_csrf()
    if not request.form.get("agree"):
        return redirect(url_for("assistant.dashboard"))
    db.set_consent(u["id"], whatsapp_opt_in=bool(request.form.get("whatsapp")))
    return redirect(url_for("assistant.connect_microsoft"))


# ── Connect / disconnect ──────────────────────────────────────────────────────
@bp.route("/assistant/connect/microsoft")
@login_required
def connect_microsoft(u):
    if not u["consent_at"] or not vault.is_configured():
        return redirect(url_for("assistant.dashboard"))
    session["asst_connect"] = True
    return redirect("/login/microsoft?next=/assistant%3Fconnected%3D1")


def on_microsoft_login(email, name, cache):
    """Called by the library's Microsoft callback when the user came from
    'Connect mailbox'. Stores the encrypted MSAL cache and kicks off a first scan."""
    u = db.upsert_user(email, name, role="admin" if email.lower() in _admins() else None)
    if not u["consent_at"]:
        return
    graph.store_cache(u["id"], cache, email)
    scan.scan_user_async(u["id"])


@bp.route("/assistant/disconnect/<provider>", methods=["POST"])
@login_required
def disconnect(u, provider):
    _check_csrf()
    if provider not in ("microsoft", "whatsapp", "moveware", "clickup"):
        abort(404)
    db.disconnect(u["id"], provider)
    if provider == "whatsapp":
        db.set_consent(u["id"], whatsapp_opt_in=False)
    return redirect(url_for("assistant.dashboard"))


# ── Dashboard ──────────────────────────────────────────────────────────────────
DASH_TPL = """
<h1>{{ greeting }}, {{ first }}.</h1>
<p class="sub">{{ t('dash_sub') }}</p>

{% if not ready %}
  <div class="banner warn"><div style="flex:1"><b>{{ t('almost_ready') }}</b><br>
    <span class="sub" style="margin:0">{{ t('almost_ready_sub') }}</span></div></div>
{% elif not ms %}
  <div class="banner warn"><div style="flex:1"><b>{{ t('connect_title') }}</b><br>
    <span class="sub" style="margin:0">{{ t('connect_sub') }}</span></div>
    <a class="btn" href="/assistant/connect/microsoft">{{ t('connect_btn') }}</a></div>
{% elif ms.status == 'error' %}
  <div class="banner warn"><div style="flex:1"><b>{{ t('reconnect_title') }}</b><br>
    <span class="sub" style="margin:0">{{ ms.last_error or '' }}</span></div>
    <a class="btn" href="/assistant/connect/microsoft">{{ t('reconnect_btn') }}</a></div>
{% endif %}
{% if refreshing %}<div class="banner">{{ t('refreshing') }}</div>
<script>setTimeout(function(){location.href='/assistant'},15000)</script>{% endif %}

<div class="kpis">
  <div class="kpi urgent"><div class="n">{{ counts.urgent }}</div><div class="l">{{ t('urgent') }}</div></div>
  <div class="kpi today"><div class="n">{{ counts.today }}</div><div class="l">{{ t('today') }}</div></div>
  <div class="kpi"><div class="n">{{ counts.soon }}</div><div class="l">{{ t('soon') }}</div></div>
  {% if has.moveware %}<div class="kpi"><div class="n">{{ counts.moveware }}</div><div class="l">{{ t('kpi_moveware') }}</div></div>{% endif %}
  {% if has.clickup %}<div class="kpi"><div class="n">{{ counts.clickup }}</div><div class="l">{{ t('kpi_clickup') }}</div></div>{% endif %}
  <div class="kpi"><div class="n">{{ counts.mail }}</div><div class="l">{{ t('kpi_mail') }}</div></div>
</div>

<div class="fresh">
  <span>{{ t('fresh_mail') }}: <b>{{ fresh.microsoft }}</b></span>
  {% if has.moveware %}<span>Moveware: <b>{{ fresh.moveware }}</b></span>{% endif %}
  {% if has.clickup %}<span>ClickUp: <b>{{ fresh.clickup }}</b></span>{% endif %}
  {% if wa_on %}<span>WhatsApp: <b>{{ fresh.whatsapp }}</b> <a href="/assistant/whatsapp">{{ t('set_up') }}</a></span>{% endif %}
  <span>{{ t('fresh_next') }}: <b>{{ next_run }}</b></span>
</div>

<div class="bar">
  <button class="chip on" data-f="all">{{ t('all') }}</button>
  <button class="chip" data-f="microsoft">{{ source_label('microsoft') }}</button>
  {% if has.moveware %}<button class="chip" data-f="moveware">Moveware</button>{% endif %}
  {% if has.clickup %}<button class="chip" data-f="clickup">ClickUp</button>{% endif %}
  {% if wa_on %}<button class="chip" data-f="whatsapp">WhatsApp</button>{% endif %}
  <span class="spacer"></span>
  <form method="post" action="/assistant/refresh"><input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn light" type="submit">{{ t('refresh_now') }}</button></form>
</div>

{% if not items %}
  <div class="empty">{{ t('empty') }}</div>
{% endif %}
{% for key, color in tiers %}
  {% set group = items | selectattr('tier', 'equalto', key) | list %}
  {% if group %}
  <div class="tier-h" style="color:{{ color }}"><span class="dot" style="background:{{ color }}"></span>{{ t(key) }} ({{ group|length }})</div>
  {% for it in group %}
  <div class="card {{ it.tier }} {% if it.seen %}seen{% endif %}" data-src="{{ it.source }}" id="i-{{ it.id }}">
    <div class="row"><div class="body">
      <span class="tag {{ it.source }}">{{ source_label(it.source) }}</span>{% if it.kind != 'whatsapp' %}<span class="tag kind">{{ kind_label(it.kind) }}</span>{% endif %}
      <div class="title">{{ it.subject }}</div>
      <div class="who">{{ it.from_name or '' }}{% if it.from_addr %} &lt;{{ it.from_addr }}&gt;{% endif %}</div>
      {% if it.snippet %}<div class="snip">{{ it.snippet[:260] }}</div>{% endif %}
    </div>
    <div class="side" title="{{ t('urgency_title', score=it.score) }}">{% if it.received_at %}{% if it.source == 'moveware' %}{{ t('side_pack') if it.kind == 'request_docs' else t('side_since') }} {% elif it.source == 'clickup' %}{{ t('side_last_step') }} {% endif %}{% endif %}{{ when(it.received_at) }}</div></div>
    <div class="acts">
      {% if it.url %}<a class="btn small light" href="/assistant/item/{{ it.id }}/open" target="_blank" rel="noopener">{{ t('open') }}</a>{% endif %}
      {% if it.source == 'microsoft' and draft_on %}
      <form method="post" action="/assistant/item/{{ it.id }}/draft"><input type="hidden" name="csrf" value="{{ csrf }}">
        <button class="btn small light" type="submit">{{ t('suggest_reply') }}</button></form>{% endif %}
      <form method="post" action="/assistant/item/{{ it.id }}/seen"><input type="hidden" name="csrf" value="{{ csrf }}">
        <input type="hidden" name="seen" value="{{ '0' if it.seen else '1' }}">
        <button class="btn small light" type="submit">{{ t('undo_done') if it.seen else t('done') }}</button></form>
    </div>
    {% for d in drafts.get(it.id, []) %}
    <div class="draft">
      <form method="post" action="/assistant/draft/{{ d.id }}/save"><input type="hidden" name="csrf" value="{{ csrf }}">
        <textarea name="body">{{ d.body }}</textarea>
        <div class="acts">
          {% if d.status == 'saved' %}<span class="ok">{{ t('draft_saved') }}</span>
          {% elif can_write %}<button class="btn small" type="submit">{{ t('draft_save') }}</button>
          {% else %}<span class="sub" style="margin:0">{{ t('draft_copy_hint') }}</span>{% endif %}
          <button class="btn small light" type="button" data-copied="{{ t('copied') }}" onclick="navigator.clipboard.writeText(this.form.body.value);this.textContent=this.dataset.copied">{{ t('copy') }}</button>
        </div></form>
    </div>
    {% endfor %}
  </div>
  {% endfor %}
  {% endif %}
{% endfor %}

<div class="foot">
  {{ t('foot_private') }}
  {% if has.moveware %}{{ t('foot_moveware') }}{% if u.moveware_email %} ({{ u.moveware_email }}){% endif %}{% endif %}
  {% if has.clickup %}{{ t('foot_clickup_all') if u.tim_scope == 'all' else t('foot_clickup_assigned') }}{% endif %}
  {% if ms %}<form method="post" action="/assistant/disconnect/microsoft" style="display:inline"
     data-confirm="{{ t('disconnect_confirm') }}" onsubmit="return confirm(this.dataset.confirm)">
     <input type="hidden" name="csrf" value="{{ csrf }}"><button class="btn small light" type="submit">{{ t('disconnect_mailbox') }}</button></form>{% endif %}
  {% if not wa_on %}<a href="/assistant/whatsapp">{{ t('add_whatsapp') }}</a>{% endif %}
</div>
<script>
document.querySelectorAll('.chip').forEach(function(c){c.onclick=function(){
  document.querySelectorAll('.chip').forEach(function(x){x.classList.remove('on')});c.classList.add('on');
  var f=c.dataset.f;document.querySelectorAll('.card').forEach(function(k){
    k.style.display=(f==='all'||k.dataset.src===f)?'':'none'});
  document.querySelectorAll('.tier-h').forEach(function(h){var n=h.nextElementSibling,any=false;
    while(n&&n.classList.contains('card')){if(n.style.display!=='none')any=true;n=n.nextElementSibling}
    h.style.display=any?'':'none'});
}});
</script>"""


def _next_run_label(lang="en"):
    now = _dt.datetime.now(MX)
    hours = sorted(int(h) for h in scan.HOURS.split(",") if h.strip())
    for h in hours:
        t = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if t > now:
            return t.strftime("%-I:%M %p")
    return i18n.t(lang, "tomorrow_at", time=now.replace(hour=hours[0], minute=0).strftime("%-I:%M %p"))


@bp.route("/assistant")
@login_required
def dashboard(u):
    lang = user_lang(u)
    if not u["consent_at"]:
        return _render(i18n.t(lang, "get_started"), CONSENT_TPL, u, lang=lang)
    ms = db.get_connection(u["id"], "microsoft")
    ranked = priority.rank(db.list_items(u["id"]))
    for it in ranked:
        it["subject"], it["snippet"] = i18n.item_text(it, lang)
    wa_on = bool(u["whatsapp_opt_in"])
    if not wa_on:
        ranked = [i for i in ranked if i["source"] != "whatsapp"]
    drafts = {}
    for d in db.list_drafts(u["id"]):
        if d["status"] != "dismissed":
            drafts.setdefault(d["item_id"], []).append(d)
    for k in drafts:
        drafts[k] = drafts[k][:1]
    st = db.sync_times(u["id"])
    fresh = {s: _stamp(st.get(s), lang) for s in ("microsoft", "moveware", "clickup", "whatsapp")}
    if not ms:
        fresh["microsoft"] = i18n.t(lang, "not_connected")
    # Only show a source's box / filter / "as of" line if this person has items from it.
    has = {src: any(i["source"] == src for i in ranked) for src in ("moveware", "clickup")}
    counts = {t: sum(1 for i in ranked if i["tier"] == t and not i["seen"])
              for t in ("urgent", "today", "soon")}
    counts["moveware"] = sum(1 for i in ranked if i["source"] == "moveware" and not i["seen"])
    counts["clickup"] = sum(1 for i in ranked if i["source"] == "clickup" and not i["seen"])
    counts["mail"] = sum(1 for i in ranked if i["source"] == "microsoft" and not i["seen"])
    hour = _dt.datetime.now(MX).hour
    greeting = i18n.t(lang, "good_morning" if hour < 12 else ("good_afternoon" if hour < 19 else "good_evening"))
    return _render(i18n.t(lang, "nav_dashboard"), DASH_TPL, u, lang=lang,
                   first=(u["name"] or u["email"]).split(" ")[0].split("@")[0],
                   greeting=greeting, ms=ms, items=ranked, drafts=drafts, counts=counts,
                   fresh=fresh, wa_on=wa_on, next_run=_next_run_label(lang), has=has,
                   refreshing=request.args.get("refreshing") or request.args.get("connected"),
                   tiers=[("urgent", "#c0392b"), ("today", "#d68910"), ("soon", "#7f8c8d")],
                   source_label=lambda s_: i18n.source_label(s_, lang),
                   kind_label=lambda k_: i18n.kind_label(k_, lang),
                   when=lambda d_: _when(d_, lang),
                   draft_on=drafting.enabled() and ms is not None,
                   ready=vault.is_configured(),
                   can_write=graph.can_write_drafts())


@bp.route("/assistant/refresh", methods=["POST"])
@login_required
def refresh(u):
    _check_csrf()
    scan.scan_user_async(u["id"])
    return redirect("/assistant?refreshing=1")


def open_url(raw: str) -> str:
    """The link we actually send someone to when they press Open.

    Outlook's webLink carries exvsurl=1, which asks the browser to hand the
    message to the desktop Outlook app. Where no handler is registered — or the
    dashboard is inside a sandboxed frame — that hand-off can end in a tab that
    never renders, which reads as "the button does nothing". Stripping it makes
    the message open in Outlook on the web, which always works in a browser.
    Anything that is not an http(s) URL is refused, so a bad stored value can
    never turn into a javascript: or data: link.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() != "exvsurl"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query), parts.fragment))


@bp.route("/assistant/item/<item_id>/open")
@login_required
def item_open(u, item_id):
    """Same-origin redirect to the item's source. Going through the server keeps
    the click working even where a new tab is blocked, and keeps message ids out
    of the page."""
    it = db.get_item(u["id"], item_id)          # None for anyone else's item
    if not it:
        abort(404)
    target = open_url(it["url"])
    if not target:
        abort(404)
    return redirect(target)


@bp.route("/assistant/item/<item_id>/seen", methods=["POST"])
@login_required
def item_seen(u, item_id):
    _check_csrf()
    db.mark_seen(u["id"], item_id, request.form.get("seen", "1") == "1")
    return redirect(f"/assistant#i-{item_id}")


@bp.route("/assistant/item/<item_id>/draft", methods=["POST"])
@login_required
def item_draft(u, item_id):
    _check_csrf()
    it = db.get_item(u["id"], item_id)
    if not it or it["source"] != "microsoft":
        abort(404)
    try:
        body = graph.get_message_text(u["id"], it["external_id"])
    except Exception:
        body = it["snippet"] or ""
    try:
        text = drafting.suggest_reply(u["name"] or u["email"], f"{it['from_name']} <{it['from_addr']}>",
                                      it["subject"] or "", body)
    except Exception as exc:
        text = i18n.t(user_lang(u), "draft_error", err=exc)
    db.save_draft(u["id"], item_id, text)
    return redirect(f"/assistant#i-{item_id}")


@bp.route("/assistant/draft/<draft_id>/save", methods=["POST"])
@login_required
def draft_save(u, draft_id):
    _check_csrf()
    d = next((x for x in db.list_drafts(u["id"]) if x["id"] == draft_id), None)
    if not d:
        abort(404)
    it = db.get_item(u["id"], d["item_id"])
    body = (request.form.get("body") or d["body"]).strip()
    db.update_draft(u["id"], draft_id, body=body)
    try:
        res = graph.create_reply_draft(u["id"], it["external_id"], body)
        db.update_draft(u["id"], draft_id, status="saved", provider_draft_id=res.get("id"))
    except Exception as exc:
        db.update_draft(u["id"], draft_id,
                        body=f"{body}\n\n" + i18n.t(user_lang(u), "draft_not_saved", err=exc))
    return redirect(f"/assistant#i-{it['id']}")


# ── WhatsApp (opt-in beta) ────────────────────────────────────────────────────
WA_TPL = """
<h1>WhatsApp <span class="tag">beta</span></h1>
<p class="sub">{{ t('wa_intro')|safe }}</p>
{% if not u.whatsapp_opt_in %}
  <form method="post" action="/assistant/whatsapp/optin"><input type="hidden" name="csrf" value="{{ csrf }}">
    <label class="ck"><input type="checkbox" name="agree" required><span>{{ t('wa_agree') }}</span></label>
    <button class="btn" type="submit">{{ t('wa_turn_on') }}</button></form>
{% else %}
  <div class="banner"><div style="flex:1">
    {{ t('wa_step1')|safe }}<br>{{ t('wa_step2')|safe }}<br>{{ t('wa_step3')|safe }}<br>{{ t('wa_step4')|safe }}</div></div>
  {% if key %}<div class="banner warn"><div>{{ t('wa_key')|safe }}<br><code>{{ key }}</code></div></div>{% endif %}
  <p class="sub">{{ t('wa_last') }}: <b>{{ last }}</b></p>
  <form method="post" action="/assistant/whatsapp/key" style="display:inline"><input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn light" type="submit">{{ t('wa_new_key') if has_key else t('wa_first_key') }}</button></form>
  <form method="post" action="/assistant/disconnect/whatsapp" style="display:inline"><input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn light" type="submit">{{ t('wa_off')|safe }}</button></form>
{% endif %}"""


@bp.route("/assistant/whatsapp")
@login_required
def whatsapp(u):
    key = session.pop("asst_wa_key", None)
    lang = user_lang(u)
    return _render("WhatsApp", WA_TPL, u, lang=lang, key=key,
                   has_key=db.get_connection(u["id"], "whatsapp") is not None,
                   last=_stamp(db.sync_times(u["id"]).get("whatsapp"), lang))


@bp.route("/assistant/whatsapp/optin", methods=["POST"])
@login_required
def whatsapp_optin(u):
    _check_csrf()
    if request.form.get("agree"):
        db.set_consent(u["id"], whatsapp_opt_in=True)
    return redirect(url_for("assistant.whatsapp"))


@bp.route("/assistant/whatsapp/key", methods=["POST"])
@login_required
def whatsapp_key(u):
    _check_csrf()
    if not u["whatsapp_opt_in"]:
        abort(403)
    session["asst_wa_key"] = db.new_whatsapp_key(u["id"])
    return redirect(url_for("assistant.whatsapp"))


@bp.route("/assistant/whatsapp/extension.zip")
@login_required
def whatsapp_extension(u):
    base = request.host_url.rstrip("/")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(EXT_DIR.iterdir()):
            if p.is_file():
                data = p.read_text().replace("__SERVER__", base)
                z.writestr(f"thelsa-assistant-whatsapp/{p.name}", data)
    return Response(buf.getvalue(), mimetype="application/zip", headers={
        "Content-Disposition": "attachment; filename=thelsa-assistant-whatsapp.zip"})


@bp.route("/api/assistant/whatsapp/sync", methods=["POST", "OPTIONS"])
def whatsapp_sync():
    if request.method == "OPTIONS":
        return ("", 204)
    auth = request.headers.get("Authorization", "")
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    u = db.user_for_whatsapp_key(key)
    if not u:
        return jsonify({"ok": False, "error": "invalid key or WhatsApp not enabled"}), 401
    chats = (request.get_json(silent=True) or {}).get("chats") or []
    kept = []
    for c in chats[:60]:
        name = str(c.get("name") or "")[:200].strip()
        unread = int(c.get("unread") or 0)
        if not name or unread <= 0:
            continue
        if not wa_triage.passes_watch(name, u.get("wa_watch")):
            continue
        kept.append({"name": name, "unread": unread,
                     "preview": str(c.get("preview") or "")[:300],
                     "time": str(c.get("time") or "")[:40]})

    # Work/personal pass. A chat with no verdict was not judged, so it stays.
    verdicts = wa_triage.classify(kept)
    found = []
    for c in kept:
        v = verdicts.get(wa_triage.key(c["name"], c["preview"])) or {}
        if v.get("work") is False:
            continue
        name, unread = c["name"], c["unread"]
        action = v.get("action") or ""
        found.append({"kind": "whatsapp", "external_id": "wa:" + name.lower()[:480],
                      "from_name": name, "from_addr": None,
                      "subject": f"{unread} unread message{'s' if unread != 1 else ''} from {name}",
                      "snippet": action or c["preview"],
                      "url": "https://web.whatsapp.com", "received_at": db.now(),
                      "meta": {"unread": unread, "time": c["time"],
                               "action": action, "triaged": bool(v)}})
    db.replace_items(u["id"], "whatsapp", found)
    db.mark_connection(u["id"], "whatsapp", ok=True)
    return jsonify({"ok": True, "items": len(found)})


# ── Scheduler backup trigger ──────────────────────────────────────────────────
@bp.route("/assistant/cron")
def cron():
    tok = os.environ.get("CRON_TOKEN", "")
    if not tok or not secrets.compare_digest(request.args.get("token", ""), tok):
        return ("forbidden", 403)
    scan.run_all_async("cron")
    return jsonify({"ok": True, "started": True})


# ── Admin ──────────────────────────────────────────────────────────────────────
ADMIN_TPL = """
<h1>Assistant admin</h1>
<p class="sub">Connection health for every user. You can see whether each assistant is working —
never anyone's emails or to-dos.</p>
<div class="banner"><div style="flex:1">
  Encryption key: {% if vault_ok %}<span class="ok">configured</span>{% else %}<span class="bad">TOKEN_ENC_KEY missing</span>{% endif %} ·
  Database: <b>{{ db_kind }}</b> ·
  Drafting: {% if draft_on %}<span class="ok">on</span>{% else %}<span class="bad">ANTHROPIC_API_KEY missing</span>{% endif %} ·
  Save to Outlook: {% if can_write %}<span class="ok">on</span>{% else %}off (needs Mail.ReadWrite){% endif %} ·
  Scheduler: {% if sched %}<span class="ok">running</span>{% else %}<span class="bad">not running here</span>{% endif %}</div>
  <form method="post" action="/assistant/admin/run"><input type="hidden" name="csrf" value="{{ csrf }}">
    <button class="btn" type="submit">Run all now</button></form></div>

<h2>Add a user</h2>
<form method="post" action="/assistant/admin/add" class="bar"><input type="hidden" name="csrf" value="{{ csrf }}">
  <input type="email" name="email" placeholder="name@thelsa.com" required>
  <input type="text" name="name" placeholder="Full name">
  <button class="btn" type="submit">Add</button>
  <span class="sub" style="margin:0">They open the AI Assistant tile, sign in, and connect their mailbox.</span></form>

<h2>Users</h2>
<table><tr><th>User</th><th>Mailbox</th><th>WhatsApp</th><th>Moveware email</th><th>Also sees (Moveware)</th><th>TIM (ClickUp)</th><th>WhatsApp chats</th><th></th></tr>
{% for r in rows %}<tr>
  <td><b>{{ r.name or r.email }}</b><br>{{ r.email }}{% if r.role=='admin' %} · admin{% endif %}
      {% if not r.active %}<br><span class="bad">deactivated</span>{% endif %}
      {% if not r.consent %}<br><span class="sub">hasn't opened yet</span>{% endif %}</td>
  <td>{% if r.microsoft %}<span class="{{ 'ok' if r.microsoft.status=='connected' else 'bad' }}">{{ r.microsoft.status }}</span><br>
      last OK {{ stamp(r.microsoft.last_ok_at) }}{% if r.microsoft.last_error %}<br><span class="bad">{{ r.microsoft.last_error[:120] }}</span>{% endif %}
      {% else %}—{% endif %}</td>
  <td>{% if r.whatsapp %}{{ r.whatsapp.status }}<br>{{ stamp(r.whatsapp.last_ok_at) }}{% else %}—{% endif %}</td>
  <td><form method="post" action="/assistant/admin/user/{{ r.id }}/moveware"><input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="email" name="mw" value="{{ r.moveware_email or '' }}" placeholder="same as login" style="width:170px">
      <button class="btn small light" type="submit">Save</button></form></td>
  <td><form method="post" action="/assistant/admin/user/{{ r.id }}/mwwatch"><input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="text" name="watch" value="{{ r.mw_watch or '' }}" placeholder="coordinator emails, comma-separated" style="width:230px">
      <button class="btn small light" type="submit">Save</button>
      <br><span class="sub" style="margin:0">Their files appear on this dashboard too.</span></form>
      <form method="post" action="/assistant/admin/user/{{ r.id }}/embassy"><input type="hidden" name="csrf" value="{{ csrf }}">
        <input type="hidden" name="on" value="{{ '0' if r.mw_embassy else '1' }}">
        <button class="btn small {{ 'light' if r.mw_embassy else 'light' }}" type="submit">
          {{ '☑' if r.mw_embassy else '☐' }} All US Embassy / Consulate files</button></form></td>
  <td><form method="post" action="/assistant/admin/user/{{ r.id }}/tim"><input type="hidden" name="csrf" value="{{ csrf }}">
      <select name="scope" onchange="this.form.submit()">
        {% for v, l in [('assigned','Assigned only'),('all','All TIM files'),('none','None')] %}
        <option value="{{ v }}" {% if (r.tim_scope or 'assigned') == v %}selected{% endif %}>{{ l }}</option>{% endfor %}
      </select></form></td>
  <td>{% if r.whatsapp %}<form method="post" action="/assistant/admin/user/{{ r.id }}/wa"><input type="hidden" name="csrf" value="{{ csrf }}">
      <input type="text" name="wa" value="{{ r.wa_watch or '' }}" placeholder="all chats" style="width:170px">
      <button class="btn small light" type="submit">Save</button>
      <br><span class="sub" style="margin:0">Names to keep. Empty = all.
      {% if wa_ai %}Personal chats are filtered out by AI.{% else %}AI filtering off (no API key).{% endif %}</span></form>
      {% else %}—{% endif %}</td>
  <td><form method="post" action="/assistant/admin/user/{{ r.id }}/active"
        {% if r.active %}onsubmit="return confirm('Deactivate and delete this user\\'s stored sign-in and data?')"{% endif %}>
      <input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="active" value="{{ '0' if r.active else '1' }}">
      <button class="btn small {{ 'danger' if r.active else 'light' }}" type="submit">{{ 'Deactivate' if r.active else 'Reactivate' }}</button></form></td>
</tr>{% endfor %}</table>

<h2 style="margin-top:22px">Recent runs</h2>
<table><tr><th>Started (MX)</th><th>Trigger</th><th>Users</th><th>Errors</th><th>Notes</th></tr>
{% for r in runs %}<tr><td>{{ stamp(r.started_at) }}</td><td>{{ r.trigger }}</td><td>{{ r.users_scanned }}</td>
  <td>{{ r.errors }}</td><td style="white-space:pre-wrap">{{ (r.note or '')[:400] }}</td></tr>{% endfor %}</table>"""


@bp.route("/assistant/admin")
@admin_required
def admin(u):
    by_user = {}
    for r in db.connection_health():
        e = by_user.setdefault(r["id"], {"id": r["id"], "email": r["email"], "name": r["name"],
                                         "active": r["active"], "role": r["role"]})
        if r["provider"]:
            e[r["provider"]] = {"status": r["status"], "last_ok_at": r["last_ok_at"],
                                "last_error": r["last_error"]}
    for usr in db.list_users():
        if usr["id"] in by_user:
            by_user[usr["id"]]["consent"] = bool(usr["consent_at"])
            by_user[usr["id"]]["moveware_email"] = usr["moveware_email"]
            by_user[usr["id"]]["mw_watch"] = usr["mw_watch"]
            by_user[usr["id"]]["wa_watch"] = usr["wa_watch"]
            by_user[usr["id"]]["mw_embassy"] = usr["mw_embassy"]
            by_user[usr["id"]]["tim_scope"] = usr["tim_scope"]
    return _render("Admin", ADMIN_TPL, u, rows=list(by_user.values()), runs=db.recent_runs(15),
                   stamp=_stamp, vault_ok=vault.is_configured(),
                   db_kind="Postgres" if db._db_url().startswith("postgresql") else "SQLite (temporary!)",
                   draft_on=drafting.enabled(), can_write=graph.can_write_drafts(),
                   wa_ai=wa_triage.enabled(),
                   sched=scan._sched is not None)


@bp.route("/assistant/admin/add", methods=["POST"])
@admin_required
def admin_add(u):
    _check_csrf()
    email = (request.form.get("email") or "").strip().lower()
    allowed = {d.strip().lower() for d in os.environ.get(
        "ALLOWED_EMAIL_DOMAINS", "thelsa.com,inflectionpointnow.com").split(",") if d.strip()}
    if "@" in email and email.rsplit("@", 1)[1] in allowed:
        db.upsert_user(email, request.form.get("name") or None)
    return redirect(url_for("assistant.admin"))


@bp.route("/assistant/admin/user/<user_id>/moveware", methods=["POST"])
@admin_required
def admin_moveware(u, user_id):
    _check_csrf()
    db.set_moveware_email(user_id, request.form.get("mw"))
    scan.scan_user_async(user_id)
    return redirect(url_for("assistant.admin"))


@bp.route("/assistant/admin/user/<user_id>/wa", methods=["POST"])
@admin_required
def admin_wa_watch(u, user_id):
    _check_csrf()
    db.set_wa_watch(user_id, request.form.get("wa"))
    return redirect(url_for("assistant.admin"))


@bp.route("/assistant/admin/user/<user_id>/embassy", methods=["POST"])
@admin_required
def admin_mw_embassy(u, user_id):
    _check_csrf()
    db.set_mw_embassy(user_id, request.form.get("on") == "1")
    scan.scan_user_async(user_id)
    return redirect(url_for("assistant.admin"))


@bp.route("/assistant/admin/user/<user_id>/mwwatch", methods=["POST"])
@admin_required
def admin_mw_watch(u, user_id):
    _check_csrf()
    db.set_mw_watch(user_id, request.form.get("watch"))
    scan.scan_user_async(user_id)
    return redirect(url_for("assistant.admin"))


@bp.route("/assistant/admin/user/<user_id>/tim", methods=["POST"])
@admin_required
def admin_tim(u, user_id):
    _check_csrf()
    try:
        db.set_tim_scope(user_id, request.form.get("scope", "assigned"))
    except ValueError:
        abort(400)
    scan.scan_user_async(user_id)
    return redirect(url_for("assistant.admin"))


@bp.route("/assistant/admin/user/<user_id>/active", methods=["POST"])
@admin_required
def admin_active(u, user_id):
    _check_csrf()
    if user_id == u["id"]:
        abort(400, "You can't deactivate yourself.")
    db.set_active(user_id, request.form.get("active") == "1")
    return redirect(url_for("assistant.admin"))


@bp.route("/assistant/admin/run", methods=["POST"])
@admin_required
def admin_run(u):
    _check_csrf()
    scan.run_all_async("manual")
    return redirect(url_for("assistant.admin"))
