"""
invoice_mail.py — Finance's invoicing reports, read from email.

Wendy (Administradora UNI) emails "REPORTE FACTURACION <month>" with the month's
invoices (FACT UNI <MES>.xlsx: Cía, Tipo, Fecha, Serie, Folio, Expediente, ...)
to the invoicing team, with Edgar, Guillermo and Mario in copy. Lupita forwarded
October's to Bill on 3 Nov 2026 after the "ready to invoice" alerts listed files
that were already billed (Edgar: "110940 has been invoiced already. Who is to
update that?"). Nobody should have to: once a day the app looks in the mailboxes
it can read (people who connected their mailbox), opens each report, and every
file it lists stops being flagged.

Read-only. Only .xlsx attachments on emails whose subject names the invoicing
report are opened; nothing is moved, sent or changed. The parsed invoices are
kept in the shared store under "invoice_reports_mail", one entry per distinct
attachment (forwards of the same file count once).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import threading
import time

import requests

import ms_graph
import survey_finder as sf

KEY = "invoice_reports_mail"
SUBJECT_WORDS = ("REPORTE FACTURACION", "REPORTE DE FACTURACION")
LOOKBACK_DAYS = 120
_STATE = {"running": False, "finished": None, "error": None, "diag": None}
_LOCK = threading.Lock()


def _subject_ok(s: str) -> bool:
    t = sf._norm(s or "").upper()
    return any(w in t for w in SUBJECT_WORDS)


def _messages(mbx: str, h: dict, url: str, diag: dict) -> list:
    params = {"$search": '"REPORTE FACTURACION"', "$top": "25",
              "$select": "id,subject,from,receivedDateTime,hasAttachments"}
    try:
        r = requests.get(url, headers=h, params=params, timeout=25)
    except Exception as e:
        diag["errors"].append(f"{mbx}: {e}"[:160])
        return []
    if r.status_code != 200:
        diag["errors"].append(f"{mbx}: {r.status_code}")
        return []
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    return [m for m in r.json().get("value") or []
            if m.get("hasAttachments") and _subject_ok(m.get("subject"))
            and (m.get("receivedDateTime") or "") >= since[:19]]


def _xlsx_attachments(url: str, msg_id: str, h: dict) -> list:
    """[(name, bytes)] of the Excel files on one email."""
    out = []
    try:
        r = requests.get(f"{url}/{msg_id}/attachments", headers=h,
                         params={"$select": "id,name,contentType,size"}, timeout=25)
        if r.status_code != 200:
            return out
        for a in r.json().get("value") or []:
            name = a.get("name") or ""
            if not name.lower().endswith((".xlsx", ".xlsm")) or (a.get("size") or 0) > 15_000_000:
                continue
            g = requests.get(f"{url}/{msg_id}/attachments/{a['id']}", headers=h, timeout=40)
            if g.status_code == 200 and g.json().get("contentBytes"):
                out.append((name, base64.b64decode(g.json()["contentBytes"])))
    except Exception:
        pass
    return out


def parse(data: bytes):
    """(invoices, info) from one report, or None if it isn't an invoice report."""
    from commissions import invoices as I
    try:
        return I.load(io.BytesIO(data))
    except Exception:
        return None


def refresh() -> dict:
    """Look for new invoicing reports in every connected mailbox and keep them."""
    from commissions import invoices as I
    from commissions import store
    diag = {"mailboxes": [], "messages": 0, "files": 0, "new": 0, "errors": []}
    with _LOCK:
        _STATE.update(running=True, error=None, diag=diag)
    try:
        doc, _ = store.get(KEY)
        doc = doc or {"reports": {}}
        seen = doc.setdefault("reports", {})
        delegated = sf.delegated_mailboxes()
        tok = ms_graph._get_token()
        app_h = {"Authorization": f"Bearer {tok}", "ConsistencyLevel": "eventual"} if tok else None
        sdiag = {"first_error": None}
        for mbx in sorted(set(delegated) | set(sf.MAILBOXES)):
            h, url = sf._auth_for(mbx, app_h, delegated, sdiag)
            if not h:
                continue
            diag["mailboxes"].append(mbx)
            for m in _messages(mbx, h, url, diag):
                diag["messages"] += 1
                for name, data in _xlsx_attachments(url, m["id"], h):
                    diag["files"] += 1
                    sha = hashlib.sha1(data).hexdigest()
                    if sha in seen:
                        continue
                    got = parse(data)
                    if not got:
                        continue
                    invs, info = got
                    seen[sha] = {"name": name[:120], "received": (m.get("receivedDateTime") or "")[:10],
                                 "subject": (m.get("subject") or "")[:140], "mailbox": mbx,
                                 "from": info.get("from"), "to": info.get("to"),
                                 "records": I.to_records(invs, info)}
                    diag["new"] += 1
        doc["checked"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        store.put(KEY, doc, by="invoice_mail")
        try:
            import commission_feed
            commission_feed.clear_cache()
        except Exception:
            pass
    except Exception as e:
        with _LOCK:
            _STATE["error"] = str(e)[:200]
    finally:
        with _LOCK:
            _STATE.update(running=False, finished=time.time())
    return diag


def start_if_due(hours: float = 20) -> bool:
    with _LOCK:
        if _STATE["running"] or (_STATE["finished"] and time.time() - _STATE["finished"] < hours * 3600):
            return False
        _STATE["running"] = True
    threading.Thread(target=refresh, daemon=True, name="invoice-mail").start()
    return True


def state() -> dict:
    with _LOCK:
        return dict(_STATE)


def invoices() -> list:
    """Every invoice in the reports found so far (forwards counted once)."""
    from commissions import invoices as I
    from commissions import store
    try:
        doc, _ = store.get(KEY)
    except Exception:
        return []
    out = []
    for rep in ((doc or {}).get("reports") or {}).values():
        try:
            out += I.from_records(rep.get("records") or {})[0]
        except Exception:
            continue
    return out
