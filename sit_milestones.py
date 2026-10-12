"""
sit_milestones.py — pack and delivery dates for TMS files from SIT's Plan de Viajes.

Operations (SIT) runs every TMS move and republishes the Plan de Viajes workbook
about three times a day (crossborder/sit.py reads it for the consolidation board).
TMS rows carry the MoveWare file number (column headed "Teléfono"). MoveWare
itself rarely has pack or delivery dates (training session 9 Oct 2026: the diary
isn't kept), so this module reads the plan history and gives each TMS file:

  pack      = the first day a truck LOADS at the customer's end (origin is not a
              Thelsa warehouse, port, airport or border crossing)
  delivery  = the last day a truck UNLOADS at the customer's end (destination is
              not one of those)

A plan is a schedule, not a confirmation, so every date carries how sure we are:

  "done"       the day has passed and the row was still in a plan published on
               or after that day (it wasn't pulled or moved beforehand)
  "scheduled"  the day is today or later
  "unconfirmed" the day has passed but the last plan that showed the row was
               published before it (the row dropped out — moved or cancelled?)

Read-only: nothing here writes to MoveWare or to the workbook.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import threading
import time

HUB_WORDS = ("BODEGA", "THELSA", "PUERTO", "PORT ", "AEROPUERTO", "AIRPORT", "ADUANA",
             "RECINTO", "FISCAL", "PATIO", "CRUCE", "FRONTERA", "TERMINAL", "NAVIERA",
             "MANZANILLO", "LAZARO CARDENAS", "LÁZARO CÁRDENAS", "ALTAMIRA", "VERACRUZ PUERTO",
             "NUEVO LAREDO", "LAREDO", "REYNOSA", "TIJUANA", "CIUDAD JUAREZ", "CD. JUAREZ",
             "MCALLEN", "MC. ALLEN", "MC ALLEN", "EL PASO", "AICM", "AIFA")
_DATE_IN_NAME = re.compile(r"(\d{1,2})\s+DE\s+([A-ZÁÉÍÓÚ]+)\s+(\d{4})\s*(?:\((\d)\))?", re.I)
_MONTHS = {"ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7,
           "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12}


def is_hub(place: str) -> bool:
    p = " " + (place or "").upper() + " "
    return any(w in p for w in HUB_WORDS)


def workbook_date(name: str):
    """'PLAN DE VIAJES 09 DE OCTUBRE 2026 (2).xlsm' -> (date(2026,10,9), 2)."""
    m = _DATE_IN_NAME.search(name or "")
    if not m:
        return None, 0
    mon = _MONTHS.get(m.group(2).upper().replace("Á", "A").replace("É", "E").replace("Í", "I")
                      .replace("Ó", "O").replace("Ú", "U"))
    if not mon:
        return None, 0
    try:
        return dt.date(int(m.group(3)), mon, int(m.group(1))), int(m.group(4) or 1)
    except ValueError:
        return None, 0


def base_file(job: str) -> str:
    s = (job or "").strip().upper()
    while s and not s[-1].isdigit():
        s = s[:-1]
    return s


def collect(snapshots, today: dt.date | None = None) -> dict:
    """snapshots: [(published_date, [Trip...]), ...] in any order.
    Returns {job_no: {...}} for TMS rows that carry a file number."""
    today = today or dt.date.today()
    jobs: dict = {}
    for pub, trips in sorted(snapshots, key=lambda s: s[0]):
        for t in trips:
            src = getattr(getattr(t, "source", None), "value", None) or getattr(t, "source", None)
            if src != "TMS" or not t.job_no:
                continue
            j = jobs.setdefault(t.job_no, {"job": t.job_no, "customer": t.customer,
                                           "coordinator": t.coordinator, "legs": {}})
            key = (t.origin, t.destination, t.tipo)
            leg = j["legs"].setdefault(key, {"origin": t.origin, "destination": t.destination,
                                             "tipo": t.tipo, "first_seen": pub, "last_seen": pub})
            leg["last_seen"] = pub
            # the latest plan's dates win (moves get rescheduled)
            leg["load"] = t.load_date
            leg["unload"] = t.unload_date
            leg["unit"] = t.unit
    for j in jobs.values():
        pack = deliv = None
        for leg in j["legs"].values():
            kind = re.sub(r"[^a-z]", "", (leg.get("tipo") or "").lower())
            p_day = d_day = None
            if kind.startswith(("desemp", "desep")):          # unpacking at the customer = delivery
                d_day = leg.get("load") or leg.get("unload")
            elif kind in ("huacal", "suelto", "muebles", "empaque", "embalaje"):
                if not is_hub(leg["origin"]):                  # packing services at the customer
                    p_day = leg.get("load")
            else:                                             # truck movements
                if not is_hub(leg["origin"]):
                    p_day = leg.get("load")
                if not is_hub(leg["destination"]):
                    d_day = leg.get("unload")
            if p_day and (pack is None or p_day < pack[0]):
                pack = (p_day, leg)
            if d_day and (deliv is None or d_day > deliv[0]):
                deliv = (d_day, leg)
        j["pack"] = _milestone(pack, today)
        j["delivery"] = _milestone(deliv, today)
        j["legs"] = [{**v, "load": _iso(v.get("load")), "unload": _iso(v.get("unload")),
                      "first_seen": _iso(v["first_seen"]), "last_seen": _iso(v["last_seen"])}
                     for v in j["legs"].values()]
    return jobs


def _iso(d):
    return d.isoformat() if hasattr(d, "isoformat") else d


def _milestone(found, today):
    if not found:
        return None
    day, leg = found
    if day > today:
        status = "scheduled"
    elif leg["last_seen"] >= day:
        status = "done"
    else:
        status = "unconfirmed"
    return {"date": day.isoformat(), "status": status, "last_seen": leg["last_seen"].isoformat(),
            "from": leg["origin"], "to": leg["destination"], "tipo": leg["tipo"]}


def compare(sit_jobs: dict, mw_files: list, today: dt.date | None = None) -> dict:
    """SIT milestones next to MoveWare's own pack / delivery for each TMS file."""
    today = today or dt.date.today()
    by_num = {str(f.get("job") or "").upper(): f for f in mw_files}
    rows = []
    for job, j in sorted(sit_jobs.items()):
        f = by_num.get(job) or by_num.get(base_file(job)) or {}
        mw_pack, mw_del = _iso(f.get("pack")), _iso(f.get("delivery"))
        row = {"job": job, "customer": j["customer"], "coordinator": j["coordinator"],
               "in_moveware": bool(f), "mw_status": f.get("status"),
               "mw_pack": mw_pack, "mw_delivery": mw_del,
               "sit_pack": j["pack"], "sit_delivery": j["delivery"], "legs": j["legs"]}
        gaps = []
        for name, sit, mw in (("pack", j["pack"], mw_pack), ("delivery", j["delivery"], mw_del)):
            if not sit:
                continue
            if not mw:
                gaps.append(f"{name} missing in MoveWare")
            elif mw != sit["date"]:
                gaps.append(f"{name} differs (MoveWare {mw}, SIT {sit['date']})")
        row["gaps"] = gaps
        rows.append(row)
    return {
        "as_of": today.isoformat(), "files": len(rows),
        "pack_missing": sum(1 for r in rows if r["sit_pack"] and not r["mw_pack"]),
        "delivery_missing": sum(1 for r in rows if r["sit_delivery"] and not r["mw_delivery"]),
        "differs": sum(1 for r in rows if any("differs" in g for g in r["gaps"])),
        "not_in_moveware": sum(1 for r in rows if not r["in_moveware"]),
        "rows": rows,
    }


