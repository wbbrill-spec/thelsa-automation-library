"""
The shared to-do format ("assistant-todo/1") and the rules every product's
to-dos pass through before they reach someone's dashboard.

WHY ONE FORMAT (Bill, 1 Nov 2026): AuditTrack, QuoteRelay and the WhatsApp AI
Assistant each keep doing their own job and stay the record of truth. Each
hands the Assistant its CURRENT list of open to-dos in this one shape; the
Assistant routes them to people, groups them by move file, ranks them and
drafts the follow-ups. A to-do disappears when the product stops listing it,
so nothing is ever ticked off in two places.

A product's feed (JSON):
    {"contract": "assistant-todo/1", "product": "quoterelay", "tenant": "thelsa",
     "generated_at": ISO, "complete": true, "items": [ITEM, ...]}
`complete: false` means the product could not read everything this time: the
Assistant then keeps the last complete list rather than dropping to-dos that
are still real (same rule as the missing-dates email).

ITEM:
    id           stable per to-do inside the product (the same quote's day-5
                 follow-up keeps one id until it is done)
    kind         short code, <= 20 chars ("quote_followup", "wa_unanswered" ...)
    owner_email  who must act. Must be that person's OWN address: a name and an
                 address that do not belong together are rejected (the 19 Oct
                 2026 lesson, when three coordinators' lists went to Edgar).
    owner_name   their name as the product knows it (optional)
    role         sales_rep | move_manager | coordinator | advisor | billing |
                 procurement | collections — what kind of person must act. Used
                 only when no owner is named, to reach the company's people for
                 that role; never to swap one person for another (the sales rep
                 is never replaced by the move manager).
    file         Moveware display number when known ("110862", "110873A")
    title, detail  {"en": ..., "es": ...}
    due          YYYY-MM-DD the item is due, or null
    since        ISO time the clock started (request received, quote sent ...)
    urgency      0-100, the product's own judgement (Urgent >= 75, Today >= 50)
    sticky       true: opening it does not lower it; only fixing it clears it
    link         where it gets fixed (https only)
    customer     the customer / agent name, shown to the owner only
    draft        null, or the facts for a follow-up message:
                 {channel: email|whatsapp, to, cc, language, purpose,
                  facts: {...}, reply_to_message_id, handled_by_product,
                  window_open (whatsapp only)}
                 Drafts are written from these facts only and never sent.

Nothing here talks to a product; this module only checks and reshapes.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re

CONTRACT = "assistant-todo/1"
ROLES = ("sales_rep", "move_manager", "coordinator", "advisor", "billing",
         "procurement", "collections")
_TENANT_DIR = os.path.join(os.path.dirname(__file__), "tenants")
_CACHE: dict = {}


# ── Company settings ──────────────────────────────────────────────────────────
def tenant_id() -> str:
    return (os.environ.get("ASSISTANT_TENANT") or "thelsa").strip().lower()


def settings(tid: str | None = None) -> dict:
    """assistant/tenants/<id>.json — read once per process. Missing file → {}."""
    tid = (tid or tenant_id())
    if tid not in _CACHE:
        try:
            with open(os.path.join(_TENANT_DIR, f"{tid}.json"), encoding="utf-8") as fh:
                _CACHE[tid] = json.load(fh)
        except Exception:
            _CACHE[tid] = {}
    return _CACHE[tid]


def people(role: str) -> list:
    """Addresses that receive to-dos of this role when nobody is named."""
    p = settings().get("people") or {}
    key = "key_users" if role == "key_users" else role
    return [e.strip().lower() for e in (p.get(key) or []) if "@" in str(e)]


def key_users() -> set:
    return set(people("key_users"))


# ── Owners ────────────────────────────────────────────────────────────────────
def owner_ok(name: str, email: str) -> bool:
    """An address is used only if it is that person's own (or no name is given:
    the product read it from the person's own mailbox)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return False
    if not (name or "").strip() or (name or "").strip().lower() in ("unassigned", "-"):
        return True
    try:
        import coordinator_alerts as ca
        return ca.name_matches(name, email)
    except Exception:
        return True


