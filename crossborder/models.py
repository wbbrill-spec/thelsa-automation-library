"""
models.py — Unified cross-border Shipment model (spec §5), pipeline stages
(§7) and the destination → hub lookup (§12 open item, DRAFT).

Both ClickUp (TIM) and Moveware (TMS) records normalize into `Shipment` so
the dashboard and the consolidation engine treat the two silos identically.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional

# ── Enums ───────────────────────────────────────────────────────────────────────


class Source(str, Enum):
    TIM = "TIM"   # ClickUp
    TMS = "TMS"   # Moveware
    TRS = "TRS"   # SIT / "Plan de Viajes" — domestic Mexico line hauls


class Stage(str, Enum):
    """Normalized pipeline stage — the dashboard's columns (spec §7)."""
    BOOKED = "booked"
    DOCS_PENDING = "docs_pending"
    GREEN_LIGHT = "green_light"
    TO_BORDER = "in_transit_to_border"
    CUSTOMS = "customs_clearance"
    AT_HUB = "at_hub"
    ONWARD = "onward_leg"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CLOSED = "closed"
    UNKNOWN = "unknown"   # source status not yet mapped — surfaced in /raw


STAGE_ORDER = [s for s in Stage if s is not Stage.UNKNOWN]
OPEN_STAGES = [s for s in STAGE_ORDER if s not in (Stage.DELIVERED, Stage.CLOSED)]


class Hub(str, Enum):
    MONTERREY = "Monterrey"
    MEXICO_CITY = "Mexico City"
    GUADALAJARA = "Guadalajara"
    MERIDA = "Mérida"
    QUERETARO = "Querétaro"
    SAN_LUIS_POTOSI = "San Luis Potosí"
    TORREON = "Torreón"
    UNKNOWN = "Unknown"


class Leg(str, Enum):
    """Which leg of the journey a shipment is planned on (Edgar/Fernanda, 22 Sep).

    An import is TWO truck movements, not one: everyone's freight crosses the
    border together on one trailer (McAllen → Monterrey), and only then splits
    onto the trucks going to each city. Planning them as one movement — which
    the board did until 2026-09-22 — invents a separate trailer per destination
    city and misses the crossing consolidation entirely.
    """
    CROSSING = "crossing"     # US warehouse → Mexican entry hub, everyone together
    ONWARD = "onward"         # entry hub → the customer's city
    EXPORT = "export"         # Mexico → US/Canada
    DOMESTIC = "domestic"     # inside Mexico (TRS / Plan de Viajes)


# ── Capacity constants (spec §5, from the logistics training) ───────────────────

TRUCK_53_LIFT_VANS = 13
TRUCK_53_U_BOXES = 10
TRUCK_36_LIFT_VANS = 7
TRUCK_36_U_BOXES = 3
# A Thelsa-owned truck is NOT a 53 ft trailer. Fernanda (22 Sep): "about 75 m³,
# but only about three U-Boxes fit" — the boxes are tall and rigid, so the limit
# is the floor plan, not the volume. That is why she hires a 53 ft trailer for a
# U-Box load. CB_THELSA_TRUCK_U_BOXES overrides once the team measures it.
THELSA_TRUCK_U_BOXES = int(os.environ.get("CB_THELSA_TRUCK_U_BOXES", str(TRUCK_36_U_BOXES)) or TRUCK_36_U_BOXES)
THELSA_TRUCK_M3 = float(os.environ.get("CB_THELSA_TRUCK_M3", "75") or 75)
# 13 lift vans ≡ 10 U-boxes on the baseline truck → one U-box takes 1.3 slots.
U_BOX_LIFT_VAN_EQUIV = TRUCK_53_LIFT_VANS / TRUCK_53_U_BOXES
# Usable volume of one lift van (~87" x 55" x 87" ≈ 200 cuft). Used to turn a
# cubic-metre volume (Moveware / Remisiones "cdm") into truck slots when no
# explicit lift-van / U-box count exists. Confirm with the team (spec §12).
LIFT_VAN_M3 = 5.7
# U-Box container (Bill, 2026-09-21): exterior 96" × 60" × 90", usable
# capacity 257 cu ft / 7.3 m³, maximum contents weight 2,000 lb.
U_BOX_M3 = 7.3
U_BOX_CUFT = 257
U_BOX_MAX_LB = 2000
U_BOX_EXTERIOR_IN = (96, 60, 90)
# In ClickUp, a job reference starting "AA" is a U-Box job (Bill, 2026-09-21).
U_BOX_REF_PREFIX = "AA"
CUFT_PER_M3 = 35.3147
TIM_DELIVERY_WINDOW_DAYS = 30

