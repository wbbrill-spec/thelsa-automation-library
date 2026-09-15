"""
tms.py — TMS shipments from Moveware, normalized into the unified Shipment model.

Read-only. Honors the Moveware performance guardrail: one narrow, filtered,
paginated list walk (recently *created* jobs only, small pages) plus one detail
call per cross-border job, run in the background a few times an hour and cached.
Nothing here ever writes to Moveware.

What we know about the instance (moveware-api-integration-guide.md, mw_live.py):
  • V2 (from 2026-09-14): header auth is mw-username / mw-password only and the
    company id moved into the URL path (…/64000/api = LIVE, …/08800/api = TEST).
    The live host is literally named rest.moveware-test.app — vendor naming, not
    the test DB. ~2 s per call.
  • `offset` on /jobs is a 1-indexed PAGE number, feed is oldest-first
  • filtered lists silently cap at ~50–150 rows → walk small date slices;
    `updatedAfter` returns 400 on Thelsa's instance, `createdAfter/Before` work
    (measured 2026-09-08 on the test DB) — so the window is by *created* date
  • list rows carry only 2-letter country codes for origin/destination — that is
    exactly what identifies a cross-border job (US↔MX) before paying for detail
  • detail carries measurements[] (volume/weight), extras[] (dtpacking,
    dtdelivery, dtopscomplete…), moveManager, billing.name, upliftStart
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

from .models import (
    CUFT_PER_M3, Hub, Shipment, Source, Stage, hub_for_destination, norm_text, parse_date,
    to_number,
)

log = logging.getLogger(__name__)

# Moveware V2 (2026-09-14). The company id lives in the PATH, not a header.
# The live endpoint is served from a host named `rest.moveware-test.app` — that
# is the vendor's naming, not a mistake, and it is NOT the test database. The
# company segment is what picks the database: 64000 = LIVE, 08800 = TEST.
_MW_HOST = os.environ.get("MOVEWARE_HOST", "https://rest.moveware-test.app").rstrip("/")
BASE_URLS = {
    "prod": f"{_MW_HOST}/{os.environ.get('MW_COMPANY_SEGMENT', '64000').strip().strip('/')}/api",
    "test": f"{_MW_HOST}/08800/api",
    # v1, kept only as a named rollback target (TMS_MW_ENV=v1).
    "v1": "https://rest.moveconnect.com/Moveware/v1",
}
MX = "MX"
# Country codes on the US side of the border that count as "cross-border with Mexico".
US_SIDE = set((os.environ.get("TMS_US_SIDE", "US,CA") or "US").replace(" ", "").split(","))
# Job statuses (GET /codes?type=Status, measured 2026-09-08): I = Inspection
# (survey), P = Pending (quote), W = Won (booked), L = Lost, C = Cancelled.
# Only Won jobs are shipments; P/I are sales pipeline, not freight.
ACTIVE_STATUSES = set((os.environ.get("TMS_ACTIVE_STATUSES", "W") or "W").replace(" ", "").upper().split(","))
DEAD_STATUSES = {"L", "C", "X", "D", "Z"}          # lost / cancelled
CLOSED_AFTER_DAYS = int(os.environ.get("TMS_CLOSED_AFTER_DAYS", "14") or 14)


class MovewareError(RuntimeError):
    pass


# ── client ───────────────────────────────────────────────────────────────────
class MovewareClient:
    """Thin GET client over mw_live's hardened fetch (hard deadline per call)."""

    def __init__(self, env: str | None = None, base_url: str | None = None):
        import mw_live  # noqa: WPS433 — shares creds/headers/timeouts with the audit tools
        self._mw = mw_live
        env = (env or os.environ.get("TMS_MW_ENV", "prod")).lower()
        self.env = env
        self.base_url = (base_url or os.environ.get("TMS_MW_URL") or BASE_URLS.get(env, BASE_URLS["prod"])).rstrip("/")
        self.requests_made = 0
        self.errors: list[str] = []

    @staticmethod
    def have_creds() -> bool:
        # V2: username + password only — the company id is in the base URL path.
        return all(os.environ.get(k) for k in ("MW_USERNAME", "MW_PASSWORD"))

    def get(self, path: str, timeout: int = 12):
        self.requests_made += 1
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            return self._mw._fetch(url, timeout)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            self.errors.append(f"{path}: {msg}")
            raise MovewareError(msg) from exc

    def jobs(self, page: int = 1, limit: int = 50, **filters) -> list[dict]:
        q = "&".join(f"{k}={v}" for k, v in filters.items() if v not in (None, ""))
        # Both spellings of "1-indexed page number": V2 reads `page` and ignores
        # `offset`; v1 read `offset`. Sending both keeps the TMS_MW_ENV=v1
        # rollback working. Dropping `page` here is what made the V2 walk re-read
        # page 1 forever even after the walk itself had been fixed.
        body = self.get(f"/jobs?limit={limit}&page={page}&offset={page}"
                        + (f"&{q}" if q else ""))
        if isinstance(body, dict):
            rows = body.get("jobs") or body.get("data") or []
        else:
            rows = body or []
        return [r for r in rows if isinstance(r, dict)]

    def job(self, job_id: str) -> dict:
        body = self.get(f"/jobs/{job_id}")
        return body.get("data", body) if isinstance(body, dict) else {}

    def codes(self, type_: str | None = None):
        return self.get("/codes" + (f"?type={type_}" if type_ else ""))


