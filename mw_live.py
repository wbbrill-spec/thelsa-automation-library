"""
mw_live.py — Live MoveWare data pull for the audit dashboard.
 
Reads jobs, quotes, and invoices from the MoveWare REST API using the
mw-username / mw-password / mw-company-id credentials stored in Render env vars,
and maps them to the same dict shape the dashboard's reconcile()/compute_metrics()
already expect.
 
Safety:
- Activates ONLY when all three credentials are present in the environment.
- Every network path is wrapped so a failure returns None and the caller falls
  back to the demo dataset — the /audit page can never break.
- Results are cached in-memory (TTL) so page loads don't hammer MoveWare.
 
The one thing still being confirmed from real data is which charge `type` codes
mean estimated-cost vs sell-price. `_classify_charge()` centralises that so it's
a one-line change once the live structure is inspected via /audit/raw.
"""
from __future__ import annotations
 
import datetime as dt
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
 
_CACHE = {"at": 0.0, "data": None}
_CACHE_TTL = 600  # seconds

# US Embassy AND US Consulate files bill only after DELIVERY (not at pack),
# unlike other moves. Identify them by debtor/client name. Override with
# EMBASSY_PATTERN.
_EMBASSY_RE = re.compile(
    os.environ.get("EMBASSY_PATTERN",
                   r"embajada.*estados\s+unidos|estados\s+unidos.*embajada|"
                   r"u\.?\s*s\.?\s*embassy|embassy\s+of\s+the\s+united\s+states|american\s+embassy|"
                   r"u\.?\s*s\.?\s*consulate|consulate\s+general\s+of\s+the\s+united\s+states|"
                   r"consulado.*estados\s+unidos|consulado\s+(general\s+)?americano"),
    re.IGNORECASE)


def _is_embassy(name: str) -> bool:
    """US Embassy OR US Consulate — both bill only after delivery."""
    return bool(name and _EMBASSY_RE.search(name))
_MAX_JOBS = 3     # cap the deep-load sample — each job makes sub-calls
                  # (quotes/invoices) at ~2-3s each, so keep this low to stay well
                  # inside the proxy/worker timeout; result is cached (TTL) and
                  # further bounded by _LOAD_BUDGET.
 
# ── Moveware V2 endpoint (2026-09-14) ────────────────────────────────────────
# V2 replaces the v1 host (rest.moveconnect.com/Moveware/v1) and moves the
# company id OUT of the mw-company-id header and INTO the URL path.
#
# READ THIS BEFORE "FIXING" THE URL: the LIVE endpoint is served from a host
# literally named `rest.moveware-test.app`. That is correct and deliberate on
# the vendor's side — the hostname does NOT indicate the test database. What
# selects live vs test is the company segment in the path:
#
#     64000 → Thelsa LIVE          08800 → Thelsa TEST
#
# So MW_COMPANY_SEGMENT is the switch, not the hostname. Set it to 08800 to
# point everything at the test DB; leave it unset for live.
MW_HOST = os.environ.get("MOVEWARE_HOST", "https://rest.moveware-test.app").rstrip("/")
MW_COMPANY_SEGMENT = os.environ.get("MW_COMPANY_SEGMENT", "64000").strip().strip("/")
# MOVEWARE_URL still wins outright when set, so a full custom base URL (or a
# rollback to v1) needs no code change.
BASE_URL = (os.environ.get("MOVEWARE_URL")
            or f"{MW_HOST}/{MW_COMPANY_SEGMENT}/api").rstrip("/")


def have_creds() -> bool:
    """V2 authenticates on username + password alone; the company id is in the
    path. MW_COMPANY_ID is no longer required for the client to activate."""
    return all(os.environ.get(k) for k in ("MW_USERNAME", "MW_PASSWORD"))