# US Embassy / Consulate — matched on accent-folded, lower-cased account names.
# Needs BOTH a mission word and a US marker, so "Embajada de Canadá" is excluded.
_US_MISSION_RE = re.compile(
    r"(?=.*\b(embajada|embassy|consulado|consulate)\b)"
    r"(?=.*(estados unidos|united states|\bu\.\s?s\.|\busa\b|\bus\b|\be\.?\s?u\.?\s?a\b))")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()

# Bill's rule (2026-09-09): a 53 ft freight trailer carries about 20,000 lb of
# household goods; at a density factor of 6.5 lb/cuft that is ~3,077 cuft ≈ 88 m³
# of usable space. The consolidation engine plans in m³ against this number and
# flags any trailer running below it as a candidate to take another load.
TRUCK_53_LBS = 20000
HHG_DENSITY_LB_PER_CUFT = 6.5
TRUCK_53_CUFT = round(TRUCK_53_LBS / HHG_DENSITY_LB_PER_CUFT)          # 3077
TRUCK_53_M3 = round(TRUCK_53_CUFT / CUFT_PER_M3)                        # 87 → use 88
TRUCK_53_M3 = int(os.environ.get("TRUCK_53_M3", "88") or 88)
TRUCK_53_KG = round(TRUCK_53_LBS * 0.4536)                              # 9072


# ── Destination → hub lookup (DRAFT — open item §12) ────────────────────────────
# Keyed by a normalized city/state token (lowercase, accents stripped).
# The team will finalize this from the logistics-training hub network; until
# then unmatched destinations land in Hub.UNKNOWN and are listed in /raw.

_HUB_CITIES: dict[Hub, list[str]] = {
    Hub.MONTERREY: [
        "monterrey", "san pedro garza garcia", "san pedro", "apodaca", "guadalupe",
        "santa catarina", "escobedo", "san nicolas", "nuevo leon", "saltillo",
        "ramos arizpe", "reynosa", "matamoros", "nuevo laredo", "tamaulipas",
        "ciudad victoria", "tampico", "coahuila", "monclova",
    ],
    # San Luis Potosí used to sit in the Monterrey list, which made the board
    # read it as Monterrey freight. It is not: it is a DROP ON THE WAY to Mexico
    # City (Bill/Fernanda, 22 Sep). It gets its own hub so the board tells the
    # truth, and DETOUR_STOPS below is what puts it on the CDMX trailer.
    Hub.SAN_LUIS_POTOSI: [
        "san luis potosi", "slp", "soledad de graciano sanchez", "matehuala",
    ],
    Hub.MEXICO_CITY: [
        "mexico city", "ciudad de mexico", "cdmx", "distrito federal", "df",
        "estado de mexico", "edomex", "toluca", "naucalpan", "huixquilucan",
        "interlomas", "santa fe", "polanco", "iztapalapa", "cuernavaca", "morelos", "puebla", "atlixco",
        "pachuca", "hidalgo", "tlaxcala", "veracruz", "xalapa", "oaxaca",
        "acapulco", "guerrero", "chiapas", "tuxtla gutierrez",
    ],
    Hub.GUADALAJARA: [
        "guadalajara", "zapopan", "tlaquepaque", "tonala", "tlajomulco", "jalisco",
        "chapala", "ajijic", "puerto vallarta", "ixtapa", "sayulita", "nayarit", "tepic", "colima",
        "manzanillo", "aguascalientes", "michoacan", "morelia", "sinaloa",
        "culiacan", "mazatlan",
    ],
    Hub.QUERETARO: [
        "queretaro", "san juan del rio", "san miguel de allende", "guanajuato",
        "leon", "celaya", "irapuato", "salamanca", "silao",
    ],
    Hub.MERIDA: [
        "merida", "yucatan", "cancun", "playa del carmen", "tulum",
        "quintana roo", "campeche", "chetumal", "cozumel", "villahermosa",
        "tabasco",
    ],
    Hub.TORREON: [
        "torreon", "gomez palacio", "lerdo", "durango", "chihuahua",
        "ciudad juarez", "juarez", "zacatecas", "fresnillo",
    ],
}


def norm_text(s: Optional[str]) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