# ── helpers ──────────────────────────────────────────────────────────────────
def _s(v) -> str:
    if isinstance(v, dict):
        return str(v.get("text") or v.get("name") or v.get("code") or "").strip()
    return str(v or "").strip()


# V2 list rows spell the country out in full ("United States", "Mexico") where
# v1 sent a 2-letter code. Truncating a name to its first two letters turned
# "United States" into "UN" and "Mexico" into "ME", so NOTHING matched US_SIDE
# or MX and every job looked domestic — the dashboard reported 0 cross-border
# jobs on a feed that was full of them. Names are mapped explicitly; anything
# unrecognized stays whatever 2-letter code it already was.
_COUNTRY_NAMES = {
    "united states": "US", "united states of america": "US", "usa": "US",
    "us": "US", "estados unidos": "US", "eeuu": "US",
    "mexico": "MX", "estados unidos mexicanos": "MX",
    "canada": "CA", "united kingdom": "GB", "great britain": "GB",
    "germany": "DE", "france": "FR", "spain": "ES", "brazil": "BR",
    "brasil": "BR", "chile": "CL", "colombia": "CO", "argentina": "AR",
    "peru": "PE", "japan": "JP", "china": "CN", "india": "IN",
    "australia": "AU", "netherlands": "NL", "belgium": "BE", "italy": "IT",
    "switzerland": "CH", "kuwait": "KW", "qatar": "QA",
    "united arab emirates": "AE", "saudi arabia": "SA", "south korea": "KR",
    "singapore": "SG", "guatemala": "GT", "costa rica": "CR", "panama": "PA",
}


def _country(v) -> str:
    """A 2-letter ISO code from a code, a full country name, or an address dict."""
    if isinstance(v, dict):
        # V2 address objects carry iso2country ("US", "MX") alongside the
        # spelled-out country. Prefer the code — it needs no name table.
        for k in ("iso2country", "countryISO2", "countryCode", "code", "country"):
            if v.get(k):
                v = v[k]
                break
        else:
            return ""
    s = str(v or "").strip()
    if not s:
        return ""
    named = _COUNTRY_NAMES.get(norm_text(s))
    if named:
        return named
    s = s.upper()
    return s if len(s) == 2 else s[:2]


def _created(row: dict):
    """The job's created date, however this API version nests it."""
    ad = row.get("activityDates")
    if isinstance(ad, dict):
        c = ad.get("created")
        if isinstance(c, dict):
            return parse_date(c.get("date"))
        if c:
            return parse_date(c)
    return parse_date(row.get("created") or row.get("createdDate"))


def _endpoints(row: dict) -> tuple:
    """(origin, destination) however this API version nests them: V2 puts them
    under addresses{}, v1 had them at the top level."""
    addr = row.get("addresses")
    if isinstance(addr, dict) and (addr.get("origin") or addr.get("destination")):
        return addr.get("origin"), addr.get("destination")
    return row.get("origin"), row.get("destination")


def direction(row: dict) -> str | None:
    """'import' (US side → MX), 'export' (MX → US side) or None (not cross-border)."""
    _o, _d = _endpoints(row)
    o, d = _country(_o), _country(_d)
    if d == MX and o in US_SIDE:
        return "import"
    if o == MX and d in US_SIDE:
        return "export"
    return None


