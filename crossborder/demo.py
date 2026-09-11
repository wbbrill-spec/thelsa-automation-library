"""
demo.py — simulated shipments for demonstrating the dashboard.

WHY THIS EXISTS
---------------
Moveware answered 503 on every slice on 2026-09-11 and the Remisiones workbook
is still behind a Graph 403, so the board can show the TIM half and almost
nothing else. This module fabricates a plausible fleet so the dashboard can be
demonstrated end to end while the live feeds are blocked.

SAFETY — this data is fake and must never be mistaken for the real business
--------------------------------------------------------------------------
Three hard rules, enforced here and in web.py:

  1. OFF BY DEFAULT. Demo rows appear only when the request asks for them
     (`?demo=1`) or CROSSBORDER_DEMO=1 is set in the environment.
  2. NEVER CACHED, NEVER DRAFTED. Demo rows are mixed in at the edge of a web
     request only. They never enter _CACHE, so the alert drafter, the suggested
     -load email and the daily scheduler — everything that can reach a
     coordinator's inbox — cannot see them. `is_demo()` is also checked inside
     both draft paths as a belt-and-braces stop.
  3. ALWAYS LABELLED. Every row carries extra["demo"] = True, its reference
     number starts with "DEMO-", its customer name ends with " (demo)" and the
     page shows a standing banner plus a DEMO chip on every card.

Customers, drivers and references are invented. Any resemblance to a real
Thelsa file is coincidence — the volumes and lanes are shaped after the real
ones only so the consolidation engine has something realistic to pack.

Usage:
    /crossborder?demo=1              the board with demo rows mixed in
    /crossborder?demo=only           demo rows only (live feeds hidden)
    CROSSBORDER_DEMO=1               make ?demo=1 the default for every request
    /crossborder?demo=0              always wins — turns it off
"""
from __future__ import annotations

import datetime as dt
import os
import random

from .models import Shipment, Source, Stage, hub_for_destination

SEED = int(os.environ.get("CROSSBORDER_DEMO_SEED", "20260911") or 20260911)
N_TMS = int(os.environ.get("CROSSBORDER_DEMO_TMS", "35") or 35)
N_TRS = int(os.environ.get("CROSSBORDER_DEMO_TRS", "120") or 120)

DEMO_MARK = "demo"


# ── invented people and places ───────────────────────────────────────────────

_FIRST = ["Alejandro", "María", "Jorge", "Lucía", "Ricardo", "Ana", "Miguel",
          "Patricia", "Fernando", "Claudia", "Roberto", "Sofía", "Eduardo",
          "Gabriela", "Andrés", "Valeria", "Héctor", "Daniela", "Raúl", "Mónica",
          "Sergio", "Paulina", "Arturo", "Isabel", "Emilio", "Renata", "Guillermo",
          "Adriana", "Tomás", "Carolina", "James", "Sarah", "Michael", "Emily",
          "David", "Jennifer", "Robert", "Laura", "Christopher", "Megan"]
_LAST = ["Hernández", "Ramírez", "Castillo", "Ibarra", "Montes", "Delgado",
         "Valdés", "Ochoa", "Pineda", "Quintero", "Sandoval", "Treviño",
         "Escobar", "Lozano", "Bermúdez", "Carranza", "Figueroa", "Zamora",
         "Arreola", "Cordero", "Whitfield", "Larsen", "Brennan", "Okafor",
         "Kaminski", "Navarro", "Ellsworth", "Baptiste", "Rowley", "Aguirre"]

_CORPORATE = ["Grupo Aurelio", "Vantage Semiconductor", "Delmar Industrial",
              "Northbrook Pharma", "Cormorant Energy", "Helios Automotive",
              "Pinnacle Foods MX", "Sierra Blanca Mining", "Ardent Textiles",
              "Brightline Robotics"]

