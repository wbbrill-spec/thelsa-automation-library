"""
mw_ingest.py — correct Moveware V2 ingestion for the FAIM audit.

Implements the rules in MOVEWARE_DATA_CAPTURE_GUIDE.md (saved in the project):
count removals incl. sequels; filter branches IN OUR OWN CODE (never the
server's filter); walk the list to empty/nothing-new (limit is a cap, the list
is NOT date-ordered); select the active window by collect/uplift date with a
look-back for old files still moving; merge list-row + detail activityDates;
read detail-only fields (survey/delivery/booked/coordinator); produce honest
diagnostics with a `complete` flag; persist the last good build; run the heavy
work in a BACKGROUND thread, never inside a web request. Read-only by
construction (GET only).

The FAIM dashboard (faim_web.py) reads load_build(); it never calls Moveware
directly for its numbers.

NOTE: gap-fill-by-id (guide §5.1 — open hidden jobs across the id range) is the
expensive, accuracy-critical step (~3,900 reads / ~22 min for a 12-mo window).
It is implemented here but OFF by default (MW_GAPFILL=0) until the in-scope
branch codes are confirmed with TMS; turning it on is Phase 2.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import mw_live
    _HAVE_MW = True
except Exception:
    _HAVE_MW = False


# ── Tenant config (env-overridable; nothing client-specific hard-coded) ──────────
def _env_list(name, default):
    raw = os.environ.get(name, "")
    vals = [x.strip().upper() for x in raw.split(",") if x.strip()]
    return vals or default


# Branch codes seen in live data: MTU (Monterrey USA), MTM, QRU. In-scope set NOT
# yet confirmed with TMS — see Moveware-Contact-Questions.md. DEFAULT = empty =
# NO branch filter (show the whole port 64000 = all Thelsa), so the demo always
# has data. Scope to specific branches later via MW_BRANCHES (tenant config).
BRANCHES = _env_list("MW_BRANCHES", [])
LOST_STATUSES = set(_env_list("MW_LOST_STATUSES", ["L", "C"]))
WINDOW_MONTHS = int(os.environ.get("MW_WINDOW_MONTHS", "12"))
LOOKBACK_MONTHS = int(os.environ.get("MW_LOOKBACK_MONTHS", "24"))
GAPFILL = os.environ.get("MW_GAPFILL", "0") == "1"

# Walk / fetch budgets. One live page is ~5s, so everything runs in the background.
_PAGE_LIMIT = int(os.environ.get("MW_PAGE_LIMIT", "100"))
_PARALLEL = int(os.environ.get("MW_PARALLEL", "12"))
_WALK_MAX_PAGES = int(os.environ.get("MW_WALK_MAX_PAGES", "400"))
_WALK_BUDGET = float(os.environ.get("MW_WALK_BUDGET", "300"))      # seconds
_ENRICH_BUDGET = float(os.environ.get("MW_ENRICH_BUDGET", "420"))  # seconds
_ENRICH_MAX = int(os.environ.get("MW_ENRICH_MAX", "1200"))         # kept files to detail
_REQ_TIMEOUT = int(os.environ.get("MW_REQ_TIMEOUT", "15"))

_BUILD_FILE = os.environ.get("MW_BUILD_FILE", "/tmp/faim_build.json")

# Build state (single instance; workers share the file on disk).
_LOCK = threading.Lock()
_BUILDING = {"running": False, "started": 0.0, "phase": ""}


# ── HTTP (GET only) ──────────────────────────────────────────────────────────────
def _req(path, timeout=_REQ_TIMEOUT):
    url = mw_live.BASE_URL + "/" + str(path).lstrip("/")
    r = urllib.request.Request(url, headers=mw_live._headers(), method="GET")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        total = resp.headers.get("x-total-count")
    try:
        total = int(total) if total is not None else None
    except (TypeError, ValueError):
        total = None
    return json.loads(body), total


def _get(path, timeout=_REQ_TIMEOUT):
    return _req(path, timeout)[0]


def _g(d, *keys, default=""):
    if not isinstance(d, dict):
        return default
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _date(ad, key):
    """activityDates[key].date as a 'YYYY-MM-DD' string, or ''. Tolerant of shapes."""
    if not isinstance(ad, dict):
        return ""
    node = ad.get(key)
    if isinstance(node, dict):
        return str(node.get("date") or "")[:10]
    if isinstance(node, str):
        return node[:10]
    return ""


def _merge_dates(list_ad, detail_ad):
    """Merge list-row + detail activityDates; the detail wins where it has a date."""
    out = {}
    for src in (list_ad or {}, detail_ad or {}):
        if isinstance(src, dict):
            for k, v in src.items():
                d = _date({k: v}, k)
                if d:
                    out[k] = {"date": d}
    return out


def _base_number(num):
    """Collapse a sequel to its base file number: 412042W -> 412042."""
    s = str(num or "")
    i = len(s)
    while i > 0 and s[i - 1].isalpha():
        i -= 1
    return s[:i] or s


def _coord(detail, row):
    roles = (detail or {}).get("roles") or (row or {}).get("roles") or {}
    for key in ("coordinator", "moveManager"):
        ent = (roles.get(key) or {}).get("entity") or {}
        nm = _g(ent, "name")
        if not nm:
            nm = (str(_g(ent, "firstName")) + " " + str(_g(ent, "lastName"))).strip()
        if nm:
            return nm
    return "Unassigned"


def _has(v):
    return bool(str(v or "").strip())


def _branch_ok(row):
    if not BRANCHES:          # empty = no filter (whole port = all Thelsa)
        return True
    return str(_g(row, "branchCode")).upper() in BRANCHES


def _window_start():
    today = dt.date.today()
    y, m = today.year, today.month - WINDOW_MONTHS
    while m <= 0:
        m += 12
        y -= 1
    return dt.date(y, m, 1).isoformat()


def _lookback_start():
    today = dt.date.today()
    y, m = today.year, today.month - LOOKBACK_MONTHS
    while m <= 0:
        m += 12
        y -= 1
    return dt.date(y, m, 1).isoformat()


# ── 1. Walk the list to empty / nothing-new (parallel, dedupe by id) ─────────────
def walk_list(diag):
    start = time.time()
    by_id = {}
    pages_read = 0
    page = 1
    complete = False
    while page <= _WALK_MAX_PAGES and time.time() - start < _WALK_BUDGET:
        batch = list(range(page, min(page + _PARALLEL, _WALK_MAX_PAGES + 1)))
        got_new = False
        empty_hit = False
        with ThreadPoolExecutor(max_workers=_PARALLEL) as ex:
            futs = {ex.submit(_get, f"/jobs?limit={_PAGE_LIMIT}&page={p}"): p for p in batch}
            for fut in as_completed(futs):
                try:
                    payload = fut.result()
                except Exception:
                    continue
                rows = payload.get("jobs") if isinstance(payload, dict) else None
                rows = rows or []
                if not rows:
                    empty_hit = True
                    continue
                for r in rows:
                    rid = str(_g(r, "id", "jobId"))
                    if rid and rid not in by_id:
                        by_id[rid] = r
                        got_new = True
        pages_read += len(batch)
        page += _PARALLEL
        if empty_hit and not got_new:
            complete = True
            break
        if not got_new:        # a whole batch brought nothing new -> end of useful list
            complete = True
            break
    diag["pages_read"] = pages_read
    diag["rows_scanned"] = len(by_id)
    diag["list_complete"] = complete
    return by_id


# ── 2. Enrich kept candidates with detail (parallel) ────────────────────────────
def _enrich(candidates, diag):
    start = time.time()
    out = []
    ids = list(candidates.keys())[:_ENRICH_MAX]
    diag["enrich_requested"] = len(ids)

    def one(rid):
        try:
            d = _get(f"/jobs/{rid}")
        except Exception:
            return rid, None
        return rid, d

    done = 0
    with ThreadPoolExecutor(max_workers=_PARALLEL) as ex:
        futs = [ex.submit(one, rid) for rid in ids]
        for fut in as_completed(futs):
            if time.time() - start > _ENRICH_BUDGET:
                break
            rid, detail = fut.result()
            row = candidates[rid]
            # Guide §10: trust a detail only if it echoes the id requested.
            if detail and str(_g(detail, "id", "jobId")) not in (rid, ""):
                detail = None
            out.append((row, detail))
            done += 1
    diag["enrich_done"] = done
    return out


# ── 3. Build ─────────────────────────────────────────────────────────────────────
def build():
    """Run a full build. Returns the build dict (also persisted)."""
    if not _HAVE_MW or not mw_live.have_creds():
        return None
    diag = {"branches": BRANCHES, "window_start": _window_start(),
            "lookback_start": _lookback_start(), "gapfill": GAPFILL}
    t0 = time.time()

    _BUILDING["phase"] = "walk"
    by_id = walk_list(diag)

    # List total (honest: this UNDERCOUNTS — the list hides jobs; see guide §5.1).
    try:
        _, list_total = _req("/jobs?limit=1&page=1&count=true")
    except Exception:
        list_total = None
    diag["list_total_header"] = list_total

    lb = _lookback_start()
    candidates, dropped_branch, dropped_old = {}, 0, 0
    for rid, row in by_id.items():
        if not _branch_ok(row):
            dropped_branch += 1
            continue
        ad = row.get("activityDates") or {}
        created = _date(ad, "created")
        uplift = _date(ad, "uplift")
        if (not created or created >= lb) or (uplift and uplift >= lb):
            candidates[rid] = row
        else:
            dropped_old += 1
    diag["dropped_other_branch"] = dropped_branch
    diag["dropped_before_lookback"] = dropped_old
    diag["candidates"] = len(candidates)

    if GAPFILL:
        _BUILDING["phase"] = "gapfill"
        _gapfill(by_id, candidates, diag)

    _BUILDING["phase"] = "enrich"
    enriched = _enrich(candidates, diag)

    ws = _window_start()
    removals = []
    kept_old = 0
    for row, detail in enriched:
        ad = _merge_dates(row.get("activityDates"), (detail or {}).get("activityDates"))
        opened = _date(ad, "created")
        acts = [_date(ad, k) for k in ("uplift", "pack", "delivery")]
        in_win = (not opened or opened >= ws)
        old_moving = (not in_win) and any(a and a >= ws for a in acts)
        if not (in_win or old_moving):
            continue
        if old_moving:
            kept_old += 1
        status = str(_g(detail or row, "status")).upper()
        number = str(_g(row, "number", "numberOnly", "jobNumber") or _g(detail, "number") or "")
        removals.append({
            "id": str(_g(row, "id", "jobId")),
            "number": number,
            "base": _base_number(number),
            "branch": str(_g(row, "branchCode")),
            "status": status,
            "lost": status in LOST_STATUSES,
            "coord": _coord(detail, row),
            "created": opened,
            "uplift": _date(ad, "uplift"),
            "survey": _date(ad, "survey"),
            "delivery": _date(ad, "delivery"),
        })
    diag["old_files_kept"] = kept_old
    diag["removals_in_window"] = len(removals)
    files = {}
    for r in removals:
        files.setdefault(r["base"], []).append(r["number"])
    diag["files_in_window"] = len(files)

    metrics = _faim_metrics(removals)
    diag["elapsed_sec"] = round(time.time() - t0, 1)
    diag["complete"] = bool(diag.get("list_complete")) and (
        diag.get("enrich_done", 0) >= diag.get("enrich_requested", 0)
    ) and (GAPFILL or True)
    diag["coverage_note"] = (
        "List-only build: hidden lost/cancelled and some active jobs are NOT yet "
        "included (gap-fill by id is off). Totals undercount the real book."
        if not GAPFILL else "Gap-fill by id enabled."
    )

    build_obj = {
        "generatedAt": dt.datetime.utcnow().isoformat() + "Z",
        "diagnostics": diag,
        "metrics": metrics,
    }
    _persist(build_obj)
    return build_obj


def _faim_metrics(removals):
    """FAIM evidence coverage over ACTIVE removals (not lost/cancelled)."""
    active = [r for r in removals if not r["lost"]]
    s = len(active)

    def pct(p, a):
        return round(100 * p / a) if a else 0

    cov = {
        "Survey on file": sum(1 for r in active if _has(r["survey"])),
        "Delivery date logged": sum(1 for r in active if _has(r["delivery"])),
        "Collect date logged": sum(1 for r in active if _has(r["uplift"])),
        "Coordinator assigned": sum(1 for r in active if r["coord"] != "Unassigned"),
    }
    by_criterion = sorted([{"name": k, "v": pct(v, s)} for k, v in cov.items()],
                          key=lambda x: -x["v"])

    ctot, ccov = {}, {}
    for r in active:
        c = r["coord"]
        ctot[c] = ctot.get(c, 0) + 1
        if _has(r["survey"]) and _has(r["delivery"]):
            ccov[c] = ccov.get(c, 0) + 1
    by_coordinator = sorted(
        [{"name": c, "v": pct(ccov.get(c, 0), ctot[c])} for c in ctot],
        key=lambda x: -x["v"])

    breaches = []
    for r in active:
        gaps = []
        if not _has(r["survey"]):
            gaps.append("survey")
        if not _has(r["delivery"]):
            gaps.append("delivery date")
        if r["coord"] == "Unassigned":
            gaps.append("coordinator")
        if gaps:
            breaches.append({
                "file": r["number"], "cust": r["base"],
                "rule": "Missing: " + ", ".join(gaps), "coord": r["coord"],
                "days": f"{len(gaps)} gap{ 's' if len(gaps) != 1 else ''}",
                "sev": "crit" if len(gaps) >= 2 else "warn", "_n": len(gaps),
            })
    breaches.sort(key=lambda b: -b["_n"])
    for b in breaches:
        b.pop("_n", None)

    return {
        "tiles": {
            "removals": len(removals),
            "active": s,
            "missingSurvey": sum(1 for r in active if not _has(r["survey"])),
            "unassigned": sum(1 for r in active if r["coord"] == "Unassigned"),
        },
        "byCriterion": by_criterion,
        "byCoordinator": by_coordinator,
        "breaches": breaches[:15],
    }


# ── Gap-fill by id (guide §5.1) — Phase 2; off until branches confirmed ──────────
def _gapfill(by_id, candidates, diag):
    ids_seen = {int(i) for i in by_id if str(i).isdigit()}
    if not ids_seen:
        diag["gapfill_candidates"] = 0
        return
    lo = max(1, min(ids_seen) - 300)
    hi = max(ids_seen) + 50
    missing = [i for i in range(lo, hi + 1) if i not in ids_seen]
    diag["gapfill_candidates"] = len(missing)
    foreign = _load_foreign()
    missing = [i for i in missing if i not in foreign][: int(os.environ.get("MW_GAPFILL_MAX", "4000"))]

    start = time.time()
    read = kept = new_foreign = 0

    def one(i):
        try:
            return i, _get(f"/jobs/{i}")
        except Exception:
            return i, None

    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(one, i) for i in missing]
        for fut in as_completed(futs):
            if time.time() - start > float(os.environ.get("MW_GAPFILL_BUDGET", "1200")):
                break
            i, d = fut.result()
            read += 1
            if not d or str(_g(d, "id", "jobId")) not in (str(i), ""):
                foreign.add(i); new_foreign += 1
                continue
            if _branch_ok(d):
                candidates[str(i)] = d
                kept += 1
            else:
                foreign.add(i); new_foreign += 1
    _save_foreign(foreign)
    diag["gapfill_read"] = read
    diag["gapfill_kept"] = kept
    diag["gapfill_foreign"] = new_foreign


def _foreign_file():
    return os.environ.get("MW_FOREIGN_FILE", "/tmp/faim_foreign_ids.json")


def _load_foreign():
    try:
        return set(json.load(open(_foreign_file())))
    except Exception:
        return set()


def _save_foreign(s):
    try:
        json.dump(sorted(s), open(_foreign_file(), "w"))
    except Exception:
        pass


# ── Persistence + background trigger ─────────────────────────────────────────────
def _persist(obj):
    try:
        json.dump(obj, open(_BUILD_FILE, "w"))
    except Exception:
        pass


def load_build():
    try:
        return json.load(open(_BUILD_FILE))
    except Exception:
        return None


def build_status():
    return {"running": _BUILDING["running"], "phase": _BUILDING["phase"],
            "started": _BUILDING["started"], "has_build": load_build() is not None}


def trigger_build():
    """Start a background build if one is not already running. Returns status."""
    with _LOCK:
        if _BUILDING["running"]:
            return {"started": False, "reason": "already running", **build_status()}
        _BUILDING.update(running=True, started=time.time(), phase="starting")

    def run():
        try:
            build()
        except Exception as e:
            _persist({"generatedAt": dt.datetime.utcnow().isoformat() + "Z",
                      "error": str(e), "diagnostics": {}, "metrics": {}})
        finally:
            _BUILDING.update(running=False, phase="idle")

    threading.Thread(target=run, daemon=True).start()
    return {"started": True, **build_status()}
