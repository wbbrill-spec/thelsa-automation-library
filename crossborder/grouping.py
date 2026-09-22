"""
grouping.py — read Fernanda's consolidation note off a ClickUp shipment.

Why this exists (decision D6, meeting with Edgar 2026-09-22)
-----------------------------------------------------------
When Fernanda puts several files on one truck, the dashboard must stop
suggesting them — they are already consolidated — and instead show that truck
as something OTHER coordinators can add freight to. Sara's problem today is
that she cannot see a truck Fernanda is filling, so she books her own.

The mechanism the team chose is the one they already use: Fernanda writes a
short note on the customer's ClickUp list (its description, or a comment). This
module turns that free text into a fact the engine can plan with. It reads;
it never writes to ClickUp.

What it understands
-------------------
Anything a coordinator would actually type, in Spanish or English:

    "consolidado con Ana Ruiz y Pedro Lara"
    "va junto con la carga de Ruiz - sale el 24"
    "CONSOLIDADO grupo MTY-CDMX 24SEP"
    "urgente, no consolidar - sale directo"
    "se va con tercero (Gran Casa)"
    "[CONSOLIDADO] grupo: MTY-CDMX-24SEP | con: Ana Ruiz | tercero: Gran Casa"

The last form is the one to teach in the working session — it parses exactly
and reads well to a human — but none of the others are rejected. A note that
says nothing about consolidation returns None, so an ordinary comment about
anything else changes nothing.

Ambiguity is resolved towards DOING LESS: a note that both groups and says
"no consolidar" is treated as do-not-consolidate, because the cost of wrongly
holding freight off a truck is a phone call, and the cost of wrongly putting it
on one is a truck that leaves without it.
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional

__all__ = ["parse_note", "note_texts", "apply_note", "group_key", "SUGGESTED_FORMAT"]

SUGGESTED_FORMAT = "[CONSOLIDADO] grupo: <nombre> | con: <cliente>, <cliente> | tercero: <transportista>"

# Carriers the team names when freight moves with somebody else (training,
# 21 Sep). CB_THIRD_PARTY_CARRIERS adds to the list without a code change.
DEFAULT_CARRIERS = ["gran casa", "tren logistico", "autotransportes modelos", "trs", "pedro"]

_GROUPED = re.compile(
    r"consolidad|consolidated|"
    r"\bgrupo\b|\bgroup\b|"
    r"\b(?:va|van|viaja|viajan|sale|salen|se va|se van)\s+(?:junto|juntos|junta|juntas)?\s*con\b|"
    r"\bjunto\s+con\b|\bmismo\s+(?:camion|trailer|traler|truck|viaje)\b|\bsame\s+truck\b")
_BLOCKED = re.compile(
    r"\bno\s+consolidar\b|\bsin\s+consolidar\b|\bdo\s+not\s+consolidate\b|\bdon'?t\s+consolidate\b|"
    r"\burgente\b|\burgent\b|\bsale\s+sol[oa]\b|\bva\s+sol[oa]\b|\bships?\s+alone\b|\bdirecto\b|\bdirect\b")
_THIRD_PARTY = re.compile(r"\btercer[oa]s?\b|\bthird\s*party\b|\bexterno\b|\botro\s+transportista\b")

# "grupo: X", "grupo X", "consolidado #X", "group: X"
_GROUP_LABEL = re.compile(
    r"(?:grupo|group|consolidacion|consolidation)\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9 ./_-]{1,40})")
# "\bcon\b" and not "\bcon" — without the closing boundary this matches the
# "con" inside "consolidado" and reads the group name as "solidado con Ana".
_WITH = re.compile(r"\bcon\b\s*:?\s*([^|;\n]{2,120})", re.I)
# An explicit consolidation word, as opposed to a bare "va con …".
_EXPLICIT = re.compile(r"consolidad|consolidated|\bgrupo\b|\bgroup\b|\bmismo\s+(?:camion|trailer|traler|truck|viaje)\b")
_TERCERO_NAMED = re.compile(r"(?:tercero|third\s*party|transportista)\s*:?\s*([^|;,\n]{2,60})", re.I)

# Words that end a "con ..." customer list — "con Ana Ruiz el martes" must not
# swallow the date, and "con tercero" is a carrier, not a customer.
_STOP_WORDS = {"el", "la", "los", "las", "en", "para", "que", "y", "e", "de", "del", "al",
               "tercero", "terceros", "third", "party", "porque", "pero", "ya", "se",
               "sale", "salen", "va", "van", "fecha", "hoy", "manana", "lunes", "martes",
               "miercoles", "jueves", "viernes", "sabado", "domingo", "this", "the", "on"}
_MONTHS = {"enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre", "sep", "oct", "nov", "dic",
           "ene", "feb", "mar", "abr", "jun", "jul", "ago"}


def _fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


def _carriers() -> list[str]:
    extra = (os.environ.get("CB_THIRD_PARTY_CARRIERS") or "").replace(";", ",")
    return DEFAULT_CARRIERS + [_fold(x) for x in extra.split(",") if x.strip()]


def _names(chunk: str) -> list[str]:
    """Pull customer names out of 'Ana Ruiz y Pedro Lara el martes'."""
    out: list[str] = []
    for part in re.split(r"\s*(?:,|/|\+|\by\b|\band\b|\be\b)\s*", chunk.strip()):
        words: list[str] = []
        for w in part.split():
            token = _fold(w).strip(".:;-")
            if not token or token in _STOP_WORDS or token in _MONTHS or token.isdigit():
                break
            words.append(w.strip(".,;:"))
            if len(words) >= 4:
                break
        name = " ".join(words).strip()
        if len(name) >= 3 and name.lower() not in ("la carga", "carga"):
            out.append(name)
    # de-duplicate, keep order
    seen, uniq = set(), []
    for n in out:
        if _fold(n) not in seen:
            seen.add(_fold(n))
            uniq.append(n)
    return uniq[:6]


def parse_note(text: Optional[str]) -> Optional[dict]:
    """A consolidation note → facts, or None when the text is about something else.

    Returns {"grouped", "do_not_consolidate", "group", "with", "third_party",
             "note", "why"}.
    """
    raw = str(text or "").strip()
    if not raw:
        return None
    flat = _fold(raw)
    grouped = bool(_GROUPED.search(flat))
    blocked = bool(_BLOCKED.search(flat))
    third = bool(_THIRD_PARTY.search(flat))
    carrier = next((c for c in _carriers() if re.search(rf"\b{re.escape(c)}\b", flat)), "")
    if not (grouped or blocked or third or carrier):
        return None

    label = ""
    m = _GROUP_LABEL.search(raw)
    if m:
        label = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
        # "grupo con Ana" is not a label, it is the start of a with-list.
        if _fold(label).split()[:1] == ["con"]:
            label = ""

    with_names: list[str] = []
    for w in _WITH.finditer(raw):
        chunk = w.group(1)
        if re.match(r"\s*(?:tercero|third)", _fold(chunk)):
            continue
        with_names.extend(_names(chunk))

    third_party = ""
    tm = _TERCERO_NAMED.search(raw)
    if tm:
        cand = tm.group(1).strip(" .:-()")
        if cand and _fold(cand) not in ("", "s"):
            third_party = cand
    if not third_party and carrier:
        third_party = carrier.title()
    if third and not third_party:
        third_party = "third party (not named)"

    # "se va con tercero" and "se va con Gran Casa" both match the "va con …"
    # shape, but neither is one of OUR consolidations — the freight is on
    # somebody else's truck. Only an explicit consolidation word makes it a
    # group we can add to.
    if third_party and not _EXPLICIT.search(flat):
        grouped = False

    why = []
    if blocked:
        why.append("note says it ships on its own / urgent / with another carrier")
    elif grouped:
        why.append("note says it is consolidated with other files")
    if third_party:
        why.append(f"moving with {third_party}")

    return {
        "grouped": bool(grouped and not blocked),
        # Freight already travelling with a third party is not ours to plan
        # either, even when the note also says it was consolidated.
        "do_not_consolidate": bool(blocked or third_party),
        "group": label,
        "with": with_names,
        "third_party": third_party,
        "note": raw[:400],
        "why": "; ".join(why),
    }


def note_texts(list_payload: dict, comments: Optional[list[dict]] = None,
               tasks: Optional[list[dict]] = None) -> list[str]:
    """Every place a coordinator might have written the note, newest first.

    The list description is read always (it costs nothing — it arrives with the
    list). Comments are read when `comments` is supplied; `tim.py` decides
    whether spending a request per list on them is worth it.
    """
    out: list[str] = []
    for c in comments or []:
        txt = c.get("comment_text") or c.get("text") or ""
        if isinstance(c.get("comment"), list):      # ClickUp's rich-text form
            txt = txt or "".join(str(b.get("text") or "") for b in c["comment"])
        if txt:
            out.append(str(txt))
    for key in ("content", "description"):
        v = (list_payload or {}).get(key)
        if v:
            out.append(str(v))
    for t in tasks or []:
        # A note left on the checklist step itself, rather than on the list.
        for key in ("description", "text_content"):
            v = t.get(key)
            if v and _GROUPED.search(_fold(v)):
                out.append(str(v))
    return out


def apply_note(shipment, texts: list[str], source: str = "clickup") -> Optional[dict]:
    """Parse the first text that says something, and hang it on the shipment."""
    for text in texts or []:
        parsed = parse_note(text)
        if parsed:
            parsed["source"] = source
            shipment.extra = dict(shipment.extra or {})
            shipment.extra["consolidation"] = parsed
            flag = "grouped" if parsed["grouped"] else "ships_alone"
            if flag not in (shipment.status_flags or []):
                shipment.status_flags = list(shipment.status_flags or []) + [flag]
            return parsed
    return None


def group_key(shipment) -> str:
    """The key that puts two grouped shipments on the same truck card.

    A written group label wins. Without one, the names in the note tie the
    files together: "consolidado con Ana Ruiz" on Ana's file and on Luis's file
    should be one truck, so the key is the sorted set of everyone involved.
    """
    c = shipment.consolidation
    if not c:
        return ""
    if c.get("group"):
        return f"label:{_fold(c['group'])}"
    names = {_fold(shipment.customer_name)} | {_fold(n) for n in c.get("with") or []}
    names = {n for n in names if n}
    if not names:
        return ""
    return "names:" + "|".join(sorted(names))
