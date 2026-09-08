"""
clickup.py — thin ClickUp REST v2 client (spec §4.1): auth, rate limiting,
hierarchy discovery, list/task reads, list inspection, and webhook helpers.
The TIM shipment interpretation lives in tim.py.

Env vars (Render):
  CLICKUP_TOKEN        Personal API token (pk_…) — sent raw in the
                       Authorization header, no "Bearer".
  CLICKUP_TEAM_ID      Workspace id (needed for webhooks; discovery finds it).
  CLICKUP_RATE_LIMIT   Requests per rolling minute across all threads (default 85).

ClickUp allows 100 req/min per token; on 429 we sleep until X-RateLimit-Reset.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from typing import Optional

import requests

from .models import parse_date, to_number

log = logging.getLogger(__name__)

BASE_URL = "https://api.clickup.com/api/v2"
PAGE_SIZE = 100
_TIMEOUT = 20


# ── HTTP client ─────────────────────────────────────────────────────────────────


class ClickUpError(RuntimeError):
    pass


class _RateLimiter:
    """Process-wide token bucket: at most `limit` requests per rolling `window`
    seconds, shared by every client/thread so parallel fetches stay under
    ClickUp's 100 req/min."""

    def __init__(self, limit: int = 85, window: float = 60.0):
        import collections
        import threading
        self.limit, self.window = limit, window
        self._stamps: collections.deque = collections.deque()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = time.time()
                while self._stamps and now - self._stamps[0] > self.window:
                    self._stamps.popleft()
                if len(self._stamps) < self.limit:
                    self._stamps.append(now)
                    return
                wait = self.window - (now - self._stamps[0]) + 0.05
            time.sleep(min(max(wait, 0.05), 5.0))


RATE_LIMITER = _RateLimiter(int(os.environ.get("CLICKUP_RATE_LIMIT", "85") or 85))


class ClickUpClient:
    def __init__(self, token: Optional[str] = None, session: Optional[requests.Session] = None):
        self.token = (token or os.environ.get("CLICKUP_TOKEN", "")).strip()
        if not self.token:
            raise ClickUpError("CLICKUP_TOKEN is not set")
        self.http = session or requests.Session()
        self.http.headers.update({"Authorization": self.token, "Accept": "application/json"})
        self.requests_made = 0
        self._count_lock = __import__("threading").Lock()

    def get(self, path: str, **params) -> dict:
        url = f"{BASE_URL}/{path.lstrip('/')}"
        for attempt in range(4):
            RATE_LIMITER.acquire()
            resp = self.http.get(url, params=params or None, timeout=_TIMEOUT)
            with self._count_lock:
                self.requests_made += 1
            if resp.status_code == 429:
                reset = resp.headers.get("X-RateLimit-Reset")
                wait = max(1.0, float(reset) - time.time()) if reset else 5.0 * (attempt + 1)
                log.warning("ClickUp 429 — sleeping %.0fs", wait)
                time.sleep(min(wait, 60))
                continue
            if resp.status_code >= 400:
                raise ClickUpError(f"ClickUp {resp.status_code} on {path}: {resp.text[:300]}")
            return resp.json()
        raise ClickUpError(f"ClickUp rate limit persisted on {path}")

    # ── discovery (run once to find ids) ──
    def teams(self) -> list[dict]:
        return self.get("team").get("teams", [])

    def spaces(self, team_id: str) -> list[dict]:
        return self.get(f"team/{team_id}/space", archived="false").get("spaces", [])

    def folders(self, space_id: str) -> list[dict]:
        return self.get(f"space/{space_id}/folder", archived="false").get("folders", [])

    def lists_in_folder(self, folder_id: str) -> list[dict]:
        return self.get(f"folder/{folder_id}/list", archived="false").get("lists", [])

    def folderless_lists(self, space_id: str) -> list[dict]:
        return self.get(f"space/{space_id}/list", archived="false").get("lists", [])

    def hierarchy(self) -> list[dict]:
        """Team → space → folder → list tree with ids, for picking CLICKUP_LIST_ID."""
        out = []
        for team in self.teams():
            t = {"team_id": team["id"], "team": team.get("name"), "spaces": []}
            for space in self.spaces(team["id"]):
                s = {"space_id": space["id"], "space": space.get("name"), "lists": []}
                for lst in self.folderless_lists(space["id"]):
                    s["lists"].append({"list_id": lst["id"], "list": lst.get("name"),
                                       "folder": None, "task_count": lst.get("task_count")})
                for folder in self.folders(space["id"]):
                    for lst in folder.get("lists") or self.lists_in_folder(folder["id"]):
                        s["lists"].append({"list_id": lst["id"], "list": lst.get("name"),
                                           "folder": folder.get("name"),
                                           "task_count": lst.get("task_count")})
                t["spaces"].append(s)
            out.append(t)
        return out

    # ── reads ──
    def list_fields(self, list_id: str) -> list[dict]:
        return self.get(f"list/{list_id}/field").get("fields", [])

    def list_statuses(self, list_id: str) -> list[str]:
        lst = self.get(f"list/{list_id}")
        return [s.get("status") for s in lst.get("statuses", [])]

    def list_tasks(self, list_id: str, include_closed: bool = False,
                   updated_after: Optional[dt.datetime] = None) -> list[dict]:
        tasks, page = [], 0
        params = {"subtasks": "true", "include_closed": str(include_closed).lower()}
        if updated_after:
            params["date_updated_gt"] = int(updated_after.timestamp() * 1000)
        while True:
            data = self.get(f"list/{list_id}/task", page=page, **params)
            batch = data.get("tasks", [])
            tasks.extend(batch)
            if data.get("last_page", True) or len(batch) < PAGE_SIZE or page > 200:
                return tasks
            page += 1

    def task(self, task_id: str) -> dict:
        return self.get(f"task/{task_id}", include_subtasks="true")

    def list_detail(self, list_id: str) -> dict:
        return self.get(f"list/{list_id}")

    def inspect_list(self, list_id: str) -> dict:
        """Everything about one list, for validating the real ClickUp structure:
        list metadata, its custom-field definitions, and every task (open and
        closed) with status, dates, assignees, and decoded custom-field values."""
        detail = self.list_detail(list_id)
        fields = self.list_fields(list_id)
        tasks = self.list_tasks(list_id, include_closed=True)
        names = {f.get("id"): f.get("name") for f in fields}
        rows = []
        for t in sorted(tasks, key=lambda x: (x.get("orderindex") or "0")):
            rows.append({
                "id": t.get("id"),
                "name": t.get("name"),
                "status": (t.get("status") or {}).get("status"),
                "status_type": (t.get("status") or {}).get("type"),
                "orderindex": t.get("orderindex"),
                "parent": t.get("parent"),
                "date_created": parse_date(t.get("date_created")),
                "date_updated": parse_date(t.get("date_updated")),
                "date_closed": parse_date(t.get("date_closed")),
                "start_date": parse_date(t.get("start_date")),
                "due_date": parse_date(t.get("due_date")),
                "assignees": [a.get("username") or a.get("email") for a in t.get("assignees") or []],
                "tags": [x.get("name") for x in t.get("tags") or []],
                "custom_fields": {cf.get("name") or names.get(cf.get("id"), cf.get("id")): field_value(cf)
                                  for cf in t.get("custom_fields") or []
                                  if cf.get("value") not in (None, "")},
                "description": (t.get("description") or "")[:500],
            })
        return {
            "list": {k: detail.get(k) for k in ("id", "name", "content", "status",
                                                 "due_date", "start_date", "archived")},
            "folder": (detail.get("folder") or {}).get("name"),
            "space": (detail.get("space") or {}).get("name"),
            "statuses": [s.get("status") for s in detail.get("statuses") or []],
            "custom_fields": [{"name": f.get("name"), "type": f.get("type"),
                               "options": [o.get("name") for o in
                                           (f.get("type_config") or {}).get("options") or []]}
                              for f in fields],
            "task_count": len(rows),
            "tasks": rows,
        }


