"""
models.py — Unified cross-border Shipment model (spec §5), pipeline stages
(§7) and the destination → hub lookup (§12 open item, DRAFT).

Both ClickUp (TIM) and Moveware (TMS) records normalize into `Shipment` so
the dashboard and the consolidation engine treat the two silos identically.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional

# ── Enums ───────────────────────────────────────────────────────────────────────


class Source(str, Enum):
    TIM = "TIM"   # ClickUp
    TMS = "TMS"   # Moveware


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
    TORREON = "Torreón"
    UNKNOWN = "Unknown"


# ── Capacity constants (spec §5, from the logistics training) ───────────────────

TRUCK_53_LIFT_VANS = 13
TRUCK_53_U_BOXES = 10
TRUCK_36_LIFT_VANS = 7
TRUCK_36_U_BOXES = 3
# 13 lift vans ≡ 10 U-boxes on the baseline truck → one U-box takes 1.3 slots.
U_BOX_LIFT_VAN_EQUIV = TRUCK_53_LIFT_VANS / TRUCK_53_U_BOXES
CUFT_PER_M3 = 35.3147
TIM_DELIVERY_WINDOW_DAYS = 30


# ── Destination → hub lookup (DRAFT — open item §12) ────────────────────────────
# Keyed by a normalized city/state token (lowercase, accents stripped).
# The team will finalize this from the logistics-training hub network; until
# then unmatched destinations land in Hub.UNKNOWN and are listed in /raw.

_HUB_CITIES: dict[Hub, list[str]] = {
    Hub.MONTERREY: [
        "monterrey", "san pedro garza garcia", "san pedro", "apodaca", "guadalupe",
        "santa catarina", "escobedo", "san nicolas", "nuevo leon", "saltillo",
        "ramos arizpe", "reynosa", "matamoros", "nuevo laredo", "tamaulipas",
        "ciudad victoria", "tampico", "san luis potosi", "coahuila", "monclova",
    ],
    Hub.MEXICO_CITY: [
        "mexico city", "ciudad de mexico", "cdmx", "distrito federal", "df",
        "estado de mexico", "edomex", "toluca", "naucalpan", "huixquilucan",
        "interlomas", "santa fe", "polanco", "cuernavaca", "morelos", "puebla",
        "pachuca", "hidalgo", "tlaxcala", "veracruz", "xalapa", "oaxaca",
        "acapulco", "guerrero", "chiapas", "tuxtla gutierrez",
    ],
    Hub.GUADALAJARA: [
        "guadalajara", "zapopan", "tlaquepaque", "tonala", "tlajomulco", "jalisco",
        "chapala", "ajijic", "puerto vallarta", "nayarit", "tepic", "colima",
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

    # ── derived ──
    @property
    def lift_van_equivalents(self) -> float:
        """Truck slots consumed on the baseline 53 ft truck."""
        lv = self.lift_vans or 0
        ub = self.u_boxes or 0
        if lv or ub:
            return round(lv + ub * U_BOX_LIFT_VAN_EQUIV, 2)
        return 0.0

    @property
    def is_open(self) -> bool:
        return self.stage in OPEN_STAGES or self.stage is Stage.UNKNOWN

    def days_to_delivery(self, today: Optional[dt.date] = None) -> Optional[int]:
        if not self.delivery_date:
            return None
        return (self.delivery_date - (today or dt.date.today())).days

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["stage"] = self.stage.value
        d["destination_hub"] = self.destination_hub.value
        for k in ("ready_date", "clearance_date", "delivery_date", "updated_at"):
            d[k] = d[k].isoformat() if d[k] else None
        d["lift_van_equivalents"] = self.lift_van_equivalents
        d["is_open"] = self.is_open
        return d