def _mw_date(v):
    """Moveware emits '2026-09-05T00:00:00+10:00' style stamps — take the calendar date literally."""
    if isinstance(v, dict):
        v = v.get("dateTime") or v.get("date") or v.get("value")
    if not v:
        return None
    s = str(v)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        try:
            return dt.date.fromisoformat(m.group(1))
        except ValueError:
            return None
    return parse_date(s)


def _extras(detail: dict) -> dict:
    out = {}
    for e in detail.get("extras") or []:
        if isinstance(e, dict) and e.get("field"):
            out[str(e["field"]).strip().lower()] = e.get("value")
    return out


def _v2_measures(detail: dict) -> dict:
    """V2 carries the size on the job as `measures[]`, already converted:

        measures[0].volume.{net,gross}.{m3,f3}
        measures[0].weight.{net,gross}.{kg,lb}

    (v1 sent a flat `measurements[]` of {type, uom, value} rows — see below.)
    Nett is preferred over gross, and the metric figure is taken directly rather
    than converting the imperial one, which Moveware derives and can leave stale.
    """
    vol = wt = None
    for m in detail.get("measures") or []:
        if not isinstance(m, dict):
            continue
        v, w = m.get("volume"), m.get("weight")
        if isinstance(v, dict) and vol is None:
            for side in ("net", "gross"):
                n = to_number((v.get(side) or {}).get("m3")) if isinstance(v.get(side), dict) else None
                if not n:
                    f3 = to_number((v.get(side) or {}).get("f3")) if isinstance(v.get(side), dict) else None
                    n = round(f3 / CUFT_PER_M3, 2) if f3 else None
                if n:
                    vol = round(n, 2)
                    break
        if isinstance(w, dict) and wt is None:
            for side in ("net", "gross"):
                n = to_number((w.get(side) or {}).get("kg")) if isinstance(w.get(side), dict) else None
                if not n:
                    lb = to_number((w.get(side) or {}).get("lb")) if isinstance(w.get(side), dict) else None
                    n = round(lb * 0.4536, 1) if lb else None
                if n:
                    wt = round(n, 1)
                    break
    return {"volume_m3": vol, "weight_kg": wt, "items": None}


def _measurements(detail: dict) -> dict:
    """{'volume_m3': float|None, 'weight_kg': float|None, 'items': int|None}

    Reads V2's `measures[]` first, then falls back to v1's `measurements[]`.
    """
    v2 = _v2_measures(detail)
    if v2["volume_m3"] or v2["weight_kg"]:
        return v2
    vol_m3 = weight = items = None
    weight_src = -1
    vol_src = -1
    for m in detail.get("measurements") or []:
        if not isinstance(m, dict):
            continue
        t = str(m.get("type") or "").lower()
        uom = str(m.get("uom") or "").lower()
        val = to_number(m.get("value"))
        if val in (None, 0):
            continue
        if t in ("volumenett", "volumegross"):
            # Preference: nett > gross; a metric row beats a converted ft row.
            rank = (2 if t == "volumenett" else 1) * 2 + (1 if uom.startswith("m") else 0)
            if vol_m3 is None or rank > vol_src:
                vol_m3 = round(val / CUFT_PER_M3, 2) if uom.startswith("f") else round(val, 2)
                vol_src = rank
        elif t in ("actualweight", "weightnett", "weightgross"):
            kg = round(val * 0.4536, 1) if uom.startswith("lb") else round(val, 1)
            # Preference: weightNett kg > actualWeight kg > gross; a kg row beats a
            # lb row of the same type (Moveware derives lb and it can go stale).
            rank = {"weightnett": 3, "actualweight": 2, "weightgross": 1}[t] * 2 + (0 if uom.startswith("lb") else 1)
            if weight is None or rank > weight_src:
                weight, weight_src = kg, rank
        elif t == "items":
            items = int(val)
    return {"volume_m3": vol_m3, "weight_kg": weight, "items": items}


def _named(v) -> str:
    """A name from a value that may be an object OR a bare string.

    v1 sent ports and agents as {"name": …}; V2 sends the code as a plain
    string ("USBOS", "MXVER"). Calling .get() on that string is what took the
    whole TMS pull down with
    `AttributeError: 'str' object has no attribute 'get'` — and it only
    surfaced once these fields started being populated at all.
    """
    if isinstance(v, dict):
        return _s(v.get("name") or v.get("code") or v.get("text"))
    return _s(v)


