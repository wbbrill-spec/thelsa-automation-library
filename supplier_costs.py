"""
supplier_costs.py — find supplier costs in TMS email and queue them to post to MoveWare.

Two kinds of cost arrive by email:

  1. SUPPLIER INVOICES — Mexican suppliers send a CFDI (the SAT e-invoice XML, often
     with a PDF). parse_cfdi() reads supplier, RFC, series/folio, UUID, date,
     currency, exchange rate, subtotal, taxes and total, and finds the MoveWare file
     number in the concepts, the subject or the file name.
  2. EXTRA COSTS QUOTED IN EMAIL — an agent, carrier or supplier tells the coordinator
     about additional charges ("Demoras: 2,040 USD + 15% finance fee", "Almacenajes:
     $3,690", "additional storage USD 450"). parse_extra_costs() pulls each line:
     concept, amount, currency, and any percentage fee on top.

Every hit becomes a queue row keyed by MoveWare file. MoveWare has NO write endpoint
for cost yet (MoveConnect 2026-09-17: option charges are sell-only; request with Dave),
so the queue is "costs to post": the page lists them for Lupita's team, and once
MoveWare opens a cost endpoint, post_to_moveware() is the single place to switch on.

Parsing is conservative, like underbilling.py: an amount must carry a currency token
or sit on a line with a cost keyword, only the top of each message is read (quoted
history is skipped, so a thread doesn't repeat the same cost), and file numbers must
look like TMS files (6 digits starting 10/11, optional sequel letter).

Mailboxes: the 12 TMS coordinators (ms_graph.TMS_COORDINATORS). Credential and
graceful no-op behaviour are exactly as ms_graph / underbilling.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET

_FILE_RE = re.compile(r"(?<![\d/])(1[01]\d{4})([A-Z])?(?![\d])")
_CCY_TOKEN = r"(?:USD|US\$|U\.S\.D\.|DLS|MXN|M\.N\.|MN|EUR|CAD|\$|€)"
_NUM = r"[0-9]{1,3}(?:[.,][0-9]{3})*(?:[.,][0-9]{1,2})?|[0-9]+(?:[.,][0-9]{1,2})?"
_AMOUNT_RE = re.compile(
    rf"(?P<c1>{_CCY_TOKEN})\s*(?P<a1>{_NUM})(?:\s*(?P<c1b>USD|MXN|EUR|CAD|DLS))?"
    rf"|(?P<a2>{_NUM})\s*(?P<c2>USD|US\$|DLS|MXN|M\.N\.|EUR|CAD)\b",
    re.IGNORECASE)
_FEE_RE = re.compile(r"\+\s*(\d{1,2}(?:[.,]\d+)?)\s*%\s*([a-záéíóúñ ]{0,30})", re.IGNORECASE)

# What a cost line is about. Spanish first — most supplier mail is in Spanish.
COST_KEYWORDS = {
    "demurrage": ("demora", "demoras", "demurrage", "estadía", "estadia", "detention", "detencion", "detención"),
    "storage": ("almacenaje", "almacenajes", "almacenamiento", "storage", "bodegaje"),
    "roll / rebooking": ("roll de buque", "rolado", "rollover", "roll over", "re-booking", "rebooking"),
    "inspection": ("inspección", "inspeccion", "inspection", "revisión aduanal", "reconocimiento aduanero"),
    "customs": ("despacho aduanal", "honorarios aduanales", "customs clearance", "impuestos", "duties"),
    "handling": ("maniobra", "maniobras", "handling", "carga y descarga", "shuttle", "lanzadera"),
    "waiting time": ("tiempo de espera", "waiting time", "horas extra", "overtime"),
    "freight": ("flete", "freight", "ocean freight", "trucking", "transporte terrestre"),
    "extra service": ("cargo adicional", "cargos adicionales", "gasto adicional", "gastos adicionales",
                      "costo adicional", "costos adicionales", "additional charge", "additional charges",
                      "additional cost", "extra charge", "accesorial", "servicio adicional"),
}
_ALL_KW = tuple(k for v in COST_KEYWORDS.values() for k in v)

# Where quoted history starts in an HTML-stripped body (often all on one line).
_QUOTE_RES = [
    re.compile(r"\b(?:De|From):\s.{0,200}?\b(?:Enviado el|Enviado|Sent|Date|Fecha):", re.IGNORECASE | re.S),
    re.compile(r"\bEl\s.{0,80}?escribi[oó]:", re.IGNORECASE | re.S),
    re.compile(r"\bOn\s.{0,120}?wrote:", re.IGNORECASE | re.S),
    re.compile(r"-{3,}\s*(?:Original Message|Mensaje original)", re.IGNORECASE),
    re.compile(r"_{8,}"),
]
_SIGNATURE_RES = [re.compile(r"\bCONFIDENTIALITY NOTICE\b|\bAVISO DE CONFIDENCIALIDAD\b", re.IGNORECASE)]


def _num(s: str):
    s = (s or "").strip().rstrip(".,")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{2}$", s) else s.replace(",", "")
    elif s.count(".") > 1 or re.search(r"\.\d{3}$", s):
        s = s.replace(".", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def _ccy(tok: str | None, default: str | None = None) -> str | None:
    t = (tok or "").upper().replace(".", "")
    if t in ("USD", "US$", "USD$", "DLS", "U S D"):
        return "USD"
    if t in ("MXN", "MN"):
        return "MXN"
    if t in ("EUR", "€"):
        return "EUR"
    if t == "CAD":
        return "CAD"
    if t == "$":
        return default
    return default


def top_of_message(body: str) -> str:
    """Only the newest part of a message — quoted history and footers removed."""
    body = body or ""
    cut = len(body)
    for rx in _QUOTE_RES + _SIGNATURE_RES:
        m = rx.search(body)
        if m and m.start() < cut:
            cut = m.start()
    return body[:cut]


def find_files(*texts, known=None) -> list[str]:
    """TMS file numbers in order of appearance (sequel letter kept: '110779A')."""
    out = []
    for t in texts:
        for m in _FILE_RE.finditer(t or ""):
            f = m.group(1) + (m.group(2) or "")
            if f not in out:
                out.append(f)
    if known:
        hits = [f for f in out if f in known or re.sub(r"[A-Z]$", "", f) in known]
        if hits:
            return hits
    return out


def _kind(text: str) -> str | None:
    t = (text or "").lower()
    for kind, words in COST_KEYWORDS.items():
        if any(w in t for w in words):
            return kind
    return None


def parse_extra_costs(text: str, default_ccy: str | None = None) -> list[dict]:
    """Cost lines in free text: [{concept, kind, amount, currency, fee_pct, fee_label,
    amount_with_fee}]. A line counts only if it names a cost (keyword) AND carries an
    amount with a currency token (a bare '$' takes the currency stated elsewhere)."""
    text = text or ""
    doc_ccy = default_ccy
    m = re.search(r"\b(USD|MXN|EUR|CAD|DLS)\b", text, re.IGNORECASE)
    if m:
        doc_ccy = _ccy(m.group(1), default_ccy)
    # Split into candidate lines: real newlines, bullets, semicolons.
    parts = re.split(r"[\n\r]+|\s[•·▪●]\s|•|;\s+(?=[A-ZÁÉÍÓÚÑa-z])", text)
    out = []
    for raw in parts:
        line = raw.strip(" -–*\t")
        if not line or len(line) > 400:
            continue
        kind = _kind(line)
        if not kind:
            continue
        am = _AMOUNT_RE.search(line)
        if not am:
            continue
        amt = _num(am.group("a1") or am.group("a2"))
        if not amt:
            continue
        tok = am.group("c1b") or am.group("c2") or am.group("c1")
        ccy = _ccy(tok, doc_ccy) or doc_ccy
        concept = line[:am.start()].strip(" :=-–") or kind
        concept = re.sub(r"\s+", " ", concept)[:80]
        fee = _FEE_RE.search(line[am.end():])
        fee_pct = _num(fee.group(1)) if fee else None
        out.append({
            "concept": concept, "kind": kind, "amount": round(amt, 2), "currency": ccy,
            "fee_pct": fee_pct, "fee_label": (fee.group(2).strip() if fee else "") or None,
            "amount_with_fee": round(amt * (1 + fee_pct / 100), 2) if fee_pct else round(amt, 2),
        })
    return out


# ── CFDI (SAT e-invoice) ─────────────────────────────────────────────────────────
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_cfdi(xml_bytes: bytes, filename: str = "", subject: str = "", known=None) -> dict | None:
    """Read a CFDI 3.3 / 4.0 XML. None if it isn't one."""
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None
    if _local(root.tag) != "Comprobante":
        return None
    a = {k.rsplit("}", 1)[-1]: v for k, v in root.attrib.items()}
    out = {
        "type": a.get("TipoDeComprobante") or a.get("tipoDeComprobante") or "",
        "serie": a.get("Serie") or a.get("serie") or "",
        "folio": a.get("Folio") or a.get("folio") or "",
        "date": (a.get("Fecha") or a.get("fecha") or "")[:10],
        "currency": (a.get("Moneda") or "MXN").upper().replace("XXX", "MXN"),
        "fx": _num(a.get("TipoCambio") or "") or None,
        "subtotal": _num(a.get("SubTotal") or a.get("subTotal") or "") or 0.0,
        "total": _num(a.get("Total") or a.get("total") or "") or 0.0,
        "supplier": "", "rfc": "", "receiver_rfc": "", "uuid": "", "concepts": [], "tax": 0.0,
        "retained": 0.0,
    }
    for el in root.iter():
        n = _local(el.tag)
        at = el.attrib
        if n == "Emisor":
            out["supplier"] = at.get("Nombre") or at.get("nombre") or ""
            out["rfc"] = at.get("Rfc") or at.get("rfc") or ""
        elif n == "Receptor":
            out["receiver_rfc"] = at.get("Rfc") or at.get("rfc") or ""
        elif n == "Concepto":
            out["concepts"].append({"desc": at.get("Descripcion") or at.get("descripcion") or "",
                                    "amount": _num(at.get("Importe") or "") or 0.0})
        elif n == "TimbreFiscalDigital":
            out["uuid"] = (at.get("UUID") or "").upper()
    for ch in root:                     # document-level taxes (not the per-concept ones)
        if _local(ch.tag) == "Impuestos":
            out["tax"] = _num(ch.attrib.get("TotalImpuestosTrasladados") or "") or 0.0
            out["retained"] = _num(ch.attrib.get("TotalImpuestosRetenidos") or "") or 0.0
    # File number: concepts first (suppliers usually reference it there), then any
    # free text on the XML (addenda / observations), then the subject and file name.
    xml_text = " ".join(c["desc"] for c in out["concepts"])
    other = " ".join(v for el in root.iter() for v in el.attrib.values())
    out["files"] = find_files(xml_text, other, subject, filename, known=known)
    out["net"] = round(out["subtotal"], 2)
    return out


