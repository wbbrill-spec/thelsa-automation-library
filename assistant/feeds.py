"""
Reading the to-do lists of the separate products (QuoteRelay, the WhatsApp AI
Assistant) and handing each user their share.

  refresh_all()   once per scheduled run, BEFORE the users are scanned: one
                  request per product (never one per user), checked against
                  the shared format (assistant/todo.py). A complete good read
                  replaces that product's saved list; anything else (no token
                  yet, timeout, bad JSON, `complete: false`) keeps the last
                  good list and records why, for the admin page.
  items_for(user) every product's to-dos owned by this user (their own address,
                  their Moveware override, the people on their watch list), or
                  — for a to-do nobody owns — the company's people for that
                  role, else the key users. Never a stranger.

Products and their addresses come from the company settings
(assistant/tenants/<id>.json, "feeds"). Each product's token lives in the
environment variable the settings name: no token → that product is simply
"waiting for its token" on the admin page and nothing is requested.
Read-only: the Assistant only ever GETs a product's list.
"""
from __future__ import annotations

import os

import requests

from . import db, todo

TIMEOUT = int(os.environ.get("ASSISTANT_FEED_TIMEOUT", "45"))


def configured() -> list:
    """[{id, name, url, token_env, has_token}] from the company settings."""
    out = []
    for f in (todo.settings().get("feeds") or []):
        fid = str(f.get("id") or "").strip().lower()[:20]
        if not fid or not str(f.get("url") or "").startswith("https://"):
            continue
        env = str(f.get("token_env") or "")
        out.append({"id": fid, "name": f.get("name") or fid, "url": f["url"], "token_env": env,
                    "has_token": bool(env and os.environ.get(env, "").strip())})
    return out


def fetch(feed: dict) -> dict:
    """One product's list. Never raises: returns parse_feed()'s shape."""
    token = os.environ.get(feed.get("token_env") or "", "").strip()
    if not token:
        return {"ok": False, "error": f"waiting for its token ({feed.get('token_env')})",
                "items": [], "complete": False, "skipped": True}
    try:
        r = requests.get(feed["url"], timeout=TIMEOUT,
                         headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    except Exception as exc:                      # network
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                "items": [], "complete": False}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}", "items": [], "complete": False}
    try:
        payload = r.json()
    except ValueError:
        return {"ok": False, "error": "not JSON", "items": [], "complete": False}
    return todo.parse_feed(feed["id"], payload)


def refresh_all() -> dict:
    """Pull every configured product once. {id: short status} (no contents)."""
    report = {}
    for feed in configured():
        res = fetch(feed)
        if res.get("ok") and res.get("complete"):
            db.save_feed(feed["id"], items=res["items"], ok=True)
            report[feed["id"]] = f"{len(res['items'])} to-dos"
        else:
            why = res.get("error") or ("partial read: kept the last complete list"
                                       if res.get("ok") else "failed")
            db.save_feed(feed["id"], items=None, ok=False, error=why)
            report[feed["id"]] = why
    return report


def items_for(user, emails: set, directory: dict | None = None) -> dict:
    """{source: rows} for this user. A product never read yet is left out, so the
    caller keeps whatever that user had before (same rule as Moveware)."""
    out = {}
    for feed in configured():
        items, _at = db.get_feed(feed["id"])
        if items is None:
            continue
        out[feed["id"]] = todo.for_user(items, emails, directory)
    return out


def health() -> list:
    """Admin rows: configured products joined with their last read."""
    seen = {r["source"]: r for r in db.feed_health()}
    rows = []
    for feed in configured():
        h = seen.get(feed["id"]) or {}
        rows.append({"id": feed["id"], "name": feed["name"], "has_token": feed["has_token"],
                     "token_env": feed["token_env"], "good_at": h.get("good_at"),
                     "tried_at": h.get("tried_at"), "ok": h.get("ok"), "error": h.get("error"),
                     "count": h.get("count") or 0})
    au = seen.get("auditrack")
    if au:
        rows.insert(0, {"id": "auditrack", "name": "AuditTrack", "has_token": True, "token_env": "",
                        "good_at": au.get("good_at"), "tried_at": au.get("tried_at"),
                        "ok": au.get("ok"), "error": au.get("error"), "count": au.get("count") or 0})
    return rows