def _activity_dates(obj: dict):
    """A lookup into V2's activityDates block: ad("pack") -> date | None.

    Each entry is {"date": "YYYY-MM-DD", ...} and most are null on any given
    job, so callers read several names in preference order.
    """
    ad = obj.get("activityDates") if isinstance(obj, dict) else None
    if not isinstance(ad, dict):
        return lambda _name: None

    def get(name: str):
        v = ad.get(name)
        if isinstance(v, dict):
            return _mw_date(v.get("date"))
        return _mw_date(v)
    return get


def _place(v) -> str:
    """Best human-readable place from a list value or a detail address object."""
    if isinstance(v, dict):
        a = v.get("address") if isinstance(v.get("address"), dict) else v
        parts = [a.get(k) for k in ("city", "suburb", "town", "state") if a.get(k)]
        if not parts:
            # No city/state: never surface a street address — country is enough.
            parts = [a.get("country") or a.get("countryISO2")]
        return ", ".join(str(p).strip() for p in parts if p) or _s(v)
    return _s(v)


def _loc(detail: dict, which: str, row: dict | None = None) -> dict:
    """The origin/destination object. v1 nested these under `locations`; V2 puts
    them under `addresses` (on the list row as well as the detail).

    Without the `addresses` branch the destination text came out empty, which
    cost more than a blank column: the hub lookup had nothing to match, so every
    import landed in "Unassigned hub", and the suggested-load lanes read
    "Export → ?".
    """
    for src in (detail, row):
        if not isinstance(src, dict):
            continue
        for key in ("locations", "addresses"):
            block = src.get(key)
            if isinstance(block, dict) and isinstance(block.get(which), dict):
                return block[which]
    return {}


def stage_for(row: dict, detail: dict | None, today: dt.date) -> tuple[Stage, list[str], dict]:
    """Coarse stage from Moveware dates + status. Moveware has no customs-step
    granularity, so a job between uplift and delivery sits in 'in transit to
    border' until the team tells us which extras carry the border/hub dates."""
    ex = _extras(detail or {})
    status = _s(row.get("status") or (detail or {}).get("jobStatus")).upper()[:1]
    d = detail or {}
    # V2 nests every milestone under activityDates.<name>.date. The full
    # vocabulary on a live job: analysis, arrival, booked, cartonDel, created,
    # delivery, departure, estimatedDelivery, estimatedMove, followup, pack,
    # survey, unload, unpack, uplift.
    #
    # `uplift` is usually EMPTY and `pack` carries the move-out date — the audit
    # tool (mw_live.py) has always read it that way. Reading only upliftStart,
    # as the v1 mapper did, is why every V2 job looked like it had no dates.
    ad = _activity_dates(d) or _activity_dates(row)
    uplift = (ad("uplift") or ad("pack") or ad("departure")
              or _mw_date(d.get("upliftStart")) or _mw_date(d.get("pack"))
              or _mw_date(row.get("uplift")) or _mw_date(ex.get("dtpacking"))
              or ad("estimatedMove") or _mw_date(d.get("estimatedMove")))
    delivery = (ad("delivery") or ad("cartonDel") or ad("unpack") or ad("unload")
                or _mw_date(d.get("deliveryStart")) or _mw_date(ex.get("dtdelivery"))
                or _mw_date(row.get("delivery"))
                or ad("estimatedDelivery") or _mw_date(d.get("estimatedDelivery")))
    ops_done = _mw_date(ex.get("dtopscomplete")) or ad("unpack")
    dates = {"uplift": uplift, "delivery": delivery, "ops_complete": ops_done}
    flags: list[str] = []
    if status in DEAD_STATUSES or str(d.get("isClosed") or "").upper() == "Y":
        return Stage.CLOSED, flags, dates
    if ops_done and ops_done <= today:
        return (Stage.CLOSED if (today - ops_done).days > CLOSED_AFTER_DAYS else Stage.DELIVERED), flags, dates
    if delivery and delivery <= today:
        return (Stage.CLOSED if (today - delivery).days > CLOSED_AFTER_DAYS else Stage.DELIVERED), flags, dates
    if uplift and uplift <= today:
        if delivery and (delivery - today).days <= 2:
            return Stage.OUT_FOR_DELIVERY, flags, dates
        return Stage.TO_BORDER, flags, dates
    if status == "E":
        flags.append("enquiry")
        return Stage.BOOKED, flags, dates
    return Stage.BOOKED, flags, dates


