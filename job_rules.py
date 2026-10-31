"""
job_rules.py — which key dates a file is EXPECTED to carry, by MoveWare job type.

Agreed in the MoveWare diary-action training of 16 Oct 2026 (Jose Rivas with
Lupita, Sara, Stephanie, Elizabeth) and confirmed by Bill the same day:

  * agent export (EXA): Thelsa's part ends at origin, so NO Delivery date is
    expected (Delivery is deliberately not auto-created on EXA);
  * agent import (IMA): the agent packs at origin, so NO Pack date is expected
    (Lupita: "IMA ... no nos interesa cuando se empaca").

Every completeness measure (missing-date counts, Plan de Viajes vs MoveWare, the
diary write-back) uses these two functions, so a file is never counted as
"missing" a date that is not supposed to exist. Same environment settings as the
FAIM audit (faim_web.AGENT_EXPORT_TYPES / AGENT_IMPORT_TYPES), so both agree.

In MoveWare a diary action is identified by its KEY ACTION, not its code: Jose
showed several Pack actions (different codes, some children of Uplift at −1/−2/−3
days) that all carry key action "Pack". Match on `keyaction` first.
"""
from __future__ import annotations

import os


def _codes(name: str, default: list) -> set:
    vals = [x.strip().upper() for x in os.environ.get(name, "").split(",") if x.strip()]
    return set(vals or default)


AGENT_EXPORT_TYPES = _codes("FAIM_AGENT_EXPORT_TYPES", ["EXA"])   # no delivery date due
AGENT_IMPORT_TYPES = _codes("FAIM_AGENT_IMPORT_TYPES", ["IMA"])   # no pack date due

KEY_ACTIONS = {"pack": "PACK", "delivery": "DELIVERY", "load": "UPLIFT"}


def _base(job_type) -> str:
    """'EXA', 'EXA1', 'exa 3' → 'EXA' (job-type combos share the service code)."""
    t = str(job_type or "").strip().upper().replace(" ", "")
    return t[:3] if len(t) > 3 and t[:3].isalpha() and t[3:].isdigit() else t


def pack_expected(job_type) -> bool:
    return _base(job_type) not in AGENT_IMPORT_TYPES


def delivery_expected(job_type) -> bool:
    return _base(job_type) not in AGENT_EXPORT_TYPES


def expected(milestone: str, job_type) -> bool:
    # Load follows the Pack rule: on an agent import (IMA) the agent loads at
    # origin, so neither is Thelsa's date to record.
    if milestone in ("pack", "load"):
        return pack_expected(job_type)
    if milestone == "delivery":
        return delivery_expected(job_type)
    return True


def is_milestone(activity: dict, milestone: str) -> bool:
    """True when a MoveWare activity is the given milestone, by key action
    (falling back to the code when the key action isn't returned)."""
    want = KEY_ACTIONS.get(milestone, milestone.upper())
    ka = str(activity.get("keyaction") or activity.get("keyAction") or "").strip().upper()
    if ka:
        return ka == want
    code = str(activity.get("type") or "").strip().upper()
    return code == want or (milestone == "pack" and code.startswith("PACK") and code[4:].isdigit()) \
        or (milestone == "load" and code.startswith("UPLIFT"))


def confirmed_dates(activities) -> dict:
    """{'pack': 'YYYY-MM-DD' | None, 'delivery': …, 'load': …, 'has_load': bool}
    from activities marked COMPLETED only. A date that is merely planned is not
    a fact. The LOAD date is the one that counts (Bill, 16 Oct 2026)."""
    out = {"pack": None, "delivery": None, "load": None}
    for m in out:
        days = sorted(str(a.get("date") or "")[:10] for a in activities or []
                      if is_milestone(a, m) and str(a.get("completed") or "").upper() == "Y"
                      and a.get("date"))
        if days:
            out[m] = days[0] if m in ("pack", "load") else days[-1]
    out["has_load"] = any(is_milestone(a, "load") for a in activities or [])
    return out


# ── When a file may be INVOICED (Bill, 30 Oct 2026) ─────────────────────────────
#   * inbound — delivered somewhere in Mexico (IMP, IMA): invoice when it DELIVERS;
#   * outbound (EXP, EXA) and domestic (DIN): invoice when it LOADS (uplift/pack);
#   * US Embassy and consulates: only when the move is COMPLETED (delivered) —
#     applied by the callers, who know whether a file is an embassy file;
#   * any other type (EXT, STG…): whichever of load or delivery comes first.
INVOICE_ON_DELIVERY_TYPES = _codes("INVOICE_ON_DELIVERY_TYPES", ["IMP", "IMA"])
INVOICE_ON_LOAD_TYPES = _codes("INVOICE_ON_LOAD_TYPES", ["EXP", "EXA", "DIN"])


def invoice_trigger(job_type) -> str:
    """'delivery', 'load' or 'either' — the move milestone that makes a file billable."""
    t = _base(job_type)
    if t in INVOICE_ON_DELIVERY_TYPES:
        return "delivery"
    if t in INVOICE_ON_LOAD_TYPES:
        return "load"
    return "either"


def invoice_due_date(job_type, load_date, delivery_date, embassy: bool = False):
    """The date from which a file is billable (None if that milestone has no date)."""
    if embassy:
        return delivery_date
    trig = invoice_trigger(job_type)
    if trig == "delivery":
        return delivery_date
    if trig == "load":
        return load_date
    return min([d for d in (load_date, delivery_date) if d], default=None)
