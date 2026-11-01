"""
AuditTrack's findings as to-dos (same app, so read in-process: no request).

One list per run, built from EXACTLY the calculations AuditTrack's own daily
emails use (audit_web.run_daily_coordinator_alerts), so the dashboard and the
emails can never disagree:

  invoice_ready   booked move whose invoice is due (job-type rules, US Embassy
                  files only after delivery), not issued in Moveware nor in
                  Finance's report. Replaces the Assistant's older invoice rule.
  cost_check      revenue / cost on a file do not reconcile
  price_check     Moveware sale price differs from the booked price (Removals
                  Summary) — fix the price in Moveware
  open_invoice    an invoice Finance issued that the payments reports do not
                  show as paid, older than open_invoice_min_days
  supplier_check  Move Costing Sheet findings (cost twice, over quote, no
                  approval number, supplier invoice missing 60+ days)
  plan_review     Moveware and SIT's Plan de Viajes disagree on a Load /
                  Delivery date a person entered — the automation never
                  overwrites a person, so someone must decide

Owners: the file's coordinator, only at their own address (todo.resolve_owner);
otherwise the company's people for that role (billing, procurement,
collections), otherwise the key users. Read-only: nothing here writes anywhere.
A half-read file book never replaces the saved list.
"""
from __future__ import annotations

import datetime as _dt
import hashlib

from . import db, todo

PRODUCT = "auditrack"


def _cfg() -> dict:
    return todo.settings().get("auditrack") or {}


def enabled() -> bool:
    return bool(_cfg().get("enabled"))


def _money(v, ccy="") -> str:
    try:
        return f"{float(v or 0):,.0f}" + (f" {ccy}" if ccy else "")
    except (TypeError, ValueError):
        return str(v)


