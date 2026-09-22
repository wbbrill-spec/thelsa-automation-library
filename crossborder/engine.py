"""
engine.py — Consolidation planning engine (rules-based; advisory only).

Groups open cross-border shipments from BOTH sources (TIM/ClickUp and
TMS/Moveware) into suggested 53 ft trailer loads by lane and load window,
so the team can fill trucks instead of running them light. It never books
anything — its output is a suggestion list for a human to approve.

Unit of planning: cubic metres against TRUCK_53_M3 (88 m³ — Bill's rule:
20,000 lb of household goods at 6.5 lb/cuft ≈ 3,077 cuft ≈ 88 m³).

Rules (spec §6 + Bill 2026-09-09, revised with Edgar/Fernanda 2026-09-22):
  0. AN IMPORT IS TWO MOVEMENTS, NOT ONE. Stage 1 is the border crossing:
     everybody's freight leaves the McAllen warehouse on ONE trailer to
     Monterrey, whatever city it is bound for. Stage 2 is the onward truck from
     Monterrey to each city, where San Luis Potosí and Querétaro are drops on
     the Mexico City run rather than destinations of their own. Planning these
     as a single movement — which this engine did until 22 Sep — invented a
     trailer per city and missed the crossing consolidation entirely.
  1. Lane = leg + corridor. Stage 1 has one lane; stage 2 has one per corridor
     out of the hub; exports are grouped by destination state/province. Sea and
     air jobs are excluded — they are not truck freight.
  1b. Exports never wait (Fernanda): each is its own load, ready to go, with
     any same-lane pairing offered as an option the coordinator may take.
  1c. Freight a coordinator has ALREADY consolidated (read from her ClickUp
     note — see grouping.py) leaves the suggestions and becomes a truck others
     can add to.
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

from . import grouping, rules
from .models import (
    CROSSING_ORIGIN, ENTRY_HUB, LIFT_VAN_M3, THELSA_TRUCK_M3, THELSA_TRUCK_U_BOXES,
    TRUCK_53_LIFT_VANS, TRUCK_53_U_BOXES, U_BOX_M3, Hub, Leg, Shipment, Source, Stage,
    TIM_DELIVERY_WINDOW_DAYS, TRUCK_53_KG, TRUCK_53_M3, detour_for_hub,
)

# A lift van is a rigid crate: it takes a whole floor position whatever its
# contents, and a 53 ft trailer has 13 of them (Bill, 2026-09-21). So a lift-van
# shipment consumes positions × (88 ÷ 13) m³ of trailer — not its gross m³ — and
# 13 vans exactly fill a trailer. Loose freight still consumes its own m³, which
# keeps mixed loads (vans plus loose) on one consistent scale.
LV_SLOT_M3 = TRUCK_53_M3 / TRUCK_53_LIFT_VANS
# Same logic for U-Boxes: 10 per 53 ft trailer (Bill, 2026-09-21), so each box
# consumes 88 ÷ 10 = 8.8 m³ of trailer, not its 7.3 m³ usable capacity.
UB_SLOT_M3 = TRUCK_53_M3 / TRUCK_53_U_BOXES

READY_STAGES = {Stage.GREEN_LIGHT, Stage.TO_BORDER, Stage.CUSTOMS}
COMING_STAGES = {Stage.BOOKED, Stage.DOCS_PENDING}
# Freight sitting in the Mexican hub waiting for its onward truck. It has
# already crossed, so it is ready by definition — there is no document or
# customs step left between it and the truck to its city.
AT_HUB_STAGES = {Stage.AT_HUB, Stage.ONWARD}
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
    lift_vans: int = 0                # >0 → lift-van loaded; capacity counts positions
    u_boxes: int = 0                  # >0 → U-Box job; capacity counts positions
    space: float = 0.0                # trailer m³ this item actually consumes
    leg: Leg = Leg.CROSSING           # which movement this item is planned on
    detour_stop: str = ""             # dropped short of the lane's end point

    def __post_init__(self):
        if not self.space:
            if self.lift_vans:
                self.space = round(self.lift_vans * LV_SLOT_M3, 4)
            elif self.u_boxes:
                self.space = round(self.u_boxes * UB_SLOT_M3, 4)
            else:
                self.space = self.m3

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
    leg: Leg = Leg.CROSSING
    ships_alone: bool = False          # an export: it leaves without waiting
    group: str = ""                    # an existing consolidation, not a suggestion

    @property
    def m3(self) -> float:
        return round(sum(i.m3 for i in self.items), 2)

    @property
    def kg(self) -> float:
        return round(sum(i.kg for i in self.items), 1)

    @property
    def space(self) -> float:
        """Trailer capacity consumed — lift vans count as whole positions."""
        return round(sum(i.space for i in self.items), 4)

    @property
    def lift_vans(self) -> int:
        return sum(i.lift_vans for i in self.items)

    @property
    def u_boxes(self) -> int:
        return sum(i.u_boxes for i in self.items)

    @property
    def free_u_box_positions(self) -> int:
        return int((max(0.0, TRUCK_53_M3 - self.space) + 0.05) // UB_SLOT_M3)

    @property
    def fill(self) -> float:
        return round(self.space / TRUCK_53_M3, 3)

    @property
    def spare_m3(self) -> float:
        return round(max(0.0, TRUCK_53_M3 - self.space), 2)

    @property
    def free_lift_van_positions(self) -> int:
        # 0.05 m³ tolerance so rounding never costs a whole lift-van position.
        return int((max(0.0, TRUCK_53_M3 - self.space) + 0.05) // LV_SLOT_M3)

    @property
    def depart_by(self) -> dt.date | None:
        ds = [i.deadline for i in self.items if i.deadline]
        return min(ds) if ds else None

    @property
    def ready_by(self) -> dt.date | None:
        ds = [i.ready_date for i in self.items if i.ready_date]
        return max(ds) if ds else None

    def fits(self, it: Item) -> bool:
        return self.space + it.space <= TRUCK_53_M3 + 1e-6 and self.kg + it.kg <= TRUCK_53_KG + 1e-6

    @property
    def detour_stops(self) -> list[str]:
        """Cities this trailer drops at before its final hub, in no fixed order."""
        seen: list[str] = []
        for i in self.items:
            if i.detour_stop and i.detour_stop not in seen:
                seen.append(i.detour_stop)
        return seen

    @property
    def thelsa_truck_ok(self) -> bool:
        """Could one of Thelsa's own trucks carry this, or does it need a 53 ft?

        Fernanda's constraint (22 Sep): a Thelsa truck takes about three
        U-Boxes, not ten, whatever the cubic metres say. Offering her own truck
        for a five-box load would send her to the yard for nothing.
        """
        if self.u_boxes > THELSA_TRUCK_U_BOXES:
            return False
        return self.space <= THELSA_TRUCK_M3 + 1e-6

    def to_dict(self, today: dt.date) -> dict:
        srcs = {s.value: sum(1 for i in self.items if i.shipment.source is s) for s in Source}
        risk = [i for i in self.items if i.deadline and (i.deadline - today).days <= WINDOW_RISK_DAYS]
        return {
            "lane": self.lane, "direction": self.direction, "hub": self.hub,
            "leg": self.leg.value, "leg_label": LEG_LABELS.get(self.leg, self.leg.value),
            "ships_alone": self.ships_alone, "group": self.group,
            "detour_stops": self.detour_stops,
            "thelsa_truck_ok": self.thelsa_truck_ok,
            "thelsa_truck_u_boxes": THELSA_TRUCK_U_BOXES,
            "truck_m3": TRUCK_53_M3, "m3": self.m3, "kg": self.kg, "fill_pct": round(self.fill * 100),
            "space_m3": round(self.space, 2), "lift_vans": self.lift_vans, "lift_van_positions": TRUCK_53_LIFT_VANS,
            "free_lift_van_positions": self.free_lift_van_positions,
            "u_boxes": self.u_boxes, "u_box_positions": TRUCK_53_U_BOXES,
            "free_u_box_positions": self.free_u_box_positions,
            "spare_m3": self.spare_m3, "light": self.fill < MIN_FILL_TO_SUGGEST,
            "anchor": self.anchor.id if self.anchor else None,
            "anchors": sum(1 for i in self.items if i.anchor),
            "sources": srcs, "cross_silo": all(srcs.get(k) for k in ("TIM", "TMS")),
            "ready_by": self.ready_by.isoformat() if self.ready_by else None,
            "depart_by": self.depart_by.isoformat() if self.depart_by else None,
            "window_risk": [i.id for i in risk],
            # Revenue riding on this trailer, kept split by the currency it was
            # booked in. Summing MXN and USD here would be wrong by ~17x; the UI
            # converts at the books rate when it renders.
            "revenue": _revenue_by_currency(i.shipment for i in self.items),
            "shipments": [{
                "id": i.id, "source": i.shipment.source.value, "customer": i.shipment.customer_name,
                "agent": i.shipment.agent, "reference": i.shipment.reference_number,
                "destination": i.shipment.destination, "m3": i.m3, "kg": i.kg,
                "hub": i.shipment.destination_hub.value, "detour_stop": i.detour_stop,
                "lift_vans": i.lift_vans, "space_m3": round(i.space, 2),
                "us_diplomatic": i.shipment.is_us_diplomatic,
                "ubox": i.shipment.is_ubox_job, "u_boxes": i.shipment.u_boxes_planned,
                "stage": i.shipment.stage.value, "ready": i.ready,
                "ready_date": i.ready_date.isoformat() if i.ready_date else None,
                "deadline": i.deadline.isoformat() if i.deadline else None,
                "anchor": i.anchor, "service": (i.shipment.extra or {}).get("service", ""),
                "revenue": i.shipment.revenue, "revenue_currency": i.shipment.revenue_currency,
                "revenue_month": i.shipment.revenue_month,
                "corporate_account": i.shipment.corporate_account,
                "booking_agent": i.shipment.booking_agent,
                "reasons": i.reasons,
            } for i in self.items],
        }


def _revenue_by_currency(shipments) -> list[dict]:
    """[{currency, amount, month, files}] — one entry per booked currency.

    Kept separate on purpose. Thelsa books some files in MXN and some in USD, so
    a single total is only meaningful once a rate is applied, and the rate
    belongs to the month each file earned in. Files with no revenue on record
    are counted in `files_without_revenue` by the caller, never as zero.
    """
    buckets: dict[str, dict] = {}
    for s in shipments:
        if s.revenue in (None, "") or not s.revenue_currency:
            continue
        b = buckets.setdefault(s.revenue_currency, {"currency": s.revenue_currency, "amount": 0.0,
                                                    "files": 0, "months": set()})
        b["amount"] += float(s.revenue)
        b["files"] += 1
        if s.revenue_month:
            b["months"].add(s.revenue_month)
    out = []
    for b in buckets.values():
        months = sorted(b.pop("months"))
        # One month means the whole bucket converts at one rate; several means
        # the UI must convert file by file rather than on the total.
        out.append({**b, "amount": round(b["amount"], 2),
                    "month": months[0] if len(months) == 1 else "",
                    "months": months})
    return sorted(out, key=lambda x: -x["amount"])


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


LEG_LABELS = {
    Leg.CROSSING: f"Stage 1 — border crossing ({CROSSING_ORIGIN} → {ENTRY_HUB.value})",
    Leg.ONWARD: f"Stage 2 — onward from {ENTRY_HUB.value}",
    Leg.EXPORT: "Export",
    Leg.DOMESTIC: "Domestic Mexico",
}


def leg_for(s: Shipment) -> Leg:
    """Which movement this shipment is waiting for right now.

    An import is planned twice in its life: once on the trailer that takes
    everybody's freight across the border together, and again on the truck that
    carries it from the hub to its city. Which one it needs depends only on
    whether it has crossed yet.
    """
    dirn = _direction(s)
    if dirn == "export":
        return Leg.EXPORT
    if dirn == "domestic":
        return Leg.DOMESTIC
    return Leg.ONWARD if s.has_crossed else Leg.CROSSING


def lane_for(s: Shipment) -> tuple[str, str, Leg]:
    """(lane label, hub/region label, leg).

    The lane is the unit of consolidation: two shipments share a trailer only
    if they share a lane. Stage 1 has exactly one lane — everything crossing
    the border goes together, whatever city it is bound for afterwards. Stage 2
    has one lane per corridor out of the hub, with San Luis Potosí and Querétaro
    folded into the Mexico City corridor as drops on the way.
    """
    leg = leg_for(s)
    if leg is Leg.EXPORT:
        region = export_region(s)
        return f"Export → {region}", region, leg
    if leg is Leg.CROSSING:
        return f"{CROSSING_ORIGIN} → {ENTRY_HUB.value} (crossing)", ENTRY_HUB.value, leg

    hub = s.destination_hub
    if hub is Hub.UNKNOWN:
        label = "Unassigned hub"
        return (f"{ENTRY_HUB.value} → {label}" if leg is Leg.ONWARD else f"Domestic → {label}"), label, leg
    detour = detour_for_hub(hub)
    # The stop is deliberately NOT part of the lane name: San Luis Potosí and
    # Querétaro freight has to land in the SAME lane as the Mexico City freight,
    # or the two never share the truck they are supposed to share. Which cities
    # a trailer drops at is reported per load (Load.detour_stops).
    end = detour["corridor_to"] if detour else hub
    prefix = ENTRY_HUB.value if leg is Leg.ONWARD else "Domestic"
    return f"{prefix} → {end.value}", end.value, leg


def detour_stop_for(s: Shipment) -> str:
    """The city this shipment is dropped at, when it rides a longer run."""
    d = detour_for_hub(s.destination_hub)
    return d["stop"] if d else ""


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


def screen(s: Shipment) -> str:
    """Why this shipment is not a consolidation candidate at all, or "".

    Returning the reason rather than a bare False is what lets the board answer
    "why isn't my file on a truck?" without anyone reading the code.
    """
    if not s.is_open:
        return "closed or delivered"
    if _method(s) not in TRUCK_METHODS or _service(s) in NON_TRUCK_SERVICES:
        return f"not truck freight ({_service(s) or _method(s) or 'method not set'})"
    if s.stage not in READY_STAGES | COMING_STAGES | AT_HUB_STAGES:
        return f"stage {s.stage.value} — past the point of planning a truck"
    blocked = rules.consolidation_block(s)
    if blocked:
        return blocked
    if leg_for(s) is Leg.ONWARD and s.destination_hub is ENTRY_HUB:
        return (f"delivers locally from the {ENTRY_HUB.value} hub — there is no "
                "onward line haul to share")
    return ""


def make_item(s: Shipment, today: dt.date) -> Item | None:
    """Return an Item for a consolidation-eligible shipment, else None."""
    if screen(s):
        return None
    m3 = s.planning_m3
    reasons: list[str] = []
    if m3 > IMPLAUSIBLE_M3:
        reasons.append(f"{m3} m³ on record is not possible for one truck — read as cubic feet ({round(m3 / CUFT_PER_M3, 2)} m³); fix in the source")
        m3 = round(m3 / CUFT_PER_M3, 2)
    if m3 <= 0:
        if s.is_ubox_job:
            reasons.append("U-Box job (AA reference) — number of U-Boxes not on record yet, so it cannot be planned")
        else:
            reasons.append("no volume on record — cannot be planned until a volume is entered")

    kg = float(s.weight or 0.0)
    leg = leg_for(s)
    if leg is Leg.ONWARD:
        # Already across the border and sitting in the hub: nothing stands
        # between it and a truck except the truck.
        ready = True
        ready_date = (s.milestones.get("at_hub") or s.milestones.get("crossed")
                      or s.clearance_date or today)
        deadline = s.delivery_date
        if s.source is Source.TIM and not deadline:
            base = s.milestones.get("green_light") or s.ready_date
            deadline = (base + dt.timedelta(days=TIM_DELIVERY_WINDOW_DAYS)) if base else None
        reasons.append(f"in the {ENTRY_HUB.value} hub — waiting for the truck to "
                       f"{s.destination or 'its city'}")
    elif s.source is Source.TIM:
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
    if m3 > 0 and s.is_ubox_job:
        n = s.u_boxes_planned
        how = "count on record" if s.u_boxes else f"{s.volume_m3} m³ ÷ {U_BOX_M3} usable per box"
        reasons.append(f"U-Box job — {n} U-Box{'es' if n != 1 else ''} ({how})")
    # Position-based capacity is applied to US Embassy / Consulate bookings — the
    # rule Bill gave (2026-09-21). TIM lift-van / U-box counts keep the volume
    # conversion they have always used; extending positions to them is a
    # separate decision, not something to change silently here.
    lift_vans = s.lift_vans_planned if (s.is_us_diplomatic and m3 > 0) else 0
    u_boxes = s.u_boxes_planned if (not lift_vans and s.is_ubox_job and m3 > 0) else 0
    if lift_vans:
        space = round(lift_vans * LV_SLOT_M3, 4)
    elif u_boxes:
        space = round(u_boxes * UB_SLOT_M3, 4)
        reasons.append(f"takes {u_boxes} of {TRUCK_53_U_BOXES} U-Box positions on a 53 ft trailer")
    else:
        space = m3
    if lift_vans:
        why = (f"{m3} m³ gross ÷ {LIFT_VAN_M3}" if s.is_us_diplomatic and not s.lift_vans
               else "lift-van count on record")
        reasons.append(f"{lift_vans} lift van{'s' if lift_vans != 1 else ''} ({why}) — "
                       f"takes {lift_vans} of {TRUCK_53_LIFT_VANS} trailer positions")
        if lift_vans > TRUCK_53_LIFT_VANS:
            reasons.append(f"more than {TRUCK_53_LIFT_VANS} lift vans — needs more than one trailer")
    anchor = _service(s) in FULL_SERVICES or space >= TRUCK_53_M3 * ANCHOR_FRACTION
    if anchor:
        reasons.append("full-truck job — anchors its own trailer" if _service(s) in FULL_SERVICES
                       else (f"{lift_vans} lift vans is ≥ {int(ANCHOR_FRACTION * 100)}% of a trailer — anchors its own trailer"
                             if lift_vans else
                             f"{u_boxes} U-Boxes is ≥ {int(ANCHOR_FRACTION * 100)}% of a trailer — anchors its own trailer"
                             if u_boxes else
                             f"{m3} m³ is ≥ {int(ANCHOR_FRACTION * 100)}% of a trailer — anchors its own trailer"))
    detour = detour_stop_for(s) if leg in (Leg.ONWARD, Leg.DOMESTIC) else ""
    if detour:
        reasons.append(f"dropped at {detour} on the way — rides the corridor truck "
                       "rather than opening a trailer of its own")
    if leg is Leg.CROSSING and s.destination_hub is not Hub.UNKNOWN and s.destination_hub is not ENTRY_HUB:
        reasons.append(f"crosses with everyone else, then goes on to {s.destination_hub.value}")
    if u_boxes and u_boxes > THELSA_TRUCK_U_BOXES:
        reasons.append(f"more than {THELSA_TRUCK_U_BOXES} U-Boxes — a Thelsa truck cannot "
                       "take this; it needs a hired 53 ft trailer")
    it = Item(shipment=s, m3=m3, kg=kg, ready=ready, ready_date=ready_date, deadline=deadline,
              anchor=anchor, reasons=reasons, lift_vans=lift_vans, u_boxes=u_boxes, space=space,
              leg=leg, detour_stop=detour)
    it.sized = m3 > 0
    return it


# ── packing ──────────────────────────────────────────────────────────────────
def _urgency(it: Item, today: dt.date):
    """Sort key: soonest deadline first, then biggest volume first."""
    dl = (it.deadline - today).days if it.deadline else 10_000
    return (dl, -it.space)


def _hold_exports() -> bool:
    return (os.environ.get("CB_HOLD_EXPORTS") or "").strip().lower() in ("1", "true", "yes")


def pack_exports(lane: str, hub: str, items: list[Item], today: dt.date) -> list[Load]:
    """One load per export — exports do not wait (Fernanda, 22 Sep).

    Exports are infrequent, so holding one back for a second customer means
    holding it indefinitely. Fernanda ships a single U-Box on Thelsa's own
    truck at the border rather than wait. Each export is therefore its own
    load, ready to go now; where two happen to be going the same way the plan
    offers the pairing as an option, and the coordinator decides.
    """
    return [Load(lane=lane, direction="export", hub=hub, items=[it],
                 anchor=it if it.anchor else None, leg=Leg.EXPORT, ships_alone=True)
            for it in sorted(items, key=lambda i: _urgency(i, today))]


def pack_lane(lane: str, direction: str, hub: str, items: list[Item], today: dt.date,
              leg: Leg = Leg.CROSSING) -> list[Load]:
    """Anchors first (each gets its own trailer), then first-fit-decreasing
    by urgency for the rest; a new trailer opens only when nothing fits."""
    if leg is Leg.EXPORT and not _hold_exports():
        return pack_exports(lane, hub, items, today)
    loads: list[Load] = []
    # Anchors (FTL / half-trailer jobs) open trailers — but two anchors that fit
    # together share one: that is exactly the "trailer running light" case Bill
    # wants surfaced (two 35 m³ FTL exports on the same lane = one 70 m³ truck).
    for a in sorted([i for i in items if i.anchor], key=lambda i: (_urgency(i, today), -i.space)):
        target = next((ld for ld in sorted(loads, key=lambda l: -l.space) if ld.fits(a)), None)
        if target is None:
            loads.append(Load(lane=lane, direction=direction, hub=hub, items=[a], anchor=a, leg=leg))
        else:
            target.items.append(a)
            a.reasons.append("shares a trailer with another full-truck job — confirm both customers accept a shared trailer")
    rest = sorted([i for i in items if not i.anchor], key=lambda i: _urgency(i, today))
    for it in rest:
        target = None
        # Prefer the fullest trailer that still fits (best-fit), so light trailers
        # are topped up before a new one is opened.
        for ld in sorted(loads, key=lambda l: -l.space):
            if ld.fits(it):
                target = ld
                break
        if target is None:
            target = Load(lane=lane, direction=direction, hub=hub, leg=leg)
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
    meta: dict[str, tuple[str, str, Leg]] = {}
    for it in ready:
        lane, hub, leg = lane_for(it.shipment)
        by_lane.setdefault(lane, []).append(it)
        meta[lane] = (_direction(it.shipment), hub, leg)
    loads: list[Load] = []
    for lane, its in by_lane.items():
        dirn, hub, leg = meta[lane]
        loads.extend(pack_lane(lane, dirn, hub, its, today, leg=leg))

    # Opportunities: light trailers + what is coming on the same lane.
    coming_by_lane: dict[str, list[Item]] = {}
    for it in coming:
        coming_by_lane.setdefault(lane_for(it.shipment)[0], []).append(it)
    opportunities = []
    for ld in loads:
        if ld.fill >= MIN_FILL_TO_SUGGEST:
            continue
        soon = sorted(coming_by_lane.get(ld.lane, []), key=lambda i: (i.ready_date or dt.date.max, -i.space))
        addable, m3 = [], 0.0          # m3 here is trailer SPACE added (lift vans as positions)
        for it in soon:
            if m3 + it.space <= ld.spare_m3 + 1e-6:
                addable.append(it)
                m3 += it.space
        opportunities.append({
            "lane": ld.lane, "hub": ld.hub, "direction": ld.direction,
            "trailer_m3": ld.m3, "fill_pct": round(ld.fill * 100), "spare_m3": ld.spare_m3,
            "anchor": ld.anchor.id if ld.anchor else None,
            "depart_by": ld.depart_by.isoformat() if ld.depart_by else None,
            "coming": [{"id": i.id, "customer": i.shipment.customer_name, "source": i.shipment.source.value,
                        "m3": i.m3, "ready_date": i.ready_date.isoformat() if i.ready_date else None,
                        "why_not_yet": i.reasons} for i in addable],
            "would_reach_pct": round((ld.space + m3) / TRUCK_53_M3 * 100),
            "advice": _advice(ld, addable, m3, today),
        })

    # Legs run in order: nothing can take the onward truck until it has crossed,
    # so stage 1 is shown first whatever each load's departure date says.
    leg_order = {Leg.CROSSING: 0, Leg.ONWARD: 1, Leg.EXPORT: 2, Leg.DOMESTIC: 3}
    loads_sorted = sorted(loads, key=lambda l: (leg_order.get(l.leg, 9),
                                                -(l.anchor is not None),
                                                l.depart_by or dt.date.max, -l.space))
    out_loads = [l.to_dict(today) for l in loads_sorted]
    _add_export_pairings(loads_sorted, out_loads)
    excluded_detail = _excluded_detail(shipments)
    return {
        "as_of": today.isoformat(), "truck_m3": TRUCK_53_M3, "truck_kg": TRUCK_53_KG,
        "min_fill_pct": round(MIN_FILL_TO_SUGGEST * 100), "horizon_days": HORIZON_DAYS,
        "eligible": len(items), "ready": len(ready), "coming": len(coming), "unsized": len(unsized),
        "excluded": len([s for s in shipments if s.is_open]) - len(items),
        "excluded_detail": excluded_detail,
        "unsized_by_lane": _group_ids(unsized),
        "legs": _legs_summary(out_loads),
        "groups": existing_groups(shipments, ready, today),
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
            "crossing_trailers": sum(1 for l in out_loads if l.get("leg") == Leg.CROSSING.value),
            "onward_trailers": sum(1 for l in out_loads if l.get("leg") == Leg.ONWARD.value),
            "exports_alone": sum(1 for l in out_loads if l.get("ships_alone")),
        },
    }


def _group_ids(items: list[Item]) -> dict:
    out: dict = {}
    for i in items:
        out.setdefault(lane_for(i.shipment)[0], []).append({"id": i.id, "customer": i.shipment.customer_name,
                                                             "source": i.shipment.source.value, "stage": i.shipment.stage.value})
    return out


def _legs_summary(out_loads: list[dict]) -> list[dict]:
    """Trailers and fill per leg — the headline of the two-stage import plan."""
    order = [Leg.CROSSING, Leg.ONWARD, Leg.EXPORT, Leg.DOMESTIC]
    out = []
    for leg in order:
        mine = [l for l in out_loads if l.get("leg") == leg.value]
        if not mine:
            continue
        out.append({
            "leg": leg.value, "label": LEG_LABELS[leg], "trailers": len(mine),
            "shipments": sum(len(l["shipments"]) for l in mine),
            "m3": round(sum(l["m3"] for l in mine), 1),
            "avg_fill_pct": round(sum(l["fill_pct"] for l in mine) / len(mine)),
            "light": sum(1 for l in mine if l["light"]),
        })
    return out


def _excluded_detail(shipments: list[Shipment]) -> list[dict]:
    """Open shipments the planner left alone, grouped by the reason why.

    Without this the board can only say "37 excluded", which is exactly the
    number somebody will query in the training session.
    """
    buckets: dict[str, dict] = {}
    for s in shipments or []:
        if not s.is_open:
            continue
        why = screen(s)
        if not why:
            continue
        # Collapse the variable part ("stage at_hub — …") so the list stays short.
        key = why.split(" (")[0].split(" — ")[0]
        b = buckets.setdefault(key, {"reason": key, "count": 0, "examples": []})
        b["count"] += 1
        if len(b["examples"]) < 5:
            b["examples"].append({"id": s.id, "customer": s.customer_name,
                                  "source": s.source.value, "why": why})
    return sorted(buckets.values(), key=lambda b: -b["count"])


def _add_export_pairings(loads: list[Load], out_loads: list[dict]) -> None:
    """Offer, but never assume, that two exports going the same way share a truck.

    Exports ship on their own (Fernanda, 22 Sep). Where two are ready on the
    same lane and would physically fit together, that is worth a coordinator's
    thirty seconds — so it is offered as an option on the card, and the load
    plan still says each one leaves by itself.
    """
    by_lane: dict[str, list[tuple[Load, dict]]] = {}
    for ld, od in zip(loads, out_loads):
        if ld.leg is Leg.EXPORT and ld.ships_alone:
            by_lane.setdefault(ld.lane, []).append((ld, od))
    for lane, pairs in by_lane.items():
        if len(pairs) < 2:
            continue
        for ld, od in pairs:
            others = []
            for other, _ in pairs:
                if other is ld:
                    continue
                if ld.space + other.space <= TRUCK_53_M3 + 1e-6 and ld.kg + other.kg <= TRUCK_53_KG + 1e-6:
                    names = ", ".join(i.shipment.customer_name for i in other.items)
                    others.append({"customers": names,
                                   "ids": [i.id for i in other.items],
                                   "space_m3": round(other.space, 2),
                                   "combined_fill_pct": round((ld.space + other.space) / TRUCK_53_M3 * 100)})
            if others:
                od["optional_pairings"] = others
                od["pairing_advice"] = (
                    "Exports do not wait — this one is ready to go on its own. "
                    f"If it suits the customers, it could share with: "
                    + "; ".join(f"{o['customers']} (together {o['combined_fill_pct']}% of a trailer)"
                                for o in others[:3]))


def existing_groups(shipments: list[Shipment], ready: list[Item],
                    today: dt.date | None = None) -> list[dict]:
    """Consolidations Fernanda has ALREADY made, and what could still join them.

    This is decision D6 from the 22 Sep meeting. A file she has put on a truck
    must stop appearing in the suggestions — it is spoken for — but the truck
    itself becomes the most useful thing on the board, because Sara can see a
    trailer being filled and add her freight to it instead of booking another.
    """
    today = today or dt.date.today()
    by_key: dict[str, dict] = {}
    for s in shipments or []:
        if not (s.is_open and s.is_grouped):
            continue
        key = grouping.group_key(s)
        if not key:
            continue
        lane, hub, leg = lane_for(s)
        g = by_key.setdefault(key, {
            "key": key, "name": s.group_name or "", "lane": lane, "hub": hub,
            "leg": leg.value, "leg_label": LEG_LABELS.get(leg, leg.value),
            "members": [], "note": s.consolidation.get("note", ""),
            "third_party": s.consolidation.get("third_party", ""),
            "space_m3": 0.0, "m3": 0.0, "lift_vans": 0, "u_boxes": 0,
        })
        it = make_item(s, today)
        space = it.space if it else s.planning_m3
        g["members"].append({
            "id": s.id, "source": s.source.value, "customer": s.customer_name,
            "reference": s.reference_number, "destination": s.destination,
            # "TMS - 110719" — source and job number, what a coordinator needs
            # in order to go and look the other file up (Bill, 2026-09-22).
            "label": grouping._label(s),
            "consolidated_with": s.consolidated_with,
            "carrier": bool(s.consolidation.get("with")),
            "m3": s.planning_m3, "space_m3": round(space, 2),
            "u_boxes": s.u_boxes_planned, "lift_vans": s.lift_vans_planned,
            "stage": s.stage.value, "url": s.url,
        })
        g["space_m3"] = round(g["space_m3"] + space, 2)
        g["m3"] = round(g["m3"] + (s.planning_m3 or 0), 2)
        g["u_boxes"] += s.u_boxes_planned
        g["lift_vans"] += s.lift_vans_planned
        if not g["name"] and s.group_name:
            g["name"] = s.group_name
        gg = (s.extra or {}).get("consolidation_group") or {}
        if gg.get("named") and not g.get("unmatched"):
            g["unmatched"] = [n["name"] for n in gg["named"] if not n.get("id")]

    out = []
    for g in by_key.values():
        spare = round(max(0.0, TRUCK_53_M3 - g["space_m3"]), 2)
        joinable = [i for i in ready
                    if lane_for(i.shipment)[0] == g["lane"]
                    and i.space <= spare + 1e-6
                    and not i.shipment.is_grouped]
        joinable.sort(key=lambda i: -i.space)
        g.update({
            "customers": len(g["members"]),
            "spare_m3": spare,
            "fill_pct": round(g["space_m3"] / TRUCK_53_M3 * 100),
            "free_u_box_positions": int((spare + 0.05) // UB_SLOT_M3),
            "free_lift_van_positions": int((spare + 0.05) // LV_SLOT_M3),
            "thelsa_truck_ok": g["u_boxes"] <= THELSA_TRUCK_U_BOXES and g["space_m3"] <= THELSA_TRUCK_M3 + 1e-6,
            "could_join": [{"id": i.id, "customer": i.shipment.customer_name,
                            "source": i.shipment.source.value, "space_m3": round(i.space, 2),
                            "destination": i.shipment.destination} for i in joinable[:6]],
        })
        g["advice"] = _group_advice(g)
        out.append(g)
    return sorted(out, key=lambda g: -g["spare_m3"])


def _group_advice(g: dict) -> str:
    who = ", ".join(m["customer"] for m in g["members"][:4])
    head = (f"{g['customers']} file{'s' if g['customers'] != 1 else ''} already "
            f"consolidated ({who}) — {g['fill_pct']}% of a trailer.")
    if g["third_party"]:
        head += f" Moving with {g['third_party']}."
    if g.get("unmatched"):
        # A name in the note that matches nothing on the board is the one thing
        # here worth interrupting somebody about: either the file is not in
        # ClickUp or Moveware yet, or it is spelled differently.
        head += (" Named in the note but not found on the board: "
                 + ", ".join(g["unmatched"]) + ".")
    if g["could_join"]:
        names = ", ".join(c["customer"] for c in g["could_join"][:3])
        return head + (f" {g['spare_m3']} m³ is still free — {names} "
                       "could go on the same truck.")
    return head + f" {g['spare_m3']} m³ free, nothing else ready on this lane."


def _used(ld: Load) -> str:
    if ld.u_boxes and not ld.lift_vans:
        loose = round(ld.space - ld.u_boxes * UB_SLOT_M3, 1)
        return (f"{ld.u_boxes} of {TRUCK_53_U_BOXES} U-Box positions"
                + (f" plus {loose} m³ loose" if loose > 0.05 else ""))
    if ld.lift_vans:
        loose = round(ld.space - ld.lift_vans * LV_SLOT_M3, 1)
        return (f"{ld.lift_vans} of {TRUCK_53_LIFT_VANS} lift van positions"
                + (f" plus {loose} m³ loose" if loose > 0.05 else ""))
    return f"{ld.m3} m³ of {TRUCK_53_M3}"


def _free(ld: Load) -> str:
    if ld.u_boxes and not ld.lift_vans:
        n = ld.free_u_box_positions
        return f"{n} U-Box position{'s' if n != 1 else ''} ({ld.spare_m3} m³)"
    if ld.lift_vans:
        n = ld.free_lift_van_positions
        return f"{n} lift van position{'s' if n != 1 else ''} ({ld.spare_m3} m³)"
    return f"{ld.spare_m3} m³"


def _advice(ld: Load, addable: list[Item], add_m3: float, today: dt.date) -> str:
    dep = f" It must leave by {ld.depart_by.isoformat()} to protect a delivery window." if ld.depart_by else ""
    if ld.anchor and not addable:
        return (f"Trailer for {ld.anchor.shipment.customer_name} is running at {round(ld.fill*100)}% "
                f"({_used(ld)}); {_free(ld)} is free but nothing else on this lane is ready.{dep}")
    if addable:
        names = ", ".join(f"{i.shipment.customer_name} ({i.m3} m³, {i.ready_date.isoformat() if i.ready_date else 'date TBC'})" for i in addable[:4])
        return (f"{round(ld.fill*100)}% full with {_free(ld)} free. Waiting for {names} would take it to "
                f"{round((ld.space + add_m3) / TRUCK_53_M3 * 100)}%.{dep}")
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
    if p.get("legs"):
        L.append("BY LEG")
        for lg in p["legs"]:
            L.append(f"  {lg['label']}: {lg['trailers']} trailer(s), "
                     f"{lg['shipments']} shipments, {lg['avg_fill_pct']}% average fill")
        L.append("")
    if p.get("groups"):
        L.append("ALREADY CONSOLIDATED (a coordinator has put these together)")
        for g in p["groups"]:
            L.append(f"- {g['lane']}{(' · ' + g['name']) if g['name'] else ''}: {g['advice']}")
        L.append("")
    if not p["loads"]:
        L.append("No consolidatable shipments are ready today.")
    last_leg = None
    for n, ld in enumerate(p["loads"], 1):
        if ld.get("leg") != last_leg:
            last_leg = ld.get("leg")
            L.append(f"== {ld.get('leg_label') or last_leg} ==")
        tag = " · LIGHT" if ld["light"] else (" · FULL" if ld["fill_pct"] >= 85 else "")
        xs = " · TIM + TMS" if ld["cross_silo"] else ""
        size = (f"{ld['lift_vans']} of {ld['lift_van_positions']} lift van positions · {ld['m3']} m³ gross"
                if ld.get("lift_vans") else
                f"{ld['u_boxes']} of {ld['u_box_positions']} U-Box positions · {ld['m3']} m³"
                if ld.get("u_boxes") else f"{ld['m3']} m³")
        L.append(f"TRAILER {n} — {ld['lane']} — {size} ({ld['fill_pct']}%){tag}{xs}")
        if ld.get("detour_stops"):
            L.append(f"  Drops on the way: {', '.join(ld['detour_stops'])}")
        if ld.get("ships_alone"):
            L.append("  Export — goes on its own, not held for consolidation"
                     + (f". {ld['pairing_advice']}" if ld.get("pairing_advice") else ""))
        if ld.get("u_boxes") and not ld.get("thelsa_truck_ok"):
            L.append(f"  Needs a hired 53 ft trailer — more than {ld.get('thelsa_truck_u_boxes')} "
                     "U-Boxes will not fit on a Thelsa truck")
        if ld["depart_by"]:
            L.append(f"  Depart by {ld['depart_by']} (earliest delivery-window deadline on board)")
        for it in ld["shipments"]:
            flag = " *WINDOW RISK*" if it["id"] in ld["window_risk"] else ""
            L.append(f"  - [{it['source']}] {it['customer']} — {it['agent'] or ''} {it['reference'] or ''} → {it['destination'] or '?'} · {it['m3']} m³"
                     + (f" · {it['lift_vans']} lift van{'s' if it['lift_vans'] != 1 else ''}" if it.get("lift_vans") else "")
                     + (f" · {it['u_boxes']} U-Box{'es' if it['u_boxes'] != 1 else ''}" if it.get("u_boxes") else "")
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
