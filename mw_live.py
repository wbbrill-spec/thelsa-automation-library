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
 
_CACHE = {"at": 0.0, "data": None}
_CACHE_TTL = 600  # seconds
_MAX_JOBS = 6     # cap the deep-load sample — each job makes 3 sub-calls
                  # (quotes/invoices/account), so keep this low to stay well
                  # inside the gunicorn worker timeout; result is cached (TTL)
                  # and further bounded by _LOAD_BUDGET.
 
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
_PAGE_SIZE = 500          # rows per page — 500-row pages return in ~1-2s, so the
                          # whole ~7k-file book is ~15 pages; the hard per-request
                          # deadline (_fetch) makes a slow page safe.
_MAX_COUNT_PAGES = 60     # 60 × 500 = 30,000-file ceiling (backstop, not a target)
_COUNT_BUDGET = 50.0      # total seconds spent counting (well under gunicorn 180s)
_COUNT_TIMEOUT = 10       # hard per-request cap; a stuck page aborts here → floor.


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
    """Return the TRUE file counts from the light /jobs feed, or None.

    Shape: {total, active, pages, exhausted, by_status}. This pages the whole
    feed WITHOUT per-file sub-calls (fast + safe), so it gives the real number of
    files instead of the deep-loaded sample cap. Cached separately (TTL) so page
    loads don't re-page the feed every time.
    """
    if not have_creds():
        return None
    now = time.time()
    if _COUNT_CACHE["data"] is not None and now - _COUNT_CACHE["at"] < _COUNT_TTL:
        return _COUNT_CACHE["data"]
    try:
        jobs, pages, exhausted = _paginate_all_jobs()
    except Exception:
        return None
    if not jobs:
        return None
    by_status: dict = {}
    active = 0
    ids = []
    for j in jobs:
        st = _job_status(j) or "(blank)"
        by_status[st] = by_status.get(st, 0) + 1
        if _job_active(j):
            active += 1
        jid = _first(j, "id", "jobId", "jobNumber", "jobFile")
        if jid not in (None, ""):
            ids.append(str(jid))
    data = {
        "total": len(jobs),
        "active": active,
        "pages": pages,
        "exhausted": exhausted,
        "by_status": by_status,
        "id_min": min(ids) if ids else None,
        "id_max": max(ids) if ids else None,
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
 
    # Invoices → invoiced amount.
    invoiced_amt = 0.0
    invoiced = False
    try:
        inv = _get(f"/jobs/{job_id}/invoices")
        for it in (_first(inv, "invoices", default=[]) or []):
            invoiced_amt += _num(_first(it, "value", "total", "amount"))
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
 
    return {
        "job": job_id,
        "client": client or "",
        "mode": mode,
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
