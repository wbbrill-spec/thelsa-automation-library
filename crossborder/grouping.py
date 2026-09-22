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

import difflib
import os
import re
import unicodedata
from typing import Optional

__all__ = ["parse_note", "note_texts", "apply_note", "group_key", "resolve_groups",
           "SUGGESTED_FORMAT", "MAX_NAMES", "NAME_THRESHOLD"]

SUGGESTED_FORMAT = "[CONSOLIDADO] grupo: <nombre> | con: <cliente>, <cliente> | tercero: <transportista>"

# How many customers one note may name. Fernanda's first live note (22 Sep)
# named seven; a consolidated import runs to five or six customers and
# occasionally more, so this is set well clear of real practice.
MAX_NAMES = int(os.environ.get("CB_GROUP_MAX_NAMES", "12") or 12)

# How close a name in the note has to be to a customer on the board.
# Deliberately strict: putting the wrong file on a truck is worse than
# leaving a name unmatched and saying so.
NAME_THRESHOLD = float(os.environ.get("CB_GROUP_NAME_THRESHOLD", "0.84") or 0.84)

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
    # Fernanda's first real note (22 Sep) named SEVEN other customers on one
    # import. A cap of six silently dropped the last one, which is the worst
    # possible failure here — a file quietly left off a truck it is on.
    return uniq[:MAX_NAMES]


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


# ── turning the names in a note into real files on the board ────────────────
# How Fernanda actually works (her first live note, 22 Sep 2026):
#
#     on "Ana Saldivar - GoArmstrong - …", step 5:
#     "importación junto con:
#      jorge loredo, sandra guadalupe, carol ann ashworth, christian latino,
#      orlando perez, daniel moya, giovanna bartel"
#
# She writes the note ONCE, on one file, and names the others. The other seven
# files carry nothing. So a group is not "the shipments that have a note" — it
# is one note's file plus everyone it names, resolved against the board. Those
# seven must come off the suggestion list too, or the planner will cheerfully
# propose a second truck for freight that is already on the first.


def _match_name(name: str, pool: list, exclude_ids: set) -> tuple:
    """(shipment, score) for the closest customer on the board, or (None, score)."""
    target = _fold(name)
    if not target:
        return None, 0.0
    best, score = None, 0.0
    for s in pool:
        if s.id in exclude_ids:
            continue
        cand = _fold(getattr(s, "customer_name", ""))
        if not cand:
            continue
        r = difflib.SequenceMatcher(None, target, cand).ratio()
        # "jorge loredo" against "Jorge Loredo Martinez" is the same person;
        # a containment hit counts as a strong match even though the ratio dips.
        if target in cand or cand in target:
            r = max(r, 0.95)
        if r > score:
            best, score = s, r
    return (best, score) if score >= NAME_THRESHOLD else (None, score)


def _label(s) -> str:
    """How a file is named on the board: 'TMS - 110719' / 'TIM - 121722'."""
    src = getattr(getattr(s, "source", None), "value", "") or "?"
    ref = str(getattr(s, "reference_number", "") or "").strip() or str(getattr(s, "source_ref", "") or "").strip()
    return f"{src} - {ref}" if ref else f"{src} - {getattr(s, 'customer_name', '?')}"


