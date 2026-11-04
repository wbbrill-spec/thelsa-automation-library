"""
doc_check.py — FAIM MS1.4 documents, found in the coordinators' email
(Bill, 2 Nov 2026: "we should build it on email attachments for now. We don't
know if or when Moveware's API will ever expose the document endpoints").

For every audited file, the TMS mailboxes the app can read (each person's own
connection, or the app-wide credential when IT grants it) are searched by FILE
NUMBER. Each email that names the file is classified by its attachment names,
subject and text into the documents FAIM 3.4 MS1.4 asks for:

  instructions   pre-advice / shipment instructions to or from the other agent
  bol            carrier's Bill of Lading / Waybill / Air Waybill
  insurance      insurance certificate (only required when the file is insured)
  delivered      confirmation of delivery (an email saying it was delivered)
  inventory      packing list / inventory (signatures are NOT checked: the app
                 reads names and text, not the scanned page)

Which documents a file needs depends on its job type (Thelsa's codes, FAIM
MS1.4 columns) and on how far the move has gone — a bill of lading only after the
goods left, delivery papers only after delivery:

  EXP  outbound, we are the Booker      instructions, bol, insurance*, delivered, inventory
  EXA  outbound, origin agent           instructions, bol, inventory (signed at origin)
  IMP  inbound, we are the Booker       instructions, bol, insurance*, delivered, inventory
  IMA  inbound, destination agent       instructions, bol, delivered, inventory
  other (domestic, storage)             not checked (MS1.4 covers international moves)

Read-only: nothing is sent, moved or changed in anyone's mailbox. Attachments
are NOT downloaded; only their names are read. What is kept per file is the
document type, the email date, its subject and a link — no customer names.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
import time

import requests

import ms_graph
import survey_finder as sf

DOC_LABELS = {
    "instructions": ("Shipment instructions / pre-advice", "Instrucciones de embarque / pre-aviso"),
    "bol": ("Bill of lading / waybill", "Conocimiento de embarque / guía"),
    "insurance": ("Insurance certificate", "Certificado de seguro"),
    "delivered": ("Delivery confirmation", "Confirmación de entrega"),
    "inventory": ("Packing list / inventory", "Lista de empaque / inventario"),
}

# Words that identify each document, in attachment names, subjects and text
# (accents and case removed before matching).
DOC_WORDS = {
    "instructions": ("pre-advice", "preadvice", "pre advice", "pre-alert", "prealert", "pre alert",
                     "pre-alerta", "prealerta", "shipping instruction", "shipment instruction",
                     "instrucciones de embarque", "instruccion de embarque", "instrucciones",
                     "booking confirmation", "delivery instruction", "agent instruction"),
    "bol": ("bill of lading", "b/l", "hbl", "mbl", "obl", "bl draft", "draft bl", "awb", "air waybill",
            "airway bill", "waybill", "way bill", "guia aerea", "conocimiento de embarque",
            "carta porte", "cmr"),
    "insurance": ("insurance certificate", "certificate of insurance", "certificado de seguro",
                  "poliza", "insurance policy", "valued inventory", "inventario valorizado"),
    "delivered": ("delivery completed", "delivered", "was delivered", "has been delivered",
                  "delivery confirmation", "confirmation of delivery", "proof of delivery", "pod",
                  "entregado", "entrega realizada", "se entrego", "entrega completada",
                  "confirmacion de entrega", "comprobante de entrega", "acuse de entrega"),
    "inventory": ("inventory", "inventario", "packing list", "packinglist", "lista de empaque",
                  "listado de empaque", "inv firmado", "signed inv"),
}

REQUIRED = {
    "EXP": ("instructions", "bol", "insurance", "delivered", "inventory"),
    "IMP": ("instructions", "bol", "insurance", "delivered", "inventory"),
    "EXA": ("instructions", "bol", "inventory"),
    "IMA": ("instructions", "bol", "delivered", "inventory"),
}
# Which milestone a document waits for: nothing is missing before it is due.
DUE_AFTER = {"instructions": "load", "bol": "load", "insurance": "load",
             "delivered": "delivery", "inventory": "delivery"}
# An agent export ends at origin: its inventory is due once loaded.
DUE_AFTER_BY_TYPE = {("EXA", "inventory"): "load"}
GRACE_DAYS = {"instructions": 0, "bol": 7, "insurance": 0, "delivered": 2, "inventory": 5}

_STATE = {"running": False, "started": None, "finished": None, "error": None,
          "files": 0, "done": 0, "results": [], "diag": None}
_LOCK = threading.Lock()

try:  # last run survives restarts (4 Nov 2026 crash loop)
    import job_state as _js
    _js.restore('doc_check', _STATE)
except Exception:
    _js = None



def _norm(s: str) -> str:
    return sf._norm(s or "")


def classify(text: str) -> set:
    """Document types an email (attachment names + subject + text) shows."""
    t = _norm(text)
    out = set()
    for kind, words in DOC_WORDS.items():
        for w in words:
            if sf._word_re(w).search(t):
                out.add(kind)
                break
    return out


def required_docs(job_type: str, insured: bool, load_day, delivery_day, today=None) -> list:
    """The documents this file should have by today."""
    today = today or dt.date.today()
    jt = (job_type or "").strip().upper()
    out = []
    for kind in REQUIRED.get(jt, ()):
        if kind == "insurance" and not insured:
            continue
        stage = DUE_AFTER_BY_TYPE.get((jt, kind), DUE_AFTER[kind])
        day = load_day if stage == "load" else delivery_day
        if not day:
            continue
        if isinstance(day, str):
            try:
                day = dt.date.fromisoformat(day[:10])
            except ValueError:
                continue
        if (today - day).days < GRACE_DAYS[kind]:
            continue
        out.append(kind)
    return out


def _attachment_names(url_base: str, msg_id: str, hh: dict) -> list:
    try:
        r = requests.get(f"{url_base}/{msg_id}/attachments", headers=hh,
                         params={"$select": "name,contentType,size"}, timeout=20)
        if r.status_code == 200:
            return [a.get("name") or "" for a in r.json().get("value") or []]
    except Exception:
        pass
    return []


def _search(mbx: str, number: str, app_h, diag: dict) -> list:
    """Messages in this mailbox that mention the file number (subject, text or
    attachment name), with their attachment names."""
    if mbx in (diag.get("refused_mailboxes") or []):
        return []
    hh, url = sf._auth_for(mbx, app_h, diag.get("_delegated") or {}, diag)
    if not hh:
        diag.setdefault("refused_mailboxes", []).append(mbx)
        return []
    params = {"$search": f'"{number}"', "$top": "25",
              "$select": "id,subject,from,receivedDateTime,bodyPreview,webLink,hasAttachments"}
    try:
        r = requests.get(url, headers=hh, params=params, timeout=25)
    except Exception as e:
        diag["errors"] += 1
        diag["first_error"] = diag["first_error"] or f"{mbx}: {e}"[:160]
        return []
    diag["searches"] += 1
    if r.status_code in (401, 403):
        diag.setdefault("refused_mailboxes", [])
        if mbx not in diag["refused_mailboxes"]:
            diag["refused_mailboxes"].append(mbx)
        return []
    if r.status_code != 200:
        diag["errors"] += 1
        diag["first_error"] = diag["first_error"] or f"{mbx}: {r.status_code}"
        return []
    diag.setdefault("read_mailboxes", [])
    if mbx not in diag["read_mailboxes"]:
        diag["read_mailboxes"].append(mbx)
    out = []
    for m in r.json().get("value") or []:
        names = _attachment_names(url, m["id"], hh) if m.get("hasAttachments") else []
        out.append(dict(m, _attachments=names, _mailbox=mbx))
    return out


def docs_for_file(row: dict, app_h, diag: dict) -> dict:
    """{kind: {date, subject, link, mailbox, attachment}} — the earliest email
    showing each document type. Only emails that name the file number count."""
    m = re.match(r"\d{5,7}", str(row.get("file") or ""))
    if not m:
        return {}
    number = m.group(0)
    boxes = sf._mailboxes_for(row.get("coord_email"))
    boxes += [b for b in (diag.get("_delegated") or {}) if b not in boxes]
    found, seen = {}, set()
    for mbx in boxes:
        for msg in _search(mbx, number, app_h, diag):
            key = (msg.get("subject"), msg.get("receivedDateTime"))
            if key in seen:
                continue
            seen.add(key)
            names = msg.get("_attachments") or []
            text = " ".join([msg.get("subject") or "", msg.get("bodyPreview") or ""] + names)
            if number not in text:
                continue                      # a search hit that doesn't name the file
            kinds = classify(" ".join(names)) | classify((msg.get("subject") or "") + " "
                                                          + (msg.get("bodyPreview") or ""))
            day = (msg.get("receivedDateTime") or "")[:10]
            dd = str(row.get("delivery_day") or "")[:10]
            if "delivered" in kinds and dd:
                try:   # "will be delivered" before the move is not a confirmation
                    if dt.date.fromisoformat(day) < dt.date.fromisoformat(dd) - dt.timedelta(days=1):
                        kinds = kinds - {"delivered"}
                except ValueError:
                    pass
            for k in kinds:
                att = next((n for n in names if k in classify(n)), "")
                prev = found.get(k)
                # attachments beat a mention in the text; then the earliest email
                better = (prev is None or (att and not prev["attachment"])
                          or (bool(att) == bool(prev["attachment"]) and day < prev["date"]))
                if better:
                    found[k] = {"date": day, "subject": (msg.get("subject") or "")[:140],
                                "link": msg.get("webLink") or "", "mailbox": mbx,
                                "attachment": att[:120]}
    return found


def run(rows: list) -> None:
    """Background pass over the audited files (faim_snapshot()['doc_rows'])."""
    diag = {"searches": 0, "errors": 0, "first_error": None}
    with _LOCK:
        _STATE.update(running=True, started=time.time(), finished=None, error=None,
                      files=len(rows), done=0, results=[], diag=diag)
    if _js:
        _js.mark_started('doc_check')
    try:
        tok = ms_graph._get_token()
        diag["_delegated"] = sf.delegated_mailboxes()
        diag["connected"] = sorted(diag["_delegated"])
        if not tok and not diag["_delegated"]:
            raise RuntimeError("no mailbox the app can read")
        app_h = ({"Authorization": f"Bearer {tok}", "ConsistencyLevel": "eventual"} if tok else None)
        out = []
        today = dt.date.today()
        for row in rows:
            need = required_docs(row.get("type"), row.get("insured"), row.get("load_day"),
                                 row.get("delivery_day"), today)
            found = docs_for_file(row, app_h, diag) if need else {}
            out.append(dict(row, needed=need, found=found,
                            missing=[k for k in need if k not in found]))
            with _LOCK:
                _STATE["done"] += 1
                _STATE["results"] = list(out)
    except Exception as e:
        with _LOCK:
            _STATE["error"] = str(e)[:200]
    finally:
        diag.pop("_delegated", None)
        with _LOCK:
            _STATE.update(running=False, finished=time.time())
    if _js:
        with _LOCK:
            _snap = dict(_STATE)
        _snap["finished_ok"] = True
        _js.save('doc_check', _snap)
        _js.release_memory()


def start(rows) -> bool:
    with _LOCK:
        if _STATE["running"]:
            return False
        _STATE["running"] = True
    threading.Thread(target=run, args=(rows,), daemon=True, name="doc-check").start()
    return True


def state() -> dict:
    with _LOCK:
        return dict(_STATE, results=list(_STATE["results"]))


def summary(results=None) -> dict:
    """Counts for the dashboard: per document type, files that need it and files
    where an email shows it; and per coordinator."""
    results = state()["results"] if results is None else results
    by_doc = {k: {"needed": 0, "found": 0} for k in DOC_LABELS}
    by_coord = {}
    for r in results:
        c = by_coord.setdefault(r.get("coord") or "Unassigned", {"needed": 0, "found": 0, "files": 0})
        if r.get("needed"):
            c["files"] += 1
        for k in r.get("needed") or []:
            by_doc[k]["needed"] += 1
            c["needed"] += 1
            if k in (r.get("found") or {}):
                by_doc[k]["found"] += 1
                c["found"] += 1
    return {"by_doc": by_doc, "by_coord": by_coord,
            "files_checked": sum(1 for r in results if r.get("needed")),
            "files_complete": sum(1 for r in results if r.get("needed") and not r.get("missing"))}