def build_shipment(row: dict, detail: dict | None, *, today: dt.date | None = None, env: str = "prod") -> Shipment:
    today = today or dt.date.today()
    d = detail or {}
    ex = _extras(d)
    meas = _measurements(d)
    dirn = direction(row) or "import"
    stage, flags, dates = stage_for(row, d, today)
    list_id = str(row.get("id") or "")
    display = str(d.get("id") or list_id)
    billing = d.get("billing") if isinstance(d.get("billing"), dict) else {}
    mm = d.get("moveManager") if isinstance(d.get("moveManager"), dict) else {}
    # The list row's `name` is the transferee; `billing.name` is who pays —
    # for agent-booked jobs that is the agent (extras.debtortype == "Agent").
    customer = _s(row.get("name")) or _s(billing.get("name"))
    payer = _s(billing.get("name"))
    agent = payer if str(ex.get("debtortype") or "").lower() == "agent" and payer else (
        _s(d.get("branchName")) or payer or "TMS")
    oloc, dloc = _loc(d, "origin", row), _loc(d, "destination", row)
    origin = _place(oloc) if oloc else (_place(d.get("origin")) or _country(row.get("origin")))
    destination = _place(dloc) if dloc else (_place(d.get("destination")) or _country(row.get("destination")))
    hub = hub_for_destination(destination) if dirn == "import" else Hub.UNKNOWN
    # V2 names this dateModified; v1 sent lastUpdated. Read both.
    updated = (_mw_date(row.get("dateModified")) or _mw_date(row.get("lastUpdated"))
               or _mw_date(d.get("dateModified")) or _mw_date(d.get("lastUpdated")))
    last_progress = max([x for x in (dates["ops_complete"], dates["delivery"] if dates["delivery"] and dates["delivery"] <= today else None,
                                    dates["uplift"] if dates["uplift"] and dates["uplift"] <= today else None, updated) if x], default=None)
    days = (today - last_progress).days if last_progress else None
    if stage not in (Stage.CLOSED, Stage.DELIVERED) and days is not None and days >= int(os.environ.get("CLICKUP_STALLED_DAYS", "7") or 7):
        flags.append("stalled")
    method = _s(d.get("method") or row.get("method")).upper()
    svc = _s(d.get("service"))
    crew_note = ""
    for n in d.get("notes") or []:
        if isinstance(n, dict) and n.get("type") == "crewNote" and n.get("comment"):
            crew_note = str(n["comment"]).strip()
    sale = to_number(ex.get("revenue"))
    return Shipment(
        id=f"TMS:{list_id}", source=Source.TMS, source_ref=list_id,
        reference_number=display, customer_name=customer,
        agent=agent,
        origin=origin, destination=destination, destination_hub=hub,
        volume_m3=meas["volume_m3"], weight=meas["weight_kg"],
        stage=stage, source_status=_s(d.get("jobStatus")) or _s(row.get("status")),
        ready_date=dates["uplift"], delivery_date=dates["delivery"],
        status_flags=flags, updated_at=updated, url="",
        assignees=[x for x in [_s(mm.get("name")) or _s(row.get("moveManager"))] if x],
        process_format="Moveware", current_step="", steps_done=0, steps_total=0,
        milestones={"booked": (_activity_dates(d)("booked") or _activity_dates(row)("booked")
                               or _created(d) or _created(row) or _mw_date(row.get("created"))),
                    "uplift": dates["uplift"],
                    "delivered": dates["delivery"] if dates["delivery"] and dates["delivery"] <= today else None,
                    "closed": dates["ops_complete"]},
        last_progress_at=last_progress, days_since_progress=days,
        extra={"direction": dirn, "method": method, "job_type": _s(row.get("jobType")), "service": svc,
               "payer": payer, "branch": _s(d.get("branchName")), "branch_code": _s(d.get("branchCode")),
               "customer_type": _s(d.get("customerType")), "currency": _s(d.get("currency")),
               "coordinator_email": _s(mm.get("email")), "items": meas["items"],
               "origin_country": _country(oloc) or _country(row.get("origin")),
               "destination_country": _country(dloc) or _country(row.get("destination")),
               "origin_port": _named(oloc.get("port")), "destination_port": _named(dloc.get("port")),
               "destination_agent": _named(dloc.get("agent")),
               "sale_value": sale, "is_closed": str(d.get("isClosed") or ""), "note": crew_note[:400],
               "mw_env": env, "load_type": _s(ex.get("loadtype")), "sit_location": _s(ex.get("sitloc")),
               "delivery_type": _s(d.get("deliveryType"))},
    )