def resolve_groups(shipments: list) -> dict:
    """Link every consolidation note to the files it names. Mutates shipments.

    Returns diagnostics: how many notes were read, how many files ended up in a
    group, and — importantly — which names could NOT be matched to anything on
    the board, so an unmatched customer is a visible fact rather than a silent
    omission.
    """
    ships = [s for s in shipments or [] if getattr(s, "is_open", True)]
    by_id = {s.id: s for s in ships}
    carriers = [s for s in ships if s.consolidation and s.consolidation.get("grouped")]
    diag = {"notes": len(carriers), "groups": 0, "members": 0,
            "matched": 0, "unmatched": [], "linked": 0}
    if not carriers:
        return diag

    # 1. Each note gives a set of files: its own, plus everyone it names.
    sets: list[dict] = []
    for s in carriers:
        c = s.consolidation
        members = {s.id}
        named: list[dict] = []
        for name in c.get("with") or []:
            hit, score = _match_name(name, ships, {s.id})
            if hit is not None:
                members.add(hit.id)
                named.append({"name": name, "id": hit.id, "score": round(score, 2)})
                diag["matched"] += 1
            else:
                named.append({"name": name, "id": None, "score": round(score, 2)})
                diag["unmatched"].append(f"{name} (best {score:.2f}, note on {s.customer_name})")
        sets.append({"carrier": s.id, "members": members, "named": named,
                     "note": c.get("note", ""), "third_party": c.get("third_party", ""),
                     "label": c.get("group", ""),
                     "do_not_consolidate": bool(c.get("do_not_consolidate"))})

    # 2. Two notes that name each other are ONE truck, not two. Merge any sets
    #    that share a file.
    merged: list[dict] = []
    for cur in sets:
        hit = next((m for m in merged if m["members"] & cur["members"]), None)
        if hit is None:
            merged.append({**cur, "carriers": [cur["carrier"]], "members": set(cur["members"]),
                           "named": list(cur["named"])})
            continue
        hit["members"] |= cur["members"]
        hit["carriers"].append(cur["carrier"])
        hit["named"].extend(cur["named"])
        hit["note"] = hit["note"] or cur["note"]
        hit["third_party"] = hit["third_party"] or cur["third_party"]
        hit["label"] = hit["label"] or cur["label"]
        hit["do_not_consolidate"] = hit["do_not_consolidate"] or cur["do_not_consolidate"]

    # 3. Write the group onto every file in it, including the ones that were
    #    only named by somebody else's note.
    for n, g in enumerate(merged, 1):
        members = [by_id[i] for i in g["members"] if i in by_id]
        if len(members) < 2 and not g["third_party"]:
            # A note naming nobody we can find is still a note — keep it on its
            # own file so the board shows it, but it is not a truck yet.
            pass
        gid = g["label"] or f"G{n}"
        roster = [{"id": m.id, "source": m.source.value, "customer": m.customer_name,
                   "reference": m.reference_number, "label": _label(m),
                   "carrier": m.id in g["carriers"]} for m in members]
        for m in members:
            others = [r["label"] for r in roster if r["id"] != m.id]
            m.extra = dict(m.extra or {})
            m.extra["consolidation_group"] = {
                "id": gid, "members": roster, "consolidated_with": others,
                "note": g["note"], "third_party": g["third_party"],
                "named": g["named"], "carriers": g["carriers"],
            }
            if not m.consolidation:
                # Named by someone else's note. Say so plainly — whose note it
                # was matters when a coordinator wants to check.
                who = ", ".join(by_id[c].customer_name for c in g["carriers"] if c in by_id)
                m.extra["consolidation"] = {
                    "grouped": True,
                    "do_not_consolidate": g["do_not_consolidate"],
                    "group": gid, "with": [], "third_party": g["third_party"],
                    "note": g["note"], "source": f"named in {who}'s note",
                    "why": f"named in {who}'s consolidation note",
                }
                if "grouped" not in (m.status_flags or []):
                    m.status_flags = list(m.status_flags or []) + ["grouped"]
                diag["linked"] += 1
            else:
                m.extra["consolidation"] = {**m.consolidation, "group": gid}
        diag["members"] += len(members)
    diag["groups"] = len(merged)
    diag["unmatched"] = diag["unmatched"][:20]
    return diag


def group_key(shipment) -> str:
    """The key that puts two grouped shipments on the same truck card.

    A group resolved against the board wins outright — that is the real one.
    Otherwise a written group label, and failing that the names in the note:
    "consolidado con Ana Ruiz" on Ana's file and on Luis's file should be one
    truck, so the key is the sorted set of everyone involved.
    """
    g = (shipment.extra or {}).get("consolidation_group")
    if isinstance(g, dict) and g.get("id"):
        return f"group:{_fold(g['id'])}"
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