# ── Custom-field value decoding ─────────────────────────────────────────────────


def field_value(cf: dict):
    """Turn a ClickUp custom-field entry into a plain Python value."""
    v = cf.get("value")
    if v in (None, ""):
        return None
    t = cf.get("type")
    cfg = cf.get("type_config") or {}
    if t == "drop_down":
        opts = cfg.get("options") or []
        for o in opts:
            if o.get("id") == v or o.get("orderindex") == v:
                return o.get("name")
        if isinstance(v, int) and 0 <= v < len(opts):
            return opts[v].get("name")
        return str(v)
    if t == "labels":
        names = {o.get("id"): o.get("name") for o in cfg.get("options") or []}
        return ", ".join(names.get(x, str(x)) for x in (v if isinstance(v, list) else [v]))
    if t == "location":
        return v.get("formatted_address") if isinstance(v, dict) else str(v)
    if t in ("users", "people"):
        return ", ".join(u.get("username") or u.get("email", "") for u in v) if isinstance(v, list) else str(v)
    if t == "checkbox":
        return str(v).lower() in ("true", "1")
    if t == "date":
        return v  # epoch millis string — parse_date handles it
    if t in ("number", "currency"):
        return to_number(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return v


# ── Webhooks (spec §4.1) ────────────────────────────────────────────────────────

WEBHOOK_EVENTS = ["taskCreated", "taskUpdated", "taskStatusUpdated", "taskMoved",
                  "taskDueDateUpdated", "taskDeleted"]


def verify_signature(body: bytes, signature: Optional[str], secret: Optional[str] = None) -> bool:
    """ClickUp signs deliveries: X-Signature = HMAC-SHA256(secret, raw body), hex."""
    import hashlib
    import hmac
    secret = secret or os.environ.get("CLICKUP_WEBHOOK_SECRET", "")
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


def register_webhook(client: ClickUpClient, endpoint: str, team_id: Optional[str] = None,
                     list_id: Optional[str] = None) -> dict:
    """Create the webhook; returns {"id", "secret", ...}. Store the secret in
    CLICKUP_WEBHOOK_SECRET. Call once, after the Render endpoint is live."""
    team_id = team_id or os.environ.get("CLICKUP_TEAM_ID", "")
    payload = {"endpoint": endpoint, "events": WEBHOOK_EVENTS}
    if list_id:
        payload["list_id"] = int(list_id)
    resp = client.http.post(f"{BASE_URL}/team/{team_id}/webhook", json=payload, timeout=_TIMEOUT)
    if resp.status_code >= 400:
        raise ClickUpError(f"webhook create failed {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("webhook", resp.json())
