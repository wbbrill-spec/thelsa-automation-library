"""
tms.py — TMS shipments from Moveware, normalized into the unified Shipment model.

Read-only. Honors the Moveware performance guardrail: one narrow, filtered,
paginated list walk (recently *created* jobs only, small pages) plus one detail
call per cross-border job, run in the background a few times an hour and cached.
Nothing here ever writes to Moveware.

What we know about the instance (moveware-api-integration-guide.md, mw_live.py):
  • header auth (mw-username / mw-password / mw-company-id), ~2 s per call
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
    CUFT_PER_M3, Hub, Shipment, Source, Stage, hub_for_destination, parse_date, to_number,
)

log = logging.getLogger(__name__)

BASE_URLS = {
    "prod": "https://rest.moveconnect.com/Moveware/v1",
    "test": "https://rest.moveconnect.com/MovewareREST-test/v1",
    "uat": "https://rest.moveconnect.com/movewareUAT/v1",
}
MX = "MX"
# Country codes on the US side of the border that count as "cross-border with Mexico".
US_SIDE = set((os.environ.get("TMS_US_SIDE", "US,CA") or "US").replace(" ", "").split(","))
# Job statuses that mean the file is a live move (single-letter Moveware codes).
ACTIVE_STATUSES = set((os.environ.get("TMS_ACTIVE_STATUSES", "W,P,N,I") or "W").replace(" ", "").upper().split(","))
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
        return all(os.environ.get(k) for k in ("MW_USERNAME", "MW_PASSWORD", "MW_COMPANY_ID"))

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
        body = self.get(f"/jobs?limit={limit}&offset={page}" + (f"&{q}" if q else ""))
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


def _country(v) -> str:
    """List rows carry 2-letter codes; detail may carry an address object."""
    if isinstance(v, dict):
        for k in ("country", "countryCode", "code"):
            if v.get(k):
                return str(v[k]).strip().upper()[:2]
        return ""
    s = str(v or "").strip().upper()
    return s if len(s) == 2 else s[:2] if s else ""


def direction(row: dict) -> str | None:
    """'import' (US side → MX), 'export' (MX → US side) or None (not cross-border)."""
    o, d = _country(row.get("origin")), _country(row.get("destination"))
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


def _measurements(detail: dict) -> dict:
    """{'volume_m3': float|None, 'weight_kg': float|None, 'items': int|None}"""
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


def _place(v) -> str:
    """Best human-readable place from a list value or a detail address object."""
    if isinstance(v, dict):
        a = v.get("address") if isinstance(v.get("address"), dict) else v
        parts = [a.get(k) for k in ("city", "suburb", "town", "state") if a.get(k)]
        if not parts and a.get("formattedAddress"):
            return str(a["formattedAddress"]).replace("\n", ", ").strip()
        if not parts:
            parts = [a.get("country") or a.get("countryISO2")]
        return ", ".join(str(p).strip() for p in parts if p) or _s(v)
    return _s(v)


def _loc(detail: dict, which: str) -> dict:
    locs = detail.get("locations") if isinstance(detail.get("locations"), dict) else {}
    return locs.get(which) if isinstance(locs.get(which), dict) else {}


def stage_for(row: dict, detail: dict | None, today: dt.date) -> tuple[Stage, list[str], dict]:
    """Coarse stage from Moveware dates + status. Moveware has no customs-step
    granularity, so a job between uplift and delivery sits in 'in transit to
    border' until the team tells us which extras carry the border/hub dates."""
    ex = _extras(detail or {})
    status = _s(row.get("status") or (detail or {}).get("jobStatus")).upper()[:1]
    d = detail or {}
    uplift = (_mw_date(d.get("upliftStart")) or _mw_date(d.get("pack")) or _mw_date(row.get("uplift"))
              or _mw_date(ex.get("dtpacking")) or _mw_date(d.get("estimatedMove")))
    delivery = (_mw_date(d.get("deliveryStart")) or _mw_date(ex.get("dtdelivery")) or _mw_date(row.get("delivery"))
                or _mw_date(d.get("estimatedDelivery")))
    ops_done = _mw_date(ex.get("dtopscomplete"))
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
    oloc, dloc = _loc(d, "origin"), _loc(d, "destination")
    origin = _place(oloc) if oloc else (_place(d.get("origin")) or _country(row.get("origin")))
    destination = _place(dloc) if dloc else (_place(d.get("destination")) or _country(row.get("destination")))
    hub = hub_for_destination(destination) if dirn == "import" else Hub.UNKNOWN
    updated = _mw_date(row.get("lastUpdated")) or _mw_date(d.get("lastUpdated"))
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
        milestones={"booked": _mw_date(row.get("created")), "uplift": dates["uplift"],
                    "delivered": dates["delivery"] if dates["delivery"] and dates["delivery"] <= today else None,
                    "closed": dates["ops_complete"]},
        last_progress_at=last_progress, days_since_progress=days,
        extra={"direction": dirn, "method": method, "job_type": _s(row.get("jobType")), "service": svc,
               "payer": payer, "branch": _s(d.get("branchName")), "branch_code": _s(d.get("branchCode")),
               "customer_type": _s(d.get("customerType")), "currency": _s(d.get("currency")),
               "coordinator_email": _s(mm.get("email")), "items": meas["items"],
               "origin_country": _country(row.get("origin")), "destination_country": _country(row.get("destination")),
               "origin_port": _s((oloc.get("port") or {}).get("name")) if oloc else "",
               "destination_port": _s((dloc.get("port") or {}).get("name")) if dloc else "",
               "destination_agent": _s((dloc.get("agent") or {}).get("name")) if dloc else "",
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
    days = days or int(os.environ.get("TMS_DAYS", "180") or 180)
    slice_days = slice_days or int(os.environ.get("TMS_SLICE_DAYS", "7") or 7)
    workers = workers or int(os.environ.get("TMS_WORKERS", "3") or 3)
    max_details = max_details if max_details is not None else int(os.environ.get("TMS_MAX_DETAILS", "150") or 150)
    prog = progress if progress is not None else {}
    diag: dict = {"env": client.env, "base_url": client.base_url, "days": days, "slices": [],
                  "rows_seen": 0, "cross_border": 0, "by_direction": {}, "by_status": {},
                  "by_lane": {}, "details_fetched": 0, "detail_errors": [], "errors": client.errors}

    seen: dict[str, dict] = {}
    end = today
    start_limit = today - dt.timedelta(days=days)
    while end > start_limit:
        begin = max(start_limit, end - dt.timedelta(days=slice_days))
        got = 0
        for page in range(1, max_pages_per_slice + 1):
            try:
                rows = client.jobs(page=page, limit=page_limit,
                                   createdAfter=begin.isoformat(), createdBefore=end.isoformat())
            except MovewareError as exc:
                diag["slices"].append({"from": begin.isoformat(), "to": end.isoformat(), "error": str(exc)})
                rows = []
                break
            for r in rows:
                rid = str(r.get("id") or "")
                if rid and rid not in seen:
                    seen[rid] = r
            got += len(rows)
            if len(rows) < page_limit:
                break
        diag["slices"].append({"from": begin.isoformat(), "to": end.isoformat(), "rows": got})
        prog["slices_done"] = len(diag["slices"])
        end = begin - dt.timedelta(days=1)

    diag["rows_seen"] = len(seen)
    xb = []
    for r in seen.values():
        st = _s(r.get("status")).upper()[:1] or "?"
        diag["by_status"][st] = diag["by_status"].get(st, 0) + 1
        lane = f"{_country(r.get('origin')) or '?'}→{_country(r.get('destination')) or '?'}"
        diag["by_lane"][lane] = diag["by_lane"].get(lane, 0) + 1
        dn = direction(r)
        if dn and st not in DEAD_STATUSES:
            xb.append(r)
            diag["by_direction"][dn] = diag["by_direction"].get(dn, 0) + 1
    diag["cross_border"] = len(xb)
    diag["by_lane"] = dict(sorted(diag["by_lane"].items(), key=lambda kv: -kv[1])[:12])
    xb.sort(key=lambda r: str(r.get("lastUpdated") or ""), reverse=True)

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
