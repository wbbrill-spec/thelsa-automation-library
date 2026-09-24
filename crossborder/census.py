"""
census.py — how many files has TIM actually booked, and when?

The dashboard answers "what is moving right now". It could not answer "how many
files did we book this year", which is the first question anyone in management
asks, because the board deliberately carries only OPEN CROSS-BORDER shipments:
finished files move to the Completed space and drop off it, and exports and
domestic work are filtered out. Counting the tiles undercounts the business
badly.

**How a TIM file is dated.** In TIM's workspace one shipment is one ClickUp
LIST, cloned from a template. ClickUp's v2 API does not return a creation date
on a list — only on tasks. So a file's booking date is taken as the **earliest
`date_created` among its tasks**, which is the moment the template was cloned.
That is the closest thing to "the day the file was opened" that the data
contains, and it is exact rather than inferred: a clone stamps every task at
once. The alternative — the "presentarse" milestone — only exists once somebody
ticks that step, so it would silently miss every file where they did not.

Template lists run 59–85 tasks, comfortably inside one 100-task page, so one
request per list is enough to date it.

**Cost.** One call per space and folder, then one per list: roughly 120–160
requests for a full year. That is above ClickUp's 100/minute ceiling, so this
runs through the same token bucket as everything else (85/min, with backoff)
and takes two to three minutes. It is deliberately NOT part of any page load
or scheduled job — it runs only when somebody asks for it.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from collections import Counter
from typing import Optional

from .clickup import ClickUpClient, ClickUpError

log = logging.getLogger(__name__)

__all__ = ["census", "PLACEHOLDER_NAMES", "NON_SHIPMENT_SPACES"]

# Every agent folder carries an empty list literally called "List". It is a
# ClickUp artefact, not a shipment.
PLACEHOLDER_NAMES = {"list", "lista", "untitled list"}

# Spaces that hold something other than shipment files. Templates are the
# checklists shipments are cloned FROM; the certificate space is a separate
# per-customer process that would double-count a move that also has a file.
NON_SHIPMENT_SPACES = ("template", "plantilla", "certificado", "certificate")


def _is_shipment_list(lst: dict) -> bool:
    name = str(lst.get("name") or "").strip().lower()
    if not name or name in PLACEHOLDER_NAMES:
        return False
    # A shipment list is cloned from a template and therefore always has tasks.
    if str(lst.get("task_count") or "0") in ("0", "None"):
        return False
    return True


def _is_shipment_space(space: dict) -> bool:
    name = str(space.get("name") or "").lower()
    return not any(k in name for k in NON_SHIPMENT_SPACES)


def _first_task_date(client: ClickUpClient, list_id: str) -> Optional[dt.date]:
    """When the template was cloned — the file's booking date."""
    try:
        data = client.get(f"list/{list_id}/task", page=0,
                          subtasks="true", include_closed="true")
    except Exception as exc:  # noqa: BLE001 — one unreadable list must not stop the census
        log.debug("census: list %s unreadable: %s", list_id, exc)
        return None
    stamps = [int(t["date_created"]) for t in (data.get("tasks") or [])
              if t.get("date_created")]
    if not stamps:
        return None
    return dt.datetime.fromtimestamp(min(stamps) / 1000, dt.timezone.utc).date()


def census(client: Optional[ClickUpClient] = None, *, year: Optional[int] = None,
           today: Optional[dt.date] = None) -> dict:
    """Count TIM shipment files by the month they were opened."""
    client = client or ClickUpClient()
    today = today or dt.date.today()
    year = year or today.year

    team_id = os.environ.get("CLICKUP_TEAM_ID", "").strip()
    if not team_id:
        teams = client.teams()
        if not teams:
            raise ClickUpError("no ClickUp workspaces visible to this token")
        team_id = teams[0]["id"]

    spaces = client.spaces(team_id)
    rows: list[dict] = []
    skipped_spaces: list[str] = []

    for space in spaces:
        if not _is_shipment_space(space):
            skipped_spaces.append(space.get("name") or space["id"])
            continue
        found: list[tuple[dict, Optional[str]]] = []
        for lst in client.folderless_lists(space["id"]):
            found.append((lst, None))
        for folder in client.folders(space["id"]):
            for lst in folder.get("lists") or client.lists_in_folder(folder["id"]):
                found.append((lst, folder.get("name")))
        for lst, folder_name in found:
            if not _is_shipment_list(lst):
                continue
            rows.append({
                "list_id": lst["id"], "name": lst.get("name"),
                "space": space.get("name"), "folder": folder_name,
                "tasks": lst.get("task_count"),
                "opened": None,
            })

    for r in rows:
        d = _first_task_date(client, r["list_id"])
        r["opened"] = d.isoformat() if d else None

    dated = [r for r in rows if r["opened"]]
    undated = [r for r in rows if not r["opened"]]
    in_year = [r for r in dated if r["opened"][:4] == str(year)]

    by_month = Counter(r["opened"][:7] for r in in_year)
    months = sorted(by_month)
    first = min((r["opened"] for r in in_year), default=None)
    last = max((r["opened"] for r in in_year), default=None)

    # Months elapsed, counting the part-month we are in as a fraction, so the
    # average is not flattered by dividing a part-month's files by a whole one.
    elapsed = 0.0
    if first:
        start = dt.date.fromisoformat(first)
        end = today if today.year == year else dt.date(year, 12, 31)
        whole = (end.year - start.year) * 12 + (end.month - start.month)
        frac = end.day / _days_in_month(end.year, end.month)
        elapsed = round(whole + frac, 2)

    return {
        "year": year,
        "as_of": today.isoformat(),
        "files": len(in_year),
        "first_file_opened": first,
        "latest_file_opened": last,
        "months_elapsed": elapsed,
        "per_month_average": round(len(in_year) / elapsed, 1) if elapsed else None,
        "by_month": [{"month": m, "files": by_month[m]} for m in months],
        "by_space": [{"space": k, "files": v} for k, v in
                     Counter(r["space"] for r in in_year).most_common()],
        "by_agent_folder": [{"folder": k or "(no folder)", "files": v} for k, v in
                            Counter(r["folder"] for r in in_year).most_common()],
        "lists_seen": len(rows),
        "dated": len(dated),
        "undated": len(undated),
        "undated_sample": [r["name"] for r in undated[:10]],
        "before_this_year": len(dated) - len(in_year),
        "spaces_skipped": skipped_spaces,
        "requests_made": client.requests_made,
        "basis": ("A file is one ClickUp list; its date is the earliest task "
                  "creation in that list, which is when the template was cloned. "
                  "Counts every shipment space including Completed, so finished "
                  "files are included. Templates and the certificate process "
                  "space are excluded."),
    }


def _days_in_month(y: int, m: int) -> int:
    import calendar
    return calendar.monthrange(y, m)[1]
