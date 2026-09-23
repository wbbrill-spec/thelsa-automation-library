"""
notices.py — "these shipments travel together. Please confirm."

The feature Bill asked for in the consolidation meeting, 23 Sep (D32):

    "We need the ability to have a check mark box, like include in
     consolidation, and you just check it, and then this would trigger the
     alerts to Sara, to TRS… so the system can trigger an email to operations
     and everybody involved that this is what we're going to do, and then
     that'll help us build the scorecard, so we know if we're doing better
     or not."

Two jobs in one, and they are worth separating:

  1. **Tell the people who have to act.** Today a consolidation lives in
     somebody's head until they write a note or make a call. Sara cannot act on
     a truck she has not been told about, and TRS cannot hold space it has not
     been asked for.

  2. **Leave a record.** Until now the scoreboard could only count what the
     planner suggested and what a coordinator happened to write in a ClickUp
     note. A confirmed pick is the team saying "yes, this one" — which is the
     only honest measure of whether the board is changing any decisions.

What this deliberately does NOT do: send anything. It drafts, a person reads
it, a person sends it. That is the standing rule for this programme, and a
consolidation notice is exactly the kind of message that is embarrassing to
get wrong — it asks another company to hold space on a truck.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import uuid

from . import alerts, metrics
from .models import TRUCK_53_M3

__all__ = ["build", "record", "recent", "reset", "recipients"]

_LOCK = threading.Lock()
MAX_KEPT = int(os.environ.get("CB_NOTICES_MAX", "500") or 500)


def recipients() -> list[str]:
    """Who hears about a consolidation. Overridable without a deploy.

    Sara and Fernanda because they own the freight; Gustavo because he asked
    for the roll-up. TRS is addressed by name in the body rather than the
    header until somebody gives us Iram's address — writing a guessed address
    into a draft is how a draft gets sent to the wrong company.
    """
    raw = (os.environ.get("CB_NOTICE_TO") or "").replace(";", ",")
    listed = [x.strip() for x in raw.split(",") if x.strip()]
    if listed:
        return listed
    return [alerts.KNOWN_EMAILS["fernanda mora"],
            alerts.KNOWN_EMAILS["sara reyes"],
            alerts.KNOWN_EMAILS["gustavo"]]


def _fmt_date(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else (str(d) if d else "")


def _line(s: dict, lang: str) -> str:
    bits = [f"  • {s.get('customer') or '?'}"]
    ref = s.get("reference") or s.get("source_ref")
    if ref:
        bits.append(f"({s.get('source', '')} {ref})")
    vol = s.get("m3")
    if vol:
        bits.append(f"— {vol} m³")
    if s.get("lift_vans"):
        bits.append(f", {s['lift_vans']} " + ("lift vans" if lang == "en" else "huacales"))
    elif s.get("u_boxes"):
        bits.append(f", {s['u_boxes']} U-Box")
    dest = s.get("destination")
    if dest:
        bits.append(("→ " if lang == "en" else "→ ") + str(dest))
    when = s.get("delivery_date")
    if when:
        bits.append(("(delivery " if lang == "en" else "(entrega ") + _fmt_date(when) + ")")
    return " ".join(bits)


def summarise(picked: list[dict], lane: str = "") -> dict:
    """The arithmetic behind the notice — and behind the scorecard entry."""
    m3 = round(sum(float(s.get("m3") or 0) for s in picked), 1)
    lift = sum(int(s.get("lift_vans") or 0) for s in picked)
    ubox = sum(int(s.get("u_boxes") or 0) for s in picked)
    fill = round(m3 / TRUCK_53_M3 * 100) if TRUCK_53_M3 else 0
    saving = metrics.consolidation_saving(len(picked), m3, lane)
    return {"files": len(picked), "m3": m3, "lift_vans": lift, "u_boxes": ubox,
            "fill_pct": min(fill, 999), "spare_m3": round(max(0.0, TRUCK_53_M3 - m3), 1),
            "lane": lane, "saving": saving}


def build(picked: list[dict], lane: str = "", *, actor: str = "", note: str = "") -> dict:
    """A drafted notice in both languages. Nothing is sent."""
    s = summarise(picked, lane)
    where = lane or "—"
    subject_en = (f"Consolidation for confirmation — {where} — "
                  f"{s['files']} shipments, {s['m3']} m³")
    subject_es = (f"Consolidación para confirmar — {where} — "
                  f"{s['files']} embarques, {s['m3']} m³")

    def body(lang: str) -> str:
        en = lang == "en"
        head = ("We are putting these shipments on one truck. Please confirm from your side."
                if en else
                "Vamos a poner estos embarques en un mismo camión. Por favor confirmen de su lado.")
        lines = [head, ""]
        lines.append(("Lane: " if en else "Ruta: ") + where)
        lines.append("")
        lines.extend(_line(x, lang) for x in picked)
        lines.append("")
        cap = (f"Total {s['m3']} m³ — about {s['fill_pct']}% of a 53 ft trailer, "
               f"{s['spare_m3']} m³ still free."
               if en else
               f"Total {s['m3']} m³ — cerca del {s['fill_pct']}% de un tráiler de 53', "
               f"quedan {s['spare_m3']} m³ libres.")
        lines.append(cap)
        if s["saving"]:
            sv = s["saving"]
            lines.append(
                (f"Moving these separately would cost about {sv['alone_mxn']:,} MXN at the "
                 f"TRS shared-freight rate; together it is about {sv['together_mxn']:,} MXN."
                 if en else
                 f"Moverlos por separado costaría alrededor de {sv['alone_mxn']:,} MXN a la "
                 f"tarifa de flete compartido de TRS; juntos son alrededor de "
                 f"{sv['together_mxn']:,} MXN.").replace(",", ","))
        if note:
            lines += ["", note]
        lines += ["",
                  ("What we need: TRS to hold the space, and each coordinator to confirm "
                   "their file is ready for that departure."
                   if en else
                   "Lo que necesitamos: que TRS reserve el espacio y que cada coordinador "
                   "confirme que su expediente está listo para esa salida."),
                  "",
                  ("Sent from the cross-border board. Reply to this message if anything is wrong."
                   if en else
                   "Enviado desde el tablero transfronterizo. Respondan a este mensaje si algo no cuadra.")]
        return "\n".join(lines)

    return {
        "to": recipients(),
        "subject": f"{subject_en} / {subject_es}",
        "subject_en": subject_en, "subject_es": subject_es,
        "body_en": body("en"), "body_es": body("es"),
        "body": body("en") + "\n\n— — —\n\n" + body("es"),
        "summary": s,
        "shipment_ids": [x.get("id") for x in picked if x.get("id")],
        "actor": actor,
        "drafted_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "sent": False,
        "note": "Drafted for a person to review and send. Nothing has been sent.",
    }


# ── the record ───────────────────────────────────────────────────────────────
def _path() -> str:
    override = (os.environ.get("CB_NOTICES_PATH") or "").strip()
    if override:
        return override
    for c in ("/var/data", "/data"):
        if os.path.isdir(c) and os.access(c, os.W_OK):
            return os.path.join(c, "crossborder_notices.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "crossborder_notices.json")


def _read() -> list:
    try:
        with open(_path()) as fh:
            return json.load(fh) or []
    except Exception:  # noqa: BLE001
        return []


def record(draft: dict) -> dict:
    """Keep the pick. This is what makes the scorecard about decisions rather
    than about suggestions nobody acted on."""
    row = {
        "id": uuid.uuid4().hex[:12],
        "at": draft.get("drafted_at") or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "day": dt.date.today().isoformat(),
        "actor": draft.get("actor") or "",
        "lane": (draft.get("summary") or {}).get("lane") or "",
        "shipment_ids": draft.get("shipment_ids") or [],
        "files": (draft.get("summary") or {}).get("files") or 0,
        "m3": (draft.get("summary") or {}).get("m3") or 0,
        "fill_pct": (draft.get("summary") or {}).get("fill_pct") or 0,
        "saved_mxn": ((draft.get("summary") or {}).get("saving") or {}).get("saved_mxn") or 0,
    }
    with _LOCK:
        rows = (_read() + [row])[-MAX_KEPT:]
        try:
            p = _path()
            with open(p + ".tmp", "w") as fh:
                json.dump(rows, fh)
            os.replace(p + ".tmp", p)
        except Exception:  # noqa: BLE001 — a record is a nicety, never load-bearing
            pass
    return row


def recent(limit: int = 50, days: int = 30) -> dict:
    rows = _read()
    if days:
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        rows = [r for r in rows if (r.get("day") or "") >= cutoff]
    rows = rows[-max(1, limit):]
    return {
        "count": len(rows),
        "files": sum(int(r.get("files") or 0) for r in rows),
        "saved_mxn": sum(int(r.get("saved_mxn") or 0) for r in rows),
        "notices": list(reversed(rows)),
    }


def reset() -> None:
    with _LOCK:
        try:
            os.remove(_path())
        except OSError:
            pass