def people_directory(files=None) -> dict:
    """{lower name: own email} for staff: the confirmed map plus every file on
    which Moveware's name and address belong together."""
    out = {}
    try:
        import coordinator_alerts as ca
        out.update({k: v.lower() for k, v in ca._coordinator_map().items()})
    except Exception:
        ca = None
    for f in files or []:
        for nm in (f.get("mw_coordinator"), f.get("coordinator")):
            em = str(f.get("coordinator_email") or "").strip().lower()
            if nm and em and ca is not None and ca.name_matches(nm, em):
                out.setdefault(str(nm).strip().lower(), em)
    return {k: v for k, v in out.items() if ca is None or ca.name_matches(k, v)}


def resolve_owner(name: str, email: str = "", directory: dict | None = None) -> str:
    """The person's own address, or '' (never someone else's)."""
    if owner_ok(name, email):
        return (email or "").strip().lower()
    nm = (name or "").strip().lower()
    if nm and directory and directory.get(nm):
        return directory[nm]
    return ""


# ── Files ─────────────────────────────────────────────────────────────────────
_FILE = re.compile(r"^\s*(\d{5,7})([A-Z]{0,2})\s*$", re.I)


def base_file(job) -> str:
    """'110873A' → '110873'. Groups a file's to-dos; each to-do keeps its own
    job number (a sequel is its own move — guide §2)."""
    m = _FILE.match(str(job or ""))
    return m.group(1) if m else str(job or "").strip()


# ── Validation / reshaping ────────────────────────────────────────────────────
def _txt(v, n=400) -> dict:
    if isinstance(v, dict):
        return {k: str(v.get(k) or "")[:n] for k in ("en", "es") if v.get(k)}
    return {"en": str(v or "")[:n]} if v else {}


def _iso_date(v):
    try:
        return _dt.date.fromisoformat(str(v)[:10]).isoformat() if v else None
    except ValueError:
        return None


def _iso_time(v):
    if not v:
        return None
    try:
        t = _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return (t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)).isoformat()
    except ValueError:
        return None


def _link(v) -> str:
    v = str(v or "").strip()
    return v if v.startswith("https://") or v.startswith("http://localhost") else ""


def _draft(d):
    if not isinstance(d, dict):
        return None
    ch = str(d.get("channel") or "email").lower()
    if ch not in ("email", "whatsapp"):
        return None
    to = str(d.get("to") or "").strip()
    cc = [str(x).strip() for x in (d.get("cc") or []) if "@" in str(x)][:10]
    facts = d.get("facts") if isinstance(d.get("facts"), dict) else {}
    return {"channel": ch, "to": to if "@" in to else "", "cc": cc,
            "language": "en" if str(d.get("language") or "").lower().startswith("en") else "es",
            "purpose": str(d.get("purpose") or "")[:300],
            "facts": {str(k)[:40]: (v if isinstance(v, (int, float, bool)) or v is None else str(v)[:300])
                      for k, v in list(facts.items())[:25]},
            "reply_to_message_id": str(d.get("reply_to_message_id") or "")[:512],
            "handled_by_product": bool(d.get("handled_by_product")),
            "window_open": bool(d.get("window_open", True))}


