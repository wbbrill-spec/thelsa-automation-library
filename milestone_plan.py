"""
milestone_plan.py — what the automation WOULD change in MoveWare's diary so pack
and delivery match SIT's Plan de Viajes. DRY RUN ONLY: nothing here writes.

MoveWare V2 (Dave Pile, MOS-51412, 13 and 15 Oct 2026): diary actions are "activities".
  GET   /jobs/{id}/activities
  POST  /jobs/{id}/activities                {"type","date","comment","completed","completedby"}
  PATCH /jobs/{id}/activities/{activityId}   only the fields to change
Updating a Pack or Uplift activity also moves the matching job-header date; child
actions are not recalculated.

Diary action codes vary by client; the full list is in the MoveWare app under
Reports → Other → Diary Actions Listing. Codes read from Thelsa's own activities
(15 Oct 2026): Pack, Delivery, Unpack,
Uplift, "Uplift O/S", UpliftandReturntoWH. The parent actions to keep green are
Pack and Delivery.

Rules — chosen to never overwrite a person's work by mistake:
  * use only plan dates the plan shows as DONE (the day passed and the move stayed
    in the plan) — never mark a future date completed;
  * no action of that type  → POST a new one, completed;
  * action exists, open (not completed) → PATCH date + completed;
  * completed with the same date → nothing to do;
  * completed by a person with a different date → REVIEW, not overwrite
    (the coordinator may know the real date; 1–2 days apart is normal);
  * completed with a date before 2025 (a typing slip) → PATCH to the plan date;
  * completed with a FUTURE date → REVIEW: should be reopened until it happens.
  * job type decides what is due (job_rules, training 16 Oct 2026): no Delivery
    on an agent export (EXA), no Pack on an agent import (IMA);
  * actions are matched by KEY ACTION, not code. The load date counts (Bill,
    16 Oct): the plan's pack milestone goes to the Load action (key action
    Uplift: Uplift, "Uplift O/S", UpliftandReturntoWH). The Pack actions are
    reminders hung off Load at −1/−2/−3 days and are left alone. The completed
    action is preferred, then the plain code, so a variant is never duplicated.
"""
from __future__ import annotations

import datetime as dt
import os

import job_rules

# Bill, 16 Oct 2026: the LOAD date is the one that counts. In MoveWare, Uplift
# (shown as "Load") and Delivery are the parent key actions; Pack, Pack1, Pack2
# are children dated −1…−3 days from Uplift (Jose Rivas, training 16 Oct 2026).
# The Plan de Viajes lists both: truck rows (DIA CARGA = load day) and separate
# packing-crew rows (EMPAQUE / EMBALAJE / HUACAL). So:
#   load     ← the plan's first truck load at the customer  → Uplift
#   pack     ← the plan's packing-crew row, only when there is one → Pack
#   delivery ← the plan's last unload at the customer        → Delivery
# Dave Pile: updating an Uplift activity also moves the job-header load date.
CODES = {"load": "Uplift", "pack": "Pack", "delivery": "Delivery"}
LABELS = {"load": "Load", "pack": "Pack", "delivery": "Delivery"}
MATCH = {"load": "load", "pack": "pack", "delivery": "delivery"}   # job_rules key action
# which sit_milestones date feeds each MoveWare action
SOURCE = {"load": "load", "pack": "packing", "delivery": "delivery"}
TYPO_BEFORE = dt.date(2025, 1, 1)
# Writes are signed (completedby) with a set of Moveware initials; access comes
# from the API credentials (Dave Pile, MOS-51412, 18 Oct 2026). No new user is
# created (Lupita, 20 Oct; Bill agreed 21 Oct): MW_AUTO_USER = Lupita's existing
# initials. "AUT" is only a placeholder and the executor refuses it.
AUTO_USER = (os.environ.get("MW_AUTO_USER") or "AUT").strip().upper()[:3]


def _d(v):
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def plan_for_job(job: str, sit_job: dict, activities: list, today: dt.date | None = None,
                 job_type: str = "") -> list:
    """Steps for one file. `sit_job` from sit_milestones.collect(); `activities`
    the job's MoveWare activities (dicts with type, keyaction, date, completed,
    completedby, id); `job_type` the MoveWare service code (EXA, IMA, XP …)."""
    today = today or dt.date.today()
    steps = []
    for key, code in CODES.items():
        if not job_rules.expected(key, job_type):
            continue                                   # e.g. Delivery on an EXA: not ours
        sj = sit_job or {}
        src = SOURCE[key]
        if key == "load" and src not in sj:
            src = "pack"                                # older plan data without the split
        m = sj.get(src) or {}
        mine = [a for a in activities or [] if job_rules.is_milestone(a, MATCH[key])]
        plan_day = _d(m.get("date"))
        done = m.get("status") == "done" and plan_day and plan_day <= today
        base = {"job": job, "milestone": key, "code": code, "plan_date": m.get("date"),
                "plan_status": m.get("status")}
        # A completed action with a future date is wrong whatever the plan says.
        for a in mine:
            ad = _d(a.get("date"))
            if (a.get("completed") or "").upper() == "Y" and ad and ad > today:
                steps.append({**base, "action": "review", "activity_id": a.get("id"),
                              "mw_date": a.get("date", "")[:10], "mw_completed": "Y",
                              "why": "Marked completed with a future date; reopen until the move happens."})
        if not done:
            continue
        if not mine:
            steps.append({**base, "action": "create", "body": {
                "type": code, "date": plan_day.isoformat(), "comment": LABELS[key],
                "completed": "Y", "completedby": AUTO_USER,
                "notes": "From SIT Plan de Viajes (automation)"},
                "why": f"No {code} action on the file."})
            continue
        # Prefer the action that is already completed, then the one with the plain
        # code (Pack / Delivery), else the first one.
        a = next((x for x in mine if (x.get("completed") or "").upper() == "Y"), None) \
            or next((x for x in mine if (x.get("type") or "") == code), mine[0])
        ad, comp = _d(a.get("date")), (a.get("completed") or "").upper() == "Y"
        info = {"activity_id": a.get("id"), "mw_date": (a.get("date") or "")[:10],
                "mw_completed": "Y" if comp else "N", "mw_by": a.get("completedby") or ""}
        if comp and ad == plan_day:
            continue
        if comp and ad and ad > today:
            continue                                   # already reported as review above
        if comp and ad and ad >= TYPO_BEFORE:
            steps.append({**base, **info, "action": "review",
                          "why": f"Completed by {info['mw_by'] or 'someone'} on {info['mw_date']}; plan says "
                                 f"{plan_day.isoformat()}. Left as is."})
            continue
        why = ("Date looks mistyped (before 2025)." if ad and ad < TYPO_BEFORE
               else "Action open or without a date.")
        steps.append({**base, **info, "action": "update",
                      "body": {"date": plan_day.isoformat(), "completed": "Y", "completedby": AUTO_USER},
                      "why": why})
    return steps


def summarize(steps: list) -> dict:
    out = {"create": 0, "update": 0, "review": 0}
    for s in steps:
        out[s["action"]] = out.get(s["action"], 0) + 1
    out["files"] = len({s["job"] for s in steps})
    return out
