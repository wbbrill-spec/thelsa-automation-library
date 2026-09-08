"""
clickup.py — ClickUp REST v2 reader for TIM shipments (spec §4.1).

Read-only. Pulls every task in the shipments list (subtasks included),
reads its custom fields, and normalizes each into a `Shipment`.

Env vars (Render):
  CLICKUP_TOKEN        Personal API token from a ClickUp Admin (pk_…) — sent
                       raw in the Authorization header, no "Bearer".
  CLICKUP_LIST_ID      The shipments list id (comma-separate several).
  CLICKUP_TEAM_ID      Workspace id — only needed for webhooks / discovery.
  CLICKUP_STAGE_MAP    Optional JSON {"<clickup status>": "<stage value>"} to
                       override the default status→stage mapping below.
  CLICKUP_FIELD_MAP    Optional JSON {"<shipment field>": "<custom field name>"}
                       to pin a custom field when auto-matching gets it wrong.

Rate limit: 100 req/min per token. We list at 100 tasks/page and cache, so a
full refresh of a few hundred shipments costs a handful of requests. On 429
we sleep until X-RateLimit-Reset.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import time
from typing import Iterable, Optional

import requests

from .models import (
    Hub, Shipment, Source, Stage,
    hub_for_destination, norm_text, parse_date, to_int, to_number, volume_to_m3,
)

log = logging.getLogger(__name__)

BASE_URL = "https://api.clickup.com/api/v2"
PAGE_SIZE = 100
_TIMEOUT = 20


# ── Custom-field auto-matching ──────────────────────────────────────────────────
# Shipment field → candidate ClickUp custom-field names (normalized). The first
# custom field on the list whose normalized name matches wins. Override any
# entry with CLICKUP_FIELD_MAP.

FIELD_ALIASES: dict[str, list[str]] = {
    "reference_number": ["reference number", "reference", "ref", "ref no", "ref number",
                         "file number", "file no", "job number", "agent ref", "agent reference"],
    "customer_name":    ["customer", "customer name", "shipper", "client", "transferee", "name"],
    "agent":            ["agent", "agent company", "booking agent", "company", "account", "partner"],
    "origin":           ["origin", "origin city", "from", "pickup", "pick up", "origin location"],
    "destination":      ["destination", "destination city", "to", "delivery city",
                         "final destination", "delivery address", "destination location"],
    "current_location": ["current location", "current warehouse", "location", "warehouse",
                         "current hub", "where is it"],
    "volume":           ["volume", "volume m3", "m3", "cbm", "volume cuft", "cuft", "cu ft",
                         "cubic feet", "cubic meters", "vol"],
    "weight":           ["weight", "weight kg", "weight lbs", "kg", "lbs", "gross weight"],
    "lift_vans":        ["lift vans", "lift van", "liftvans", "liftvan", "lv", "no lift vans",
                         "number of lift vans", "lift vans qty"],
    "u_boxes":          ["u boxes", "u box", "uboxes", "ubox", "u haul boxes", "no u boxes",
                         "number of u boxes", "u boxes qty"],
    "ready_date":       ["ready date", "ready", "ready to move", "available date", "pickup date"],
    "clearance_date":   ["estimated clearance date", "clearance date", "customs clearance",
                         "clearance", "eta clearance", "customs date"],
    "delivery_date":    ["delivery date", "delivery", "scheduled delivery", "target delivery",
                        "eta delivery", "delivery eta"],
}

# ── Default ClickUp status → pipeline stage mapping ─────────────────────────────
# Matched on normalized status text; substring match, first hit wins, so keep
# the more specific phrases first. Unmatched statuses → Stage.UNKNOWN and are
# reported by /crossborder/raw so the mapping can be finalized against the
# real board.

DEFAULT_STAGE_RULES: list[tuple[str, Stage]] = [
    ("out for delivery", Stage.OUT_FOR_DELIVERY),
    ("delivering", Stage.OUT_FOR_DELIVERY),
    ("delivered", Stage.DELIVERED),
    ("complete", Stage.CLOSED),
    ("closed", Stage.CLOSED),
    ("invoiced", Stage.CLOSED),
    ("cancel", Stage.CLOSED),
    ("customs", Stage.CUSTOMS),
    ("clearance", Stage.CUSTOMS),
    ("aduana", Stage.CUSTOMS),
    ("in transit to hub", Stage.ONWARD),
    ("onward", Stage.ONWARD),
    ("to destination", Stage.ONWARD),
    ("in transit", Stage.TO_BORDER),
    ("to border", Stage.TO_BORDER),
    ("en route", Stage.TO_BORDER),
    ("dispatched", Stage.TO_BORDER),
    ("at hub", Stage.AT_HUB),
    ("in warehouse", Stage.AT_HUB),
    ("at warehouse", Stage.AT_HUB),
    ("warehouse", Stage.AT_HUB),
    ("monterrey", Stage.AT_HUB),
    ("green light", Stage.GREEN_LIGHT),
    ("cleared to ship", Stage.GREEN_LIGHT),
    ("ready to ship", Stage.GREEN_LIGHT),
    ("docs pending", Stage.DOCS_PENDING),
    ("documents", Stage.DOCS_PENDING),
    ("pending docs", Stage.DOCS_PENDING),
    ("awaiting", Stage.DOCS_PENDING),
    ("booked", Stage.BOOKED),
    ("new", Stage.BOOKED),
    ("open", Stage.BOOKED),
    ("to do", Stage.BOOKED),
]

FLAG_RULES: list[tuple[str, str]] = [
    ("on hold", "on_hold"), ("hold", "on_hold"),
    ("red light", "red_light"), ("inspection", "red_light"), ("semaforo rojo", "red_light"),
    ("docs pending", "docs_incomplete"), ("pending docs", "docs_incomplete"),
]


def _env_json(name: str) -> dict:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("%s is not valid JSON — ignored", name)
        return {}


def stage_for_status(status: Optional[str]) -> Stage:
    text = norm_text(status)
    if not text:
        return Stage.UNKNOWN
    override = {norm_text(k): v for k, v in _env_json("CLICKUP_STAGE_MAP").items()}
    if text in override:
        try:
            return Stage(override[text])
        except ValueError:
            log.warning("CLICKUP_STAGE_MAP: unknown stage %r for %r", override[text], status)
    for needle, stage in DEFAULT_STAGE_RULES:
        if needle in text:
            return stage
    return Stage.UNKNOWN


def flags_for_status(status: Optional[str], tags: Iterable[str] = ()) -> list[str]:
    flags: list[str] = []
    for text in [norm_text(status), *(norm_text(t) for t in tags)]:
        for needle, flag in FLAG_RULES:
            if needle and needle in text and flag not in flags:
                flags.append(flag)
    return flags


# ── HTTP client ─────────────────────────────────────────────────────────────────


class ClickUpError(RuntimeError):
    pass


class ClickUpClient:
    def __init__(self, token: Optional[str] = None, session: Optional[requests.Session] = None):
        self.token = (token or os.environ.get("CLICKUP_TOKEN", "")).strip()
        if not self.token:
            raise ClickUpError("CLICKUP_TOKEN is not set")
        self.http = session or requests.Session()
        self.http.headers.update({"Authorization": self.token, "Accept": "application/json"})
        self.requests_made = 0

    def get(self, path: str, **params) -> dict:
        url = f"{BASE_URL}/{path.lstrip('/')}"
        for attempt in range(4):
            resp = self.http.get(url, params=params or None, timeout=_TIMEOUT)
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


def build_field_map(fields: list[dict]) -> dict[str, dict]:
    """Shipment field → {"id", "name", "type"} of the matched ClickUp custom field."""
    by_norm = {norm_text(f.get("name")): f for f in fields}
    override = {k: norm_text(v) for k, v in _env_json("CLICKUP_FIELD_MAP").items()}
    result: dict[str, dict] = {}
    used: set = set()

    def take(key, f):
        result[key] = {"id": f.get("id"), "name": f.get("name"), "type": f.get("type")}
        used.add(f.get("id"))

    # Pass 1: exact (normalized) name match.
    for key, aliases in FIELD_ALIASES.items():
        candidates = [override[key]] if key in override else aliases
        for alias in candidates:
            f = by_norm.get(norm_text(alias))
            if f and f.get("id") not in used:
                take(key, f)
                break
    # Pass 2: alias appears inside a longer field name ("Current Location / Warehouse").
    for key, aliases in FIELD_ALIASES.items():
        if key in result or key in override:
            continue
        for alias in aliases:
            a = f" {norm_text(alias)} "
            if len(a.strip()) < 4:          # skip 'lv', 'kg', 'to' etc.
                continue
            for name, f in by_norm.items():
                if a in f" {name} " and f.get("id") not in used:
                    take(key, f)
                    break
            if key in result:
                break
    return result


# ── Task → Shipment ─────────────────────────────────────────────────────────────


def task_to_shipment(task: dict, field_map: dict[str, dict]) -> Shipment:
    cfs = {cf.get("id"): cf for cf in task.get("custom_fields") or []}

    def val(key):
        m = field_map.get(key)
        if not m:
            return None
        cf = cfs.get(m["id"])
        return field_value(cf) if cf else None

    status_text = ((task.get("status") or {}).get("status") or "").strip()
    tags = [t.get("name", "") for t in task.get("tags") or []]
    destination = str(val("destination") or "")
    vol_field_name = (field_map.get("volume") or {}).get("name")

    updated = task.get("date_updated")
    updated_at = (dt.datetime.fromtimestamp(int(updated) / 1000, dt.timezone.utc)
                  if updated else None)

    return Shipment(
        id=f"TIM:{task['id']}",
        source=Source.TIM,
        source_ref=task["id"],
        reference_number=str(val("reference_number") or task.get("custom_id") or ""),
        customer_name=str(val("customer_name") or task.get("name") or ""),
        agent=str(val("agent") or ""),
        origin=str(val("origin") or ""),
        destination=destination,
        destination_hub=hub_for_destination(destination),
        current_location=str(val("current_location") or ""),
        volume_m3=volume_to_m3(val("volume"), vol_field_name),
        weight=to_number(val("weight")),
        lift_vans=to_int(val("lift_vans")),
        u_boxes=to_int(val("u_boxes")),
        stage=stage_for_status(status_text),
        source_status=status_text,
        ready_date=parse_date(val("ready_date") or task.get("start_date")),
        clearance_date=parse_date(val("clearance_date")),
        delivery_date=parse_date(val("delivery_date") or task.get("due_date")),
        status_flags=flags_for_status(status_text, tags),
        updated_at=updated_at,
        url=task.get("url", ""),
        assignees=[a.get("username") or a.get("email", "") for a in task.get("assignees") or []],
    )


def _list_ids() -> list[str]:
    return [x.strip() for x in os.environ.get("CLICKUP_LIST_ID", "").split(",") if x.strip()]


def fetch_shipments(client: Optional[ClickUpClient] = None,
                    list_ids: Optional[list[str]] = None,
                    include_closed: bool = False) -> tuple[list[Shipment], dict]:
    """Pull + normalize every TIM shipment. Returns (shipments, diagnostics)."""
    client = client or ClickUpClient()
    list_ids = list_ids or _list_ids()
    if not list_ids:
        raise ClickUpError("CLICKUP_LIST_ID is not set")
    shipments: list[Shipment] = []
    diag: dict = {"lists": [], "unmapped_statuses": {}, "unknown_hubs": {}}
    for lid in list_ids:
        fields = client.list_fields(lid)
        fmap = build_field_map(fields)
        tasks = client.list_tasks(lid, include_closed=include_closed)
        for t in tasks:
            s = task_to_shipment(t, fmap)
            shipments.append(s)
            if s.stage is Stage.UNKNOWN:
                diag["unmapped_statuses"][s.source_status] = diag["unmapped_statuses"].get(s.source_status, 0) + 1
            if s.destination_hub is Hub.UNKNOWN and s.destination:
                diag["unknown_hubs"][s.destination] = diag["unknown_hubs"].get(s.destination, 0) + 1
        diag["lists"].append({
            "list_id": lid,
            "tasks": len(tasks),
            "statuses": client.list_statuses(lid),
            "custom_fields": [{"name": f.get("name"), "type": f.get("type")} for f in fields],
            "field_map": fmap,
            "unmatched_shipment_fields": [k for k in FIELD_ALIASES if k not in fmap],
        })
    diag["requests_made"] = client.requests_made
    return shipments, diag


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