def clean_item(product: str, raw: dict) -> dict | None:
    """One product item → the stored shape, or None if it is unusable."""
    if not isinstance(raw, dict):
        return None
    iid = str(raw.get("id") or "").strip()
    kind = re.sub(r"[^a-z0-9_]", "", str(raw.get("kind") or "").lower())[:20]
    title = _txt(raw.get("title"), 200)
    if not iid or not kind or not title:
        return None
    try:
        urgency = max(0, min(100, int(raw.get("urgency") or 50)))
    except (TypeError, ValueError):
        urgency = 50
    role = str(raw.get("role") or "").lower()
    return {
        "product": product, "id": iid[:300], "kind": kind,
        "owner_email": str(raw.get("owner_email") or "").strip().lower(),
        "owner_name": str(raw.get("owner_name") or "").strip()[:120],
        "role": role if role in ROLES else "",
        "file": str(raw.get("file") or "").strip()[:20],
        "title": title, "detail": _txt(raw.get("detail"), 600),
        "due": _iso_date(raw.get("due")), "since": _iso_time(raw.get("since")),
        "urgency": urgency, "sticky": bool(raw.get("sticky")),
        "link": _link(raw.get("link")),
        "customer": str(raw.get("customer") or "").strip()[:160],
        "draft": _draft(raw.get("draft")),
    }


def parse_feed(product: str, payload) -> dict:
    """Check a product's feed. Returns {ok, complete, items, error, generated_at}."""
    if not isinstance(payload, dict):
        return {"ok": False, "error": "not a JSON object", "items": [], "complete": False}
    if payload.get("contract") != CONTRACT:
        return {"ok": False, "error": f"unknown contract {payload.get('contract')!r}",
                "items": [], "complete": False}
    items, seen, bad = [], set(), 0
    for raw in payload.get("items") or []:
        it = clean_item(product, raw)
        if not it or it["id"] in seen:
            bad += 1
            continue
        seen.add(it["id"])
        items.append(it)
    return {"ok": True, "complete": bool(payload.get("complete", True)), "items": items,
            "rejected": bad, "error": None, "generated_at": payload.get("generated_at")}


# ── Routing to one user ───────────────────────────────────────────────────────
def owners(item: dict, directory: dict | None = None) -> set:
    """Everyone this to-do belongs to: its verified owner, else the company's
    people for its role, else the key users. Never an unverified address."""
    em = resolve_owner(item.get("owner_name"), item.get("owner_email"), directory)
    if em:
        return {em}
    if item.get("role"):
        found = set(people(item["role"]))
        if found:
            return found
    return key_users()


def for_user(items: list, emails: set, directory: dict | None = None) -> list:
    """Product to-dos that belong on this user's dashboard, as asst_items rows."""
    emails = {e.lower() for e in emails or set() if e}
    out = []
    for it in items or []:
        mine = owners(it, directory)
        if not (mine & emails):
            continue
        out.append(to_row(it, unowned=not resolve_owner(it.get("owner_name"),
                                                        it.get("owner_email"), directory)))
    return out


def to_row(it: dict, unowned: bool = False) -> dict:
    """Stored dashboard row. Text in both languages lives in meta; i18n picks."""
    since = it.get("since")
    rec = None
    if since:
        try:
            rec = _dt.datetime.fromisoformat(since)
        except ValueError:
            rec = None
    return {
        "kind": it["kind"], "external_id": it["id"][:512],
        "from_name": it.get("customer") or it.get("owner_name") or "",
        "from_addr": None,
        "subject": (it["title"].get("en") or it["title"].get("es") or "")[:500],
        "snippet": (it["detail"].get("en") or it["detail"].get("es") or "")[:500],
        "url": it.get("link") or None, "received_at": rec,
        "meta": {"feed": it["product"], "title": it["title"], "detail": it["detail"],
                 "job": it.get("file") or "", "due": it.get("due"),
                 "urgency": it.get("urgency"), "sticky": it.get("sticky"),
                 "owner": it.get("owner_name") or "", "role": it.get("role") or "",
                 "unowned": unowned, "draft": it.get("draft")},
    }


def feed_payload(product: str, items: list, complete: bool = True, tenant: str | None = None) -> dict:
    """Wrap items as a feed (used by AuditTrack in-process and in tests)."""
    return {"contract": CONTRACT, "product": product, "tenant": tenant or tenant_id(),
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "complete": bool(complete), "items": items}