# Where TIM/TMS cross-border files originate (US) and land (MX).
_US_ORIGINS = [
    "Houston, Texas", "Dallas, Texas", "San Antonio, Texas", "Austin, Texas",
    "Laredo, Texas", "El Paso, Texas", "Phoenix, Arizona", "San Diego, California",
    "Los Angeles, California", "Chicago, Illinois", "Atlanta, Georgia",
    "Charlotte, North Carolina", "Denver, Colorado", "Detroit, Michigan",
    "Miami, Florida", "Seattle, Washington", "Boston, Massachusetts",
    "Nashville, Tennessee", "Columbus, Ohio", "Portland, Oregon",
]

# Destination city → the hub it routes through (models.hub_for_destination
# resolves these; the hub is recomputed from the text, never hard-coded).
_MX_DESTS = [
    "Monterrey, N.L.", "San Pedro Garza García, N.L.", "Apodaca, N.L.",
    "Saltillo, Coah.", "Ciudad de México", "Santa Fe, CDMX", "Polanco, CDMX",
    "Interlomas, Edo. Méx.", "Toluca, Edo. Méx.", "Puebla, Pue.",
    "Cuernavaca, Mor.", "Querétaro, Qro.", "San Miguel de Allende, Gto.",
    "León, Gto.", "Guadalajara, Jal.", "Zapopan, Jal.", "Ajijic, Jal.",
    "Puerto Vallarta, Jal.", "Mérida, Yuc.", "Cancún, Q. Roo",
    "Playa del Carmen, Q. Roo", "Villahermosa, Tab.", "Torreón, Coah.",
    "Chihuahua, Chih.", "Veracruz, Ver.", "Aguascalientes, Ags.",
    "San Luis Potosí, S.L.P.", "Culiacán, Sin.", "Mazatlán, Sin.", "Morelia, Mich.",
]

_AGENTS = ["Aires MX", "Crown Relocations", "Santa Fe Relocation", "Interdean",
           "Allied Pickfords", "Sirva", "Gosselin", "Asian Tigers", "Writer Relocations",
           "Direct Client"]

# TRS is domestic Mexico: branch → branch line hauls off the Plan de Viajes.
# (plaza code, city text the hub lookup understands)
_TRS_BRANCHES = [
    ("MEX", "Ciudad de México"), ("MTY", "Monterrey, N.L."),
    ("GDL", "Guadalajara, Jal."), ("QRO", "Querétaro, Qro."),
    ("MID", "Mérida, Yuc."), ("TRC", "Torreón, Coah."),
    ("VHSA", "Villahermosa, Tab."), ("CUN", "Cancún, Q. Roo"),
    ("PBC", "Puebla, Pue."), ("VER", "Veracruz, Ver."),
    ("SLP", "San Luis Potosí, S.L.P."), ("LEO", "León, Gto."),
    ("CJS", "Ciudad Juárez, Chih."), ("HMO", "Hermosillo, Son."),
    ("TIJ", "Tijuana, B.C."), ("MZT", "Mazatlán, Sin."),
]

_TRS_TYPES = ["FOR", "CARGAFOR", "TRANFCOMP", "INTC", "INTD", "LOC"]
_TRS_UNITS = [f"A{n}" for n in range(12, 46)] + ["T6", "T7", "T8"]

_TMS_SERVICES = ["LTL", "FTL", "GROUPAGE"]
_TMS_STAGES = [Stage.BOOKED, Stage.DOCS_PENDING, Stage.GREEN_LIGHT,
               Stage.TO_BORDER, Stage.CUSTOMS, Stage.AT_HUB, Stage.ONWARD,
               Stage.OUT_FOR_DELIVERY, Stage.DELIVERED]
_TMS_WEIGHTS = [6, 5, 6, 4, 3, 4, 3, 3, 2]

_TRS_STAGES = [Stage.BOOKED, Stage.GREEN_LIGHT, Stage.ONWARD, Stage.AT_HUB,
               Stage.OUT_FOR_DELIVERY, Stage.DELIVERED]