# ── turning messages into queue rows ─────────────────────────────────────────────
def _rid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def rows_from_message(msg: dict, known=None, thelsa_domains=("thelsa.com",)) -> list[dict]:
    """msg: {subject, body, sender, date, mailbox, attachments:[{name, bytes}]}"""
    subject, sender = msg.get("subject") or "", (msg.get("sender") or "").lower()
    date = (msg.get("date") or "")[:10]
    files = find_files(subject, known=known)
    rows = []
    # 1) CFDI attachments — a supplier invoice.
    for att in msg.get("attachments") or []:
        name = att.get("name") or ""
        if not name.lower().endswith(".xml"):
            continue
        c = parse_cfdi(att.get("bytes") or b"", name, subject, known=known)
        if not c or c["type"] not in ("I", "ingreso", "E", "egreso", ""):
            continue
        sign = -1 if c["type"] in ("E", "egreso") else 1     # E = supplier credit note
        f = (c["files"] or files or [""])[0]
        rows.append({
            "id": _rid("cfdi", c["uuid"] or (c["rfc"], c["serie"], c["folio"], c["total"])),
            "source": "supplier invoice (CFDI)", "file": f, "files_seen": c["files"] or files,
            "supplier": c["supplier"] or c["rfc"], "rfc": c["rfc"],
            "concept": "; ".join(x["desc"] for x in c["concepts"])[:160],
            "kind": _kind(" ".join(x["desc"] for x in c["concepts"])) or "supplier invoice",
            "amount": round(sign * c["net"], 2), "currency": c["currency"], "fx": c["fx"],
            "total_with_tax": round(sign * c["total"], 2),
            "ref": f"{c['serie']}{c['folio']}".strip() or c["uuid"][:8], "uuid": c["uuid"],
            "date": c["date"] or date, "mailbox": msg.get("mailbox"), "sender": sender,
            "subject": subject[:160], "status": "to post" if f else "file not found",
        })
    # 2) Extra costs written in the message itself (newest part only).
    top = top_of_message(msg.get("body") or "")
    lines = parse_extra_costs(top)
    if lines:
        f = (files or find_files(top, known=known) or [""])[0]
        external = sender and not any(sender.endswith("@" + d) for d in thelsa_domains)
        for ln in lines:
            rows.append({
                "id": _rid("email", f, ln["kind"], ln["amount"], ln["currency"], date),
                "source": "extra cost in email" + (" (from supplier/agent)" if external else " (coordinator)"),
                "file": f, "files_seen": files, "supplier": sender if external else "",
                "rfc": "", "concept": ln["concept"], "kind": ln["kind"],
                "amount": ln["amount_with_fee"] if ln["fee_pct"] else ln["amount"],
                "base_amount": ln["amount"], "fee_pct": ln["fee_pct"], "fee_label": ln["fee_label"],
                "currency": ln["currency"], "fx": None, "total_with_tax": None, "ref": "", "uuid": "",
                "date": date, "mailbox": msg.get("mailbox"), "sender": sender,
                "subject": subject[:160],
                "status": ("to confirm" if not external else "to post") if f else "file not found",
            })
    return rows