# Longest tokens first so "san pedro garza garcia" wins over "san pedro".
_HUB_INDEX: list[tuple[str, Hub]] = sorted(
    ((tok, hub) for hub, toks in _HUB_CITIES.items() for tok in toks),
    key=lambda t: -len(t[0]),
)


def hub_for_destination(destination: Optional[str]) -> Hub:
    """Map a free-text destination ("Zapopan, Jal.", "CDMX") to its hub."""
    text = f" {norm_text(destination)} "
    if not text.strip():
        return Hub.UNKNOWN
    for tok, hub in _HUB_INDEX:
        if f" {tok} " in text:
            return hub
    return Hub.UNKNOWN


# ── the border crossing and the corridors out of it ─────────────────────────────
# Fernanda's import routine (training 21 Sep, confirmed with Edgar 22 Sep):
# collect everyone's freight at the McAllen warehouse, cross it to Monterrey on
# ONE trailer, then split it onto the trucks running to each city.
CROSSING_ORIGIN = os.environ.get("CB_CROSSING_ORIGIN", "McAllen").strip() or "McAllen"
ENTRY_HUB = Hub.MONTERREY

# Cities that are a STOP on a corridor rather than a destination of their own.
# Fernanda asks the supplier for a drop in San Luis Potosí or Querétaro on the
# Monterrey → Mexico City run, so that freight belongs on the CDMX trailer and
# must never open a trailer of its own.
DETOUR_STOPS: dict[Hub, dict] = {
    Hub.SAN_LUIS_POTOSI: {"stop": "San Luis Potosí", "corridor_to": Hub.MEXICO_CITY},
    Hub.QUERETARO: {"stop": "Querétaro", "corridor_to": Hub.MEXICO_CITY},
}


def detour_for_hub(hub: Hub) -> Optional[dict]:
    """{'stop', 'corridor_to'} when this hub is a drop on a longer run, else None.

    CB_NO_DETOURS=1 turns the behaviour off and gives every hub its own trailer
    again — the escape hatch if the team decides a stop is not reliable.
    """
    if os.environ.get("CB_NO_DETOURS", "") in ("1", "true", "yes"):
        return None
    return DETOUR_STOPS.get(hub)


# ── Unit helpers ────────────────────────────────────────────────────────────────


