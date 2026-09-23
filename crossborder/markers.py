"""
markers.py — the facts the team agreed to write down in words.

Three things the board could not see before the 23 Sep consolidation meeting,
all of which the team agreed to record as free text rather than as new fields
in either system. Free text is what they will actually do, so the reader has to
be generous: Spanish and English, with and without accents, in a ClickUp
comment or a Moveware crew note.

  port of entry   D16/D17. Policy is now McAllen for everything, so TIM files
                  default to McAllen and TMS coordinators write
                  "port of entry: McAllen" (or Laredo) in the crew notes.
                  Recording it is what lets us measure the switch rather than
                  assume it happened.

  door to door    D22. The previous rule inferred this from the ClickUp
                  checklist length and was simply wrong (the finished import
                  template is 13 steps, not 17). It was removed, leaving the
                  board unable to identify a door-to-door move at all.
                  Fernanda agreed to write it in the comments instead.

  load type       D22. Loose-loaded, lift vans or U-Boxes. A 15 m³ shipment
                  could be any of them, and the packing maths differs: 13 lift
                  vans or 10 U-Boxes to a 53 ft trailer. Her defaults, for
                  reference rather than for guessing: exports around 15 m³ are
                  usually U-Boxes, imports are rarely loose-loaded, and
                  door-to-door moves are mostly loose because they are large.

Nothing here infers. If the words are absent the answer is "unknown", and the
board says so — that is the lesson from the door-to-door rule.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .models import norm_text

__all__ = ["port_of_entry", "door_to_door", "load_type", "read_markers", "apply_markers",
           "PORTS", "DEFAULT_TIM_PORT"]

# The two crossings in play. Reynosa is the Mexican side of the McAllen
# crossing, so a coordinator writing either means the same trailer; Nuevo
# Laredo likewise for Laredo. Keeping the aliases means we do not lose a file
# because somebody wrote the city on the other side of the river.
PORTS: dict[str, tuple[str, ...]] = {
    "McAllen": ("mcallen", "mc allen", "macallen", "hidalgo", "pharr", "reynosa"),
    "Laredo": ("laredo", "nuevo laredo", "colombia"),
}

DEFAULT_TIM_PORT = "McAllen"

# "port of entry: McAllen", "puerto de entrada - Laredo", "cruce por McAllen",
# "crossing via Laredo". The label is optional: a bare "por Laredo" counts too,
# which is how people actually write it.
_PORT_LABEL = re.compile(
    r"(?:port\s*of\s*entry|puerto\s*de\s*entrada|aduana|cruce|crossing|cruza(?:mos)?|"
    r"clear(?:ing|ance)?|despacho)\s*(?:de|por|at|via|en|through|:|-|=)*\s*([a-z ]{3,20})")
_PORT_BARE = re.compile(r"\b(?:por|via|v[ií]a|through|at)\s+([a-z ]{3,20})")

_DTD = re.compile(r"\bdtd\b|door\s*[-to ]*\s*door|puerta\s*a\s*puerta|casa\s*a\s*casa")
# "not door to door", "no es puerta a puerta" — cheap insurance against reading
# a negation as a positive.
_NOT_DTD = re.compile(r"\b(?:no|not|isn'?t|sin)\b[^.;|\n]{0,20}"
                      r"(?:dtd\b|door\s*[-to ]*\s*door|puerta\s*a\s*puerta)")

_LOAD_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ubox", r"\bu[\s-]*box(?:es)?\b|\bubox(?:es)?\b"),
    ("liftvan", r"\blift\s*van(?:s)?\b|\bliftvan(?:s)?\b|\blift\s*ban(?:s)?\b|"
                r"\bhuacal(?:es)?\b|\bduela(?:s)?\b"),
    ("loose", r"\bloose\s*load(?:ed)?\b|\bloose\b|\bsuelta?\b|\ba\s*granel\b|"
              r"\bcarga\s*suelta\b"),
)


def _match_port(word: str) -> str:
    w = norm_text(word)
    for canonical, aliases in PORTS.items():
        for a in aliases:
            if a in w:
                return canonical
    return ""


def port_of_entry(*texts: str) -> str:
    """The crossing named in this text, canonicalised. "" when nobody said."""
    for raw in texts:
        t = norm_text(raw)
        if not t:
            continue
        for m in _PORT_LABEL.finditer(t):
            hit = _match_port(m.group(1))
            if hit:
                return hit
        for m in _PORT_BARE.finditer(t):
            hit = _match_port(m.group(1))
            if hit:
                return hit
        # Last resort: the bare city name anywhere in a short note. Only for
        # short texts, so a long consolidation note mentioning a customer in
        # Laredo does not reassign the file.
        if len(t) <= 120:
            hit = _match_port(t)
            if hit:
                return hit
    return ""


def door_to_door(*texts: str) -> Optional[bool]:
    """True / False when the text says so, None when it does not mention it."""
    for raw in texts:
        t = norm_text(raw)
        if not t:
            continue
        if _NOT_DTD.search(t):
            return False
        if _DTD.search(t):
            return True
    return None


def load_type(*texts: str) -> str:
    """"ubox" / "liftvan" / "loose" / "". First match wins, most specific first."""
    for raw in texts:
        t = norm_text(raw)
        if not t:
            continue
        for name, pattern in _LOAD_PATTERNS:
            if re.search(pattern, t):
                return name
    return ""


def read_markers(texts: Iterable[str]) -> dict:
    """Everything this pile of free text tells us. Absent keys mean unknown."""
    items = [t for t in (texts or []) if t]
    out: dict = {}
    port = port_of_entry(*items)
    if port:
        out["port_of_entry"] = port
        out["port_source"] = "written on the file"
    dtd = door_to_door(*items)
    if dtd is not None:
        out["door_to_door"] = dtd
    lt = load_type(*items)
    if lt:
        out["load_type_marked"] = lt
    return out


def apply_markers(shipment, texts: Iterable[str]) -> dict:
    """Merge what the text says into `shipment.extra`, without clobbering."""
    found = read_markers(texts)
    if not found:
        return {}
    extra = shipment.extra if isinstance(shipment.extra, dict) else {}
    for k, v in found.items():
        extra[k] = v
    shipment.extra = extra
    return found