_TRS_WEIGHTS = [7, 6, 5, 4, 3, 4]

_FLAG_POOL = [
    ("on_hold", 0.05), ("payment_pending", 0.07), ("docs_incomplete", 0.10),
    ("docs_pending", 0.08), ("unresponsive", 0.04), ("certificate_pending", 0.04),
    ("in_storage", 0.05), ("stalled", 0.08),
]


# ── switch ───────────────────────────────────────────────────────────────────


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on", "only")


def demo_mode(args=None) -> str:
    """"off" | "mix" | "only" — what this request asked for.

    An explicit ?demo=0 always wins, so a demo left switched on in the
    environment can be turned off from the URL without a redeploy.
    """
    val = None
    if args is not None:
        val = args.get("demo")
    if val is not None and str(val).strip() != "":
        if str(val).strip().lower() == "only":
            return "only"
        return "mix" if _truthy(val) else "off"
    return "mix" if _truthy(os.environ.get("CROSSBORDER_DEMO", "")) else "off"


def is_demo(args=None) -> bool:
    return demo_mode(args) != "off"


# ── generation ───────────────────────────────────────────────────────────────


def _person(rng: random.Random) -> str:
    if rng.random() < 0.14:
        return rng.choice(_CORPORATE)
    return f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"


def _flags(rng: random.Random, stage: Stage) -> list[str]:
    if stage in (Stage.DELIVERED, Stage.CLOSED):
        return []
    out = [f for f, p in _FLAG_POOL if rng.random() < p]
    if stage in (Stage.BOOKED, Stage.DOCS_PENDING, Stage.GREEN_LIGHT) and not out:
        if rng.random() < 0.35:
            out.append("in_progress")
    return out


def _label(name: str) -> str:
    """Every demo customer is visibly a demo customer, even pasted into a mail."""
    return f"{name} (demo)"


def tms_shipments(n: int = N_TMS, today: dt.date | None = None,
                  rng: random.Random | None = None) -> list[Shipment]:
    """Simulated Moveware (TMS) cross-border files: US → Mexico."""
    today = today or dt.date.today()
    rng = rng or random.Random(SEED)
    out: list[Shipment] = []
    for i in range(n):
        job = 500100 + i * 7
        stage = rng.choices(_TMS_STAGES, weights=_TMS_WEIGHTS, k=1)[0]
        dest = rng.choice(_MX_DESTS)
        service = rng.choices(_TMS_SERVICES, weights=[7, 2, 3], k=1)[0]
        if service == "FTL":
            m3 = round(rng.uniform(60, 86), 1)
        else:
            m3 = round(rng.uniform(4, 34), 1)
        uplift = today + dt.timedelta(days=rng.randint(-25, 14))
        delivery = uplift + dt.timedelta(days=rng.randint(9, 34))
        flags = _flags(rng, stage)
        if delivery < today + dt.timedelta(days=6) and stage not in (Stage.DELIVERED,):
            flags.append("window_risk")
        s = Shipment(
            id=f"TMS:DEMO-{job}",
            source=Source.TMS,
            source_ref=str(job),
            reference_number=f"DEMO-{job}",
            customer_name=_label(_person(rng)),
            agent=rng.choice(_AGENTS),
            origin=rng.choice(_US_ORIGINS),
            destination=dest,
            destination_hub=hub_for_destination(dest),
            volume_m3=m3,
            weight=round(m3 * rng.uniform(95, 135)),
            stage=stage,
            source_status={"W": "Won"}.get("W", "Won"),
            ready_date=uplift,
            delivery_date=delivery,
            status_flags=sorted(set(flags)),
            updated_at=dt.datetime.combine(today - dt.timedelta(days=rng.randint(0, 9)),
                                           dt.time(9, 30)),
            url="",
            assignees=[],
            milestones={"booked": uplift - dt.timedelta(days=rng.randint(6, 30)),
                        "uplift": uplift if uplift <= today else None,
                        "delivered": delivery if stage is Stage.DELIVERED else None},
            extra={"demo": True, "direction": "import", "method": "ROAD",
                   "service": service, "destination_country": "MX",
                   "demo_note": "simulated record — not a real Moveware job"},
        )
        s.milestones = {k: v for k, v in s.milestones.items() if v}
        out.append(s)
    return out