def merge(rows: list[dict], existing: dict | None = None) -> dict:
    """Queue keyed by row id; keeps any status a person set (posted / ignored)."""
    q = dict(existing or {})
    for r in rows:
        old = q.get(r["id"])
        if old and old.get("status") in ("posted", "ignored", "duplicate"):
            continue
        q[r["id"]] = r
    return q


def summarize(queue: dict) -> dict:
    rows = sorted(queue.values(), key=lambda r: (r.get("date") or ""), reverse=True)
    open_rows = [r for r in rows if r.get("status") in ("to post", "to confirm", "file not found")]
    by_ccy = {}
    for r in open_rows:
        if r.get("status") == "to post":
            by_ccy[r.get("currency") or "?"] = round(by_ccy.get(r.get("currency") or "?", 0) + (r.get("amount") or 0), 2)
    return {"rows": rows, "open": open_rows, "n_open": len(open_rows),
            "n_to_post": sum(1 for r in open_rows if r["status"] == "to post"),
            "n_to_confirm": sum(1 for r in open_rows if r["status"] == "to confirm"),
            "n_no_file": sum(1 for r in open_rows if r["status"] == "file not found"),
            "open_by_ccy": by_ccy,
            "n_files": len({r.get("file") for r in open_rows if r.get("file")})}