def _headers() -> dict:
    h = {
        "mw-username": os.environ.get("MW_USERNAME", ""),
        "mw-password": os.environ.get("MW_PASSWORD", ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # V2 does not list mw-company-id. Send it only if it is still configured, so
    # leaving the old Render var in place cannot break anything and removing it
    # cannot either.
    if os.environ.get("MW_COMPANY_ID"):
        h["mw-company-id"] = os.environ["MW_COMPANY_ID"]
    return h


# Per-request timeout for every Moveware call. Kept SHORT on purpose: the /audit
# page makes many sequential calls (deep-load = several jobs × sub-calls each,
# plus the feed count), and if any one call is allowed to hang the cumulative time
# blows past gunicorn's worker timeout and the whole page 500s.
_REQ_TIMEOUT = 10

# Line-level charge reconciliation (options/{id}/charges + invoices/{id}/charges)
# adds sub-calls per file. On by default; set AUDIT_DEEP_LINES=0 in the Render env
# to drop those calls (keeps sell/dates/coordinator/invoiced, drops the line-level
# quote-vs-invoice detail) if the extra calls ever pressure the worker timeout.
_DEEP_LINES = os.environ.get("AUDIT_DEEP_LINES", "1") == "1"


def _raw_json(url: str, timeout: int):
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _raw_json_headers(url: str, timeout: int):
    """Like _raw_json but also returns the response headers (lower-cased keys).
    Used to read V2's `x-total-count` (returned when a feed query carries
    `count=true`)."""
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        hdrs = {str(k).lower(): v for k, v in resp.headers.items()}
        return json.loads(resp.read().decode("utf-8")), hdrs


def _fetch(url: str, timeout: int):
    """GET+parse `url` with a HARD TOTAL deadline.

    urllib's `timeout` is a per-socket-operation (inactivity) timeout: a response
    that trickles in slowly resets it on every chunk and can run for minutes,
    which is exactly what got the gunicorn worker aborted (→ 500s). We run the
    fetch on a daemon thread and abandon it past a hard wall (`timeout` + slack),
    so a stuck call raises TimeoutError and the worker always gets control back.
    """
    box: dict = {}

    def run():
        try:
            box["v"] = _raw_json(url, timeout)
        except Exception as e:  # noqa: BLE001 — surfaced below
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout + 3)
    if t.is_alive():
        raise TimeoutError(f"hard deadline exceeded for {url}")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _get(path: str):
    return _fetch(f"{BASE_URL}/{path.lstrip('/')}", _REQ_TIMEOUT)


def _get_abs(url: str):
    """GET a full URL (e.g. a Moveware _links href)."""
    return _fetch(url, _REQ_TIMEOUT)


def _feed_count() -> int:
    """Exact job count via V2's `x-total-count` header (returned when the query
    carries `count=true`). Confirmed by MoveConnect (Dave Pile, 2026-09-17). This
    replaces the old V1 offset binary-search (`offset` is ignored on V2). Returns
    0 if the header is missing/unavailable."""
    box: dict = {}

    def run():
        try:
            _, hdrs = _raw_json_headers(
                f"{BASE_URL}/jobs?limit=1&page=1&count=true", _COUNT_TIMEOUT)
            box["n"] = hdrs.get("x-total-count")
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(_COUNT_TIMEOUT + 3)
    try:
        return int(box.get("n")) if box.get("n") not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


# V2 caps the /jobs page at ~18 rows and silently repeats page 1 above that
# (measured), so keep the page size small.
_FEED_PAGE_MAX = 10


def _jobs_url(page: int, limit: int = _FEED_PAGE_MAX) -> str:
    """Build a /jobs feed URL the V2 way. V2 reads `page` (1-indexed) and IGNORES
    `offset`; we send BOTH so a MOVEWARE_URL rollback to v1 (which read `offset`)
    still pages. Page size is capped at _FEED_PAGE_MAX (the ~18-row trap)."""
    lim = min(int(limit or _FEED_PAGE_MAX), _FEED_PAGE_MAX)
    return f"/jobs?limit={lim}&page={page}&offset={page}"


def _link_href(links, rel):
    """Return the href for a pagination rel ('next','prev','last') from a
    Moveware `_links` block, checking both the top level and a nested `pages`."""
    if not isinstance(links, dict):
        return None
    for container in (links, links.get("pages") if isinstance(links.get("pages"), dict) else None):
        if not isinstance(container, dict):
            continue
        c = container.get(rel)
        if isinstance(c, dict) and c.get("href"):
            return c["href"]
        if isinstance(c, str) and c.startswith("http"):
            return c
    return None


def _recent_job_items(limit_jobs: int):
    """Return the MOST RECENT `limit_jobs` job list-items.

    The Moveware `/jobs` feed is ordered oldest-first (it leads with the 2016 test
    record 100001 "Prueba/Carlos"), and `offset` is a 1-indexed PAGE number. To
    surface CURRENT files we compute the last page of size `limit_jobs` from the
    total count and fetch it directly. Falls back to the first page's tail if the
    count is unavailable.
    """
    total = 0
    try:
        total = _feed_count()
    except Exception:
        total = 0

    if total:
        # Feed is oldest-first, so the newest files sit at the END of the LAST page.
        last_page = max(1, (total + _FEED_PAGE_MAX - 1) // _FEED_PAGE_MAX)
        collected = []
        for pg in (last_page - 1, last_page):   # oldest→newest so the tail is newest
            if pg < 1:
                continue
            try:
                collected += _page_jobs(_get_timed(_jobs_url(pg), _REQ_TIMEOUT))
            except Exception:
                pass
        if collected:
            return collected[-limit_jobs:]

    # Fallback: first page tail (least-bad if the count is unavailable).
    try:
        jobs = _page_jobs(_get_timed(_jobs_url(1), _REQ_TIMEOUT))
    except Exception:
        try:
            jobs = _page_jobs(_get("/jobs"))
        except Exception:
            jobs = []
    return jobs[-limit_jobs:] if jobs else []
 
 
# The company-64000 /jobs feed exposes NO `next`/`last` pagination links (only
# `self`) — confirmed live via /faim/raw. The default page size is 10, so any
# counter that reads the default page silently undercounts (that was the original
# "10 active files" bug). The only lever the feed honours is an explicit `limit`,
# so we request a page large enough to hold the whole book in one shot and only
# fall back to link-paging if a future feed/env actually exposes links.
# Counting the /jobs feed. Hard-won facts about this Moveware instance:
#   • The feed hands back NO pagination links (only `self`) — confirmed /faim/raw.
#   • It returns exactly `limit` rows (default 10). Reading the default is the
#     original "10 active files" bug; requesting `limit=100` silently returns just
#     the OLDEST 100 (ids 100001–100100) — also a cap, not the book.
#   • It HANGS on very large limits: `limit=5000` blocked the socket read until
#     the gunicorn worker was aborted (→ 500s across the dashboard).
# So we page the feed in SMALL, fast chunks using `?limit&offset`, accumulating
# until a short page ends the feed. Every request is capped well under gunicorn's
# 120s so a slow feed degrades to a floor count instead of killing the worker.
_PAGE_SIZE = _FEED_PAGE_MAX  # rows per status-scan page (V2 caps at ~18; use 10).
_MAX_COUNT_PAGES = 0      # status scan DISABLED — each 500-row page is a slow ~2s
                          # call and we can't afford them within the proxy budget.
                          # We report the exact-ish TOTAL only; active is omitted.
_COUNT_BUDGET = 8.0
_COUNT_TIMEOUT = 10       # hard per-request cap (see _fetch).

# Moveware calls have ~2s round-trip latency and the whole request must finish
# inside the proxy timeout (~30-45s), so the synchronous budget is ~12-15 calls.
_TOTAL_MAX = 32768        # assumed upper bound on job count (company has ~7k)
_TOTAL_MAX_PROBES = 9     # binary-search probes → resolution ~_TOTAL_MAX/2^9 ≈ 64.


def _feed_total():
    """EXACT file count. On V2 this is the `x-total-count` header returned when the
    feed query carries `count=true` (MoveConnect, 2026-09-17) — see `_feed_count`.
    (The old V1 offset-binary-search no longer works: V2 ignores `offset`.) Returns
    0 if unavailable."""
    return _feed_count()


def _get_timed(path: str, timeout: int):
    return _fetch(f"{BASE_URL}/{path.lstrip('/')}", timeout)


def _page_jobs(payload):
    return list(_first(payload, "jobs", default=[]) or []) if isinstance(payload, dict) else []


def _paginate_all_jobs(page_budget: float = _COUNT_BUDGET, max_pages: int = _MAX_COUNT_PAGES):
    """Return (jobs, pages_fetched, exhausted) for the LIGHT /jobs feed — NO
    per-job sub-calls (never touches quotes/invoices/account).

    Pages `?limit=_PAGE_SIZE&offset=PAGE`. CRITICAL: Moveware's `offset` is a
    1-INDEXED PAGE NUMBER, not a row offset — confirmed live: offset=1 & offset=0
    both return page 1 (ids from 100001), and offset=485 with limit=15 returns ids
    ~107261 = row (485-1)×15 = 7260. So pages start at 1 and step by 1. `exhausted`
    is True only when a page comes back SHORT (fewer than _PAGE_SIZE rows) — the
    real end of the feed — so the count is exact. If we stop for any other reason
    (budget, page cap, error, or a repeated page) the count is a floor ("N+").
    """
    start = time.time()
    jobs = []
    seen_first = set()
    page_idx = 1  # Moveware pages are 1-indexed; page 1 = the first rows.
    pages = 0
    exhausted = False
    while pages < max_pages and time.time() - start < page_budget:
        try:
            payload = _get_timed(_jobs_url(page_idx, _PAGE_SIZE), _COUNT_TIMEOUT)
        except Exception:
            break
        page = _page_jobs(payload)
        if not page:
            exhausted = True  # empty page → past the end of the feed
            break
        # Guard against a server that ignores `offset` and re-serves page 0:
        # if this page leads with an id we've already seen, we can't page on.
        first_id = str(_first(page[0], "id", "jobId", "jobNumber", "jobFile", default="") or "")
        if first_id and first_id in seen_first:
            break  # offset not honoured — stop; count is a floor
        seen_first.add(first_id)
        jobs.extend(page)
        pages += 1
        if len(page) < _PAGE_SIZE:
            exhausted = True  # short page → exact end of the feed
            break
        page_idx += 1
    return jobs, pages, exhausted


def _job_status(job) -> str:
    return str(_first(job, "status", "jobStatus", "state", default="") or "").strip().upper()


# Moveware status codes that mean the file is NOT an active/open move. 'C' is the
# confirmed cancelled code (mirrors faim_web). Others are best-effort closed/dead
# states; refine here in one line once the live status distribution is confirmed
# via /audit/counts (the by_status breakdown is exposed there for exactly this).
_INACTIVE_STATUS = {"C", "X", "D", "Z"}


def _job_active(job) -> bool:
    return _job_status(job) not in _INACTIVE_STATUS


_COUNT_CACHE = {"at": 0.0, "data": None}
_COUNT_TTL = 600  # seconds — the true feed count is cached like the deep sample.


def live_file_counts():
    """Return live file counts, or None.

    `total` is EXACT (binary search, cheap). `active` is estimated: we scan a
    BOUNDED sample of the feed for the status mix within a short budget and apply
    the non-cancelled rate to the exact total — keeping the whole call fast so it
    never trips the proxy/worker timeout. `active_estimated` flags this. Cached.
    """
    if not have_creds():
        return None
    now = time.time()
    if _COUNT_CACHE["data"] is not None and now - _COUNT_CACHE["at"] < _COUNT_TTL:
        return _COUNT_CACHE["data"]

    try:
        total = _feed_total()
    except Exception:
        total = 0
    if not total:
        return None

    # Bounded status sample (not the whole feed) → status mix + active rate.
    try:
        jobs, pages, exhausted = _paginate_all_jobs()
    except Exception:
        jobs, pages, exhausted = [], 0, False

    by_status: dict = {}
    active_seen = 0
    for j in jobs:
        st = _job_status(j) or "(blank)"
        by_status[st] = by_status.get(st, 0) + 1
        if _job_active(j):
            active_seen += 1
    seen = len(jobs)

    if seen and seen >= total:          # sample covered the whole feed → exact
        active = active_seen
        estimated = False
    elif seen:                           # extrapolate the active rate to the total
        active = round(total * active_seen / seen)
        estimated = True
    else:                                # no sample → can't estimate active
        active = None
        estimated = True

    data = {
        "total": total,                  # tight approximation (±~8)
        "total_approx": True,
        "active": active,
        "active_estimated": estimated,
        "sample_scanned": seen,
        "pages": pages,
        "exhausted": bool(seen and seen >= total),
        "by_status": by_status,
    }
    _COUNT_CACHE["data"] = data
    _COUNT_CACHE["at"] = now
    return data


def _first(d: dict, *keys, default=None):
    if not isinstance(d, dict):
        return default
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
        for actual in d:
            if actual.lower() == k.lower() and d[actual] not in (None, ""):
                return d[actual]
    return default
 
 
def _num(v) -> float:
    if v in (None, ""):
        return 0.0
    try:
        return float(str(v).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return 0.0
 
 
def _code_text(v) -> str:
    """MoveWare code objects look like {code, text}. Return text (or the value)."""
    if isinstance(v, dict):
        return str(_first(v, "text", "code", default="")).strip()
    return str(v or "").strip()
 
 
def _mode(job: dict) -> str:
    raw = (_code_text(_first(job, "method", "service", default="")) or "").lower()
    if "sea" in raw or "ocean" in raw:
        return "sea"
    if "air" in raw:
        return "air"
    if "road" in raw or "land" in raw or "truck" in raw or "ground" in raw:
        return "road"
    return "sea"  # sensible default; refine from real data
 
 
def _date(v):
    v = _first(v, "date", "value") if isinstance(v, dict) else v
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(str(v)[:19], fmt[: len(str(v)[:19])] if "T" not in str(v) else fmt).date()
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(str(v)[:19]).date()
    except ValueError:
        return None
 
 
def _classify_charge(charge: dict) -> str:
    """Return 'cost' or 'sell' for a quote charge line.
 
    Placeholder heuristic until confirmed from real data via /audit/raw:
    MoveWare quote option charges are the SELL side; cost is inferred from
    rate vs value where available. Adjust here once real charge `type` codes
    are known.
    """
    t = (_code_text(_first(charge, "type", default="")) or "").lower()
    if "cost" in t or "creditor" in t or "supplier" in t or "buy" in t:
        return "cost"
    return "sell"
 
 
def _weight_from_measurements(measurements) -> float | None:
    if not isinstance(measurements, list):
        return None
    for m in measurements:
        t = (_code_text(_first(m, "type", default="")) or "").lower()
        if "weight" in t or "kg" in (str(_first(m, "uom", default="")).lower()):
            return _num(_first(m, "value"))
    return None
 
 
def _map_job(job: dict) -> dict | None:
    job_id = _first(job, "id", "jobId", "jobNumber", "jobFile", "externalId")
    if not job_id:
        return None
    job_id = str(job_id)
 
    # `job` here is the light list item. The rich job object (dates, services,
    # method, status) comes back inside the quotes response, so pull that.
    client = _first(job, "name", "transfereeName", "customerName", default="")
    coordinator = _code_text(_first(job, "moveManager", default=""))
    coordinator_email = ""   # populated from the quote roles below (carries the email)
 
    # ── V2 enrichment ──────────────────────────────────────────────────────
    # RestV2 flattened the old V1 nesting: the rich job (dates, value, roles,
    # insurance, status) comes from GET /jobs/{id} directly — there is NO
    # /jobs/{id}/quotes and NO /jobs/{id}/account in V2 (both 404). Sell = the
    # job's headline `jobValue`; sell lines from /options(+charges); revenue from
    # /invoices. Actual supplier COST is NOT exposed by the API yet (confirmed by
    # MoveConnect 2026-09-17: option charges are sell-only), so `act` stays 0 and
    # profit/margin are suppressed downstream until MoveWare ships a cost endpoint.
    sell = est_cost = 0.0
    charge_lines_total = 0.0
    n_charge_lines = 0
    declared = ins = None
    q_lines = []          # every quoted (sell) charge line — for reconciliation
    sel_lines = []        # accepted-option sell lines (quoted-but-not-invoiced check)
    est_vol = est_wt = act_wt = None

    detail = {}
    try:
        d = _get(f"/jobs/{job_id}") or {}
        detail = _first(d, "data", default=d) or d
    except Exception:
        detail = {}
    src = detail or job

    # Client / transferee name.
    if not client:
        client = (_first(src, "name", "fullname", "searchName", default="")
                  or (f"{_first(src, 'firstName', default='') or ''} "
                      f"{_first(src, 'lastName', default='') or ''}").strip())

    # Sell = the job's headline quote value (V2 puts it on the job object).
    sell = _num(_first(src, "jobValue", "value", default=0))

    # Coordinator (name + email) from job.roles.coordinator || moveManager — each
    # role is an object carrying firstName/lastName/email.
    roles_obj = _first(src, "roles", default={}) or {}
    for _rk in ("coordinator", "moveManager"):
        r = _first(roles_obj, _rk, default={}) or {}
        if isinstance(r, dict):
            nm = (f"{_first(r, 'firstName', default='') or ''} "
                  f"{_first(r, 'lastName', default='') or ''}").strip()
            em = (_first(r, "email", default="") or "").strip()
            if nm and not coordinator:
                coordinator = nm
            if em and not coordinator_email:
                coordinator_email = em
        if coordinator and coordinator_email:
            break
    # Fallback: the /roles array (fetched once) — first by a coordinator-ish role
    # type, then, failing that, by ANY @thelsa.com email on any role. On live V2
    # files the coordinator/moveManager roles are often blank while the Thelsa
    # handler's @thelsa.com address is attached to another role (e.g. originClient);
    # the client's own email is external (never @thelsa.com), so the @thelsa.com
    # scan addresses the file to the real handler instead of "Unassigned".
    if not coordinator_email:
        role_rows = []
        if isinstance(roles_obj, dict):
            role_rows += [v for v in roles_obj.values() if isinstance(v, dict)]
        try:
            rr = _get(f"/jobs/{job_id}/roles") or {}
            role_rows += [r for r in (_first(rr, "roles", default=[]) or []) if isinstance(r, dict)]
        except Exception:
            pass
        # 1) a coordinator-ish role type.
        for r in role_rows:
            t = (_code_text(_first(r, "type", default="")) or "").lower().replace(" ", "")
            if t in ("coordinator", "movemanager", "accountmanager", "manager"):
                nm = (f"{_first(r, 'firstName', default='') or ''} "
                      f"{_first(r, 'lastName', default='') or ''}").strip()
                em = (_first(r, "email", default="") or "").strip()
                if nm and not coordinator:
                    coordinator = nm
                if em:
                    coordinator_email = em
                    break
        # 2) any @thelsa.com email on the file (the client is never @thelsa.com).
        if not coordinator_email:
            for r in role_rows:
                em = (_first(r, "email", default="") or "").strip()
                if em.lower().endswith("@thelsa.com"):
                    coordinator_email = em
                    if not coordinator:
                        nm = (f"{_first(r, 'firstName', default='') or ''} "
                              f"{_first(r, 'lastName', default='') or ''}").strip()
                        coordinator = nm or coordinator
                    break

    # Options → accepted-option sell + sell charge lines (quote scope). Only the
    # charge-line sub-calls are gated by AUDIT_DEEP_LINES (they add calls/file);
    # /options itself is fetched only when we still need a sell figure or lines.
    if _DEEP_LINES or not sell:
        try:
            od = _get(f"/jobs/{job_id}/options") or {}
            options = _first(od, "options", default=[]) or []
            accepted = None
            for opt in options:
                st = (_code_text(_first(opt, "statusQuote", "status", default="")) or "").lower()
                if (_first(opt, "selected") in (True, "true", 1)
                        or st in ("accepted", "won", "selected", "current", "active", "confirmed")):
                    accepted = opt
                    break
            if accepted is None and options:
                accepted = max(options, key=lambda o: _num(_first(o, "valueInclusive", "valueExclusive", "value")))
            if accepted is not None:
                if not sell:
                    sell = _num(_first(accepted, "valueInclusive", "valueExclusive", "value"))
                oid = _first(accepted, "id")
                if _DEEP_LINES and oid is not None:
                    try:
                        cd = _get(f"/jobs/{job_id}/options/{oid}/charges") or {}
                        for ch in (_first(cd, "charges", default=[]) or []):
                            cval = _num(_first(ch, "valueInclusive", "value", "valueExclusive"))
                            if cval <= 0:
                                continue
                            charge_lines_total += cval
                            n_charge_lines += 1
                            desc = _code_text(_first(ch, "description", default=""))
                            # V2 option charges are SELL-only (per MoveConnect); the
                            # cost branch is kept for when a cost endpoint arrives.
                            if _classify_charge(ch) == "cost":
                                est_cost += cval
                            else:
                                sel_lines.append({"desc": desc, "value": round(cval, 2)})
                            q_lines.append({"desc": desc, "value": round(cval, 2)})
                    except Exception:
                        pass
            if not sell and sel_lines:
                sell = round(sum(l["value"] for l in sel_lines), 2)
        except Exception:
            pass

    # Dates from V2 activityDates{name:{date,time}} (most blank on any file); fall
    # back to a legacy `dates` block for older cached snapshots.
    ad = _first(src, "activityDates", default={}) or {}
    legacy = _first(src, "dates", default={}) or {}

    def _adate(name):
        v = _first(ad, name, default=None)
        if isinstance(v, dict):
            d0 = _date(_first(v, "date"))
            if d0:
                return d0
        elif v:
            d0 = _date(v)
            if d0:
                return d0
        lv = _first(legacy, name, default={})
        return _date(_first(lv, "date")) if isinstance(lv, dict) else _date(lv)

    pack = _adate("uplift") or _adate("pack") or _adate("packing") or \
        _date(_first(job, "uplift", "pack", "estimatedMove"))
    delivery = _adate("delivery") or _adate("cartonDel") or _adate("unpack") or \
        _adate("cartonDelivery") or _adate("unpacking") or \
        _date(_first(job, "delivery", "deliveryStart", "estimatedDelivery"))
    created = _adate("created") or _date(_first(src, "created")) or _date(_first(job, "created"))
    survey = _adate("survey")
    est_move = _adate("estimatedMove") or _date(_first(src, "estimatedMove"))
    anchor = max([d for d in (delivery, pack, survey, est_move, created) if d], default=None)

    # Insurance: V2 puts it on the job as insurance{value,premium}.
    ins_obj = _first(src, "insurance", default={}) or {}
    if not isinstance(ins_obj, dict):
        ins_obj = {}
    declared = _num(_first(ins_obj, "value")) or None
    ins = _num(_first(ins_obj, "premium")) or None

    # Invoices → invoiced amount + invoiced charge lines (revenue reconciliation).
    invoiced_amt = 0.0
    invoiced = False
    i_lines = []
    try:
        inv = _get(f"/jobs/{job_id}/invoices") or {}
        for it in (_first(inv, "invoices", default=[]) or []):
            iv = _num(_first(it, "valueInclusive", "value", "valueExclusive", "total", "amount"))
            invoiced_amt += iv
            iid = _first(it, "id")
            got = False
            if _DEEP_LINES and iid is not None:
                try:
                    cd = _get(f"/jobs/{job_id}/invoices/{iid}/charges") or {}
                    for ch in (_first(cd, "charges", default=[]) or []):
                        cval = _num(_first(ch, "valueInclusive", "value", "valueExclusive"))
                        if cval > 0:
                            i_lines.append({"desc": _code_text(_first(ch, "description", default="")),
                                            "value": round(cval, 2)})
                            got = True
                except Exception:
                    pass
            if not got and iv > 0:
                i_lines.append({"desc": _code_text(_first(it, "description", default="")),
                                "value": round(iv, 2)})
        invoiced = invoiced_amt > 0
    except Exception:
        pass

    # Actual (supplier/creditor) cost is NOT exposed by RestV2 yet (MoveConnect,
    # 2026-09-17: option charges are sell-only). Leave it 0 so profit/margin stay
    # suppressed downstream until MoveWare ships a cost endpoint.
    actual_cost = 0.0

    mode = _mode(src if src else job)

    # Status string (W=Won, L=Lead, P=Pending, C=Cancelled). V2 puts it on the job
    # as a plain `status`; fall back to a rich jobStatus.code / the light row.
    _jstat = (_first(src, "status", default="")
              or _first(_first(src, "jobStatus", default={}) or {}, "code")
              or _first(job, "status") or "")
    status = str(_code_text(_jstat)).strip().upper()

    return {
        "job": job_id,
        "client": client or "",
        "mode": mode,
        "status": status,
        "is_embassy": _is_embassy(client or ""),
        "est": round(est_cost, 2),
        "act": round(actual_cost, 2),
        "sell": round(sell, 2),
        "inv_amt": round(invoiced_amt, 2),
        "invoiced": invoiced,
        "declared": declared,
        "ins": ins,
        "coordinator": coordinator,
        "coordinator_email": coordinator_email,
        "agent": None,
        "pack": pack,
        "delivery": delivery,
        "created": created,
        "anchor": anchor,   # most-recent activity date; window filter uses this
        # Line-level reconciliation: every quoted charge line vs every invoiced
        # charge line, plus the quote's estimated size vs the actual. audit_web
        # matches these so scope changes are explained, not flagged as errors.
        "q_lines": q_lines,
        "sel_lines": sel_lines,   # accepted-option charge lines (under-billing check)
        "i_lines": i_lines,
        "est_vol": est_vol,
        "act_wt": act_wt,
        "est_wt": est_wt,
        # Internal-recalculation inputs (revenue side). rev_reported is the
        # selected option's header value; rev_lines is the sum of that option's
        # charge lines. When n_rev_lines == 0 the check is skipped (no lines to
        # add up), so files without a line breakdown never produce false flags.
        "rev_reported": round(sell, 2),
        "rev_lines": round(charge_lines_total, 2),
        "n_rev_lines": n_charge_lines,
        # Cost side: `act` (actual/creditor total) is itself the sum of account
        # lines, so there is no separate header to recalc against yet. cost_lines
        # mirrors est (sum of cost-classified quote charges) for the quote->actual
        # cost comparison; a true cost internal-recalc needs a cost header field
        # confirmed via /audit/raw.
        "cost_lines": round(est_cost, 2),
    }
 
 
# Wall-clock budget for the deep-load. Each sampled job costs 3 Moveware
# sub-calls (quotes/invoices/account); left unbounded, a slow Moveware makes the
# loop run past gunicorn's worker timeout and 500 the page. We stop mapping new
# jobs once this budget is spent and render whatever loaded — a smaller sample,
# never a crash.
_LOAD_BUDGET = 35.0
# Last successful deep-load, kept with NO expiry. If a fresh load is slow, errors,
# or comes back empty, we serve this rather than dropping to the demo dataset, so
# the dashboard keeps showing real data through a Moveware hiccup.
_LAST_GOOD = {"data": None}


def load_live_files():
    """Return mapped live files (deep sample), or None to fall back to demo.

    Bounded on purpose: at most _MAX_JOBS jobs, and the mapping loop stops early
    once _LOAD_BUDGET seconds have elapsed so a slow Moveware can never run the
    request past the gunicorn worker timeout. On any failure or empty result we
    serve the last good sample instead of None so the page stays on live data.
    """
    if not have_creds():
        return None
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["data"]
    try:
        # Pull the MOST RECENT jobs (feed is oldest-first) so we surface current
        # operational files, not the legacy 2016 test records at the top.
        jobs = _recent_job_items(_MAX_JOBS)
        if not jobs:
            return _LAST_GOOD["data"]
        mapped = []
        start = time.time()
        for job in jobs:
            if time.time() - start > _LOAD_BUDGET:
                break  # budget spent — render with the sample gathered so far
            # _map_job pulls the rich job object from the quotes response,
            # so the light list item is enough to start from.
            try:
                m = _map_job(job)
            except Exception:
                m = None
            if m:
                mapped.append(m)
        if not mapped:
            return _LAST_GOOD["data"]
        _CACHE["data"] = mapped
        _CACHE["at"] = now
        _LAST_GOOD["data"] = mapped
        return mapped
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return _LAST_GOOD["data"]
    except Exception:
        return _LAST_GOOD["data"]
 
 
# ── Background revenue auditor ───────────────────────────────────────────────────
# The dashboard must never call the Moveware API synchronously on a page load
# (each call is ~2s and a page needs many → proxy/worker timeouts → 500s). Instead
# a daemon thread continuously walks the feed newest→oldest, deep-checks each
# file's REVENUE side (quotes + invoices, via _map_job — no cost call), and caches
# the result by job id. The /audit page renders instantly from this cache, and
# coverage grows toward the whole book over time. Cancelled files are skipped.
_AUDIT = {
    "files": {},          # job id -> mapped revenue dict (in-window only)
    "old_ids": set(),     # job ids classified as older than the window (skip on re-scan)
    "total": None,        # feed total (from _feed_total, computed once)
    "page": None,         # current 1-indexed page cursor (walking backwards)
    "cycles": 0,
    "errors": 0,
    "consec_old": 0,      # run of consecutive out-of-window files seen while walking
    "started_at": None,
    "last_cycle_at": None,
    "wrapped": False,     # True once we've reached the far edge of the window
    "window_complete": False,  # full-coverage: True once EVERY file is audited (100%)
    "window_ready": False,     # True once the recent 12-month window is fully covered
                               # (fast; the recoverable view is usable from here)
    "last_full_at": None, # unix time the last COMPLETE scan finished (persisted)
    "saved_at": None,     # unix time the snapshot was last written to disk
    "refetch": False,     # True during a scheduled refresh (re-fetch cached files)
}
_AUDIT_LOCK = threading.Lock()
_AUDIT_THREAD = None
_AUDIT_SLEEP = 2          # seconds between cycles while actively scanning
_AUDIT_IDLE_SLEEP = 60    # seconds between checks while idle (snapshot complete)
# Each cycle fetches one light page of ids and deep-checks EVERY new file on it
# (each file = 2 API calls at ~2s). The per-file fetches run CONCURRENTLY across
# a small thread pool, so a page completes in roughly (page/workers)*2*2 seconds
# instead of page*4. With 40/cycle at ~12s/cycle the full ~12-month window
# (a few hundred files) is covered in ~2-3 minutes rather than ~15. Files are
# still processed in a strict newest→oldest run so the window-edge stop is exact.
_AUDIT_PAGE = _FEED_PAGE_MAX  # ids fetched + classified per cycle (V2 caps ~18)
_AUDIT_WORKERS = 12       # concurrent per-file fetches (I/O-bound; GIL released)
_AUDIT_BATCH = _AUDIT_PAGE  # back-compat alias (per-cycle deep-check count)


def _safe_map(job):
    try:
        return _map_job(job)
    except Exception:
        return None

# ── Rolling audit window ──────────────────────────────────────────────────
# The audit only covers recent files: in the moving industry, over-charges on
# jobs older than ~a year are effectively unrecoverable, so auditing the whole
# 11k-file history is both pointless and slow. We keep a file when its move
# (delivery, else pack) date is within _WINDOW_DAYS, OR when it has no move date
# yet (an open quote / not-yet-scheduled job — current pipeline, always shown).
# Files with a move date older than the window are excluded. Because job ids are
# chronological and we walk newest→oldest, once we hit a solid run of out-of-
# window files (_WINDOW_STOP in a row) the whole window is covered and the walk
# stops advancing (idles, re-scanning the newest pages for new/updated files).
_WINDOW_DAYS = 365
_WINDOW_STOP = 60         # consecutive out-of-window files => window fully covered

# Full coverage: audit EVERY active file in the book (100% coverage), not just the
# last 12 months. In-window files keep the full record (line items etc.) for the
# recoverable-window analysis; out-of-window ("historical") files are audited too
# but kept as a LIGHT record (no charge lines) — enough for coverage/revenue, not
# for the discrepancy view (which stays window-scoped). The dashboard filters to
# the 12-month window for its metrics and reports total coverage separately.
_FULL_COVERAGE = os.environ.get("AUDIT_FULL_COVERAGE", "1") == "1"
_REFRESH_SCAN_PAGES = 40   # newest pages scanned for brand-new files on refresh
_BACKFILL_PERSIST_EVERY = 20  # persist the growing snapshot every N backfill cycles


def _trim_historical(m: dict) -> dict:
    """Light record for an out-of-window file: drop the heavy per-charge line
    lists (only the in-window discrepancy view needs those)."""
    m2 = dict(m)
    for k in ("q_lines", "i_lines", "rev_lines", "rev_reported", "n_rev_lines"):
        m2.pop(k, None)
    return m2


def _window_cutoff() -> "dt.date":
    return dt.date.today() - dt.timedelta(days=_WINDOW_DAYS)


def _file_anchor_date(m: dict):
    """Date a file is judged 'recent' by: the precomputed `anchor` (most-recent
    activity across delivery/pack/survey/estimatedMove/created), falling back to
    the individual fields for older cached snapshots. `created` is always present
    on a MoveWare job, so in practice this is never None for live data."""
    return m.get("anchor") or m.get("delivery") or m.get("pack") or m.get("created")


def _is_out_of_window(m: dict) -> bool:
    """True only for files with a move date OLDER than the window. Undated
    (open/quoting) files are never out-of-window — they're current work."""
    anchor = _file_anchor_date(m)
    return bool(anchor and anchor < _window_cutoff())


def _auditor_last_page(total: int) -> int:
    return max(1, (total + _AUDIT_PAGE - 1) // _AUDIT_PAGE)


# ── Persistence + refresh cadence ───────────────────────────────────────────
# The audit is a COMPREHENSIVE snapshot of the last 12 months that is persisted
# to disk and refreshed on a schedule — NOT a live crawl that rebuilds from zero
# on every restart. On boot we load the last snapshot (instant, complete view);
# a full re-scan then runs every _REFRESH_SECONDS to pick up new bookings /
# invoices / deliveries. A full scan is cheap (~2-3 min), so 6h (4x/day) keeps
# data <=6h stale — fresh enough to flag an unbilled delivered move same-day —
# with negligible redundant work.
_REFRESH_SECONDS = int(os.environ.get("AUDIT_REFRESH_SECONDS", 6 * 3600))
# Where the snapshot is stored. If a persistent disk is mounted (Render mounts
# them at /var/data), the snapshot auto-persists there and survives DEPLOYS with
# no extra config. Otherwise it falls back next to the app, which survives
# process restarts but not redeploys. Override explicitly with AUDIT_CACHE_PATH.
def _default_cache_path() -> str:
    for base in ("/var/data", "/data"):
        try:
            if os.path.isdir(base) and os.access(base, os.W_OK):
                return os.path.join(base, "audit_cache.json")
        except Exception:
            pass
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_cache.json")


_CACHE_PATH = os.environ.get("AUDIT_CACHE_PATH") or _default_cache_path()


def _json_default(o):
    if isinstance(o, dt.date):
        return {"__date__": o.isoformat()}
    raise TypeError(f"not serializable: {type(o)}")


def _json_obj_hook(d):
    if "__date__" in d:
        try:
            return dt.date.fromisoformat(d["__date__"])
        except Exception:
            return None
    return d


def _persist_snapshot():
    """Write the current audited window to disk so a restart/deploy can resume
    instantly instead of re-crawling the API. Best-effort; never raises."""
    with _AUDIT_LOCK:
        snap = {
            "files": _AUDIT["files"],
            "old_ids": list(_AUDIT["old_ids"]),
            "total": _AUDIT["total"],
            "window_complete": _AUDIT["window_complete"],
            "window_ready": _AUDIT["window_ready"],
            "full_coverage": _FULL_COVERAGE,
            "window_days": _WINDOW_DAYS,
            "last_full_at": _AUDIT["last_full_at"],
            "saved_at": time.time(),
        }
    try:
        tmp = _CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f, default=_json_default)
        os.replace(tmp, _CACHE_PATH)  # atomic
        with _AUDIT_LOCK:
            _AUDIT["saved_at"] = snap["saved_at"]
    except Exception:
        pass  # e.g. read-only FS — degrade to in-memory only


def _load_snapshot() -> bool:
    """Load a persisted snapshot into _AUDIT on boot. Returns True if a usable
    snapshot was loaded. Ignores snapshots built for a different window length."""
    try:
        with open(_CACHE_PATH) as f:
            snap = json.load(f, object_hook=_json_obj_hook)
    except FileNotFoundError:
        return False
    except Exception:
        return False
    if snap.get("window_days") != _WINDOW_DAYS:
        return False
    files = snap.get("files") or {}
    if not isinstance(files, dict):
        return False
    total = snap.get("total")
    snap_wc = bool(snap.get("window_complete"))
    snap_full = bool(snap.get("full_coverage"))
    if _FULL_COVERAGE:
        # In full-coverage mode window_complete means "whole book audited". A
        # snapshot from window-only mode (or a partial backfill) has far fewer
        # files than the feed — treat the book as NOT complete so the historical
        # backfill runs, but the recoverable window is already covered (ready).
        fully = bool(total) and len(files) >= int(total * 0.95)
        wc = fully
        wr = bool(snap.get("window_ready")) or snap_wc or fully
    else:
        wc = snap_wc
        wr = bool(snap.get("window_ready")) or snap_wc
    with _AUDIT_LOCK:
        _AUDIT["files"] = files
        _AUDIT["old_ids"] = set(snap.get("old_ids") or [])
        _AUDIT["total"] = total
        _AUDIT["window_complete"] = wc
        _AUDIT["window_ready"] = wr
        _AUDIT["last_full_at"] = snap.get("last_full_at") if wc else None
        _AUDIT["saved_at"] = snap.get("saved_at")
        _AUDIT["page"] = _auditor_last_page(total or 1)
    return True


_TOTAL_REFRESH_CYCLES = 30   # while idle, re-probe the feed total this often
                             # (~every 5 min) so files created after boot are seen


def _auditor_cycle():
    with _AUDIT_LOCK:
        total = _AUDIT["total"]
        page = _AUDIT["page"]
        complete = _AUDIT["window_complete"]
        cycles = _AUDIT["cycles"]
    if not total:
        total = _feed_total() or 0
        if not total:
            return
        page = _auditor_last_page(total)
        with _AUDIT_LOCK:
            _AUDIT["total"] = total
            _AUDIT["page"] = page
    # Once the window is fully covered we idle at the newest page. Periodically
    # re-probe the feed total so newly-created files (higher ids, on pages beyond
    # the old last page) come into view and get audited — keeps it a live,
    # rolling window rather than a one-shot snapshot.
    elif complete and cycles % _TOTAL_REFRESH_CYCLES == 0:
        fresh = _feed_total() or total
        if fresh > total:
            total = fresh
            with _AUDIT_LOCK:
                _AUDIT["total"] = total
                _AUDIT["page"] = _auditor_last_page(total)
                page = _AUDIT["page"]
    # Fetch exactly one batch-sized page at the cursor and process ALL of it, so
    # files are seen in a strict newest→oldest run (page size == batch). This is
    # what makes the "consecutive out-of-window" count reliable: within a page
    # ids ascend, so reversed() is newest-first, and page decreases each cycle.
    last_page = _auditor_last_page(total)
    try:
        jobs = _page_jobs(_get_timed(_jobs_url(page, _AUDIT_PAGE), _REQ_TIMEOUT))
    except Exception:
        with _AUDIT_LOCK:
            _AUDIT["errors"] += 1
        return
    # Build the newest→oldest list of candidates on this page that still need
    # classifying (skip inactive + already-seen), then fetch them all CONCURRENTLY.
    with _AUDIT_LOCK:
        refetch = _AUDIT["refetch"]
    candidates = []  # (jid, job) in newest-first order
    for j in reversed(jobs):
        jid = str(_first(j, "id", "jobId", "jobNumber", "jobFile", default="") or "")
        if not jid or _job_status(j) in _INACTIVE_STATUS:
            continue
        with _AUDIT_LOCK:
            if jid in _AUDIT["old_ids"]:
                continue
            # During a scheduled refresh we re-fetch cached files too (to pick up
            # new invoices / date changes); otherwise skip ones already audited.
            if jid in _AUDIT["files"] and not refetch:
                continue
        candidates.append((jid, j))

    mapped = {}
    if candidates:
        with ThreadPoolExecutor(max_workers=_AUDIT_WORKERS) as ex:
            futs = {ex.submit(_safe_map, j): jid for jid, j in candidates}
            for fut in futs:
                mapped[futs[fut]] = fut.result()

    # Classify in strict newest→oldest order so the consecutive-out-of-window
    # count (and the window-edge stop) stays exact even though fetches ran async.
    hit_window_edge = False
    for jid, _j in candidates:
        m = mapped.get(jid)
        if not m:
            continue
        if _FULL_COVERAGE:
            # Audit EVERYTHING. In-window files keep the full record; out-of-window
            # ("historical") files get a light record. Never trigger the window-edge
            # stop — the walk runs to page 1, so coverage reaches 100%.
            out = _is_out_of_window(m)
            with _AUDIT_LOCK:
                _AUDIT["files"][jid] = _trim_historical(m) if out else m
                if out and not _AUDIT["window_ready"]:
                    # Walking newest→oldest, the first out-of-window file means every
                    # recent (in-window) file above it is already cached: the
                    # recoverable 12-month view is ready even though the historical
                    # backfill continues.
                    _AUDIT["window_ready"] = True
            continue
        anchor = _file_anchor_date(m)
        if anchor is None:
            # Undated file (open quote / not yet scheduled). Keep it as current
            # pipeline, but DO NOT reset the out-of-window run. Because we walk
            # newest→oldest by (chronological) id, undated files reached before
            # the window edge are recent; ones below the edge are never reached
            # (we stop first). Resetting the run here would let a single ancient
            # never-dated lead keep the walk from ever terminating (it would
            # crawl the entire ~11k-file history — the bug this fixes).
            with _AUDIT_LOCK:
                _AUDIT["files"][jid] = m
        elif _is_out_of_window(m):
            with _AUDIT_LOCK:
                _AUDIT["old_ids"].add(jid)
                _AUDIT["files"].pop(jid, None)  # prune files that rolled off the window
                _AUDIT["consec_old"] += 1
                if _AUDIT["consec_old"] >= _WINDOW_STOP:
                    _AUDIT["window_complete"] = True
                    _AUDIT["wrapped"] = True
                    hit_window_edge = True
            if hit_window_edge:
                break
        else:
            with _AUDIT_LOCK:
                _AUDIT["files"][jid] = m
                _AUDIT["consec_old"] = 0  # dated & recent — window still open
    # Cursor management.
    with _AUDIT_LOCK:
        if hit_window_edge:
            # Far edge of the window confirmed — coverage of the last N months is
            # complete. Park at the newest page and keep re-scanning it so newly
            # created files get picked up; stop walking into old history.
            _AUDIT["page"] = last_page
            _AUDIT["consec_old"] = 0
        elif complete:
            # Already complete: idle by re-scanning the newest page for new files.
            _AUDIT["page"] = last_page
        elif page <= 1:
            # Reached the very oldest file without ever hitting the window edge —
            # the whole feed fits inside the window. Mark complete and idle at top.
            _AUDIT["window_complete"] = True
            _AUDIT["wrapped"] = True
            _AUDIT["consec_old"] = 0
            _AUDIT["page"] = last_page
        else:
            _AUDIT["page"] = page - 1
        _AUDIT["cycles"] += 1
        _AUDIT["last_cycle_at"] = time.time()


def _refresh_recent():
    """Scheduled refresh (full-coverage mode). Old files are immutable, so only
    RE-audit the in-window files (new invoices/dates) and pick up brand-new files
    from the newest pages. Historical records are left untouched."""
    # Re-probe the feed total so newly-created files (higher ids, on pages past the
    # old last page) are in range of the newest-pages scan below.
    total = _feed_total() or 0
    with _AUDIT_LOCK:
        if total:
            _AUDIT["total"] = total
        else:
            total = _AUDIT["total"] or 0
    if not total:
        return
    last_page = _auditor_last_page(total)
    with _AUDIT_LOCK:
        in_window_ids = [jid for jid, m in _AUDIT["files"].items() if not _is_out_of_window(m)]
    # Detect brand-new files on the newest pages (ids not yet audited).
    new_jobs = {}
    for p in range(last_page, max(0, last_page - _REFRESH_SCAN_PAGES), -1):
        try:
            jobs = _page_jobs(_get_timed(_jobs_url(p, _AUDIT_PAGE), _REQ_TIMEOUT))
        except Exception:
            continue
        for j in jobs:
            jid = str(_first(j, "id", "jobId", "jobNumber", "jobFile", default="") or "")
            if not jid or _job_status(j) in _INACTIVE_STATUS:
                continue
            with _AUDIT_LOCK:
                known = jid in _AUDIT["files"]
            if not known:
                new_jobs[jid] = j
    targets = [(jid, {"id": jid}) for jid in in_window_ids] + list(new_jobs.items())
    for i in range(0, len(targets), _AUDIT_PAGE):
        batch = targets[i:i + _AUDIT_PAGE]
        results = {}
        with ThreadPoolExecutor(max_workers=_AUDIT_WORKERS) as ex:
            futs = {ex.submit(_safe_map, j): jid for jid, j in batch}
            for fut in futs:
                results[futs[fut]] = fut.result()
        with _AUDIT_LOCK:
            for jid, m in results.items():
                if not m:
                    continue
                _AUDIT["files"][jid] = _trim_historical(m) if _is_out_of_window(m) else m
            _AUDIT["cycles"] += 1


def _auditor_loop():
    with _AUDIT_LOCK:
        _AUDIT["started_at"] = time.time()
    # Resume from the last persisted snapshot: instant comprehensive view after a
    # restart/deploy instead of re-crawling from zero.
    _load_snapshot()

    if _FULL_COVERAGE:
        while True:
            try:
                with _AUDIT_LOCK:
                    complete = _AUDIT["window_complete"]   # full backfill done?
                    ready = _AUDIT["window_ready"]
                    last_full = _AUDIT["last_full_at"] or 0
                if not complete:
                    # Backfill phase: walk the whole book, auditing every file.
                    with _AUDIT_LOCK:
                        was_ready = _AUDIT["window_ready"]
                    _auditor_cycle()
                    with _AUDIT_LOCK:
                        now_complete = _AUDIT["window_complete"]
                        now_ready = _AUDIT["window_ready"]
                        cyc = _AUDIT["cycles"]
                    # Persist as soon as the recoverable window is covered, on full
                    # completion, and periodically through the long historical crawl.
                    if now_complete:
                        with _AUDIT_LOCK:
                            _AUDIT["last_full_at"] = time.time()
                        _persist_snapshot()
                    elif (now_ready and not was_ready) or (cyc % _BACKFILL_PERSIST_EVERY == 0):
                        _persist_snapshot()
                    time.sleep(_AUDIT_SLEEP)
                elif (time.time() - last_full) >= _REFRESH_SECONDS:
                    _refresh_recent()
                    with _AUDIT_LOCK:
                        _AUDIT["last_full_at"] = time.time()
                    _persist_snapshot()
                else:
                    time.sleep(_AUDIT_IDLE_SLEEP)
            except Exception:
                with _AUDIT_LOCK:
                    _AUDIT["errors"] += 1
                time.sleep(_AUDIT_SLEEP)
        return

    # ── Window-only mode (legacy / _FULL_COVERAGE off) ──
    while True:
        try:
            with _AUDIT_LOCK:
                complete = _AUDIT["window_complete"]
                last_full = _AUDIT["last_full_at"] or 0
            if complete and (time.time() - last_full) < _REFRESH_SECONDS:
                time.sleep(_AUDIT_IDLE_SLEEP)
                continue
            if complete:
                with _AUDIT_LOCK:
                    _AUDIT["refetch"] = True
                    _AUDIT["window_complete"] = False
                    _AUDIT["wrapped"] = False
                    _AUDIT["consec_old"] = 0
                    _AUDIT["old_ids"] = set()
                    _AUDIT["page"] = _auditor_last_page(_AUDIT["total"] or 1)
            with _AUDIT_LOCK:
                was_complete = _AUDIT["window_complete"]
            _auditor_cycle()
            with _AUDIT_LOCK:
                now_complete = _AUDIT["window_complete"]
            if now_complete and not was_complete:
                with _AUDIT_LOCK:
                    _AUDIT["refetch"] = False
                    _AUDIT["last_full_at"] = time.time()
                _persist_snapshot()
        except Exception:
            with _AUDIT_LOCK:
                _AUDIT["errors"] += 1
        time.sleep(_AUDIT_SLEEP)


def ensure_auditor():
    """Start the background auditor thread if creds exist and it isn't running.
    Idempotent and safe to call on every request."""
    global _AUDIT_THREAD
    if not have_creds():
        return
    with _AUDIT_LOCK:
        if _AUDIT_THREAD is not None and _AUDIT_THREAD.is_alive():
            return
        _AUDIT_THREAD = threading.Thread(target=_auditor_loop, daemon=True, name="mw-auditor")
        _AUDIT_THREAD.start()


def audited_files():
    """All revenue-audited files gathered so far — in-window AND historical."""
    with _AUDIT_LOCK:
        return list(_AUDIT["files"].values())


_FORCE_REFRESH_RUNNING = False


def force_refresh():
    """Immediately re-audit the in-window files in a background thread (regardless
    of backfill state), so mapping changes (e.g. coordinator name/email) show up
    without waiting for the scheduled 6h refresh. Re-persists the snapshot after.
    Returns True if a refresh was started (False if one is already running)."""
    global _FORCE_REFRESH_RUNNING
    with _AUDIT_LOCK:
        if _FORCE_REFRESH_RUNNING:
            return False
        _FORCE_REFRESH_RUNNING = True
        _AUDIT["last_full_at"] = 0

    def _run():
        global _FORCE_REFRESH_RUNNING
        try:
            _refresh_recent()
            _persist_snapshot()
        except Exception:
            pass
        finally:
            _FORCE_REFRESH_RUNNING = False

    threading.Thread(target=_run, daemon=True, name="force-refresh").start()
    return True


def audited_in_window():
    """Just the in-window (last-12-months) audited files — what the recoverable
    dashboard metrics run over. Historical files are excluded here."""
    with _AUDIT_LOCK:
        return [m for m in _AUDIT["files"].values() if not _is_out_of_window(m)]


def audit_progress():
    with _AUDIT_LOCK:
        files = _AUDIT["files"]
        audited_total = len(files)
        in_window = sum(1 for m in files.values() if not _is_out_of_window(m))
        # In full-coverage mode the recoverable window is "complete" once
        # window_ready; window_complete means the whole book has been audited.
        window_done = _AUDIT["window_ready"] or _AUDIT["window_complete"]
        return {
            "audited": in_window,          # in-window files (drives the window view)
            "audited_total": audited_total,  # ALL audited files (coverage numerator)
            "in_window": in_window,
            "total": _AUDIT["total"],
            "cycles": _AUDIT["cycles"],
            "errors": _AUDIT["errors"],
            "wrapped": _AUDIT["wrapped"],
            "window_complete": window_done,          # recoverable window ready?
            "full_coverage": _FULL_COVERAGE,
            "backfill_complete": _AUDIT["window_complete"],  # 100% of the book done?
            "window_days": _WINDOW_DAYS,
            "excluded_old": len(_AUDIT["old_ids"]),
            "running": bool(_AUDIT_THREAD is not None and _AUDIT_THREAD.is_alive()),
            "last_full_at": _AUDIT["last_full_at"],
            "saved_at": _AUDIT["saved_at"],
            "refreshing": _AUDIT["refetch"],
            "refresh_seconds": _REFRESH_SECONDS,
            "persisted": bool(_AUDIT["saved_at"]),
        }


def raw_sample(job_id: str | None = None) -> dict:
    """Debug helper: return raw MoveWare structures to confirm the mapping."""
    out = {"base_url": BASE_URL, "have_creds": have_creds()}
    try:
        jobs = _get("/jobs")
        out["jobs_top_keys"] = list(jobs.keys()) if isinstance(jobs, dict) else "list"
        arr = _first(jobs, "jobs", default=[]) or []
        out["job_count"] = len(arr)
        if arr:
            out["first_job"] = arr[0]
            jid = job_id or str(_first(arr[0], "id", "jobId", "jobNumber", "jobFile"))
            out["sample_job_id"] = jid
            # V2 paths (there is no /quotes or /account in V2).
            for key, path in (("job", f"/jobs/{jid}"),
                              ("roles", f"/jobs/{jid}/roles"),
                              ("options", f"/jobs/{jid}/options"),
                              ("invoices", f"/jobs/{jid}/invoices")):
                try:
                    out[key] = _get(path)
                except Exception as e:
                    out[key + "_error"] = str(e)
    except Exception as e:
        out["error"] = str(e)
    return out