def to_number(v) -> Optional[float]:
    """Parse '12.5', '1,200 cuft', '$550' → float; None when blank/unparseable."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def to_int(v) -> Optional[int]:
    n = to_number(v)
    return int(round(n)) if n is not None else None


def volume_to_m3(v, unit_hint: Optional[str] = None) -> Optional[float]:
    """Normalize a volume to m³. Unit comes from the value text ('850 cuft'),
    else the field name / unit hint, else assumed m³."""
    n = to_number(v)
    if n is None:
        return None
    text = f"{v} {unit_hint or ''}".lower()
    if re.search(r"cu\.?\s*ft|cuft|cubic\s*f|ft3|ft³", text):
        return round(n / CUFT_PER_M3, 2)
    return round(n, 2)


def parse_date(v) -> Optional[dt.date]:
    """ClickUp gives ms-epoch strings; Moveware gives ISO strings."""
    if v in (None, "", 0, "0"):
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.date() if isinstance(v, dt.datetime) else v
    s = str(v).strip()
    if re.fullmatch(r"-?\d{11,}", s):                      # epoch millis
        return dt.datetime.fromtimestamp(int(s) / 1000, dt.timezone.utc).date()
    if re.fullmatch(r"\d{9,10}", s):                        # epoch seconds
        return dt.datetime.fromtimestamp(int(s), dt.timezone.utc).date()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%fZ", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s[:26], fmt).date()
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


# ── The unified record ──────────────────────────────────────────────────────────


@dataclass
class Shipment:
    id: str                       # internal: "TIM:<task_id>" / "TMS:<job_id>"
    source: Source
    source_ref: str
    reference_number: str = ""
    customer_name: str = ""
    agent: str = ""
    origin: str = ""
    destination: str = ""
    destination_hub: Hub = Hub.UNKNOWN
    current_location: str = ""
    volume_m3: Optional[float] = None
    weight: Optional[float] = None
    lift_vans: Optional[int] = None
    u_boxes: Optional[int] = None
    stage: Stage = Stage.UNKNOWN
    source_status: str = ""       # raw status text from ClickUp / Moveware
    ready_date: Optional[dt.date] = None
    clearance_date: Optional[dt.date] = None
    delivery_date: Optional[dt.date] = None
    status_flags: list[str] = field(default_factory=list)
    updated_at: Optional[dt.datetime] = None
    url: str = ""                 # deep link back to the source record
    assignees: list[str] = field(default_factory=list)
    # ── process-checklist progress (TIM: the numbered steps in the ClickUp list) ──
    process_format: str = ""      # "DA" (13 steps) / "DTD" (17 steps) / ""
    current_step: str = ""        # name of the first step not yet complete
    steps_done: int = 0
    steps_total: int = 0
    milestones: dict = field(default_factory=dict)   # e.g. {"green_light": date, "crossed": date}
    last_progress_at: Optional[dt.date] = None       # when the latest step was completed
    days_since_progress: Optional[int] = None
    # ── commercial (Moveware `jobValue` + `roles`; TIM fills what Remisiones has) ──
    # Revenue is stored exactly as booked, in its own currency, and converted only
    # at display time — see crossborder/fx.py. Never normalize it here.
    revenue: Optional[float] = None
    revenue_currency: str = ""      # "USD" / "MXN"; "" means do not convert
    revenue_month: str = ""         # YYYY-MM the books rate is taken from
    revenue_month_basis: str = ""   # which date set it: "pack" (export) / "delivery" (import) / "booked" (fallback)
    invoice_status: str = ""        # Moveware invoiceStatus: "Y" invoiced, "N" not
    customer_type: str = ""         # "Company" / "Diplomatic" / "" (private)
    corporate_account: str = ""     # the corporate client, when the move is one
    booking_agent: str = ""
    origin_agent: str = ""
    destination_agent: str = ""
    extra: dict = field(default_factory=dict)        # source-specific facts (e.g. remisiones sale value)

    # ── derived ──
    @property
    def lift_van_equivalents(self) -> float:
        """Truck slots consumed on the baseline 53 ft truck."""
        lv = self.lift_vans or 0
        ub = self.u_boxes or 0
        if lv or ub:
            return round(lv + ub * U_BOX_LIFT_VAN_EQUIV, 2)
        if self.volume_m3:
            return round(self.volume_m3 / LIFT_VAN_M3, 2)
        return 0.0

    @property
    def is_open(self) -> bool:
        return self.stage in OPEN_STAGES or self.stage is Stage.UNKNOWN

    @property
    def is_corporate(self) -> bool:
        """Corporate move rather than a private/consumer one.

        Some Moveware files carry the literal placeholder "CORPORATIVO" instead
        of a real account name. That still means corporate, so it counts here —
        `corporate_account_named` is what says whether we can name the client.
        """
        return bool(self.corporate_account.strip()) or self.customer_type.strip().lower() in (
            "company", "corporate", "diplomatic")

    @property
    def corporate_account_named(self) -> bool:
        """True when the corporate client is actually named, not a placeholder."""
        name = self.corporate_account.strip().upper()
        return bool(name) and name not in ("CORPORATIVO", "CORPORATE", "N/A", "-")

    @property
    def planning_m3(self) -> float:
        """Volume the consolidation engine plans with: measured m³ first, else
        lift-van / U-box counts converted, else 0 (unknown)."""
        if self.volume_m3:
            return round(float(self.volume_m3), 2)
        return round((self.lift_vans or 0) * LIFT_VAN_M3 + (self.u_boxes or 0) * U_BOX_M3, 2)

    # ── U-Box jobs (Bill, 2026-09-21) ──────────────────────────────────────
    @property
    def is_ubox_job(self) -> bool:
        """A U-Box job: ClickUp reference starts with "AA", or a U-Box count is on record."""
        ref = str(self.reference_number or "").strip().upper()
        return ref.startswith(U_BOX_REF_PREFIX) or bool(self.u_boxes)

    @property
    def u_boxes_planned(self) -> int:
        """U-Boxes this job ships in. 0 means a U-Box job whose count is unknown.

        An explicit count (Remisiones "3 uboxes") wins. Otherwise a volume on an
        AA job is read as boxes of 7.3 m³ usable each, rounded to the nearest
        whole box with a minimum of one. With neither, the job is still marked a
        U-Box job but left unsized — never guessed at one box.
        """
        if self.u_boxes:
            return int(round(self.u_boxes))
        if not self.is_ubox_job or not self.volume_m3:
            return 0
        return max(1, int(float(self.volume_m3) / U_BOX_M3 + 0.5))

    # ── lift-van loaded shipments (Bill, 2026-09-21) ──────────────────────
    @property
    def is_us_diplomatic(self) -> bool:
        """A US Embassy or US Consulate booking.

        Matched on the corporate account and the bill-to — e.g.
        "EMBAJADA DE LOS ESTADOS UNIDOS DE AMERICA",
        "U.S. Consulate General Matamoros". Other countries' missions are NOT
        included: the rule Bill gave is specifically about US bookings.
        """
        names = " | ".join(x for x in (self.corporate_account, str((self.extra or {}).get("bill_to") or "")) if x)
        return bool(names) and bool(_US_MISSION_RE.search(_fold(names)))

    @property
    def lift_van_loaded(self) -> bool:
        """Ships in lift vans: an explicit lift-van count, or a US diplomatic booking."""
        return bool(self.lift_vans) or self.is_us_diplomatic

    @property
    def lift_vans_planned(self) -> int:
        """How many lift vans this shipment occupies on a trailer.

        An explicit count wins. For US Embassy / Consulate bookings Moveware
        holds the GROSS volume including the lift vans, so the count is
        gross m³ ÷ 5.7 (Bill, 2026-09-21), rounded to the NEAREST whole van —
        the live files sit just above whole multiples (35 m³ → 6.14, 52 → 9.12,
        30 → 5.26), which is what n vans measured externally looks like.
        Rounding up instead would call every 35 m³ file 7 vans rather than 6.
        CB_LV_ROUNDING=up switches to the conservative reading.
        """
        if self.lift_vans:
            return int(self.lift_vans)
        if not self.is_us_diplomatic or not self.volume_m3:
            return 0
        raw = float(self.volume_m3) / LIFT_VAN_M3
        if (os.environ.get("CB_LV_ROUNDING") or "").strip().lower() == "up":
            import math
            return max(1, math.ceil(raw - 1e-9))
        return max(1, int(raw + 0.5))

    # ── where it is on the journey (Edgar/Fernanda, 2026-09-22) ───────────
    @property
    def has_crossed(self) -> bool:
        """True once the freight is over the border and inside Mexico.

        This is the hinge of the two-stage import plan: before it, the shipment
        belongs on the combined McAllen → Monterrey crossing trailer; after it,
        on a truck from the hub to the customer's city.
        """
        if self.milestones.get("crossed") or self.milestones.get("at_hub"):
            return True
        # Bill's ruling, consolidation meeting 23 Sep (D18): "if we have a
        # customs clearance date that's in the past in Moveware, then it's
        # cleared." This is the answer to the question that had left stage 2
        # empty — nothing recorded the moment freight crossed, so the board
        # could not plan the truck out of Monterrey for any of it.
        if self.clearance_date and self.clearance_date <= dt.date.today():
            return True
        return self.stage in (Stage.AT_HUB, Stage.ONWARD, Stage.OUT_FOR_DELIVERY,
                              Stage.DELIVERED, Stage.CLOSED)

    # ── which border it crosses (consolidation meeting, 23 Sep) ───────────
    @property
    def port_of_entry(self) -> str:
        """"McAllen" / "Laredo" / "" — the crossing this file uses.

        Policy from 23 Sep (D1) is McAllen for everything, with exceptions
        approved individually. TIM has always cleared at McAllen and Fernanda
        confirmed she never uses Laredo, so a TIM file is McAllen unless it
        says otherwise. TMS is mid-switch: coordinators write "port of entry:
        McAllen" in the Moveware crew notes (D17), and until a file says so we
        record nothing rather than assuming the policy was followed. Measuring
        the switch is the whole point; assuming it would measure nothing.
        """
        marked = str((self.extra or {}).get("port_of_entry") or "").strip()
        if marked:
            return marked
        if self.source is Source.TIM:
            return "McAllen"
        return ""

    @property
    def port_is_assumed(self) -> bool:
        """True when we are stating a port nobody wrote down."""
        return bool(self.port_of_entry) and not (self.extra or {}).get("port_of_entry")

    @property
    def load_type(self) -> str:
        """"ubox" / "liftvan" / "loose" / "" — how the freight is made up.

        Fernanda agreed to mark this (D22) because a 15 m³ shipment can be any
        of the three and they pack differently: 13 lift vans to a 53 ft trailer
        against 10 U-Boxes. Counts already on the file win over a written note.
        """
        if self.u_boxes:
            return "ubox"
        if self.lift_vans:
            return "liftvan"
        return str((self.extra or {}).get("load_type_marked") or "")

    # ── door-to-door loose-loaded moves (Fernanda, 2026-09-22) ────────────
    @property
    def is_door_to_door(self) -> bool:
        """A door-to-door loose-loaded move — shipped direct, never consolidated.

        These are private moves of roughly 8 m³ that already fill most of a
        truck, and Fernanda's rule is "there is no point making them wait".

        NOTE (Fernanda, 2026-09-23): this used to read the ClickUp checklist
        length — 17 steps meant the "DTD Impo" template, 13 the "DA" one. That
        was wrong. **The finished import template is the 13-step one**, and the
        only two 17-step lists left on the workspace were an export
        ("EXPO - Brad Sutton", destined for Los Angeles) and one old Intermove
        file. So the length identified no door-to-door move at all, and did
        wrongly exclude an export.

        RESOLVED (consolidation meeting, 23 Sep — D22): Fernanda agreed to
        write "door to door" in the ClickUp comments on new files. So the
        marker now exists, it is read from the note text by markers.py, and
        this property honours it. Anything the source describes in words still
        counts; nothing is inferred from the shape of the checklist.
        """
        marked = (self.extra or {}).get("door_to_door")
        if isinstance(marked, bool):
            return marked
        text = _fold(" ".join(str((self.extra or {}).get(k) or "")
                              for k in ("service", "process", "service_description", "job_type")))
        return bool(re.search(r"\bdtd\b|door\s*to\s*door|puerta\s*a\s*puerta", text))

    # ── consolidation grouping (Fernanda's ClickUp note, 2026-09-22) ──────
    @property
    def consolidation(self) -> dict:
        """Fernanda's note about this shipment, parsed — see grouping.py."""
        g = (self.extra or {}).get("consolidation")
        return g if isinstance(g, dict) else {}

    @property
    def group_name(self) -> str:
        return str(self.consolidation.get("group") or "")

    @property
    def consolidated_with(self) -> list:
        """The other files on this truck, as the board names them.

        ["TMS - 110719", "TIM - 121722", …] — source and job number, which is
        what a coordinator needs to go and look one up (Bill, 2026-09-22).
        Filled by grouping.resolve_groups once every source has been merged.
        """
        g = (self.extra or {}).get("consolidation_group")
        return list(g.get("consolidated_with") or []) if isinstance(g, dict) else []

    @property
    def is_grouped(self) -> bool:
        """Already put on a truck with other files — off the suggestion list."""
        return bool(self.consolidation.get("grouped"))

    @property
    def do_not_consolidate(self) -> bool:
        """Marked urgent / ships alone / moving with another carrier."""
        return bool(self.consolidation.get("do_not_consolidate"))

    def days_to_delivery(self, today: Optional[dt.date] = None) -> Optional[int]:
        if not self.delivery_date:
            return None
        return (self.delivery_date - (today or dt.date.today())).days

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["stage"] = self.stage.value
        d["destination_hub"] = self.destination_hub.value
        for k in ("ready_date", "clearance_date", "delivery_date", "updated_at", "last_progress_at"):
            d[k] = d[k].isoformat() if d[k] else None
        d["milestones"] = {k: (v.isoformat() if v else None) for k, v in self.milestones.items()}
        d["lift_van_equivalents"] = self.lift_van_equivalents
        d["planning_m3"] = self.planning_m3
        d["is_open"] = self.is_open
        d["is_corporate"] = self.is_corporate
        d["is_ubox_job"] = self.is_ubox_job
        d["u_boxes_planned"] = self.u_boxes_planned
        d["is_us_diplomatic"] = self.is_us_diplomatic
        d["lift_van_loaded"] = self.lift_van_loaded
        d["lift_vans_planned"] = self.lift_vans_planned
        d["corporate_account_named"] = self.corporate_account_named
        d["has_crossed"] = self.has_crossed
        d["is_door_to_door"] = self.is_door_to_door
        d["port_of_entry"] = self.port_of_entry
        d["port_is_assumed"] = self.port_is_assumed
        d["load_type"] = self.load_type
        d["consolidation"] = self.consolidation
        d["group_name"] = self.group_name
        d["consolidated_with"] = self.consolidated_with
        d["is_grouped"] = self.is_grouped
        d["do_not_consolidate"] = self.do_not_consolidate
        return d