def post_to_moveware(row: dict) -> dict:
    """MoveWare has no cost write endpoint yet. When it does, this is the one place
    that writes a supplier cost line to the job. Until then: not posted."""
    return {"posted": False, "reason": "MoveWare API has no cost write endpoint yet"}


# ── live scan (Graph, app-only) ──────────────────────────────────────────────────
_LOCK = threading.Lock()
_STATE = {"queue": {}, "scanned_at": None, "n_messages": 0, "error": None, "have_creds": False, "diag": None}
_THREAD = None
_INTERVAL = int(os.environ.get("SUPPLIER_COST_SCAN_SECONDS", 3 * 3600))
_DAYS = int(os.environ.get("SUPPLIER_COST_DAYS", 45))


def _store_path():
    p = os.environ.get("SUPPLIER_COST_STORE")
    if p:
        return p
    return "/var/data/supplier_costs.json" if os.path.isdir("/var/data") else "supplier_costs.json"


def _load():
    try:
        with open(_store_path()) as fh:
            d = json.load(fh)
        _STATE["queue"] = d.get("queue") or {}
        _STATE["scanned_at"] = d.get("scanned_at")
    except Exception:
        pass


def _save():
    try:
        with open(_store_path(), "w") as fh:
            json.dump({"queue": _STATE["queue"], "scanned_at": _STATE["scanned_at"]}, fh)
    except Exception:
        pass