def trs_shipments(n: int = N_TRS, today: dt.date | None = None,
                  rng: random.Random | None = None) -> list[Shipment]:
    """Simulated TRS (SIT / Plan de Viajes) domestic Mexico moves."""
    today = today or dt.date.today()
    rng = rng or random.Random(SEED + 1)
    out: list[Shipment] = []
    for i in range(n):
        ref = 160000 + i * 3
        o_code, o_city = rng.choice(_TRS_BRANCHES)
        d_code, d_city = rng.choice(_TRS_BRANCHES)
        while d_code == o_code:
            d_code, d_city = rng.choice(_TRS_BRANCHES)
        stage = rng.choices(_TRS_STAGES, weights=_TRS_WEIGHTS, k=1)[0]
        tipo = rng.choice(_TRS_TYPES)
        unit = rng.choice(_TRS_UNITS)
        cap = 100.0 if unit.startswith("T") else 85.0
        m3 = round(rng.uniform(6, cap * 0.92), 1)
        load_day = today + dt.timedelta(days=rng.randint(-18, 12))
        unload_day = load_day + dt.timedelta(days=rng.randint(1, 5))
        flags = _flags(rng, stage)
        s = Shipment(
            id=f"TRS:DEMO-{ref}",
            source=Source.TRS,
            source_ref=str(ref),
            reference_number=f"DEMO-{ref}",
            customer_name=_label(_person(rng)),
            agent=f"SIT {o_code}",
            origin=o_city,
            destination=d_city,
            destination_hub=hub_for_destination(d_city),
            volume_m3=m3,
            weight=round(m3 * rng.uniform(90, 125)),
            stage=stage,
            source_status=tipo,
            ready_date=load_day,
            delivery_date=unload_day,
            status_flags=sorted(set(flags)),
            updated_at=dt.datetime.combine(today - dt.timedelta(days=rng.randint(0, 6)),
                                           dt.time(11, 0)),
            url="",
            assignees=[],
            milestones={"booked": load_day - dt.timedelta(days=rng.randint(2, 14)),
                        "uplift": load_day if load_day <= today else None,
                        "delivered": unload_day if stage is Stage.DELIVERED else None},
            extra={"demo": True, "direction": "domestic", "method": "ROAD",
                   "service": tipo, "destination_country": "MX",
                   "plaza_origen": o_code, "plaza_destino": d_code,
                   "unidad": unit, "unidad_m3": cap,
                   "operador": f"{rng.choice(_FIRST)} {rng.choice(_LAST)}",
                   "dia_carga": load_day.isoformat(),
                   "dia_descarga": unload_day.isoformat(),
                   "demo_note": "simulated record — not a real SIT trip"},
        )
        s.milestones = {k: v for k, v in s.milestones.items() if v}
        out.append(s)
    return out


def demo_shipments(today: dt.date | None = None, n_tms: int | None = None,
                   n_trs: int | None = None) -> list[Shipment]:
    """The whole simulated fleet: TMS cross-border + TRS domestic."""
    today = today or dt.date.today()
    return (tms_shipments(N_TMS if n_tms is None else n_tms, today)
            + trs_shipments(N_TRS if n_trs is None else n_trs, today))


def diagnostics(ships: list[Shipment]) -> dict:
    return {
        "demo": True,
        "tms": sum(1 for s in ships if s.source is Source.TMS),
        "trs": sum(1 for s in ships if s.source is Source.TRS),
        "count": len(ships),
        "seed": SEED,
        "note": ("Simulated data for demonstration. Not from ClickUp, Moveware or "
                 "SIT. Alert and load-email drafts are disabled while it is on."),
    }