# ── walk ─────────────────────────────────────────────────────────────────────
def fetch_tms_shipments(client: MovewareClient | None = None, *, days: int | None = None,
                        slice_days: int | None = None, page_limit: int = 50, max_pages_per_slice: int = 4,
                        details: bool = True, max_details: int | None = None, workers: int | None = None,
                        progress: dict | None = None, today: dt.date | None = None):
    """Walk recently-updated jobs in small date slices, keep the cross-border
    ones, fetch their detail (parallel, bounded), and normalize. Returns
    (shipments, diag)."""
    today = today or dt.date.today()
    client = client or MovewareClient()
    days = days or int(os.environ.get("TMS_DAYS", "150") or 150)
    slice_days = slice_days or int(os.environ.get("TMS_SLICE_DAYS", "7") or 7)
    workers = workers or int(os.environ.get("TMS_WORKERS", "3") or 3)
    max_details = max_details if max_details is not None else int(os.environ.get("TMS_MAX_DETAILS", "150") or 150)
    prog = progress if progress is not None else {}
    diag: dict = {"env": client.env, "base_url": client.base_url, "days": days, "slices": [],
                  "rows_seen": 0, "cross_border": 0, "by_direction": {}, "by_status": {},
                  "by_lane": {}, "details_fetched": 0, "detail_errors": [], "errors": client.errors}

    # ── the walk (V2) ────────────────────────────────────────────────────────
    # V2 ignores every date filter we tried (createdAfter/createdFrom/
    # modifiedSince/updatedAfter/dateFrom and six more — measured 2026-09-14,
    # all returned the identical first page). It honours exactly three params:
    #   limit   rows per page — see the size trap below
    #   page    1-indexed page number   (`offset` is ignored — that was v1)
    #   status  job status, W = Won
    #
    # THE PAGE-SIZE TRAP (measured 2026-09-14): `page` only advances while
    # `limit` is small. At limit=5/8/10 the pages are distinct and walk steadily
    # back through time. At limit=18 — and at 50, where the server caps the page
    # at 18 anyway — page 2, 3 and 4 come back as byte-identical copies of page
    # 1, so the walk collects 18 jobs and can never see past them. 10 is used:
    # honoured exactly, pages continuously, and half the requests of 5.
    # With status=W the feed is ordered NEWEST FIRST (page 1 = today, page 60 ≈
    # 11 months back), so a date window needs no filter at all: page forward and
    # stop once the rows fall out of it. That is also far cheaper than v1's
    # date-slice walk, which is what the performance guardrail cares about.
    #
    # DO NOT re-add a "short page means the end" check. Page sizes come back
    # erratic — limit=10 returned 10, then 8, then 8; limit=15 returned 15, then
    # 3, then 15 — so a short page says nothing about whether more jobs exist.
    # An earlier version broke on `len(rows) < page_limit` and therefore stopped
    # after page 1 every single time, silently capping the board at one page.
    # The walk ends on an empty page, on reaching the date window, on a page
    # budget, or when pages stop yielding anything new.
    seen: dict[str, dict] = {}
    cutoff = today - dt.timedelta(days=days)
    max_pages = int(os.environ.get("TMS_MAX_PAGES", "40") or 40)
    page_limit = min(page_limit, int(os.environ.get("TMS_PAGE_LIMIT", "10") or 10))
    stopped = "page budget"
    barren = 0
    for page in range(1, max_pages + 1):
        try:
            rows = client.jobs(page=page, limit=page_limit,
                               status=",".join(sorted(ACTIVE_STATUSES)))
        except MovewareError as exc:
            diag["slices"].append({"page": page, "error": str(exc)})
            stopped = "error"
            break
        if not rows:
            stopped = "end of feed"
            diag["slices"].append({"page": page, "rows": 0})
            break
        oldest = None
        fresh = 0
        for r in rows:
            rid = str(r.get("id") or "")
            if rid and rid not in seen:
                seen[rid] = r
                fresh += 1
            c = _created(r)
            if c and (oldest is None or c < oldest):
                oldest = c
        diag["slices"].append({"page": page, "rows": len(rows), "new": fresh,
                               "oldest": oldest.isoformat() if oldest else None})
        prog["slices_done"] = len(diag["slices"])
        # The feed is newest-first, so once a whole page predates the window
        # every later page does too.
        if oldest and oldest < cutoff:
            stopped = "reached the window"
            break
        # If `page` ever stops working the way `offset` already did, every page
        # is the same page. Notice that instead of re-reading it 40 times.
        barren = barren + 1 if fresh == 0 else 0
        if barren >= 3:
            stopped = "pages stopped yielding new jobs"
            break

    diag["pages_walked"] = len(diag["slices"])
    diag["stopped_because"] = stopped
    # Drop anything older than the window — the last page straddles the cutoff.
    for rid in [k for k, r in seen.items()
                if (_created(r) or today) < cutoff]:
        seen.pop(rid, None)
    diag["rows_seen"] = len(seen)
    xb = []
    for r in seen.values():
        st = _s(r.get("status")).upper()[:1] or "?"
        diag["by_status"][st] = diag["by_status"].get(st, 0) + 1
        _o, _d = _endpoints(r)
        lane = f"{_country(_o) or '?'}→{_country(_d) or '?'}"
        diag["by_lane"][lane] = diag["by_lane"].get(lane, 0) + 1
        dn = direction(r)
        if dn and st in ACTIVE_STATUSES:
            xb.append(r)
            diag["by_direction"][dn] = diag["by_direction"].get(dn, 0) + 1
    diag["cross_border"] = len(xb)
    diag["by_lane"] = dict(sorted(diag["by_lane"].items(), key=lambda kv: -kv[1])[:12])
    # V2: dateModified. v1: lastUpdated. Sorting on a key that no longer exists
    # would have quietly handed max_details the wrong jobs.
    xb.sort(key=lambda r: str(r.get("dateModified") or r.get("lastUpdated") or ""), reverse=True)

    detail_map: dict[str, dict] = {}
    if details and xb:
        targets = xb[:max_details]
        prog["details_total"] = len(targets)
        prog["details_done"] = 0

        def one(r):
            rid = str(r.get("id"))
            try:
                return rid, client.job(rid)
            except MovewareError as exc:
                diag["detail_errors"].append(f"{rid}: {exc}")
                return rid, None

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for rid, d in ex.map(one, targets):
                if d:
                    detail_map[rid] = d
                prog["details_done"] = prog.get("details_done", 0) + 1
        diag["details_fetched"] = len(detail_map)
        if detail_map:
            sample = next(iter(detail_map.values()))
            diag["detail_keys"] = sorted(sample.keys())
            diag["extras_fields"] = sorted(_extras(sample).keys())

    shipments = [build_shipment(r, detail_map.get(str(r.get("id"))), today=today, env=client.env) for r in xb]
    shipments.sort(key=lambda s: (s.stage.value, s.customer_name.lower()))
    diag["count"] = len(shipments)
    diag["requests_made"] = client.requests_made
    diag["by_stage"] = {}
    for s in shipments:
        diag["by_stage"][s.stage.value] = diag["by_stage"].get(s.stage.value, 0) + 1
    return shipments, diag


def probe(client: MovewareClient | None = None, sample: int = 3) -> dict:
    """Shape discovery for /crossborder/raw?tms=probe — a handful of calls only."""
    client = client or MovewareClient()
    out: dict = {"env": client.env, "base_url": client.base_url, "have_creds": client.have_creds()}
    try:
        since = (dt.date.today() - dt.timedelta(days=14)).isoformat()
        rows = client.jobs(page=1, limit=sample, createdAfter=since)
        out["recent_rows"] = rows
        out["row_keys"] = sorted(rows[0].keys()) if rows else []
        xb = [r for r in client.jobs(page=1, limit=50, createdAfter=since) if direction(r)]
        out["cross_border_in_last_14_days"] = len(xb)
        out["lanes"] = {}
        for r in xb:
            lane = f"{_country(r.get('origin'))}→{_country(r.get('destination'))}"
            out["lanes"][lane] = out["lanes"].get(lane, 0) + 1
        if xb:
            d = client.job(str(xb[0]["id"]))
            out["sample_detail"] = d
            out["sample_shipment"] = build_shipment(xb[0], d, env=client.env).to_dict()
    except MovewareError as exc:
        out["error"] = str(exc)
    out["requests_made"] = client.requests_made
    return out