# ── reading the plan history from OneDrive ──────────────────────────────────────
_LOCK = threading.Lock()
_STATE = {"jobs": {}, "built_at": None, "workbooks": [], "error": None, "running": False}


def _list_workbooks(days: int):
    import requests
    from crossborder import sit
    from crossborder.graph_auth import get_token
    token, auth = get_token()
    if not token:
        raise RuntimeError(f"no Graph token: {auth}")
    drive = os.environ.get("SIT_PLAN_DRIVE_ID", sit.DEFAULT_DRIVE_ID).strip()
    folder = os.environ.get("SIT_PLAN_FOLDER_ID", sit.DEFAULT_FOLDER_ID).strip()
    hdr = {"Authorization": f"Bearer {token}"}
    url = (f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{folder}/children"
           "?$select=id,name,lastModifiedDateTime&$top=999")
    kids = []
    while url:
        r = requests.get(url, headers=hdr, timeout=60)
        r.raise_for_status()
        j = r.json()
        kids += j.get("value") or []
        url = j.get("@odata.nextLink")
    since = dt.date.today() - dt.timedelta(days=days)
    # One workbook per day is enough for history: the last pass of each day.
    best = {}
    for k in kids:
        d, n = workbook_date(k.get("name", ""))
        if d and d >= since and str(k.get("name", "")).lower().endswith((".xlsm", ".xlsx")):
            if d not in best or n > best[d][0]:
                best[d] = (n, k)
    return drive, hdr, [(d, v[1]) for d, v in sorted(best.items())]


def build(days: int | None = None) -> dict:
    """Read one workbook per day for the last `days` days (default 90)."""
    import requests
    from crossborder import sit
    days = days or int(os.environ.get("SIT_HISTORY_DAYS", "90"))
    with _LOCK:
        if _STATE["running"]:
            return _STATE
        _STATE["running"] = True
    try:
        drive, hdr, books = _list_workbooks(days)
        snaps, used = [], []
        for d, k in books:
            r = requests.get(f"https://graph.microsoft.com/v1.0/drives/{drive}/items/{k['id']}/content",
                             headers=hdr, timeout=120)
            if r.status_code != 200:
                continue
            trips, _fleet, info = sit.load_workbook_trips(r.content)
            snaps.append((d, trips))
            used.append({"date": d.isoformat(), "name": k.get("name"), "trips": info.get("trips")})
        jobs = collect(snaps)
        with _LOCK:
            _STATE.update(jobs=jobs, built_at=time.time(), workbooks=used, error=None)
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            _STATE["error"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        _STATE["running"] = False
    return _STATE


def ensure_built(max_age_s: int = 6 * 3600):
    if _STATE["running"]:
        return
    if _STATE["built_at"] and time.time() - _STATE["built_at"] < max_age_s:
        return
    threading.Thread(target=build, daemon=True, name="sit-milestones").start()


def state() -> dict:
    with _LOCK:
        return dict(_STATE)
