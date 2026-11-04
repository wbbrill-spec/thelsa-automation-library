"""
tenant.py — one settings file per company (4 Nov 2026: the app must serve many
moving companies, not only Thelsa).

Each company is described in tenants/<id>.json: its Moveware company, its people
(coordinators, sales, who gets which alert), invoicing routing, file rules and
which features it uses. The code reads these settings instead of Thelsa names
and addresses written into it.

Each company runs as its own deployment of this same code (TENANT=<id>), so one
company's data, mailboxes and memory never touch another's, and a problem at one
company cannot take the others down.

TENANT selects the file (default "thelsa"); TENANT_FILE points at another path.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tenants")


def tenant_id() -> str:
    return (os.environ.get("TENANT") or "thelsa").strip().lower()


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def config() -> dict:
    path = os.environ.get("TENANT_FILE") or os.path.join(_DIR, f"{tenant_id()}.json")
    return _load(path)


def get(dotted: str, default=None):
    """tenant.get("invoicing.team_to") — a value from the company's settings."""
    cur = config()
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def feature(name: str) -> bool:
    return bool(get(f"features.{name}", False))


def clear_cache() -> None:
    _load.cache_clear()
