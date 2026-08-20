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
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
 
_CACHE = {"at": 0.0, "data": None}
_CACHE_TTL = 600  # seconds
_MAX_JOBS = 3     # cap the deep-load sample — each job makes sub-calls
                  # (quotes/invoices) at ~2-3s each, so keep this low to stay well
                  # inside the proxy/worker timeout; result is cached (TTL) and
                  # further bounded by _LOAD_BUDGET.
 
# Env-driven base URL; defaults to PRODUCTION. Override with MOVEWARE_URL to
# point at UAT (https://rest.moveconnect.com/movewareUAT/v1) for testing.
BASE_URL = os.environ.get(
    "MOVEWARE_URL", "https://rest.moveconnect.com/Moveware/v1"
).rstrip("/")
 
 
def have_creds() -> bool:
    return all(os.environ.get(k) for k in ("MW_USERNAME", "MW_PASSWORD", "MW_COMPANY_ID"))
 
 
def _headers() -> dict:
    return {
        "mw-username": os.environ.get("MW_USERNAME", ""),
        "mw-password": os.environ.get("MW_PASSWORD", ""),
        "mw-company-id": os.environ.get("MW_COMPANY_ID", ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
 
 
# Per-request timeout for every Moveware call. Kept SHORT on purpose: the /audit
# page makes many sequential calls (deep-load = several jobs × sub-calls each,
# plus the feed count), and if any one call is allowed to hang the cumulative time
# blows past gunicorn's worker timeout and the whole page 500s.
_REQ_TIMEOUT = 10


def _raw_json(url: str, timeout: int):
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
        counts = live_file_counts()
        total = int((counts or {}).get("total") or 0)
    except Exception:
        total = 0

    if total and limit_jobs:
        last_page = max(1, (total + limit_jobs - 1) // limit_jobs)
        # Grab the last page (newest) and, if it's short, the page before it so we
        # always return a full `limit_jobs` of recent files.
        for pg in (last_page, last_page - 1):
            if pg < 1:
                continue
            try:
                jobs = _page_jobs(_get_timed(f"/jobs?limit={limit_jobs}&offset={pg}", _REQ_TIMEOUT))
            except Exception:
                jobs = []
            if len(jobs) >= limit_jobs:
                return jobs[-limit_jobs:]
            if jobs:
                return jobs

    # Fallback: first page tail (least-bad if the count is unavailable).
    try:
        jobs = _page_jobs(_get_timed("/jobs?limit=100", _REQ_TIMEOUT))
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
_PAGE_SIZE = 500          # rows per status-scan page.
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
    """APPROXIMATE-but-tight file count, cheaply. The feed has no count endpoint,
    but `offset` is a 1-indexed page number, so with limit=1 page N exists iff
    there are ≥ N jobs. Binary-search the largest existing page over [1,_TOTAL_MAX]
    with a hard probe cap (each probe is one ~2s request, so we cap to stay under
    the proxy timeout). ~12 probes → within ~8 of the true count. Returns 0 if
    unavailable.
    """
    def exists(off: int) -> bool:
        try:
            return len(_page_jobs(_get_timed(f"/jobs?limit=1&offset={off}", _COUNT_TIMEOUT))) > 0
        except Exception:
            return False

    if not exists(1):
        return 0
    lo, hi = 1, _TOTAL_MAX
    probes = 0
    while lo + 1 < hi and probes < _TOTAL_MAX_PROBES:
        mid = (lo + hi) // 2
        if exists(mid):
            lo = mid
        else:
            hi = mid
        probes += 1
    return lo


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
            payload = _get_timed(f"/jobs?limit={_PAGE_SIZE}&offset={page_idx}", _COUNT_TIMEOUT)
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
 
    sell = est_cost = 0.0
    # Internal-recalculation inputs: the selected option's header value (sell)
    # should equal the sum of its charge lines. We capture the line total and
    # the line count so audit_web.check_calculations() can verify "revenue adds
    # up" and only assert it when line items are actually present.
    charge_lines_total = 0.0
    n_charge_lines = 0
    declared = ins = weight = None
    # Line-level reconciliation inputs: every quoted charge line across ALL quote
    # options (multi-component quotes bill move + insurance + storage separately),
    # plus the size the quote was based on. audit_web matches invoice lines to
    # these so a legitimate scope change (extra service, volume/weight increase)
    # is surfaced as context — not flagged as an error.
    q_lines = []          # list of {"desc", "value"} for every quote charge line
    est_vol = act_vol = est_wt = act_wt = None
    rich = {}
    try:
        qd = _get(f"/jobs/{job_id}/quotes")
        quotes = _first(qd, "quotes", default=[]) or []
        if quotes:
            q0 = quotes[0]
            rich = _first(q0, "job", default={}) or {}
            roles = _first(q0, "roles", default={}) or {}
            if not client:
                corp = _first(roles, "corporate", default={}) or {}
                cust = _first(roles, "customer", default={}) or {}
                client = _first(corp, "name") or _first(cust, "name") or ""
            # Selected quote option carries the sell price + measurements.
            option = None
            for q in quotes:
                for opt in (_first(q, "options", default=[]) or []):
                    if _first(opt, "selected") in (True, "true", 1):
                        option = opt
                        break
                if option:
                    break
            if option is None:
                opts = _first(q0, "options", default=[]) or []
                option = opts[0] if opts else None
            if option:
                # Sell = option's tax-inclusive value (charges[] is the line
                # breakdown; sum it for cost lines when present).
                sell = _num(_first(option, "valueInc", "value", "valueEx"))
                weight = _weight_from_measurements(
                    _first(option, "measurements") or _first(q0, "measurements")
                )
                for ch in (_first(option, "charges", default=[]) or []):
                    cval = _num(_first(ch, "value", "valueInc"))
                    charge_lines_total += cval
                    n_charge_lines += 1
                    if _classify_charge(ch) == "cost":
                        est_cost += cval
            # Collect EVERY quote charge line across ALL options (and the quote's
            # `services`) for line-level reconciliation against the invoices.
            for q in quotes:
                for opt in (_first(q, "options", default=[]) or []):
                    for ch in (_first(opt, "charges", default=[]) or []):
                        cval = _num(_first(ch, "valueInc", "value", "valueEx"))
                        if cval > 0:
                            q_lines.append({"desc": _code_text(_first(ch, "description", default="")), "value": round(cval, 2)})
                svcs = _first(q, "services", default={}) or {}
                if isinstance(svcs, dict):
                    for sv in svcs.values():
                        cval = _num(_first(sv, "valueInc", "value", "valueEx")) if isinstance(sv, dict) else 0
                        if cval > 0:
                            q_lines.append({"desc": _code_text(_first(sv, "description", default="")), "value": round(cval, 2)})
                # Size the quote was based on (estimated vs actual measurements).
                meas = _first(q0, "measurements") or (_first(option, "measurements") if option else None) or []
                for mrow in (meas if isinstance(meas, list) else []):
                    mt = (_code_text(_first(mrow, "type", default="")) or "").lower()
                    uom = (str(_first(mrow, "uom", default="")).lower())
                    v = _num(_first(mrow, "value"))
                    if mt == "volumenett" and uom in ("m", "m3", ""):
                        est_vol = v or est_vol
                    elif mt == "actualweight" and uom == "kg":
                        act_wt = v or act_wt
                    elif mt == "weightnett" and uom == "kg":
                        est_wt = v or est_wt
                break  # measurements/services taken from the first quote only
    except Exception:
        pass
 
    src = rich or job
 
    # Dates live under job.dates.{uplift,delivery}.date (fallback to list item).
    dates = _first(src, "dates", default={}) or {}
    pack = _date(_first(_first(dates, "uplift", default={}) or {}, "date")) or \
        _date(_first(job, "uplift", "pack", "estimatedMove"))
    delivery = _date(_first(_first(dates, "delivery", default={}) or {}, "date")) or \
        _date(_first(job, "delivery", "deliveryStart", "estimatedDelivery"))
 
    # Insurance / declared value from job.services.insurance.
    services = _first(src, "services", default={}) or {}
    ins_obj = _first(services, "insurance", default={}) or {}
    declared = _num(_first(ins_obj, "value")) or None
    ins = _num(_first(ins_obj, "premium")) or None
    if not coordinator:
        mgr = _first(src, "moveManager", default="")
        coordinator = _code_text(mgr)
 
    # Invoices → invoiced amount + every invoiced charge line (for reconciliation).
    invoiced_amt = 0.0
    invoiced = False
    i_lines = []
    try:
        inv = _get(f"/jobs/{job_id}/invoices")
        for it in (_first(inv, "invoices", default=[]) or []):
            iv = _num(_first(it, "value", "total", "amount"))
            invoiced_amt += iv
            chs = _first(it, "charges", default=[]) or []
            if chs:
                for ch in chs:
                    cval = _num(_first(ch, "valueInc", "value", "valueEx"))
                    if cval > 0:
                        i_lines.append({"desc": _code_text(_first(ch, "description", default="")), "value": round(cval, 2)})
            elif iv > 0:
                i_lines.append({"desc": _code_text(_first(it, "description", default="")), "value": round(iv, 2)})
        invoiced = invoiced_amt > 0
    except Exception:
        pass
 
    # Actual (supplier/creditor) cost is NOT available in Moveware RestV1: the
    # /jobs/{id}/account endpoint returns DEBTOR (receivable) lines — what the
    # CLIENT owes — which equal revenue, not what Thelsa pays its agents/carriers.
    # Summing it produced cost == revenue → profit 0 (the bogus figures). So we do
    # NOT call /account and we leave actual cost UNKNOWN (0). Profit/margin are
    # suppressed downstream (cost_available=False) until a real cost source exists
    # (RestV2 / a creditor endpoint). This also drops a sub-call per job.
    actual_cost = 0.0
 
    mode = _mode(src if src else job)
 
    # Job status code (W=Won, L=Lead, P=Pending, C=Cancelled). Prefer the rich
    # jobStatus.code from the quotes response; fall back to the light list item.
    _jstat = _first(_first(src, "jobStatus", default={}) or {}, "code") or _first(job, "status") or ""
    status = str(_jstat).strip().upper()

    return {
        "job": job_id,
        "client": client or "",
        "mode": mode,
        "status": status,
        "est": round(est_cost, 2),
        "act": round(actual_cost, 2),
        "sell": round(sell, 2),
        "inv_amt": round(invoiced_amt, 2),
        "invoiced": invoiced,
        "declared": declared,
        "ins": ins,
        "coordinator": coordinator,
        "agent": None,
        "pack": pack,
        "delivery": delivery,
        # Line-level reconciliation: every quoted charge line vs every invoiced
        # charge line, plus the quote's estimated size vs the actual. audit_web
        # matches these so scope changes are explained, not flagged as errors.
        "q_lines": q_lines,
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
    "window_complete": False,  # True once the last 12 months are fully covered
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
_AUDIT_PAGE = 40          # ids fetched + classified per cycle
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


def _window_cutoff() -> "dt.date":
    return dt.date.today() - dt.timedelta(days=_WINDOW_DAYS)


def _file_anchor_date(m: dict):
    """Date a file is judged 'recent' by: delivery, else pack/uplift."""
    return m.get("delivery") or m.get("pack")


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
    with _AUDIT_LOCK:
        _AUDIT["files"] = files
        _AUDIT["old_ids"] = set(snap.get("old_ids") or [])
        _AUDIT["total"] = snap.get("total")
        _AUDIT["window_complete"] = bool(snap.get("window_complete"))
        _AUDIT["last_full_at"] = snap.get("last_full_at")
        _AUDIT["saved_at"] = snap.get("saved_at")
        _AUDIT["page"] = _auditor_last_page(_AUDIT["total"] or 1)
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
        jobs = _page_jobs(_get_timed(f"/jobs?limit={_AUDIT_PAGE}&offset={page}", _REQ_TIMEOUT))
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
        if _is_out_of_window(m):
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
                _AUDIT["consec_old"] = 0  # window still open; reset the run
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


def _auditor_loop():
    with _AUDIT_LOCK:
        _AUDIT["started_at"] = time.time()
    # Resume from the last persisted snapshot: instant comprehensive view after a
    # restart/deploy instead of re-crawling the whole window from zero.
    _load_snapshot()
    while True:
        try:
            with _AUDIT_LOCK:
                complete = _AUDIT["window_complete"]
                last_full = _AUDIT["last_full_at"] or 0
            # When the snapshot is complete and the next refresh isn't due, idle
            # cheaply (no API calls) instead of re-scanning every couple seconds.
            if complete and (time.time() - last_full) < _REFRESH_SECONDS:
                time.sleep(_AUDIT_IDLE_SLEEP)
                continue
            # Refresh is due: re-scan the whole window to pick up new bookings /
            # invoices / deliveries. The existing snapshot stays visible and is
            # updated file-by-file as the re-scan progresses.
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
                # A full scan just finished — stamp it and persist the snapshot.
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
    """Snapshot list of the revenue-audited files gathered so far (may be empty
    right after boot while the auditor warms up)."""
    with _AUDIT_LOCK:
        return list(_AUDIT["files"].values())


def audit_progress():
    with _AUDIT_LOCK:
        return {
            "audited": len(_AUDIT["files"]),
            "total": _AUDIT["total"],
            "cycles": _AUDIT["cycles"],
            "errors": _AUDIT["errors"],
            "wrapped": _AUDIT["wrapped"],
            "window_complete": _AUDIT["window_complete"],
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
            try:
                out["quotes"] = _get(f"/jobs/{jid}/quotes")
            except Exception as e:
                out["quotes_error"] = str(e)
            try:
                out["invoices"] = _get(f"/jobs/{jid}/invoices")
            except Exception as e:
                out["invoices_error"] = str(e)
            try:
                out["account"] = _get(f"/jobs/{jid}/account")
            except Exception as e:
                out["account_error"] = str(e)
    except Exception as e:
        out["error"] = str(e)
    return out
