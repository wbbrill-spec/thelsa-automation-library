"""
company_sync.py — read this company's settings from the one intake form
(Bill, 4 Nov 2026: one intake shared by QuoteRelay, AuditTrack and the AI
Assistant).

The company is described once, in QuoteRelay's onboarding wizard. When this
copy of the app sets COMPANY_SETTINGS_URL (e.g.
https://mattapp.inflectionpointnow.com/api/company/settings?t=acme), it reads
its settings from there with the company's token (COMPANY_SETTINGS_TOKEN, else
ASSISTANT_FEED_TOKEN_QUOTERELAY — the same per-company token the Assistant
already uses) instead of the settings files in the repo.

Safe by design:
  * a reply for another company, or from another contract, is refused;
  * the last good copy is kept on disk, so a QuoteRelay outage or restart never
    leaves the app without settings;
  * no URL set (Thelsa today) -> nothing changes: the repo files are used.
Settings only: keys and passwords are never in this data.
"""
from __future__ import annotations

import json
import os
import threading
import time

CONTRACT = "company-profile/1"
TTL = int(os.environ.get("COMPANY_SETTINGS_TTL", "900"))
_lock = threading.Lock()
_mem: dict = {}


def enabled() -> bool:
    return (os.environ.get("COMPANY_SETTINGS_URL") or "").strip().startswith("https://")


def _tenant() -> str:
    return (os.environ.get("TENANT") or os.environ.get("ASSISTANT_TENANT") or "thelsa").strip().lower()


def _cache_path(app: str) -> str:
    d = os.environ.get("COMPANY_SETTINGS_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    return os.path.join(d, f"company_settings_{_tenant()}_{app}.json")


def _token() -> str:
    return (os.environ.get("COMPANY_SETTINGS_TOKEN") or os.environ.get("ASSISTANT_FEED_TOKEN_QUOTERELAY") or "").strip()


def _fetch(app: str):
    import requests
    url = os.environ["COMPANY_SETTINGS_URL"].strip()
    sep = "&" if "?" in url else "?"
    r = requests.get(f"{url}{sep}app={app}", headers={"Authorization": f"Bearer {_token()}"}, timeout=6)
    r.raise_for_status()
    body = r.json()
    if body.get("contract") != CONTRACT or str(body.get("tenant") or "").lower() != _tenant() \
            or body.get("app") != app or not isinstance(body.get("settings"), dict):
        raise ValueError("settings reply is not for this company")
    return body["settings"]


def _read_cache(app: str):
    try:
        with open(_cache_path(app), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _write_cache(app: str, settings: dict) -> None:
    p = _cache_path(app)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


def get(app: str):
    """This company's settings for `app` ("auditrack" | "assistant"), or None
    when the intake is not in use. Fresh copy at most every TTL seconds; on any
    failure the last good copy (memory, then disk)."""
    if not enabled() or not _token():
        return None
    now = time.time()
    with _lock:
        hit = _mem.get(app)
        if hit and now - hit[0] < TTL:
            return hit[1]
        try:
            s = _fetch(app)
            _write_cache(app, s)
        except Exception:
            s = (hit[1] if hit else None) or _read_cache(app)
        if s is not None:
            _mem[app] = (now, s)
        return s


def clear() -> None:
    with _lock:
        _mem.clear()
