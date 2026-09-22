"""
tim.py — TIM shipments from ClickUp, the way the team actually uses it.

Validated 2026-09-08 (see project doc clickup-api-integration-guide.md):

  Space "Logistics Coordination"
    └─ Folder per agent (U-HAUL, LOGICSTICS, INTERMOVE, …)
         └─ One LIST per shipment, named "Customer - Agent - Reference"
              └─ Top-level tasks = the numbered process steps cloned from the
                 "Formato Seguimiento DA" (13 steps) / "DTD Impo" (17 steps)
                 templates; subtasks = the actions inside each step.
  Space "Completed 2026" — finished shipment lists are moved here.

So: Shipment = List. Stage = the first numbered step that is not complete.
Milestone dates = date_closed of specific steps. Closed = list lives in the
completed space. ClickUp holds NO volumes / destinations / due dates — those
come from a second source (Remisiones spreadsheet) and are merged later.

Env vars:
  CLICKUP_TEAM_ID          workspace id (9011168761)
  CLICKUP_ACTIVE_SPACE     name of the active space  (default "Logistics Coordination")
  CLICKUP_COMPLETED_SPACE  name/prefix of the completed space (default "Completed")
  CLICKUP_STALLED_DAYS     days without a completed step before flagging (default 7)
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import grouping
from .clickup import ClickUpClient, ClickUpError
from .models import Shipment, Source, Stage, norm_text, parse_date

log = logging.getLogger(__name__)

# ── Step → stage mapping (keyword on the normalized step name; first hit wins) ──
# Covers both the DA (13-step) and DTD Impo (17-step) formats.
STEP_STAGE_RULES: list[tuple[str, Stage]] = [
    ("cierre de servicio", Stage.DELIVERED),
    ("dia de entrega", Stage.OUT_FOR_DELIVERY),
    ("programar entrega", Stage.OUT_FOR_DELIVERY),
    ("logistica de entrega", Stage.ONWARD),
    ("descarga en bodega", Stage.AT_HUB),
    ("bodega thelsa monterrey", Stage.AT_HUB),
    ("cruce exitoso", Stage.CUSTOMS),
    ("importacion y cruce", Stage.CUSTOMS),
    ("agente aduanal", Stage.CUSTOMS),
    ("confirmar recepcion en bodega", Stage.TO_BORDER),
    ("flete bodega agente", Stage.TO_BORDER),
    ("bodega frontera", Stage.TO_BORDER),
    ("luz verde", Stage.GREEN_LIGHT),
    ("documentos completos", Stage.DOCS_PENDING),
    ("documentos de origen", Stage.DOCS_PENDING),
    ("solicitar documentos", Stage.DOCS_PENDING),
    ("recoleccion", Stage.BOOKED),
    ("presentarse", Stage.BOOKED),
]

# Milestone name → keyword of the step whose completion date it is.
MILESTONE_STEPS: dict[str, str] = {
    "booked": "presentarse",
    "docs_complete": "documentos completos",
    "green_light": "luz verde",
    "at_border_warehouse": "confirmar recepcion en bodega",
    "docs_to_broker": "agente aduanal",
    "crossed": "cruce exitoso",
    "at_hub": "descarga en bodega",
    "delivery_scheduled": "programar entrega",
    "delivered": "dia de entrega",
    "closed": "cierre de servicio",
}

PLACEHOLDER_LIST_NAMES = {"list", "lista"}
_STEP_NUM_RE = re.compile(r"^\s*(\d+)\s*[.\-–)]+\s*(.*)$")


def stage_for_step(step_name: Optional[str]) -> Stage:
    text = norm_text(step_name)
    for needle, stage in STEP_STAGE_RULES:
        if needle in text:
            return stage
    return Stage.UNKNOWN


def parse_list_name(name: str, folder: Optional[str] = None) -> dict:
    """'Rosa Molina - UHaul - 121722' → customer / agent / reference.
    Falls back to the folder name for the agent and tolerates 1–3 parts."""
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", name or "") if p.strip()]
    customer = parts[0] if parts else (name or "").strip()
    agent, ref = "", ""
    if len(parts) >= 3:
        agent, ref = parts[1], " - ".join(parts[2:])
    elif len(parts) == 2:
        # "Carolina Angrizano - TIM-48007-26": the second part is a reference
        # if it looks like one, else an agent.
        if re.search(r"\d", parts[1]):
            ref = parts[1]
        else:
            agent = parts[1]
    if not agent and folder:
        agent = folder.strip().title() if folder.isupper() else folder.strip()
    return {"customer": customer, "agent": agent, "reference": ref}


def _step_number(name: str) -> Optional[int]:
    m = _STEP_NUM_RE.match(name or "")
    return int(m.group(1)) if m else None


def build_shipment(lst: dict, tasks: list[dict], *, folder: Optional[str], space: str,
                   team_id: str, completed: bool, today: Optional[dt.date] = None,
                   comments: Optional[list[dict]] = None) -> Shipment:
    """Turn one shipment list + its tasks into a unified Shipment."""
    today = today or dt.date.today()
    meta = parse_list_name(lst.get("name", ""), folder)
    steps = [t for t in tasks if not t.get("parent")]
    steps.sort(key=lambda t: (_step_number(t.get("name")) or 999,
                              float(t.get("orderindex") or 0)))
    subs_by_parent: dict[str, list[dict]] = {}
    for t in tasks:
        if t.get("parent"):
            subs_by_parent.setdefault(t["parent"], []).append(t)

    def is_done(t: dict) -> bool:
        st = t.get("status") or {}
        return st.get("type") == "closed" or norm_text(st.get("status")) in ("complete", "completo", "closed")

    done = [s for s in steps if is_done(s)]
    pending = [s for s in steps if not is_done(s)]
    current = pending[0] if pending else None

    milestones: dict[str, Optional[dt.date]] = {}
    for key, needle in MILESTONE_STEPS.items():
        for s in done:
            if needle in norm_text(s.get("name")):
                milestones[key] = parse_date(s.get("date_closed") or s.get("date_updated"))
                break

    last_progress = max((parse_date(s.get("date_closed") or s.get("date_updated"))
                         for s in done if s.get("date_closed") or s.get("date_updated")),
                        default=None)
    updated_ms = max((int(t["date_updated"]) for t in tasks if t.get("date_updated")), default=None)
    updated_at = (dt.datetime.fromtimestamp(updated_ms / 1000, dt.timezone.utc)
                  if updated_ms else None)

    if completed:
        stage = Stage.CLOSED
    elif current is None:
        stage = Stage.DELIVERED if steps else Stage.UNKNOWN
    else:
        stage = stage_for_step(current.get("name"))
        # A step that has started but not finished is still "in" its stage;
        # the previous step's stage applies only if the current step is unmapped.
        if stage is Stage.UNKNOWN and done:
            stage = stage_for_step(done[-1].get("name"))

    n = len(steps)
    fmt = "DTD" if n >= 16 else ("DA" if n >= 12 else (f"{n}-step" if n else ""))

    flags: list[str] = []
    stalled_days = int(os.environ.get("CLICKUP_STALLED_DAYS", "7") or 7)
    days_since = (today - last_progress).days if last_progress else None
    if not completed and stage not in (Stage.DELIVERED, Stage.CLOSED):
        if days_since is not None and days_since >= stalled_days:
            flags.append("stalled")
        if stage is Stage.DOCS_PENDING and days_since is not None and days_since >= stalled_days:
            flags.append("docs_incomplete")
        if current is not None and (current.get("status") or {}).get("status", "").lower() == "in progress":
            flags.append("in_progress")

    list_id = str(lst.get("id"))
    s = Shipment(
        id=f"TIM:{list_id}",
        source=Source.TIM,
        source_ref=list_id,
        reference_number=meta["reference"],
        customer_name=meta["customer"],
        agent=meta["agent"],
        origin="",
        destination="",
        current_location="",
        stage=stage,
        source_status=(current.get("name") if current else ("all steps complete" if steps else "no steps")),
        delivery_date=milestones.get("delivered"),
        clearance_date=milestones.get("crossed"),
        ready_date=milestones.get("green_light"),
        status_flags=flags,
        updated_at=updated_at,
        url=f"https://app.clickup.com/{team_id}/v/li/{list_id}",
        assignees=sorted({a.get("username") or a.get("email", "")
                          for t in tasks for a in (t.get("assignees") or []) if a}),
        process_format=fmt,
        current_step=current.get("name", "") if current else "",
        steps_done=len(done),
        steps_total=n,
        milestones=milestones,
        last_progress_at=last_progress,
        days_since_progress=days_since,
        # Where the list lives, so the commercial / new-furniture rule can
        # recognise the commercial lists once they exist (rules.py) without another walk.
        extra={"folder": folder or "", "space": space or "",
               "list_name": lst.get("name", "")},
    )
    # Fernanda's consolidation note, if she left one (decision D6, 22 Sep).
    grouping.apply_note(s, grouping.note_texts(lst, comments, tasks))
    return s


def today_utc_ms() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


# Where a consolidation note gets written. Fernanda's first live note (22 Sep
# 2026) was a comment on step 5, "Confirmar Recepción en Bodega" — the moment
# the freight lands in the border warehouse and she decides what crosses with
# what. These are the steps around that decision. ClickUp has no bulk endpoint
# for task comments, so asking about all thirteen steps of every shipment would
# roughly quadruple the walk; asking about these four or five costs little and
# covers where the note is actually left.
# Measured on the live workspace, 2026-09-22: asking about five steps on every
# open file cost 232 extra requests and pushed a refresh past four minutes,
# which is longer than the cache it fills. Both notes Fernanda had written were
# on the warehouse step, so the list is ordered by where a note actually turns
# up and cut to two per file.
NOTE_STEP_KEYWORDS = [
    "confirmar recepcion en bodega",      # 5 — where both live notes were
    "descarga en bodega",                 # 9 — the Monterrey hub
    "importacion y cruce",                # 7 — booking the crossing
]
NOTE_STEPS_MAX = int(os.environ.get("CB_GROUP_NOTE_STEPS", "2") or 2)

# Only files that could still be consolidated are worth asking about. A file
# waiting on documents has nothing to group yet, and one out for delivery is
# past the point of caring.
NOTE_STAGES = {Stage.GREEN_LIGHT, Stage.TO_BORDER, Stage.CUSTOMS,
               Stage.AT_HUB, Stage.ONWARD}


def note_candidate_tasks(tasks: list[dict], current_step: str = "") -> list[dict]:
    """The handful of steps worth asking ClickUp for comments on."""
    steps = [t for t in tasks or [] if not t.get("parent") and t.get("id")]
    picked: list[dict] = []
    seen: set = set()
    for needle in NOTE_STEP_KEYWORDS:
        for t in steps:
            if t["id"] in seen:
                continue
            if needle in norm_text(t.get("name")):
                picked.append(t)
                seen.add(t["id"])
                break
    # Whatever step the file is sitting on right now, mapped or not — a note is
    # most likely on the step somebody is actually working.
    if current_step:
        cur = norm_text(current_step)
        for t in steps:
            if t["id"] not in seen and norm_text(t.get("name")) == cur:
                picked.append(t)
                seen.add(t["id"])
                break
    return picked[:NOTE_STEPS_MAX]


def _recently_touched(tasks: list[dict], cutoff_ms: int) -> bool:
    """Has anything on this shipment moved recently enough to be worth a look?"""
    if not cutoff_ms:
        return True
    for t in tasks or []:
        try:
            if int(t.get("date_updated") or 0) >= cutoff_ms:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _find_space(spaces: list[dict], wanted: str, prefix: bool = False) -> Optional[dict]:
    w = norm_text(wanted)
    for s in spaces:
        n = norm_text(s.get("name"))
        if n == w or (prefix and n.startswith(w)):
            return s
    return None


def fetch_tim_shipments(client: Optional[ClickUpClient] = None,
                        include_completed: bool = False,
                        team_id: Optional[str] = None,
                        progress: Optional[dict] = None) -> tuple[list[Shipment], dict]:
    """Walk the workspace and return every TIM shipment as a Shipment.
    ~1 + folders + lists requests; cache the result (see web.py)."""
    client = client or ClickUpClient()
    team_id = team_id or os.environ.get("CLICKUP_TEAM_ID", "").strip()
    if not team_id:
        teams = client.teams()
        if not teams:
            raise ClickUpError("no ClickUp workspaces visible to this token")
        team_id = teams[0]["id"]

    spaces = client.spaces(team_id)
    active_name = os.environ.get("CLICKUP_ACTIVE_SPACE", "Logistics Coordination")
    completed_name = os.environ.get("CLICKUP_COMPLETED_SPACE", "Completed")
    active = _find_space(spaces, active_name)
    if not active:
        raise ClickUpError(f"space {active_name!r} not found; spaces: {[s.get('name') for s in spaces]}")
    completed = _find_space(spaces, completed_name, prefix=True) if include_completed else None

    diag: dict = {"team_id": team_id, "active_space": active.get("name"),
                  "completed_space": completed.get("name") if completed else None,
                  "folders": [], "skipped_lists": [], "unmapped_steps": {}, "errors": []}
    shipments: list[Shipment] = []

    # 1) Enumerate every shipment list (cheap: one call per space + per folder).
    jobs: list[tuple[dict, Optional[str], dict, bool]] = []   # (list, folder, space, completed)
    for space, is_completed in [(active, False)] + ([(completed, True)] if completed else []):
        folders = client.folders(space["id"])
        groups = [(f.get("name"), f.get("lists") or client.lists_in_folder(f["id"])) for f in folders]
        groups.append((None, client.folderless_lists(space["id"])))
        for folder_name, lists in groups:
            count = 0
            for lst in lists:
                name = (lst.get("name") or "").strip()
                if norm_text(name) in PLACEHOLDER_LIST_NAMES or name.startswith("***"):
                    diag["skipped_lists"].append(name)
                    continue
                jobs.append((lst, folder_name, space, is_completed))
                count += 1
            if folder_name is not None or count:
                diag["folders"].append({"space": space.get("name"), "folder": folder_name, "shipments": count})

    # 2) Pull each list's tasks in parallel (the shared rate limiter keeps us
    #    under ClickUp's 100 req/min); ~70 lists ≈ 10–15 s instead of a minute.
    # Fernanda's consolidation note lives in a comment on the list, which costs
    # one extra request per list. Spending that on every list would roughly
    # double the walk, so it is spent only on lists that are still moving —
    # a file nobody has touched in a month is not being consolidated today.
    notes_on = (os.environ.get("CB_GROUP_NOTES", "1") or "1").lower() in ("1", "true", "yes")
    note_days = int(os.environ.get("CB_GROUP_NOTE_DAYS", "45") or 45)
    note_cutoff = (today_utc_ms() - note_days * 86_400_000) if note_days else 0
    note_stats = {"checked": 0, "found": 0, "task_reads": 0}

    def fetch(job):
        lst, folder_name, space, is_completed = job
        try:
            tasks = client.list_tasks(lst["id"], include_closed=True)
            # Build first, then decide whether this file is worth asking about:
            # a delivered or closed shipment is not being consolidated today.
            s = build_shipment(lst, tasks, folder=folder_name, space=space.get("name", ""),
                               team_id=team_id, completed=is_completed)
            worth_reading = (notes_on and not is_completed
                             and s.stage in NOTE_STAGES
                             and _recently_touched(tasks, note_cutoff))
            if worth_reading:
                texts: list[str] = []
                try:
                    texts.extend(grouping.note_texts({}, client.list_comments(lst["id"])))
                except Exception as exc:  # noqa: BLE001 — a missing note is not an outage
                    log.debug("list comments unavailable for %s: %s", lst.get("name"), exc)
                # The note actually lives on a checklist step, so ask about the
                # few steps where the consolidation decision gets made.
                for t in note_candidate_tasks(tasks, s.current_step):
                    try:
                        texts.extend(grouping.note_texts({}, client.task_comments(t["id"])))
                        note_stats["task_reads"] += 1
                    except Exception as exc:  # noqa: BLE001
                        log.debug("task comments unavailable for %s: %s", t.get("name"), exc)
                note_stats["checked"] += 1
                grouping.apply_note(s, texts + grouping.note_texts(lst, None, tasks))
            if s.consolidation:
                note_stats["found"] += 1
            return s, None
        except Exception as exc:  # noqa: BLE001 — one bad list must not sink the fleet
            return None, {"list": lst.get("name"), "error": f"{type(exc).__name__}: {exc}"}

    workers = max(1, min(int(os.environ.get("CLICKUP_WORKERS", "4") or 4), 8))
    if progress is not None:
        progress["total"] = len(jobs)
        progress["done"] = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for s, err in pool.map(fetch, jobs):
            if progress is not None:
                progress["done"] += 1
            if err:
                diag["errors"].append(err)
                continue
            if s.stage is Stage.UNKNOWN and s.current_step:
                diag["unmapped_steps"][s.current_step] = diag["unmapped_steps"].get(s.current_step, 0) + 1
            shipments.append(s)
    shipments.sort(key=lambda s: (s.agent, s.customer_name))
    diag["requests_made"] = client.requests_made
    diag["count"] = len(shipments)
    diag["consolidation_notes"] = {**note_stats, "enabled": notes_on,
                                   "window_days": note_days}
    return shipments, diag
