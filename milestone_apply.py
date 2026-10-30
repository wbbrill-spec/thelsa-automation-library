"""
milestone_apply.py — carry out milestone_plan's diary write-back in Moveware.

Moveware (Dave Pile, MOS-51412, 15 Oct 2026):
  * writes are allowed through the V2 API with the existing credentials;
  * POST /jobs/{id}/activities always creates a new record; PATCH
    /jobs/{id}/activities/{activityId} changes only the fields sent;
  * any valid diary action can be added on any job type (the job-type filter is
    only the in-app lookup list);
  * the "user" is only a set of INITIALS for the completedby field; access comes
    from the API credentials (Dave, 18 Oct: "It doesnt need any security"), and
    Moveware's event log marks every API edit as such;
  * NO new Moveware user (Lupita, 20 Oct, agreed by Bill 21 Oct: every user is
    billed monthly). MW_AUTO_USER = Lupita's existing Moveware initials. Her
    password is never needed or used;
  * action codes are client-specific (Reports → Other → Diary Actions Listing).
    Thelsa's: Load = "Uplift" (key action Uplift), Delivery = "Delivery".

Only the steps milestone_plan marks "create" or "update" are executed: a Load or
Delivery that SIT's Plan de Viajes shows as DONE, with no completed action yet.
"review" steps (a person completed it with another date, a completed date in the
future) are NEVER written — they stay for a person to decide.

SWITCHES — nothing is written unless ALL of these hold:
  MW_DIARY_WRITE (default 1)  the write-back is on since 30 Oct 2026 (Bill); 0 = off
  initials                   Lupita's, looked up by name from the files (MW_AUTO_USER overrides;
                             nothing is written until they are found)
  DRY_RUN != 1
MW_DIARY_WRITE_MAX caps writes per pass (default 40). Every write is re-read from
Moveware and logged (job, step, result — no customer data) to
milestone_writes.jsonl next to the audit cache; the assistant's "did it happen?"
items for that file clear on the next scan.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.request

PLACEHOLDER_USERS = {"", "AUT"}


# Whose initials sign the automated entries (Lupita, 20 Oct; Bill, 30 Oct 2026:
# "we already know her name"). Moveware's completedby field takes the user's
# initials code, not a name, so the app looks the code up by name in the staff
# fields of the files it reads. MW_AUTO_USER overrides the lookup.
SIGN_AS_NAME = os.environ.get("MW_SIGN_AS_NAME", "Maria Gonzalez")
SIGN_AS_EMAIL = os.environ.get("MW_SIGN_AS_EMAIL", "maria.gonzalez@thelsa.com")


def auto_user() -> str:
    # Lupita confirmed her Moveware code on 30 Oct 2026: MG2.
    code = (os.environ.get("MW_AUTO_USER") or "MG2").strip().upper()[:3]
    if code:
        return code
    try:
        import mw_live
        return (mw_live.staff_code(SIGN_AS_NAME, SIGN_AS_EMAIL) or "").strip().upper()[:3]
    except Exception:
        return ""


def write_enabled() -> bool:
    # ON (Bill, 30 Oct 2026: "switch on"). MW_DIARY_WRITE=0 turns it off.
    return (os.environ.get("MW_DIARY_WRITE", "1") == "1" and os.environ.get("DRY_RUN", "0") != "1"
            and auto_user() not in PLACEHOLDER_USERS)


def blocked_reason() -> str | None:
    if os.environ.get("MW_DIARY_WRITE", "1") != "1":
        return "MW_DIARY_WRITE=0 (write-back off)"
    if os.environ.get("DRY_RUN", "0") == "1":
        return "DRY_RUN=1"
    if auto_user() in PLACEHOLDER_USERS:
        return f"{SIGN_AS_NAME}'s Moveware initials not found on any file yet (set MW_AUTO_USER)"
    return None


def _max() -> int:
    try:
        return max(1, int(os.environ.get("MW_DIARY_WRITE_MAX", "10")))   # gentle start: ≤10 per pass
    except ValueError:
        return 10


def _log_path() -> str:
    try:
        import coordinator_alerts as ca
        return os.path.join(os.path.dirname(ca._state_path()), "milestone_writes.jsonl")
    except Exception:
        return "/tmp/milestone_writes.jsonl"


def _log(rec: dict) -> None:
    try:
        with open(_log_path(), "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def recent_log(n: int = 50) -> list:
    try:
        with open(_log_path()) as fh:
            lines = fh.readlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]
    except Exception:
        return []


def _send(method: str, path: str, body: dict):
    """One write to Moveware V2. Raises on HTTP errors."""
    import mw_live
    req = urllib.request.Request(f"{mw_live.BASE_URL}/{path.lstrip('/')}", method=method,
                                 data=json.dumps(body).encode(), headers=mw_live._headers())
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def _activities(job_id):
    import mw_live
    body = mw_live._get_timed(f"/jobs/{job_id}/activities", 15) or {}
    return body.get("activities") or []


def _verified(job_id, step) -> bool:
    """Re-read the diary: is there now a completed action of this milestone on
    the plan date?"""
    import job_rules
    want = {"load": "load", "pack": "pack", "delivery": "delivery"}.get(step["milestone"], step["milestone"])
    day = str(step.get("plan_date") or "")[:10]
    for a in _activities(job_id):
        if job_rules.is_milestone(a, want) and str(a.get("completed") or "").upper() == "Y" \
                and str(a.get("date") or "")[:10] == day:
            return True
    return False


def apply(steps: list, job_ids: dict, now=None) -> dict:
    """Execute create/update steps. `job_ids` maps file number → Moveware id.
    Returns counts; never raises."""
    now = now or dt.datetime.now(dt.timezone.utc)
    out = {"enabled": write_enabled(), "blocked": blocked_reason(), "written": 0, "verified": 0,
           "failed": 0, "skipped": 0, "review_left": 0, "done": []}
    if not out["enabled"]:
        return out
    user = auto_user()
    todo = [s for s in steps or [] if s.get("action") in ("create", "update")]
    out["review_left"] = sum(1 for s in steps or [] if s.get("action") == "review")
    for s in todo[:_max()]:
        job = str(s.get("job") or "")
        jid = job_ids.get(job)
        rec = {"at": now.isoformat(), "job": job, "milestone": s.get("milestone"), "action": s["action"],
               "plan_date": s.get("plan_date"), "user": user}
        if not jid:
            out["skipped"] += 1
            rec["result"] = "no Moveware id"
            _log(rec)
            continue
        body = dict(s.get("body") or {})
        body["completedby"] = user                      # always the dedicated user
        try:
            if s["action"] == "create":
                _send("POST", f"/jobs/{jid}/activities", body)
            else:
                if not s.get("activity_id"):
                    raise ValueError("update step without activity id")
                _send("PATCH", f"/jobs/{jid}/activities/{s['activity_id']}", body)
            out["written"] += 1
            ok = False
            try:
                ok = _verified(jid, s)
            except Exception:
                ok = False
            out["verified"] += 1 if ok else 0
            rec["result"] = "verified" if ok else "written, not yet visible"
            _forget(job)
        except Exception as e:                           # noqa: BLE001
            out["failed"] += 1
            rec["result"] = f"failed: {type(e).__name__}: {str(e)[:160]}"
        _log(rec)
        out["done"].append(rec)
    out["skipped"] += max(0, len(todo) - _max())
    return out


def _forget(job: str) -> None:
    """Drop cached diary reads for this file so dashboards re-read it."""
    for mod, attr in (("assistant.mw_confirm", "_CACHE"),):
        try:
            getattr(__import__(mod, fromlist=[attr]), attr).pop(job, None)
        except Exception:
            pass
    try:
        import audit_web
        audit_web._CONFIRMED["by_job"].pop(job, None)
    except Exception:
        pass


_LAST = {"at": 0.0, "result": None}


def run_once(min_gap_s: int = 3 * 3600) -> dict | None:
    """For the keeper: rebuild the plan and apply it, at most every 3 hours.
    Returns None when not due or not enabled."""
    if not write_enabled() or time.time() - _LAST["at"] < min_gap_s:
        return None
    _LAST["at"] = time.time()
    try:
        import audit_web
        import mw_live
        audit_web._build_milestone_plan()
        steps = audit_web._MS_PLAN.get("steps") or []
        if not steps or audit_web._MS_PLAN.get("error"):
            # Plan not ready yet (book or Plan de Viajes still loading): don't use
            # up the 3-hour slot — try again on the next keeper tick (30 min).
            _LAST["at"] = 0.0
            _LAST["result"] = {"skipped_reason": "plan not ready", "error": audit_web._MS_PLAN.get("error")}
            return _LAST["result"]
        ids = {}
        for f in mw_live.audited_files() or []:
            if f.get("job") and f.get("job_id"):
                ids[str(f["job"])] = f["job_id"]
        # Lettered lots (110771A) are found by number when not in the book.
        for s in steps:
            j = str(s.get("job") or "")
            if j and j not in ids and not j.isdigit():
                try:
                    import mw_lots
                    found = mw_lots.find(j)
                    if found.get("status") == "found":
                        ids[j] = (found.get("job") or {}).get("id")
                except Exception:
                    pass
            elif j.isdigit() and j not in ids:
                ids[j] = j
        res = apply(steps, ids)
    except Exception as e:                               # noqa: BLE001
        res = {"error": f"{type(e).__name__}: {e}"[:300]}
    _LAST["result"] = res
    return res
