"""
rules.py — the operational rules the team gave us, in one place.

Everything here came out of the consolidation training (21 Sep 2026, Fernanda
Mora and Sara Reyes) and the meeting with Edgar the next morning. They are
business decisions, not engineering ones, so they live together where a
non-engineer can be shown exactly what the dashboard does and why, and each one
has an environment variable that reverses it without a code change.

Two different kinds of rule:

  board exclusions      freight that has no business on this dashboard at all —
                        it is somebody else's process end to end.
  consolidation blocks  freight that belongs on the board, but must never be
                        offered as something to put on a shared truck.

The difference matters. Getting the first wrong hides work from the team;
getting the second wrong merely means the planner leaves a file alone.
"""
from __future__ import annotations

import os
import re
import unicodedata

__all__ = ["exclude_us_diplomatic", "commercial_patterns", "is_commercial",
           "board_exclusions", "consolidation_block", "EXCLUSION_LABELS"]


def _fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


def _on(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


EXCLUSION_LABELS = {
    "us_diplomatic": "US Embassy / Consulate — dedicated sealed trailer, own broker",
    "commercial": "commercial / new-furniture freight — separate import, own broker",
}


# ── board exclusion 1: US Embassy and Consulate (Edgar, 2026-09-22) ──────────
def exclude_us_diplomatic(shipments):
    """Drop US Embassy / US Consulate shipments from the board.

    The Embassy's tender pays for a dedicated 53 ft trailer that is sealed at
    the warehouse and photographed, it decides itself which of its lift vans
    travel together, and it uses its own customs broker. There is nothing for
    this dashboard to plan, so the files only added noise to the board, the
    load planner, the alerts and the revenue view.

    Detection is Shipment.is_us_diplomatic (a mission word AND a US marker, on
    the corporate account or the bill-to). CROSSBORDER_SHOW_DIPLOMATIC=1 brings
    them back. Returns (kept, number removed).
    """
    if _on("CROSSBORDER_SHOW_DIPLOMATIC"):
        return list(shipments), 0
    kept = [s for s in shipments if not s.is_us_diplomatic]
    return kept, len(shipments) - len(kept)


# ── board exclusion 2: commercial / new furniture (Edgar, 2026-09-22) ────────
# Gustavo's commercial sales and Rafael Larsa's new-furniture process are a
# different import with a different customs broker, and they are never
# consolidated with household goods. Uso is building their ClickUp step-by-step
# separately; when it lands, whatever names those lists go in CB_COMMERCIAL_PATTERNS
# and nothing else has to change.
DEFAULT_COMMERCIAL_PATTERNS = [
    "comercial", "commercial", "mueble nuevo", "muebles nuevos", "new furniture", "larsa",
]


def commercial_patterns() -> list[str]:
    """Patterns that mark a file as commercial freight, accent-folded.

    CB_COMMERCIAL_PATTERNS replaces the defaults outright (comma-separated);
    CB_COMMERCIAL_PATTERNS_EXTRA adds to them. Setting CB_COMMERCIAL_PATTERNS
    to a single "-" disables the rule.
    """
    raw = (os.environ.get("CB_COMMERCIAL_PATTERNS") or "").strip()
    if raw == "-":
        return []
    base = [_fold(x) for x in raw.replace(";", ",").split(",") if x.strip()] if raw \
        else list(DEFAULT_COMMERCIAL_PATTERNS)
    extra = (os.environ.get("CB_COMMERCIAL_PATTERNS_EXTRA") or "").replace(";", ",")
    return base + [_fold(x) for x in extra.split(",") if x.strip()]


def _commercial_haystack(s) -> str:
    extra = s.extra or {}
    return _fold(" | ".join(str(x or "") for x in (
        s.agent, s.corporate_account, s.reference_number,
        extra.get("folder"), extra.get("space"), extra.get("list_name"),
        extra.get("service"), extra.get("service_description"), extra.get("job_type"),
    )))


def is_commercial(s) -> bool:
    """Commercial sales / new-furniture freight rather than household goods."""
    pats = commercial_patterns()
    if not pats:
        return False
    hay = _commercial_haystack(s)
    return any(p and p in hay for p in pats)


def board_exclusions(shipments):
    """Apply every board-level exclusion. Returns (kept, report).

    `report` names what went and why, per rule and in total, so a sudden drop
    in the shipment count is explainable on the page instead of looking like
    data loss. CROSSBORDER_SHOW_ALL=1 keeps everything (diagnostics still say
    what WOULD have been dropped).
    """
    ships = list(shipments or [])
    report: dict = {"removed": 0, "by_rule": {}, "examples": {}}
    show_all = _on("CROSSBORDER_SHOW_ALL")

    def _drop(name: str, test):
        nonlocal ships
        hit = [s for s in ships if test(s)]
        if not hit:
            return
        report["by_rule"][name] = len(hit)
        report["examples"][name] = [f"{s.source.value}:{s.customer_name}" for s in hit[:5]]
        if not show_all:
            ships = [s for s in ships if not test(s)]
            report["removed"] += len(hit)

    if not _on("CROSSBORDER_SHOW_DIPLOMATIC"):
        _drop("us_diplomatic", lambda s: s.is_us_diplomatic)
    _drop("commercial", is_commercial)
    if show_all:
        report["show_all"] = True
    return ships, report


# ── consolidation blocks: on the board, but never offered a shared truck ─────
def consolidation_block(s) -> str:
    """Why this shipment must not be offered for consolidation, or "".

    Order matters: the most specific human instruction wins, so a coordinator's
    own note outranks a rule we inferred from the file's shape.
    """
    if s.do_not_consolidate:
        c = s.consolidation
        why = c.get("why") or "marked to ship on its own"
        return f"coordinator's note: {why}"
    if s.is_grouped:
        name = s.group_name or "an existing group"
        return f"already consolidated ({name}) — see the grouped-loads panel"
    if s.is_door_to_door and not _on("CB_CONSOLIDATE_DTD"):
        return ("door-to-door loose-loaded move — ships direct (Fernanda, 22 Sep: "
                "about 8 m³ is most of a truck, so there is no point making it wait)")
    return ""
