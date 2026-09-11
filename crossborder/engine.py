"""
engine.py — Consolidation planning engine (rules-based; advisory only).

Groups open cross-border shipments from BOTH sources (TIM/ClickUp and
TMS/Moveware) into suggested 53 ft trailer loads by lane and load window,
so the team can fill trucks instead of running them light. It never books
anything — its output is a suggestion list for a human to approve.

Unit of planning: cubic metres against TRUCK_53_M3 (88 m³ — Bill's rule:
20,000 lb of household goods at 6.5 lb/cuft ≈ 3,077 cuft ≈ 88 m³).

Rules (spec §6 + Bill 2026-09-09):
  1. Lane = direction + corridor. Imports (US/CA → MX) are grouped by
     destination hub (Monterrey entry hub → onward hubs); exports (MX → US/CA)
     by destination state/province. Sea and air jobs are excluded — they are
     not truck freight.
  2. An FTL job (or any job ≥ 50 % of a trailer) is an ANCHOR: it already has a
     truck. If it is under 88 m³ the remaining space is offered to other
     shipments on the same lane — this is the "trailer running light" check.
  3. Everything else is packed first-fit-decreasing into 88 m³ trailers per
     lane; an item never exceeds capacity in m³ or kg.
  4. Load window: a shipment is READY when its documents are complete (TIM
     stage ≥ green light; TMS booked jobs whose uplift date has passed or is
     within the horizon). Not-ready shipments are listed as "coming" so the
     planner can see what a few days' wait would add.
  5. TIM small shipments carry a 30-day delivery window from ready date —
     a load must depart before the earliest deadline it carries; shipments
     within WINDOW_RISK_DAYS of their deadline are flagged and must not wait.
  6. Cross-silo: every load lists its TIM and TMS members side by side; a
     lane where one silo has a light truck and the other has fillers is the
     headline opportunity.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field

from .models import (
    Hub, Shipment, Source, Stage, TIM_DELIVERY_WINDOW_DAYS, TRUCK_53_KG, TRUCK_53_M3,
)

READY_STAGES = {Stage.GREEN_LIGHT, Stage.TO_BORDER, Stage.CUSTOMS}
COMING_STAGES = {Stage.BOOKED, Stage.DOCS_PENDING}
ANCHOR_FRACTION = float(os.environ.get("PLAN_ANCHOR_FRACTION", "0.5") or 0.5)
MIN_FILL_TO_SUGGEST = float(os.environ.get("PLAN_MIN_FILL", "0.6") or 0.6)      # a load below this is "light"
HORIZON_DAYS = int(os.environ.get("PLAN_HORIZON_DAYS", "10") or 10)
WINDOW_RISK_DAYS = int(os.environ.get("PLAN_WINDOW_RISK_DAYS", "7") or 7)
TRUCK_METHODS = {"", "ROAD", "TRUCK", "LAND", "GROUND"}
FULL_SERVICES = {"FTL"}
NON_TRUCK_SERVICES = {"FCL 20", "FCL 40", "FCL 40HC", "AIR"}
# Anything above this in "m³" cannot be household goods on one truck — it was
# almost certainly typed in cubic feet. We convert and say so.
IMPLAUSIBLE_M3 = float(os.environ.get("PLAN_IMPLAUSIBLE_M3", "150") or 150)
CUFT_PER_M3 = 35.3147

US_STATES = {"alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida",
             "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
             "maryland", "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska",
             "nevada", "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
             "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas",
             "utah", "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming", "district of columbia"}
US_STATE_ABBR = {"tx": "texas", "ca": "california", "fl": "florida", "az": "arizona", "nm": "new mexico", "nc": "north carolina",
                 "sc": "south carolina", "ny": "new york", "nj": "new jersey", "il": "illinois", "oh": "ohio", "mi": "michigan",
                 "wi": "wisconsin", "tn": "tennessee", "ga": "georgia", "va": "virginia", "wa": "washington", "co": "colorado",
                 "nv": "nevada", "ut": "utah", "or": "oregon", "pa": "pennsylvania", "ma": "massachusetts", "md": "maryland",
                 "mn": "minnesota", "mo": "missouri", "la": "louisiana", "ok": "oklahoma", "ks": "kansas", "in": "indiana"}
CA_PROVINCES = {"ontario", "quebec", "british columbia", "alberta", "manitoba", "saskatchewan", "nova scotia",
                "new brunswick", "newfoundland", "prince edward island"}


@dataclass
class Item:
    shipment: Shipment
    m3: float
    kg: float
    ready: bool
    ready_date: dt.date | None
    deadline: dt.date | None          # latest sensible departure (delivery window)
    anchor: bool = False
    reasons: list[str] = field(default_factory=list)
    sized: bool = True

    @property
    def id(self) -> str:
        return self.shipment.id


@dataclass
class Load:
    lane: str
    direction: str
    hub: str
    items: list[Item] = field(default_factory=list)
    anchor: Item | None = None

    @property
    def m3(self) -> float:
        return round(sum(i.m3 for i in self.items), 2)

    @property
    def kg(self) -> float:
        return round(sum(i.kg for i in self.items), 1)

    @property
    def fill(self) -> float:
        return round(self.m3 / TRUCK_53_M3, 3)

    @property
    def spare_m3(self) -> float:
        return round(max(0.0, TRUCK_53_M3 - self.m3), 2)

    @property
    def depart_by(self) -> dt.date | None:
        ds = [i.deadline for i in self.items if i.deadline]
        return min(ds) if ds else None

    @property
    def ready_by(self) -> dt.date | None:
        ds = [i.ready_date for i in self.items if i.ready_date]
        return max(ds) if ds else None

    def fits(self, it: Item) -> bool:
        return self.m3 + it.m3 <= TRUCK_53_M3 + 1e-6 and self.kg + it.kg <= TRUCK_53_KG + 1e-6

    def to_dict(self, today: dt.date) -> dict:
        srcs = {s.value: sum(1 for i in self.items if i.shipment.source is s) for s in Source}
        risk = [i for i in self.items if i.deadline and (i.deadline - today).days <= WINDOW_RISK_DAYS]
        return {
            "lane": self.lane, "direction": self.direction, "hub": self.hub,
            "truck_m3": TRUCK_53_M3, "m3": self.m3, "kg": self.kg, "fill_pct": round(self.fill * 100),
            "spare_m3": self.spare_m3, "light": self.fill < MIN_FILL_TO_SUGGEST,
            "anchor": self.anchor.id if self.anchor else None,
            "anchors": sum(1 for i in self.items if i.anchor),
            "sources": srcs, "cross_silo": all(srcs.get(k) for k in ("TIM", "TMS")),
            "ready_by": self.ready_by.isoformat() if self.ready_by else None,
            "depart_by": self.depart_by.isoformat() if self.depart_by else None,
            "window_risk": [i.id for i in risk],
            "shipments": [{
                "id": i.id, "source": i.shipment.source.value, "customer": i.shipment.customer_name,
                "agent": i.shipment.agent, "reference": i.shipment.reference_number,
                "destination": i.shipment.destination, "m3": i.m3, "kg": i.kg,
                "stage": i.shipment.stage.value, "ready": i.ready,
                "ready_date": i.ready_date.isoformat() if i.ready_date else None,
                "deadline": i.deadline.isoformat() if i.deadline else None,
                "anchor": i.anchor, "service": (i.shipment.extra or {}).get("service", ""),
                "reasons": i.reasons,
            } for i in self.items],
        }


# ── eligibility ──────────────────────────────────────────────────────────────
def _method(s: Shipment) -> str:
    return str((s.extra or {}).get("method") or "").upper()


def _service(s: Shipment) -> str:
    return str((s.extra or {}).get("service") or "").upper()


def _direction(s: Shipment) -> str:
    d = (s.extra or {}).get("direction")
    if d:
        return d
    return "import"          # every TIM shipment is a US → MX small shipment


def lane_for(s: Shipment) -> tuple[str, str]:
    """(lane label, hub/region label)."""
    dirn = _direction(s)
    if dirn in ("import", "domestic"):
        hub = s.destination_hub.value if s.destination_hub is not Hub.UNKNOWN else "Unassigned hub"
        # Domestic (SIT / TRS) trips consolidate into the same hub lanes as the
        # imports do, but never share a trailer with a cross-border load.
        label = "Import" if dirn == "import" else "Domestic"
        return f"{label} → {hub}", hub
    region = export_region(s)
    return f"Export → {region}", region


def export_region(s: Shipment) -> str:
    """Normalize an export destination to a state/province: 'Brownsville, Texas',
    'TEXAS', 'United States Charlotte North Carolina' and 'TX' all → 'Texas'."""
    dest = (s.destination or "").strip()
    low = dest.lower().replace(".", "")
    for name in sorted(US_STATES | CA_PROVINCES, key=len, reverse=True):
        if name in low:
            return name.title()
    tokens = [t.strip() for t in dest.replace("/", ",").split(",") if t.strip()]
    last = tokens[-1].lower() if tokens else ""
    if last in US_STATE_ABBR:
        return US_STATE_ABBR[last].title()
    for t in reversed(tokens):
        tl = t.lower()
        if tl not in ("united states", "usa", "us", "canada", "estados unidos", "eeuu"):
            return t.title()
    country = (s.extra or {}).get("destination_country") or ""
    return {"US": "United States", "CA": "Canada"}.get(country, country or "?")


def make_item(s: Shipment, today: dt.date) -> Item | None:
    """Return an Item for a consolidation-eligible shipment, else None."""
    if not s.is_open:
        return None
    if _method(s) not in TRUCK_METHODS or _service(s) in NON_TRUCK_SERVICES:
        return None
    if s.stage not in READY_STAGES | COMING_STAGES:
        return None                      # already at hub / delivering / closed
    m3 = s.planning_m3
    reasons: list[str] = []
    if m3 > IMPLAUSIBLE_M3:
        reasons.append(f"{m3} m³ on record is not possible for one truck — read as cubic feet ({round(m3 / CUFT_PER_M3, 2)} m³); fix in the source")
        m3 = round(m3 / CUFT_PER_M3, 2)
    if m3 <= 0:
        reasons.append("no volume on record — cannot be planned until a volume is entered")
    kg = float(s.weight or 0.0)
    if s.source is Source.TIM:
        ready = s.stage in READY_STAGES
        ready_date = s.milestones.get("green_light") or s.ready_date
        deadline = (ready_date + dt.timedelta(days=TIM_DELIVERY_WINDOW_DAYS)) if ready_date else None
        if not ready:
            reasons.append(f"documents not complete (current step: {s.current_step or s.stage.value})")
    else:
        uplift = s.ready_date
        ready = bool(uplift) and uplift <= today + dt.timedelta(days=HORIZON_DAYS)
        ready_date = uplift
        deadline = s.delivery_date
        if not uplift:
            reasons.append("no uplift date in Moveware")
        elif not ready:
            reasons.append(f"uplift scheduled {uplift.isoformat()} (beyond the {HORIZON_DAYS}-day horizon)")
    anchor = _service(s) in FULL_SERVICES or m3 >= TRUCK_53_M3 * ANCHOR_FRACTION
    if anchor:
        reasons.append("full-truck job — anchors its own trailer" if _service(s) in FULL_SERVICES
                       else f"{m3} m³ is ≥ {int(ANCHOR_FRACTION * 100)}% of a trailer — anchors its own trailer")
    it = Item(shipment=s, m3=m3, kg=kg, ready=ready, ready_date=ready_date, deadline=deadline,
              anchor=anchor, reasons=reasons)
    it.sized = m3 > 0
    return it


# ── packing ──────────────────────────────────────────────────────────────────
def _urgency(it: Item, today: dt.date):
    """Sort key: soonest deadline first, then biggest volume first."""
    dl = (it.deadline - today).days if it.deadline else 10_000
    return (dl, -it.m3)


def pack_lane(lane: str, direction: str, hub: str, items: list[Item], today: dt.date) -> list[Load]:
    """Anchors first (each gets its own trailer), then first-fit-decreasing
    by urgency for the rest; a new trailer opens only when nothing fits."""
    loads: list[Load] = []
    # Anchors (FTL / half-trailer jobs) open trailers — but two anchors that fit
    # together share one: that is exactly the "trailer running light" case Bill
    # wants surfaced (two 35 m³ FTL exports on the same lane = one 70 m³ truck).
    for a in sorted([i for i in items if i.anchor], key=lambda i: (_urgency(i, today), -i.m3)):
        target = next((ld for ld in sorted(loads, key=lambda l: -l.m3) if ld.fits(a)), None)
        if target is None:
            loads.append(Load(lane=lane, direction=direction, hub=hub, items=[a], anchor=a))
        else:
            target.items.append(a)
            a.reasons.append("shares a trailer with another full-truck job — confirm both customers accept a shared trailer")
    rest = sorted([i for i in items if not i.anchor], key=lambda i: _urgency(i, today))
    for it in rest:
        target = None
        # Prefer the fullest trailer that still fits (best-fit), so light trailers
        # are topped up before a new one is opened.
        for ld in sorted(loads, key=lambda l: -l.m3):
            if ld.fits(it):
                target = ld
                break
        if target is None:
            target = Load(lane=lane, direction=direction, hub=hub)
            loads.append(target)
        target.items.append(it)
    return loads


def plan(shipments: list[Shipment], today: dt.date | None = None) -> dict:
    """Build the suggested loads. Returns a JSON-ready dict."""
    today = today or dt.date.today()
    items = [it for it in (make_item(s, today) for s in shipments) if it]
    unsized = [i for i in items if not i.sized]
    ready = [i for i in items if i.ready and i.sized]
    coming = [i for i in items if not i.ready and i.sized]
    by_lane: dict[str, list[Item]] = {}
    meta: dict[str, tuple[str, str]] = {}
    for it in ready:
        lane, hub = lane_for(it.shipment)
        by_lane.setdefault(lane, []).append(it)
        meta[lane] = (_direction(it.shipment), hub)
    loads: list[Load] = []
    for lane, its in by_lane.items():
        dirn, hub = meta[lane]
        loads.extend(pack_lane(lane, dirn, hub, its, today))

    # Opportunities: light trailers + what is coming on the same lane.
    coming_by_lane: dict[str, list[Item]] = {}
    for it in coming:
        coming_by_lane.setdefault(lane_for(it.shipment)[0], []).append(it)
    opportunities = []
    for ld in loads:
        if ld.fill >= MIN_FILL_TO_SUGGEST:
            continue
        soon = sorted(coming_by_lane.get(ld.lane, []), key=lambda i: (i.ready_date or dt.date.max, -i.m3))
        addable, m3 = [], 0.0
        for it in soon:
            if m3 + it.m3 <= ld.spare_m3:
                addable.append(it)
                m3 += it.m3
        opportunities.append({
            "lane": ld.lane, "hub": ld.hub, "direction": ld.direction,
            "trailer_m3": ld.m3, "fill_pct": round(ld.fill * 100), "spare_m3": ld.spare_m3,
            "anchor": ld.anchor.id if ld.anchor else None,
            "depart_by": ld.depart_by.isoformat() if ld.depart_by else None,
            "coming": [{"id": i.id, "customer": i.shipment.customer_name, "source": i.shipment.source.value,
                        "m3": i.m3, "ready_date": i.ready_date.isoformat() if i.ready_date else None,
                        "why_not_yet": i.reasons} for i in addable],
            "would_reach_pct": round((ld.m3 + m3) / TRUCK_53_M3 * 100),
            "advice": _advice(ld, addable, m3, today),
        })

    loads_sorted = sorted(loads, key=lambda l: (-(l.anchor is not None), l.depart_by or dt.date.max, -l.m3))
    out_loads = [l.to_dict(today) for l in loads_sorted]
    return {
        "as_of": today.isoformat(), "truck_m3": TRUCK_53_M3, "truck_kg": TRUCK_53_KG,
        "min_fill_pct": round(MIN_FILL_TO_SUGGEST * 100), "horizon_days": HORIZON_DAYS,
        "eligible": len(items), "ready": len(ready), "coming": len(coming), "unsized": len(unsized),
        "excluded": len([s for s in shipments if s.is_open]) - len(items),
        "unsized_by_lane": _group_ids(unsized),
        "loads": out_loads,
        "opportunities": sorted(opportunities, key=lambda o: -o["spare_m3"]),
        "coming_by_lane": {k: [{"id": i.id, "customer": i.shipment.customer_name, "source": i.shipment.source.value,
                                "m3": i.m3, "ready_date": i.ready_date.isoformat() if i.ready_date else None,
                                "reasons": i.reasons} for i in v] for k, v in coming_by_lane.items()},
        "summary": {
            "trailers": len(out_loads),
            "full": sum(1 for l in out_loads if l["fill_pct"] >= 85),
            "light": sum(1 for l in out_loads if l["light"]),
            "cross_silo": sum(1 for l in out_loads if l["cross_silo"]),
            "window_risk": sum(len(l["window_risk"]) for l in out_loads),
            "avg_fill_pct": round(sum(l["fill_pct"] for l in out_loads) / len(out_loads)) if out_loads else 0,
            "planned_m3": round(sum(l["m3"] for l in out_loads), 1),
        },
    }


def _group_ids(items: list[Item]) -> dict:
    out: dict = {}
    for i in items:
        out.setdefault(lane_for(i.shipment)[0], []).append({"id": i.id, "customer": i.shipment.customer_name,
                                                             "source": i.shipment.source.value, "stage": i.shipment.stage.value})
    return out


def _advice(ld: Load, addable: list[Item], add_m3: float, today: dt.date) -> str:
    dep = f" It must leave by {ld.depart_by.isoformat()} to protect a delivery window." if ld.depart_by else ""
    if ld.anchor and not addable:
        return (f"Trailer for {ld.anchor.shipment.customer_name} is running at {round(ld.fill*100)}% "
                f"({ld.m3} m³ of {TRUCK_53_M3}); {ld.spare_m3} m³ is free but nothing else on this lane is ready.{dep}")
    if addable:
        names = ", ".join(f"{i.shipment.customer_name} ({i.m3} m³, {i.ready_date.isoformat() if i.ready_date else 'date TBC'})" for i in addable[:4])
        return (f"{round(ld.fill*100)}% full with {ld.spare_m3} m³ free. Waiting for {names} would take it to "
                f"{round((ld.m3 + add_m3) / TRUCK_53_M3 * 100)}%.{dep}")
    return f"Only {round(ld.fill*100)}% full and nothing else is coming on this lane — send direct or hold for new bookings.{dep}"


# ── email body ───────────────────────────────────────────────────────────────
def email_body(p: dict, site: str = "https://thelsa.inflectionpointnow.com/crossborder") -> tuple[str, str]:
    """(subject, plain-text body) for the daily suggested-load email (a DRAFT)."""
    s = p["summary"]
    subject = f"Suggested cross-border loads — {p['as_of']}: {s['trailers']} trailers, {s['light']} running light"
    L = [f"Suggested loads for {p['as_of']} (advisory — nothing is booked).",
         f"Planning unit: 53 ft trailer = {p['truck_m3']} m³ / {p['truck_kg']} kg. "
         f"{p['ready']} shipments ready, {p['coming']} coming, {p['excluded']} open shipments not truck freight or already delivering.",
         ""]
    if not p["loads"]:
        L.append("No consolidatable shipments are ready today.")
    for n, ld in enumerate(p["loads"], 1):
        tag = " · LIGHT" if ld["light"] else (" · FULL" if ld["fill_pct"] >= 85 else "")
        xs = " · TIM + TMS" if ld["cross_silo"] else ""
        L.append(f"TRAILER {n} — {ld['lane']} — {ld['m3']} m³ ({ld['fill_pct']}%){tag}{xs}")
        if ld["depart_by"]:
            L.append(f"  Depart by {ld['depart_by']} (earliest delivery-window deadline on board)")
        for it in ld["shipments"]:
            flag = " *WINDOW RISK*" if it["id"] in ld["window_risk"] else ""
            L.append(f"  - [{it['source']}] {it['customer']} — {it['agent'] or ''} {it['reference'] or ''} → {it['destination'] or '?'} · {it['m3']} m³"
                     + (f" · {it['service']}" if it["service"] else "") + (" · ANCHOR" if it["anchor"] else "") + flag)
        L.append("")
    if p["opportunities"]:
        L.append("OPPORTUNITIES (trailers running light)")
        for o in p["opportunities"]:
            L.append(f"- {o['lane']}: {o['advice']}")
        L.append("")
    if p["coming_by_lane"]:
        L.append("COMING (not yet ready — documents pending or uplift ahead)")
        for lane, its in p["coming_by_lane"].items():
            L.append(f"- {lane}: " + ", ".join(f"{i['customer']} ({i['m3']} m³{', ' + i['ready_date'] if i['ready_date'] else ''})" for i in its))
        L.append("")
    if p.get("unsized"):
        L.append(f"NOT PLANNABLE — {p['unsized']} shipments have no volume on record (enter a volume in ClickUp / the Remisiones sheet / Moveware):")
        for lane, its in p["unsized_by_lane"].items():
            L.append(f"- {lane}: " + ", ".join(f"[{i['source']}] {i['customer']}" for i in its))
        L.append("")
    L.append(f"Live board: {site}")
    L.append("Reply with changes; a coordinator confirms every load before anything is booked.")
    return subject, "\n".join(L)
