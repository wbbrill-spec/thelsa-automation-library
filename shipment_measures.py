"""
shipment_measures.py — estimated volume and weight (survey / cube sheet, in
Moveware) against the actual volume and weight of the shipment (Bill, 13 Nov 2026).

Estimate: Moveware keeps one measurement record per file (measures[] on the job,
already read by the audit as est_vol / est_wt; GET /jobs/{id}/measures otherwise),
the survey / cube-sheet estimate: gross and net volume (m3, ft3) and a weight that
Moveware works out from the volume at the file's density (6.5 lb/ft3 on Thelsa,
about 104 kg per m3). Checked on 15 files, 13 Nov 2026: none had a second,
actual record. If a record's category says "actual", it is used as the actual.

Actual: Moveware doesn't hold it, so it is read from the shipping documents in
the coordinators' and sales email, found the same way as the FAIM document check
(doc_check: every email that names the file number):
  weight certificate / scale ticket   (certificado de peso, boleta de bascula)
  bill of lading / air waybill        (gross weight, volume)
  packing list / inventory            (total volume, total weight)
  the email's own text                ("gross weight 2,345 kg, 30 cbm")
PDF attachments are opened and their text read (pypdf). A scanned page with no
text can't be read; the file then shows "actual not found".

Rules:
  - Only files that have loaded (the actual exists after packing / loading).
  - A number only counts next to a weight or volume word AND a unit
    (kg / kgs / kilos / lb / lbs; m3 / cbm / ft3 / cft / cu ft).
  - lb -> kg (x 0.453592), ft3 -> m3 (x 0.0283168).
  - Gross beats net beats a plain "total"; a weight certificate beats a bill of
    lading beats an inventory beats the email text; then the latest document.
  - A value under 1/5 or over 5 times the estimate is left out (a container
    size, a phone number, another file's figure), and counted.
Read-only: nothing is sent, moved or changed in any mailbox or in Moveware.
Kept per file: numbers, document type, date, subject and link; no customer names.
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import re
import threading
import time

import requests

KG_PER_LB = 0.453592
M3_PER_FT3 = 0.0283168
WITHIN_PCT = 10            # "accurate" = actual within ±10 % of the estimate
SANE_RATIO = (0.2, 5.0)     # actual / estimate outside this is not this file's figure
MAX_PDF_BYTES = 8 * 1048576
MAX_PDF_PAGES = 12

SOURCE_RANK = {"weight": 4, "bol": 3, "inventory": 2, "email": 1}
SOURCE_LABELS = {"weight": ("Weight certificate", "Certificado de peso"),
                 "bol": ("Bill of lading / waybill", "Conocimiento de embarque / guía"),
                 "inventory": ("Packing list / inventory", "Lista de empaque / inventario"),
                 "email": ("Email text", "Texto del correo"),
                 "moveware": ("Moveware (actual)", "Moveware (real)")}
WEIGHT_DOC_WORDS = ("weight certificate", "certificate of weight", "weight ticket", "scale ticket",
                    "certificado de peso", "boleta de bascula", "ticket de bascula", "bascula",
                    "pesaje", "peso certificado", "weighbridge")

_STATE = {"running": False, "started": None, "finished": None, "error": None,
          "files": 0, "done": 0, "results": [], "diag": None}
_LOCK = threading.Lock()

try:   # last run survives restarts
    import job_state as _js
    _js.restore("measures", _STATE)
except Exception:
    _js = None


# ── numbers in text ────────────────────────────────────────────────────────
_NUM = r"(\d{1,3}(?:[,.\s]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_W_UNIT = r"(kgs?|kilos?|kilogramos?|lbs?|pounds?|libras?)\b"
_V_UNIT = r"(m3|m³|mts3|cbm|cbms|cu\.?\s?m|ft3|ft³|cft|cu\.?\s?ft|cubic\s+feet|pies\s+cubicos)"
_W_WORD = r"(gross\s+weight|peso\s+bruto|g\.?\s?w\.?|net\s+weight|peso\s+neto|n\.?\s?w\.?|" \
          r"total\s+weight|peso\s+total|weight|peso|neto|bruto|net|gross)"
_V_WORD = r"(gross\s+volume|volumen\s+bruto|net\s+volume|volumen\s+neto|total\s+volume|" \
          r"volumen\s+total|volume|volumen|cubicaje|cubic|cbm|m3|cube)"
_W_RE = re.compile(_W_WORD + r"[^0-9\n]{0,30}?" + _NUM + r"\s*" + _W_UNIT, re.I)
_V_RE = re.compile(_V_WORD + r"[^0-9\n]{0,30}?" + _NUM + r"\s*" + _V_UNIT, re.I)
# unit before the number: "G.W. (KGS): 2,345" / "CBM: 30.5"
_W_RE3 = re.compile(_W_WORD + r"\s*\(?\s*" + _W_UNIT + r"\s*\)?\s*[:=.]?\s*" + _NUM, re.I)
_V_RE3 = re.compile(r"\b(cbm|m3|m³)\s*\)?\s*[:=]\s*" + _NUM, re.I)
SANE_KG = (10, 40000)
SANE_M3 = (0.1, 200)
# "2,345 kgs gross" / "30 cbm" with the word after the number
_W_RE2 = re.compile(_NUM + r"\s*" + _W_UNIT + r"[^0-9\n]{0,12}?" + _W_WORD, re.I)
_V_RE2 = re.compile(_NUM + r"\s*" + _V_UNIT, re.I)


def _to_float(s: str, weight: bool = False) -> float | None:
    """'2,345.5' / '2.345,5' / '2 345' / '12,5' -> float. With both separators the
    last one is the decimal. A lone '1.250' is 1250 for a weight (Mexican
    thousands dot) and 1.25 for a volume."""
    s = s.strip().replace(" ", "")
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(,\d{3})+", s):
        s = s.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s) and (weight or s.count(".") > 1):
        s = s.replace(".", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _grade(label: str) -> int:
    lab = (label or "").lower()
    if "gross" in lab or "bruto" in lab or re.match(r"g\.?\s?w", lab):
        return 3
    if "net" in lab or "neto" in lab or re.match(r"n\.?\s?w", lab):
        return 2
    if "total" in lab:
        return 1
    return 0


def find_measures(text: str) -> dict:
    """{'kg': (value, grade) , 'm3': (value, grade)} found in a text; the best
    graded (gross > net > total > plain) of each, else nothing."""
    out = {}
    t = text or ""
    for rx, order in ((_W_RE, "lnu"), (_W_RE2, "nul"), (_W_RE3, "lun")):
        for m in rx.finditer(t):
            g3 = dict(zip(order, m.groups()))
            label, num, unit = g3["l"], g3["n"], g3["u"]
            v = _to_float(num, weight=True)
            if v is None or v <= 0:
                continue
            kg = v * KG_PER_LB if unit.lower().startswith(("lb", "pound", "libra")) else v
            if not (SANE_KG[0] <= kg <= SANE_KG[1]):
                continue
            g = _grade(label)
            if "kg" not in out or g > out["kg"][1]:
                out["kg"] = (round(kg, 1), g)
    for m in _V_RE.finditer(t):
        v = _to_float(m.group(2))
        if v is None or v <= 0:
            continue
        unit = m.group(3).lower()
        m3 = v * M3_PER_FT3 if ("ft" in unit or "cubic feet" in unit or "pies" in unit) else v
        if not (SANE_M3[0] <= m3 <= SANE_M3[1]):
            continue
        g = _grade(m.group(1))
        if "m3" not in out or g > out["m3"][1]:
            out["m3"] = (round(m3, 2), g)
    if "m3" not in out:
        for m in _V_RE3.finditer(t):
            v = _to_float(m.group(2))
            if v and SANE_M3[0] <= v <= SANE_M3[1]:
                out["m3"] = (round(v, 2), 0)
                break
    if "m3" not in out:
        for m in _V_RE2.finditer(t):
            v = _to_float(m.group(1))
            if v is None or v <= 0:
                continue
            unit = m.group(2).lower()
            m3 = v * M3_PER_FT3 if ("ft" in unit or "cubic feet" in unit or "pies" in unit) else v
            if SANE_M3[0] <= m3 <= SANE_M3[1]:
                out["m3"] = (round(m3, 2), 0)
                break
    return out


def pdf_text(data: bytes) -> str:
    """Text of a PDF ('' when it is a scan, encrypted, or not a PDF)."""
    if not data or len(data) > MAX_PDF_BYTES:
        return ""
    try:
        from pypdf import PdfReader
        rd = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in rd.pages[:MAX_PDF_PAGES])
    except Exception:
        return ""


# ── the estimate, from Moveware ────────────────────────────────────────────
def _num(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    try:
        return float(d) if d is not None else None
    except (TypeError, ValueError):
        return None


def parse_measures(items: list) -> dict:
    """Moveware measure records -> {'est': {...}, 'act': {...}}. A record whose
    category says 'actual' is an actual; anything else is the estimate."""
    out = {}
    for it in items or []:
        cat = str(it.get("category") or "").lower()
        kind = "act" if ("actual" in cat or "real" in cat) else "est"
        vol, wt = it.get("volume") or {}, it.get("weight") or {}
        m3 = _num(vol, "gross", "m3") or _num(vol, "net", "m3") or _num(vol, "m3")
        if not m3:
            f3 = _num(vol, "gross", "f3") or _num(vol, "net", "f3")
            m3 = f3 * M3_PER_FT3 if f3 else None
        kg = _num(wt, "gross", "kg") or _num(wt, "net", "kg") or _num(wt, "kg")
        if not kg:
            lb = _num(wt, "gross", "lb") or _num(wt, "net", "lb") or _num(wt, "lb")
            kg = lb * KG_PER_LB if lb else None
        if not (m3 or kg):
            continue
        cur = out.setdefault(kind, {"m3": 0.0, "kg": 0.0, "records": 0})
        cur["m3"] = round(cur["m3"] + (m3 or 0), 2)
        cur["kg"] = round(cur["kg"] + (kg or 0), 1)
        cur["records"] += 1
        if it.get("density") is not None:
            cur["density_lb_ft3"] = _num(it, "density")
    return out


def moveware_measures(job_id) -> dict:
    import mw_costs
    items = mw_costs.get_all(f"/jobs/{job_id}/measures", "measures")
    return parse_measures(items)


# ── the actual, from email ─────────────────────────────────────────────────
def _source_kind(names_and_subject: str) -> str:
    import doc_check as dc
    t = dc._norm(names_and_subject)
    if any(w in t for w in WEIGHT_DOC_WORDS):
        return "weight"
    kinds = dc.classify(names_and_subject)
    if "bol" in kinds:
        return "bol"
    if "inventory" in kinds:
        return "inventory"
    return "email"


def _attachments(url: str, msg_id: str, hh: dict) -> list:
    try:
        r = requests.get(f"{url}/{msg_id}/attachments", headers=hh,
                         params={"$select": "id,name,contentType,size"}, timeout=20)
        return r.json().get("value") or [] if r.status_code == 200 else []
    except Exception:
        return []


def _attachment_bytes(url: str, msg_id: str, att_id: str, hh: dict) -> bytes:
    try:
        r = requests.get(f"{url}/{msg_id}/attachments/{att_id}", headers=hh, timeout=40)
        if r.status_code == 200:
            return base64.b64decode(r.json().get("contentBytes") or "")
    except Exception:
        pass
    return b""


def _body_text(url: str, msg_id: str, hh: dict) -> str:
    try:
        h = dict(hh, Prefer='outlook.body-content-type="text"')
        r = requests.get(f"{url}/{msg_id}", headers=h, params={"$select": "body"}, timeout=20)
        if r.status_code == 200:
            return ((r.json().get("body") or {}).get("content") or "")[:20000]
    except Exception:
        pass
    return ""


def actual_from_email(row: dict, est: dict, app_h, diag: dict) -> dict:
    """Best actual weight and volume for one file from its emails, or {}."""
    import doc_check as dc
    import survey_finder as sf
    m = re.match(r"\d{5,7}", str(row.get("file") or ""))
    if not m:
        return {}
    number = m.group(0)
    boxes = sf._mailboxes_for(row.get("coord_email"))
    boxes += [b for b in (diag.get("_delegated") or {}) if b not in boxes]
    found = {}           # "kg" / "m3" -> candidate
    seen = set()
    for mbx in boxes:
        for msg in dc._search(mbx, number, app_h, diag):
            key = (msg.get("subject"), msg.get("receivedDateTime"))
            if key in seen:
                continue
            seen.add(key)
            names = msg.get("_attachments") or []
            head = " ".join([msg.get("subject") or "", msg.get("bodyPreview") or ""] + names)
            if number not in head:
                continue
            hh, url = sf._auth_for(mbx, app_h, diag.get("_delegated") or {}, diag)
            if not hh:
                continue
            texts = []                           # (kind, text, attachment name)
            subj_kind = _source_kind((msg.get("subject") or "") + " " + " ".join(names))
            for a in _attachments(url, msg["id"], hh) if msg.get("hasAttachments") else []:
                nm = a.get("name") or ""
                if not (nm.lower().endswith(".pdf") or "pdf" in str(a.get("contentType") or "")):
                    continue
                k = _source_kind(nm + " " + (msg.get("subject") or ""))
                if k == "email" and subj_kind == "email":
                    continue                     # an unrelated PDF (a quote, a photo)
                diag["pdfs"] = diag.get("pdfs", 0) + 1
                txt = pdf_text(_attachment_bytes(url, msg["id"], a.get("id"), hh))
                if not txt.strip():
                    diag["pdf_no_text"] = diag.get("pdf_no_text", 0) + 1
                    continue
                texts.append((k if k != "email" else subj_kind, txt, nm))
            if subj_kind != "email" or re.search(r"\b(peso|weight|cbm|m3|volum)", head, re.I):
                texts.append(("email", _body_text(url, msg["id"], hh), ""))
            day = (msg.get("receivedDateTime") or "")[:10]
            for kind, txt, att in texts:
                for unit, (val, grade) in find_measures(txt).items():
                    ref = (est or {}).get(unit)
                    if ref and not (SANE_RATIO[0] <= val / ref <= SANE_RATIO[1]):
                        diag["implausible"] = diag.get("implausible", 0) + 1
                        continue
                    cand = {"value": val, "grade": grade, "kind": kind, "date": day,
                            "subject": (msg.get("subject") or "")[:140],
                            "link": msg.get("webLink") or "", "attachment": att[:120]}
                    prev = found.get(unit)
                    rank = (SOURCE_RANK.get(kind, 0), grade, day)
                    if prev is None or rank > (SOURCE_RANK.get(prev["kind"], 0), prev["grade"], prev["date"]):
                        found[unit] = cand
    return found


def compare(est: dict, act: dict) -> dict:
    """Differences, actual vs estimate, in % of the estimate (+ = more than estimated)."""
    out = {}
    for unit in ("m3", "kg"):
        e, a = (est or {}).get(unit), (act or {}).get(unit)
        if e and a:
            out[f"{unit}_diff_pct"] = round((a - e) / e * 100, 1)
    return out


def run(rows: list) -> None:
    """Background pass over the audited files that have loaded."""
    import ms_graph
    import survey_finder as sf
    diag = {"searches": 0, "errors": 0, "first_error": None}
    with _LOCK:
        _STATE.update(running=True, started=time.time(), finished=None, error=None,
                      files=len(rows), done=0, results=[], diag=diag)
    if _js:
        _js.mark_started("measures")
    try:
        tok = ms_graph._get_token()
        diag["_delegated"] = sf.delegated_mailboxes()
        app_h = ({"Authorization": f"Bearer {tok}", "ConsistencyLevel": "eventual"} if tok else None)
        out = []
        for row in rows:
            rec = {k: row.get(k) for k in ("file", "type", "coord", "load_day", "delivery_day")}
            mw = {}
            if row.get("est_m3") or row.get("est_kg"):   # already read by the audit: no extra call
                mw = {"est": {"m3": row.get("est_m3"), "kg": row.get("est_kg")}}
            else:
                try:
                    mw = moveware_measures(row["job_id"])
                except Exception as e:            # noqa: BLE001
                    rec["error"] = f"Moveware: {type(e).__name__}"[:80]
            est = mw.get("est") or {}
            rec["est"] = {k: est.get(k) for k in ("m3", "kg") if est.get(k)}
            act = {}
            if mw.get("act"):
                a = mw["act"]
                act = {u: {"value": a[u], "kind": "moveware", "date": "", "subject": "", "link": "",
                           "attachment": "", "grade": 3} for u in ("m3", "kg") if a.get(u)}
            if (app_h or diag["_delegated"]) and len(act) < 2:
                for u, c in actual_from_email(row, rec["est"], app_h, diag).items():
                    act.setdefault(u, c)
            rec["act"] = {u: c["value"] for u, c in act.items()}
            rec["act_src"] = {u: {k: c[k] for k in ("kind", "date", "subject", "link", "attachment")}
                              for u, c in act.items()}
            rec.update(compare(rec["est"], rec["act"]))
            out.append(rec)
            with _LOCK:
                _STATE["done"] += 1
                _STATE["results"] = list(out)
    except Exception as e:                        # noqa: BLE001
        with _LOCK:
            _STATE["error"] = str(e)[:200]
    finally:
        diag.pop("_delegated", None)
        with _LOCK:
            _STATE.update(running=False, finished=time.time())
    if _js:
        with _LOCK:
            snap = dict(_STATE)
        snap["finished_ok"] = True
        _js.save("measures", snap)
        _js.release_memory()


def rows_for(doc_rows: list, today: dt.date | None = None) -> list:
    """The audited files that have loaded (or delivered), with their Moveware id."""
    today = (today or dt.date.today()).isoformat()
    out = []
    for r in doc_rows or []:
        day = str(r.get("load_day") or r.get("delivery_day") or "")[:10]
        if r.get("job_id") and day and day <= today:
            out.append(r)
    return out


def start(rows) -> bool:
    with _LOCK:
        if _STATE["running"]:
            return False
        _STATE["running"] = True
    import jobrunner
    if jobrunner.isolate():
        with _LOCK:
            _STATE["running"] = False
        return jobrunner.start("measures", rows)
    threading.Thread(target=run, args=(rows,), daemon=True, name="measures").start()
    return True


def state() -> dict:
    with _LOCK:
        return dict(_STATE, results=list(_STATE["results"]))


def _stats(rs: list, unit: str) -> dict:
    d = [r[f"{unit}_diff_pct"] for r in rs if r.get(f"{unit}_diff_pct") is not None]
    if not d:
        return {"files": 0}
    d.sort()
    mid = d[len(d) // 2] if len(d) % 2 else (d[len(d) // 2 - 1] + d[len(d) // 2]) / 2
    return {"files": len(d),
            "avg_abs_pct": round(sum(abs(x) for x in d) / len(d), 1),
            "median_pct": round(mid, 1),
            "within_pct": round(sum(1 for x in d if abs(x) <= WITHIN_PCT) / len(d) * 100),
            "under_estimated": sum(1 for x in d if x > WITHIN_PCT),
            "over_estimated": sum(1 for x in d if x < -WITHIN_PCT)}


def summary(results=None) -> dict:
    """Dashboard figures: overall and per coordinator, for volume and weight."""
    rs = state()["results"] if results is None else results
    by_coord = {}
    for r in rs:
        by_coord.setdefault(r.get("coord") or "Unassigned", []).append(r)
    return {
        "files_checked": len(rs),
        "with_estimate": sum(1 for r in rs if r.get("est")),
        "with_actual": sum(1 for r in rs if r.get("act")),
        "compared": sum(1 for r in rs if r.get("m3_diff_pct") is not None or r.get("kg_diff_pct") is not None),
        "volume": _stats(rs, "m3"), "weight": _stats(rs, "kg"),
        "within_pct_band": WITHIN_PCT,
        "by_coord": {c: {"files": len(v), "volume": _stats(v, "m3"), "weight": _stats(v, "kg")}
                     for c, v in by_coord.items()},
    }