def _h(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


def _owner(name, email, directory):
    return todo.resolve_owner(name or "", email or "", directory)


# ── Builders (pure: take data, return items) ──────────────────────────────────
def invoice_items(worklist, directory, invoice_to="") -> list:
    out = []
    for r in worklist or []:
        job = str(r.get("job") or "")
        if not job:
            continue
        ccy = r.get("orig_currency") or r.get("currency") or ""
        val = r.get("orig_value") if r.get("orig_value") is not None else r.get("value")
        when = r.get("delivery") if r.get("embassy") else (r.get("pack") or r.get("delivery"))
        days = None
        try:
            days = (_dt.date.today() - _dt.date.fromisoformat(str(when)[:10])).days if when else None
        except ValueError:
            days = None
        draft_ready = bool(r.get("draft"))
        es_when = ("entregada" if r.get("embassy") else "empacada/cargada")
        en_when = ("delivered" if r.get("embassy") else "packed/loaded")
        out.append({
            "id": f"inv:{job}", "kind": "invoice_ready", "role": "coordinator",
            "owner_name": r.get("coordinator") or "", "file": job,
            "owner_email": _owner(r.get("coordinator"), r.get("coordinator_email"), directory),
            "customer": r.get("client") or "",
            "title": {"en": f"Invoice file {job} — {r.get('client') or ''}".strip(" —"),
                      "es": f"Facturar expediente {job} — {r.get('client') or ''}".strip(" —")},
            "detail": {
                "en": (f"Move {en_when} {when or ''}" + (f" ({days} days ago)" if days is not None else "")
                       + f"; not invoiced. {_money(val, ccy)} to bill."
                       + (" A draft invoice is ready in Moveware — issue it." if draft_ready else "")
                       + (" US Embassy/Consulate: billed after delivery." if r.get("embassy") else "")),
                "es": (f"Mudanza {es_when} el {when or ''}" + (f" (hace {days} días)" if days is not None else "")
                       + f"; sin facturar. {_money(val, ccy)} por facturar."
                       + (" Hay una factura en borrador en Moveware: emítela." if draft_ready else "")
                       + (" Embajada/Consulado de EE. UU.: se factura tras la entrega." if r.get("embassy") else ""))},
            "since": f"{when}T12:00:00+00:00" if when else None,
            "urgency": min(95, 62 + min((days or 0) * 2, 30)), "sticky": True,
            "draft": {"channel": "email", "to": invoice_to, "language": "es",
                      "purpose": "ask billing to issue the invoice for this move",
                      "facts": {"file": job, "customer": r.get("client") or "", "amount": val,
                                "currency": ccy, "move_date": when, "embassy": bool(r.get("embassy")),
                                "draft_invoice_ready_in_moveware": draft_ready}} if invoice_to else None,
        })
    return out


def cost_items(files, directory) -> list:
    out = []
    for f in files or []:
        if not (f.get("disc_value", 0) > 0) or f.get("stage") == "closed":
            continue
        job = str(f.get("job") or "")
        ccy = f.get("currency") or ""
        out.append({
            "id": f"cost:{job}", "kind": "cost_check", "role": "coordinator", "file": job,
            "owner_name": f.get("coordinator") or "",
            "owner_email": _owner(f.get("coordinator"), f.get("coordinator_email"), directory),
            "customer": f.get("client") or "",
            "title": {"en": f"Check revenue / cost on file {job}",
                      "es": f"Revisar ingreso / costo del expediente {job}"},
            "detail": {"en": f"AuditTrack found {_money(f.get('disc_value'), ccy)} that does not reconcile "
                             "between the quote, the invoices and the cost lines. Correct it in Moveware.",
                       "es": f"AuditTrack encontró {_money(f.get('disc_value'), ccy)} que no cuadra entre la "
                             "cotización, las facturas y los costos. Corrígelo en Moveware."},
            "urgency": 58, "sticky": False,
        })
    return out


def price_items(rows, directory) -> list:
    out = []
    for r in rows or []:
        job = str(r.get("job") or "")
        ccy = r.get("currency") or ""
        out.append({
            "id": f"price:{job}", "kind": "price_check", "role": "coordinator", "file": job,
            "owner_name": r.get("coordinator") or "",
            "owner_email": _owner(r.get("coordinator"), "", directory),
            "title": {"en": f"Moveware price differs from the booked price — {job}",
                      "es": f"El precio en Moveware no coincide con el precio reservado — {job}"},
            "detail": {"en": f"Moveware {_money(r.get('moveware'), ccy)} vs booked {_money(r.get('booked'), ccy)} "
                             f"(difference {_money(r.get('difference'), ccy)}). Correct whichever is wrong.",
                       "es": f"Moveware {_money(r.get('moveware'), ccy)} vs reservado {_money(r.get('booked'), ccy)} "
                             f"(diferencia {_money(r.get('difference'), ccy)}). Corrige el que esté mal."},
            "urgency": 55, "sticky": False,
        })
    return out


def open_invoice_items(rows, file_owner, min_days=30) -> list:
    """file_owner: {base file: (name, email)} from the file book."""
    out = []
    for r in rows or []:
        days = int(r.get("days") or 0)
        if days < min_days:
            continue
        num = f"{r.get('series') or ''}{r.get('folio') or ''}"
        jobs = [str(j) for j in (r.get("jobs") or [])]
        job = jobs[0] if jobs else ""
        name, email = file_owner.get(todo.base_file(job), ("", "")) if job else ("", "")
        ccy = r.get("currency") or ""
        paid = r.get("paid_share")
        try:
            paid_txt = f"{float(paid) * 100:.0f}%" if paid is not None else "0%"
        except (TypeError, ValueError):
            paid_txt = "?"
        out.append({
            "id": f"openinv:{r.get('company') or ''}:{num}", "kind": "open_invoice",
            "role": "collections", "file": job, "owner_name": name, "owner_email": email,
            "title": {"en": f"Invoice {num} still unpaid ({days} days)",
                      "es": f"Factura {num} sin pagar ({days} días)"},
            "detail": {"en": f"Issued {r.get('date') or ''} for {_money(r.get('total'), ccy)}; paid so far {paid_txt}."
                             + (f" File(s) {', '.join(jobs)}." if jobs else ""),
                       "es": f"Emitida el {r.get('date') or ''} por {_money(r.get('total'), ccy)}; pagado {paid_txt}."
                             + (f" Expediente(s) {', '.join(jobs)}." if jobs else "")},
            "since": f"{r.get('date')}T12:00:00+00:00" if r.get("date") else None,
            "urgency": min(85, 50 + days // 3), "sticky": False,
            "draft": {"channel": "email", "to": "", "language": "es",
                      "purpose": "polite payment reminder to the customer for this invoice",
                      "facts": {"invoice": num, "invoice_date": r.get("date") or "", "amount": r.get("total"),
                                "currency": ccy, "paid_share": paid_txt, "days_open": days,
                                "files": ", ".join(jobs)}},
        })
    return out


_SUP_URG = {"duplicate_line": 70, "po_on_two_files": 68, "over_quote": 60, "invoice_missing": 55,
            "no_approval": 50}


def supplier_items(rows, labels) -> list:
    out = []
    for r in rows or []:
        chk = r.get("check") or ""
        job = str(r.get("job") or "")
        lab = labels.get(chk) or (chk, chk)
        extra_en = extra_es = ""
        if chk == "over_quote":
            extra_en = f" Quoted {_money(r.get('quoted'))}, actual {_money(r.get('actual'))}."
            extra_es = f" Cotizado {_money(r.get('quoted'))}, real {_money(r.get('actual'))}."
        elif chk == "invoice_missing":
            extra_en = f" Delivered {r.get('delivered') or ''} ({r.get('days')} days)."
            extra_es = f" Entregado {r.get('delivered') or ''} ({r.get('days')} días)."
        elif chk == "po_on_two_files":
            extra_en = f" Approval {r.get('po')} also on {', '.join(r.get('other_files') or [])}."
            extra_es = f" Aprobación {r.get('po')} también en {', '.join(r.get('other_files') or [])}."
        line = r.get("line") or ""
        cred = r.get("creditor") or ""
        out.append({
            "id": f"sup:{chk}:{job}:{_h(line, cred, r.get('po'), r.get('actual'))}",
            "kind": "supplier_check", "role": "procurement", "file": job,
            "title": {"en": f"{lab[0]} — {job}", "es": f"{lab[1]} — {job}"},
            "detail": {"en": f"{line}" + (f" · {cred}" if cred else "") + extra_en,
                       "es": f"{line}" + (f" · {cred}" if cred else "") + extra_es},
            "urgency": _SUP_URG.get(chk, 50), "sticky": False,
            "draft": ({"channel": "email", "to": "", "language": "es",
                       "purpose": "ask the supplier for their invoice for this service",
                       "facts": {"file": job, "service": line, "supplier": cred,
                                 "quoted_amount": r.get("quoted"), "delivered": r.get("delivered") or ""}}
                      if chk == "invoice_missing" else None),
        })
    return out


def plan_items(steps, directory) -> list:
    out = []
    for s in steps or []:
        if s.get("action") != "review":
            continue
        job = str(s.get("job") or "")
        ms = s.get("milestone") or ""
        label_en = "Load" if ms == "pack" else "Delivery"
        label_es = "Carga" if ms == "pack" else "Entrega"
        out.append({
            "id": f"plan:{job}:{ms}:{s.get('activity_id') or ''}", "kind": "plan_review",
            "role": "coordinator", "file": job, "owner_name": s.get("coordinator") or "",
            "owner_email": _owner(s.get("coordinator"), "", directory),
            "title": {"en": f"{label_en} date: Moveware and Plan de Viajes disagree — {job}",
                      "es": f"Fecha de {label_es.lower()}: Moveware y el Plan de Viajes no coinciden — {job}"},
            "detail": {"en": f"Moveware {s.get('mw_date') or '—'}, Plan de Viajes {s.get('plan_date') or '—'}. "
                             f"{s.get('why') or ''} Check which is right and correct Moveware.",
                       "es": f"Moveware {s.get('mw_date') or '—'}, Plan de Viajes {s.get('plan_date') or '—'}. "
                             "Revisa cuál es la correcta y corrige Moveware."},
            "urgency": 60, "sticky": True,
        })
    return out


# ── Gathering (reads AuditTrack's state) ──────────────────────────────────────
def build() -> tuple:
    """(items, complete). complete=False → keep the last good list."""
    import mw_live
    import coordinator_alerts as ca
    if not mw_live.have_creds():
        return [], False
    prog = mw_live.audit_progress() or {}
    complete = bool(prog.get("window_complete") or prog.get("complete"))
    audited = [m for m in (mw_live.audited_files() or []) if m.get("job")]
    if not audited:
        return [], False
    directory = todo.people_directory(audited)
    cfg = _cfg()
    items = []
    file_owner = {}
    for f in audited:
        nm = f.get("mw_coordinator") or f.get("coordinator") or ""
        em = todo.resolve_owner(nm, f.get("coordinator_email") or "", directory)
        if nm or em:
            file_owner.setdefault(todo.base_file(f.get("job")), (nm, em))

    import audit_web
    recent = [f for f in audited if ca.recent_booked(f)]
    files = audit_web.reconcile(recent, cost_available=False)
    audit_web.check_calculations(files)
    m = audit_web.compute_metrics(files, live_counts=None, cost_available=False)
    invoice_to = ((todo.settings().get("drafts") or {}).get("invoice_request_to") or "").strip()
    items += invoice_items(m.get("to_invoice_worklist") or [], directory, invoice_to)
    if cfg.get("cost_checks", True):
        items += cost_items(files, directory)

    raw = mw_live.audited_in_window() or []
    try:
        import exec_dash
        import sit_milestones as sm
        raw = exec_dash.overlay_plan_dates(raw, sm.state().get("jobs"))
    except Exception:
        pass
    try:
        import commission_feed as cf
        if cfg.get("price_checks", True):
            items += price_items(cf.price_differences(raw), directory)
        items += open_invoice_items((cf.open_invoices() or {}).get("open") or [], file_owner,
                                    int(cfg.get("open_invoice_min_days", 30)))
    except Exception:
        pass
    if cfg.get("supplier_checks", True):
        try:
            v = audit_web._supplier_checks_view(raw)
            items += supplier_items(v.get("rows") or [], v.get("labels") or {})
        except Exception:
            pass
    if cfg.get("plan_reviews", True):
        try:
            items += plan_items(audit_web._MS_PLAN.get("steps") or [], directory)
        except Exception:
            pass
    return items, complete


def refresh() -> str:
    """Build once per run and save (only a complete read replaces the list)."""
    if not enabled():
        return "off"
    try:
        raw, complete = build()
    except Exception as exc:                      # noqa: BLE001
        db.save_feed(PRODUCT, items=None, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return "error"
    parsed = todo.parse_feed(PRODUCT, todo.feed_payload(PRODUCT, raw, complete))
    if complete:
        db.save_feed(PRODUCT, items=parsed["items"], ok=True)
        return f"{len(parsed['items'])} to-dos"
    db.save_feed(PRODUCT, items=None, ok=False,
                 error="the file book is still being read; kept the last complete list")
    return "partial"


def items_for(emails, directory=None) -> list | None:
    """This user's AuditTrack to-dos, or None if AuditTrack has never been read."""
    if not enabled():
        return None
    items, _at = db.get_feed(PRODUCT)
    if items is None:
        return None
    return todo.for_user(items, emails, directory)
