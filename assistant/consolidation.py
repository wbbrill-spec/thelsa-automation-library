"""
Consolidation reminder for the AI Assistant (Bill, 9 Nov 2026):

  "coordinators are constantly reminded to check the consolidation dashboard
   and find opportunities for cargo consolidation" — Elizabeth, Sara, Fernanda,
   Lupita, Mario and Gustavo.

One to-do a day, on the dashboards of the people listed under
people.consolidation in the company's settings (assistant/tenants/<id>.json).
It is built from the SAME plan the consolidation dashboard shows
(crossborder.engine.plan over the cached TIM + TMS shipments), so the numbers
match the page: trailers running light, the space left in them, and the
shipments coming on the same lane that could ride along. With nothing to
combine it is still there, lower down: the habit is the point.

A new to-do each day (its id carries the date), so marking it done clears it
for today only. Read-only: it never books, emails or changes a shipment.
"""
from __future__ import annotations

import datetime as _dt

from . import db, todo

PRODUCT = "consolidation"
LINK = "https://thelsa.inflectionpointnow.com/crossborder"


def _cfg() -> dict:
    return todo.settings().get("consolidation") or {}


def enabled() -> bool:
    return bool(_cfg().get("enabled")) and bool(todo.people("consolidation"))


def _today() -> _dt.date:
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("America/Mexico_City")).date()
    except Exception:
        return _dt.date.today()


def item_from_plan(p: dict, today: _dt.date) -> dict:
    """The day's reminder, worded from the dashboard's own plan."""
    opps = [o for o in (p.get("opportunities") or [])]
    light = int((p.get("summary") or {}).get("light") or 0)
    spare = round(sum(float(o.get("spare_m3") or 0) for o in opps))
    riders = sum(len(o.get("coming") or []) for o in opps)
    lanes = [str(o.get("lane") or "") for o in opps if o.get("lane")][:3]
    if opps:
        title = {"en": f"Consolidation: {len(opps)} trailer(s) running light — {spare} m³ free",
                 "es": f"Consolidación: {len(opps)} tráiler(es) con espacio — {spare} m³ libres"}
        lane_txt = ", ".join(lanes)
        detail = {
            "en": (f"The consolidation dashboard shows {len(opps)} trailer(s) under the fill target"
                   + (f" ({lane_txt})" if lane_txt else "")
                   + (f"; {riders} shipment(s) coming on the same lanes could ride along" if riders else "")
                   + ". Open it, check each lane and combine loads where you can."),
            "es": (f"El tablero de consolidación muestra {len(opps)} tráiler(es) debajo del llenado objetivo"
                   + (f" ({lane_txt})" if lane_txt else "")
                   + (f"; {riders} embarque(s) en las mismas rutas podrían viajar juntos" if riders else "")
                   + ". Ábrelo, revisa cada ruta y consolida cargas donde se pueda."),
        }
        urgency = 74 if riders else 66
    else:
        title = {"en": "Check the consolidation dashboard for loads to combine",
                 "es": "Revisa el tablero de consolidación para combinar cargas"}
        detail = {
            "en": (f"{int(p.get('ready') or 0)} shipment(s) ready, {int(p.get('coming') or 0)} coming"
                   + (f", {light} light trailer(s)" if light else "")
                   + ". Look for any trailer you can fill before it leaves."),
            "es": (f"{int(p.get('ready') or 0)} embarque(s) listos, {int(p.get('coming') or 0)} por llegar"
                   + (f", {light} tráiler(es) con espacio" if light else "")
                   + ". Busca cualquier tráiler que puedas llenar antes de que salga."),
        }
        urgency = 48
    return {"id": f"consolidate:{today.isoformat()}", "kind": "consolidate",
            "owner_email": "", "team": todo.people("consolidation"),
            "title": title, "detail": detail, "link": LINK,
            "since": f"{today.isoformat()}T12:00:00+00:00", "due": today.isoformat(),
            "urgency": urgency, "sticky": False, "customer": "", "file": ""}


def build():
    """(items, complete). Incomplete while the shipment cache is still loading."""
    from crossborder import engine as cb_engine
    from crossborder import web as cb_web
    shipments, _diag, _status = cb_web.load_shipments()
    if not shipments:
        return [], False
    today = _today()
    return [item_from_plan(cb_engine.plan(shipments, today), today)], True


def refresh() -> str:
    if not enabled():
        return "off"
    try:
        raw, complete = build()
    except Exception as exc:                      # noqa: BLE001
        db.save_feed(PRODUCT, items=None, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return "error"
    if not complete:
        # Shipments not loaded yet: still remind, from the last good plan or plainly.
        prev, _at = db.get_feed(PRODUCT)
        today = _today().isoformat()
        if not prev or not any(str(i.get("id", "")).endswith(today) for i in prev):
            fallback = item_from_plan({}, _today())
            parsed = todo.parse_feed(PRODUCT, todo.feed_payload(PRODUCT, [fallback], True))
            db.save_feed(PRODUCT, items=parsed["items"], ok=False,
                         error="shipments still loading; plain reminder")
        return "shipments still loading"
    parsed = todo.parse_feed(PRODUCT, todo.feed_payload(PRODUCT, raw, True))
    db.save_feed(PRODUCT, items=parsed["items"])
    return f"{len(parsed['items'])} to-do"


def items_for(emails) -> list | None:
    if not enabled():
        return None
    items, _at = db.get_feed(PRODUCT)
    if items is None:
        return None
    return todo.for_user(items, emails)