def fetch_messages(days: int = _DAYS, per_mailbox: int = 100) -> list[dict]:
    """Recent mail in the TMS coordinator mailboxes that could carry a cost: CFDI
    attachments, or cost keywords. Attachments are read only when they are .xml."""
    import requests
    import ms_graph
    h = ms_graph._headers()
    if not h:
        return []
    since = (dt.datetime.utcnow() - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    diag = {"mailboxes": len(ms_graph.TMS_COORDINATORS), "ok": 0, "denied": 0, "other": 0,
            "messages_read": 0, "with_xml": 0, "first_error": None,
            "app_client_id": ms_graph.MS_CLIENT_ID, "tenant": ms_graph.MS_TENANT_ID,
            "credential": "MS_*" if os.environ.get("MS_CLIENT_SECRET") else "GRAPH_*"}
    _STATE["diag"] = diag
    for mbx in ms_graph.TMS_COORDINATORS:
        params = {"$filter": f"receivedDateTime ge {since}",
                  "$select": "id,subject,from,receivedDateTime,body,hasAttachments",
                  "$orderby": "receivedDateTime desc", "$top": str(per_mailbox)}
        try:
            r = requests.get(f"{ms_graph.GRAPH}/users/{mbx}/messages", headers=h, params=params, timeout=25)
            if r.status_code != 200:
                diag["denied" if r.status_code in (401, 403) else "other"] += 1
                diag["first_error"] = diag["first_error"] or f"{mbx}: {r.status_code} {ms_graph._short(r)}"
                continue
            diag["ok"] += 1
            vals = r.json().get("value", [])
            diag["messages_read"] += len(vals)
            for m in vals:
                body = ms_graph._plain(m)
                subj = m.get("subject") or ""
                atts = []
                if m.get("hasAttachments"):
                    try:
                        ar = requests.get(f"{ms_graph.GRAPH}/users/{mbx}/messages/{m['id']}/attachments",
                                          headers=h, params={"$select": "name,contentType,size"}, timeout=25)
                        for a in (ar.json().get("value", []) if ar.status_code == 200 else []):
                            if (a.get("name") or "").lower().endswith(".xml") and (a.get("size") or 0) < 2_000_000:
                                ab = requests.get(f"{ms_graph.GRAPH}/users/{mbx}/messages/{m['id']}/attachments/{a['id']}/$value",
                                                  headers=h, timeout=25)
                                if ab.status_code == 200:
                                    atts.append({"name": a.get("name"), "bytes": ab.content})
                    except Exception:
                        pass
                diag["with_xml"] += bool(atts)
                if not atts and not any(k in (subj + " " + body).lower() for k in _ALL_KW):
                    continue
                out.append({"subject": subj, "body": body, "date": m.get("receivedDateTime"),
                            "sender": ((m.get("from") or {}).get("emailAddress") or {}).get("address", ""),
                            "mailbox": mbx, "attachments": atts})
        except Exception as e:
            diag["other"] += 1
            diag["first_error"] = diag["first_error"] or f"{mbx}: {e}"
            continue
    return out


def scan_live():
    try:
        import ms_graph
    except Exception as e:
        _STATE["error"] = f"import: {e}"
        return _STATE
    _STATE["have_creds"] = ms_graph.have_ms_creds()
    if not _STATE["have_creds"]:
        return _STATE
    try:
        known = None
        try:
            import mw_live
            known = {str(m.get("job")) for m in mw_live.audited_files() if m.get("job")}
        except Exception:
            pass
        msgs = fetch_messages()
        rows = [r for m in msgs for r in rows_from_message(m, known=known)]
        with _LOCK:
            _STATE["queue"] = merge(rows, _STATE["queue"])
            d = _STATE.get("diag") or {}
            err = None
            if d and d.get("ok", 0) == 0 and (d.get("denied") or d.get("other")):
                err = "mailbox reads failed — " + (d.get("first_error") or "")
            _STATE.update(scanned_at=time.time(), n_messages=len(msgs), error=err)
            _save()
    except Exception as e:
        _STATE["error"] = str(e)
    return _STATE


def _loop():
    while True:
        scan_live()
        time.sleep(_INTERVAL)


def ensure_scanner():
    global _THREAD
    if not _STATE["queue"]:
        _load()
    try:
        import ms_graph
        if not ms_graph.have_ms_creds():
            return
    except Exception:
        return
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_loop, daemon=True, name="supplier-cost-scan")
        _THREAD.start()


def get_state() -> dict:
    with _LOCK:
        s = dict(_STATE)
    s.update(summarize(s.get("queue") or {}))
    return s


def set_status(row_id: str, status: str) -> bool:
    if status not in ("to post", "posted", "ignored", "duplicate", "to confirm"):
        return False
    with _LOCK:
        r = _STATE["queue"].get(row_id)
        if not r:
            return False
        r["status"] = status
        r["status_at"] = dt.date.today().isoformat()
        _save()
    return True
