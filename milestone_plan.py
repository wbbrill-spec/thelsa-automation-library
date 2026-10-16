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
"""
from __future__ import annotations

import datetime as dt
import os

CODES = {"pack": "Pack", "delivery": "Delivery"}
LABELS = {"pack": "Pack", "delivery": "Delivery"}
TYPO_BEFORE = dt.date(2025, 1, 1)
# Dave Pile (MOS-51412, 15 Oct 2026): writes are signed by a dedicated MoveWare
# user whose code is three characters. Create that user in the MoveWare app, then
# set MW_AUTO_USER on Render to its code. "AUT" is only a placeholder.
AUTO_USER = (os.environ.get("MW_AUTO_USER") or "AUT").strip().upper()[:3]


def _d(v):
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def plan_for_job(job: str, sit_job: dict, activities: list, today: dt.date | None = None) -> list:
    """Steps for one file. `sit_job` from sit_milestones.collect(); `activities`
    the job's MoveWare activities (dicts with type, date, completed, completedby, id)."""
    today = today or dt.date.today()
    steps = []
    for key, code in CODES.items():
        m = (sit_job or {}).get(key) or {}
        mine = [a for a in activities or [] if (a.get("type") or "") == code]
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
        # Prefer the action that is already completed, else the first one.
        a = next((x for x in mine if (x.get("completed") or "").upper() == "Y"), mine[0])
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
