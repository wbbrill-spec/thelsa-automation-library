"""
mw_lots.py — find a Moveware job by the number staff actually use, lots included.

Why this exists (Moveware Data Capture Guide §2, §5.1, §5.4):

  * One file can carry several removals: 110771, 110771A, 110771B … Thelsa calls
    them lots, Moveware calls them sequel jobs. Each one is a real job with its
    OWN status, dates, value, invoices and cost.
  * The number staff see (`number`, e.g. 110771A) is NOT the number the API
    opens a job by. The API opens jobs by Moveware's internal `id`, and a lot's
    id is a different number: 110771A is id 110782, 111135A is id 111145,
    105142A is id 105492. Asking the API for /jobs/110771A finds nothing, and
    /jobs/110782 is a job nobody at Thelsa has ever heard of.
  * The job list hides jobs (most lost and cancelled ones, and others), and
    `?number=` finds many jobs but not all. Every job opens at /jobs/{id}.

So a lot is found in this order, cheapest first, and every answer is checked
against what Moveware echoes back before it is believed:

  1. the remembered index (ids this module has already opened)   — no API call
  2. the audit dashboard's cache (every job the auditor has read) — no API call
  3. /jobs/{number} directly, for a plain number                  — 1 call
  4. the list's `number` / `file` filters                         — 3 calls
  5. opening the ids above the base number one by one             — background

Steps 3 and 4 trust nothing the server says about its own filters: a row counts
only if its own `number` is exactly the one asked for. An ignored filter returns
an unrelated page, which simply matches nothing.

Step 5 is the guide's gap-fill by id. A lot is always created after its file,
so its id is above the base number; how far above depends on how much later the
lot was added (110771A is 11 ids later, 105142A is 350). It runs in the
background, writes its result to a file, and is collected with a second request
— a web request must never wait on it. Every id it opens is remembered, so the
next search skips them and reaches further.

Read-only: every call goes through mw_live's GET-only fetch. Nothing here can
write to Moveware. Output is job numbers, ids, status, branch and dates only —
no customer names, no addresses, no money (guide §12).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import mw_live

# ── settings ─────────────────────────────────────────────────────────────────
# How many NOT-YET-KNOWN ids one background search opens above the base number.
# Ids already in the index cost nothing, so repeating a search reaches further.
SCAN_IDS = int(os.environ.get("MW_LOT_SCAN_IDS", "600") or 600)
SCAN_IDS_MAX = 20000                      # hard ceiling whatever is asked for
SCAN_LANES = int(os.environ.get("MW_LOT_SCAN_LANES", "12") or 12)   # guide: 12–16
SCAN_BLOCK = 48                           # ids opened between progress writes
# This many ids in a row with no job means the search has run past the newest
# job in the database. Ids are dense, so a long empty run is the top.
TOP_OF_IDS_RUN = 60
_CALL_TIMEOUT = 10
_STALE_AFTER = 180                        # a search silent this long has died

_LOCK = threading.Lock()
_INDEX = {"loaded": False, "ids": {}}     # internal id (str) -> display number ("" = no job)
_SCANS: dict[str, threading.Thread] = {}

# 4–8 digits, then at most two letters; staff type it as 110771A, 110771-A,
# 110771 a, 110771/A.
_NUMBER = re.compile(r"^\s*(\d{4,8})\s*[-/ ]?\s*([A-Za-z]{0,2})\s*$")


# ── job numbers ──────────────────────────────────────────────────────────────
def parse(number) -> tuple[str, str] | None:
    """'110771-a' → ('110771', 'A'); '110771' → ('110771', ''); junk → None."""
    m = _NUMBER.match(str(number or ""))
    return (m.group(1), m.group(2).upper()) if m else None


def normalize(number) -> str:
    p = parse(number)
    return p[0] + p[1] if p else ""


def base_number(number) -> str:
    """The FILE a job belongs to: 110771A → 110771."""
    p = parse(number)
    return p[0] if p else ""


def is_lot(number) -> bool:
    p = parse(number)
    return bool(p and p[1])


def display_number(job: dict) -> str:
    """The number staff see on a list row or a job detail. Never the id."""
    if not isinstance(job, dict):
        return ""
    for key in ("number", "numberOnly", "fileNumber"):
        v = job.get(key)
        if isinstance(v, dict):
            v = v.get("text") or v.get("code")
        if v not in (None, ""):
            return normalize(v) or str(v).strip().upper()
    return ""


def database() -> str:
    """Which database the app is really reading, from the URL in use — so a
    result can never be labelled TEST while it is showing live jobs (guide §1)."""
    url = mw_live.BASE_URL
    if "/64000/" in url + "/":
        return "live"
    if "/08800/" in url + "/":
        return "test"
    return "other"


# ── the remembered index ─────────────────────────────────────────────────────
def _dir() -> str:
    return os.path.dirname(os.path.abspath(mw_live._CACHE_PATH))


def _index_path() -> str:
    return os.environ.get("MW_LOT_INDEX_PATH") or os.path.join(
        _dir(), f"mw_number_index_{database()}.json")


def _scan_path(number: str) -> str:
    return os.path.join(_dir(), f"mw_lot_search_{database()}_{number}.json")


def _write_json(path: str, obj) -> None:
    try:
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)                       # atomic
    except Exception:
        pass                                        # read-only disk: memory only


def _read_json(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _load_index() -> None:
    with _LOCK:
        if _INDEX["loaded"]:
            return
        snap = _read_json(_index_path())
        ids = snap.get("ids") if isinstance(snap, dict) else None
        if isinstance(ids, dict):
            _INDEX["ids"].update({str(k): str(v or "") for k, v in ids.items()})
        _INDEX["loaded"] = True


def _save_index() -> None:
    with _LOCK:
        snap = {"ids": dict(_INDEX["ids"]), "saved_at": time.time()}
    _write_json(_index_path(), snap)


def _remember(pairs: dict, save: bool = True) -> None:
    """pairs: internal id -> display number, or '' for an id with no job."""
    _load_index()
    with _LOCK:
        for jid, num in pairs.items():
            # A real number is never overwritten by "no job".
            if num or not _INDEX["ids"].get(str(jid)):
                _INDEX["ids"][str(jid)] = num or ""
    if save and pairs:
        _save_index()


def _known_id(number: str) -> str | None:
    _load_index()
    with _LOCK:
        for jid, num in _INDEX["ids"].items():
            if num == number:
                return jid
    return None


def _from_auditor(number: str) -> str | None:
    """The audit dashboard has already opened every job it lists. Its records
    carry both the display number and the internal id."""
    try:
        files = mw_live.audited_files()
    except Exception:
        return None
    hit = None
    learned = {}
    for f in files:
        jid, num = str(f.get("job_id") or ""), normalize(f.get("number"))
        if jid and num:
            learned[jid] = num
            if num == number:
                hit = jid
    if learned:
        _remember(learned, save=bool(hit))
    return hit


def known_on_file(base: str) -> list[dict]:
    """Every job already known on one file: the file itself and its lots."""
    _load_index()
    with _LOCK:
        rows = [{"number": num, "id": jid} for jid, num in _INDEX["ids"].items()
                if num and base_number(num) == base]
    return sorted(rows, key=lambda r: r["number"])


# ── reading Moveware ─────────────────────────────────────────────────────────
class WrongJob(RuntimeError):
    """Moveware answered a request for one id with a different job."""


def _open(jid) -> dict | None:
    """GET /jobs/{id}. Returns the job, or None when there is no such id.
    Any other failure raises — an outage is never recorded as 'no job'."""
    try:
        body = mw_live._get_timed(f"/jobs/{jid}", _CALL_TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not isinstance(body, dict):
        return None
    job = body.get("data") if isinstance(body.get("data"), dict) else body
    # Trust a detail only if it is the job that was asked for (guide §10). A
    # different job coming back is a fault, not an empty id.
    if str(job.get("id") or "") not in ("", str(jid)):
        raise WrongJob(f"asked for id {jid}, Moveware answered with id {job.get('id')}")
    return job if (job.get("id") or display_number(job)) else None


def _list(query: str) -> list[dict]:
    body = mw_live._get_timed(f"/jobs?{query}&limit=10&page=1", _CALL_TIMEOUT)
    rows = (body.get("jobs") or body.get("data") or []) if isinstance(body, dict) else (body or [])
    return [r for r in rows if isinstance(r, dict)]


def _date(job: dict, name: str):
    ad = job.get("activityDates")
    v = ad.get(name) if isinstance(ad, dict) else None
    if isinstance(v, dict):
        v = v.get("date")
    return str(v)[:10] if v else None


def _bill_to_name(job: dict) -> str:
    roles = job.get("roles")
    slot = roles.get("billTo") if isinstance(roles, dict) else None
    if not isinstance(slot, dict):
        return ""
    ent = slot.get("entity") if isinstance(slot.get("entity"), dict) else {}
    return str(ent.get("name") or slot.get("name") or "")


def summary(job: dict) -> dict:
    """What a lookup may show: numbers, status, branch, dates. No names, no
    addresses, no money. `embassy` is a yes/no, not the account name."""
    num = display_number(job)
    code = job.get("branchCode")
    if isinstance(code, dict):
        code = code.get("code") or code.get("text")
    typ = job.get("type")
    if isinstance(typ, dict):
        typ = typ.get("code") or typ.get("text")
    return {
        "number": num,                       # what staff call it
        "id": str(job.get("id") or ""),      # what the API opens it by
        "file": base_number(num),
        "lot": (parse(num) or ("", ""))[1],
        "status": str(job.get("status") or "").strip().upper(),
        "branch": str(code or "").strip().upper(),
        "type": str(typ or "").strip(),
        "embassy": mw_live._is_embassy(_bill_to_name(job)),
        "entered": _date(job, "created"),
        "won": _date(job, "booked"),
        "packing": _date(job, "pack"),
        "uplift": _date(job, "uplift"),
        "delivery": _date(job, "delivery"),
    }


def _confirm(jid: str, number: str) -> dict | None:
    """Open the id and believe it only if Moveware says it is this number."""
    try:
        job = _open(jid)
    except WrongJob:
        return None
    if job is None:
        return None
    got = display_number(job)
    if got:
        _remember({str(jid): got})
    return job if got == number else None


# ── the lookup ───────────────────────────────────────────────────────────────
def find(number) -> dict:
    """Find one job by the number staff use. Fast steps only (a few seconds);
    when they miss, `status` is 'not_found_yet' and the background search is the
    next step (start_search / search_result)."""
    want = normalize(number)
    out = {"asked": str(number or "").strip(), "number": want, "database": database(),
           "status": "invalid", "found_by": None, "tried": []}
    if not want:
        out["error"] = "Not a job number. Expected digits with an optional lot letter, e.g. 110771 or 110771A."
        return out
    base, lot = parse(want)
    out.update({"file": base, "lot": lot})
    if not mw_live.have_creds():
        out.update(status="unavailable", error="Moveware credentials are not set.")
        return out

    def found(job, how):
        out.update(status="found", found_by=how, job=summary(job),
                   known_on_file=known_on_file(base))
        return out

    try:
        # 1 + 2: things already read. Still confirmed against Moveware.
        for how, jid in (("remembered index", _known_id(want)),
                         ("audit dashboard cache", _from_auditor(want))):
            out["tried"].append({"step": how, "id": jid})
            if jid:
                job = _confirm(jid, want)
                if job:
                    return found(job, how)

        # 3: a plain number usually IS its own id. A lot never is.
        if not lot:
            job = _confirm(base, want)
            out["tried"].append({"step": f"open /jobs/{base}", "matched": bool(job)})
            if job:
                return found(job, "opened directly by number")

        # 4: the list filters, in parallel, each row checked in our own code.
        queries = [f"number={want}"] + ([f"number={base}"] if lot else []) + [f"file={base}"]

        def ask(q):
            try:
                return q, _list(q), None
            except Exception as exc:  # noqa: BLE001 — reported, not fatal
                return q, [], f"{type(exc).__name__}"

        learned, hit = {}, None
        with ThreadPoolExecutor(max_workers=len(queries)) as ex:
            for q, rows, err in ex.map(ask, queries):
                same_file = 0
                for r in rows:
                    rid, num = str(r.get("id") or ""), display_number(r)
                    if rid and num:
                        learned[rid] = num
                        same_file += base_number(num) == base
                        if num == want and hit is None:
                            hit = (rid, q)
                out["tried"].append({"step": f"list ?{q}", "rows": len(rows),
                                     "rows_on_this_file": same_file,
                                     **({"error": err} if err else {})})
        _remember(learned)
        if hit:
            job = _confirm(hit[0], want)
            if job:
                return found(job, f"list filter ?{hit[1]}")
    except Exception as exc:  # noqa: BLE001 — Moveware down / slow
        out.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:160]}")
        return out

    out.update(status="not_found_yet", known_on_file=known_on_file(base),
               next="The quick checks missed. The id-by-id search finds jobs the list hides.")
    return out


# ── the background search (guide §5.1: open the ids the list did not show) ───
def _progress(path: str, state: dict) -> None:
    state["updated_at"] = time.time()
    _write_json(path, state)


def _search(want: str, limit: int, every_lot: bool) -> None:
    base = base_number(want)
    path = _scan_path(want)
    state = {"number": want, "file": base, "database": database(), "status": "running",
             "started_at": time.time(), "from_id": int(base) + 1, "ids_opened": 0,
             "ids_skipped_known": 0, "jobs_seen": 0, "no_job": 0, "errors": 0,
             "found": None, "lots_found": [], "complete": False, "stopped_because": None}
    _progress(path, state)
    _load_index()

    def one(jid):
        try:
            job = _open(jid)
            if job is not None and not display_number(job):
                return jid, None, None, "NoNumber"      # a job, but unreadable
            return jid, (display_number(job) if job else ""), job, None
        except Exception as exc:  # noqa: BLE001
            return jid, None, None, type(exc).__name__

    nxt = int(base) + 1
    ceiling = int(base) + SCAN_IDS_MAX * 4
    empty_run = 0
    budget = limit
    hit_job = None

    def on_file(num):
        if num and base_number(num) == base and num not in state["lots_found"]:
            state["lots_found"].append(num)

    try:
        while budget > 0 and state["stopped_because"] is None:
            # The next stretch of ids, in order: (id, number already known or None).
            span, to_open = [], []
            while len(to_open) < min(SCAN_BLOCK, budget) and nxt <= ceiling:
                with _LOCK:
                    known = _INDEX["ids"].get(str(nxt))
                span.append((nxt, known))
                if known is None:
                    to_open.append(nxt)
                nxt += 1
            if not to_open:
                state["stopped_because"] = "id ceiling"
                break
            with ThreadPoolExecutor(max_workers=SCAN_LANES) as ex:
                opened = {jid: (num, job, err) for jid, num, job, err in ex.map(one, to_open)}
            learned, holes = {}, []
            for jid, known in span:                     # strictly in id order
                if known is not None:
                    state["ids_skipped_known"] += 1
                    empty_run = 0 if known else empty_run + 1
                    on_file(known)
                    continue
                num, job, err = opened[jid]
                state["ids_opened"] += 1
                if err:                                 # unknown, so not "empty"
                    state["errors"] += 1
                    empty_run = 0
                elif not num:
                    state["no_job"] += 1
                    holes.append(jid)
                    empty_run += 1
                else:
                    empty_run = 0
                    state["jobs_seen"] += 1
                    learned[str(jid)] = num
                    on_file(num)
                    if num == want and hit_job is None:
                        hit_job = job
                        state["found"] = summary(job)
                if empty_run >= TOP_OF_IDS_RUN:
                    break
            # An id with no job is remembered only when a real job sits above
            # it. Past the newest job, "no job" just means "not created yet".
            with _LOCK:
                top_real = max([int(j) for j in learned]
                               + [int(j) for j, n in _INDEX["ids"].items() if n and j.isdigit()]
                               + [0])
            learned.update({str(j): "" for j in holes if j < top_real})
            _remember(learned)
            budget -= len(to_open)
            if hit_job is not None and not every_lot:
                state["stopped_because"] = "found"
            elif empty_run >= TOP_OF_IDS_RUN:
                state["stopped_because"] = "reached the newest job in the database"
            _progress(path, state)
        if state["stopped_because"] is None:
            state["stopped_because"] = "search limit reached"
    except Exception as exc:  # noqa: BLE001
        state["stopped_because"] = f"error: {type(exc).__name__}"
    # Complete means the answer is final: either the job was found, or every id
    # above the file was opened up to the newest job, with no failed calls.
    reached_top = state["stopped_because"] == "reached the newest job in the database"
    state["complete"] = bool((hit_job is not None and not every_lot)
                             or (reached_top and state["errors"] == 0))
    state["lots_found"] = sorted(state["lots_found"])
    state["to_id"] = nxt - 1
    if hit_job is not None:
        state["status"] = "found"
    elif state["complete"]:
        state["status"] = "does_not_exist"
    else:
        state["status"] = "not_found"
        state["note"] = ("Not proof the job does not exist: the search stopped before the "
                         "newest job. Run it again to continue from id "
                         f"{nxt} — ids already opened are skipped.")
    state["finished_at"] = time.time()
    _progress(path, state)


def start_search(number, *, limit: int | None = None, every_lot: bool = False) -> dict:
    """Start the id-by-id search in the background. Collect it with search_result."""
    want = normalize(number)
    if not want:
        return {"status": "invalid", "asked": str(number or "")}
    limit = max(1, min(int(limit or SCAN_IDS), SCAN_IDS_MAX))
    with _LOCK:
        t = _SCANS.get(want)
        if t is not None and t.is_alive():
            return {"status": "running", "number": want}
        t = threading.Thread(target=_search, args=(want, limit, every_lot),
                             daemon=True, name=f"mw-lot-{want}")
        _SCANS[want] = t
        # Written before the thread starts, so a collect request that lands on
        # another worker never reads the previous search's answer.
        _write_json(_scan_path(want), {"number": want, "status": "running",
                                       "ids_opened": 0, "updated_at": time.time()})
        t.start()
    return {"status": "started", "number": want, "ids_to_open": limit}


def search_result(number) -> dict:
    """The search's result file. Works from any web worker (guide §12)."""
    want = normalize(number)
    state = _read_json(_scan_path(want)) if want else None
    if not isinstance(state, dict):
        return {"status": "no_search", "number": want}
    if state.get("status") == "running" and time.time() - float(state.get("updated_at") or 0) > _STALE_AFTER:
        state["status"] = "interrupted"
        state["note"] = "The search stopped (restart or deploy). Start it again; ids already opened are skipped."
    return state
