"""
costing_sheet.py — supplier cost per file from MoveWare's Move Costing Sheet.

Why: the V2 API does not return cost yet (MOS-49173). Lupita exports the Move
Costing Sheet every month; it carries, per file and cost option, the quoted cost,
the sale price and, line by line, the ACTUAL cost loaded with its supplier
(creditor), approval number (PO, the "Invoice" column) and date. This module reads
it line by line so the audit can

  * show a margin for files Finance has not costed yet (interim cost source), and
  * check supplier costs: over the quote, the same approval number on two files,
    actual cost with no approval number, quoted cost with no supplier invoice.

Rules (Lupita, 9–12 Oct 2026): only cost options with status W count (C cancelled,
L lost); a file can have several W options (air + extras) and they add up; lines
printed below "Option Total" are extras and count. Actual costs are net of IVA
(they match Finance's net cost); sale prices on taxed files include IVA.
The currency is the file's: MoveWare's own, else the branch (…U dollars, …M pesos).

Nothing kept here carries a customer name: job numbers, codes, supplier names,
approval numbers, dates and amounts only.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import threading
import warnings

_JOB = re.compile(r"^\d{5,7}[A-Z]{0,2}$")
_LOCK = threading.Lock()
_CACHE: dict = {}
STORE_KEY = "audit_costing_lines"
SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "costing_sheet_seed.json")


def base(job: str) -> str:
    s = str(job or "").strip().upper()
    while s and not s[-1].isdigit():
        s = s[:-1]
    return s


def _num(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _iso(v):
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    return None


def parse(src) -> dict:
    """src: path, bytes or file object of the .xlsx. Returns
    {"period": {won_from, won_to}, "files": {job: {...}}}; raises ValueError when
    the workbook is not a Move Costing Sheet."""
    import openpyxl
    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(src)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ws = openpyxl.load_workbook(src, data_only=True, read_only=True).active
        rows = [list(r) + [None] * 17 for r in ws.iter_rows(values_only=True)]
    title = str(rows[0][0] or "") if rows else ""
    period = {}
    for key, label in (("won_from", "Won From"), ("won_to", "Won To")):
        m = re.search(label + r":\s*(\d{1,2})/(\d{1,2})/(\d{2})\b", title)
        period[key] = dt.date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat() if m else ""
    files: dict = {}
    cur = opt = None
    for r in rows[1:]:
        a = r[0].strip() if isinstance(r[0], str) else r[0]
        if a in (None, ""):
            continue
        if a == "Removal Plan":
            job = str(r[2] or "").strip().upper()
            if not _JOB.match(job):
                cur = opt = None
                continue
            meta = " ".join(str(c) for c in r[12:17] if c)
            ct = re.search(r"Client Type:\s*(\w+)", meta)
            cur = files.setdefault(job, {"job": job, "move_type": str(r[11] or "").strip().upper(),
                                         "client_type": ct.group(1) if ct else "", "options": []})
            opt = None
        elif a == "Cost Option" and cur is not None:
            opt = {"no": str(r[2] or "").strip(), "status": str(r[8] or "").strip().upper(), "lines": []}
            cur["options"].append(opt)
        elif a in ("ESTIMATED COSTS", "Profit Margin", "Option Total") or opt is None:
            continue
        else:
            opt["lines"].append({
                "desc": str(a)[:80], "est": _num(r[3]), "sell": _num(r[6]), "act": _num(r[8]),
                "date": _iso(r[9]), "acc": str(r[10] or "").strip(), "inv": str(r[11] or "").strip(),
                "po": str(r[12] or "").strip(), "creditor": str(r[13] or "").strip()[:80]})
    if not files or not any(f["options"] for f in files.values()):
        raise ValueError("This does not look like the MoveWare Move Costing Sheet "
                         "(no Removal Plan with cost options was found).")
    return {"period": period, "files": files}


def summarize(parsed: dict) -> dict:
    """Per BASE file (sequels A, B… added to their base, as Finance reports them):
    quoted cost, actual cost, sale price (file currency), line counts and whether
    every quoted line has its actual cost loaded."""
    out: dict = {}
    for job, f in (parsed.get("files") or {}).items():
        b = base(job)
        e = out.setdefault(b, {"job": b, "parts": [], "est": 0.0, "act": 0.0, "sell": 0.0,
                               "lines": 0, "lines_with_act": 0, "lines_pending": 0, "est_pending": 0.0,
                               "last_actual": "", "client_type": f.get("client_type") or "",
                               "move_type": f.get("move_type") or ""})
        e["parts"].append(job)
        for o in f.get("options") or []:
            if o.get("status") != "W":
                continue
            for ln in o.get("lines") or []:
                e["lines"] += 1
                e["est"] += ln.get("est") or 0.0
                e["sell"] += ln.get("sell") or 0.0
                if ln.get("act") is not None:
                    e["lines_with_act"] += 1
                    e["act"] += ln["act"]
                    e["last_actual"] = max(e["last_actual"], ln.get("date") or "")
                elif (ln.get("est") or 0) > 0:
                    e["lines_pending"] += 1
                    e["est_pending"] += ln["est"]
    for e in out.values():
        for k in ("est", "act", "sell", "est_pending"):
            e[k] = round(e[k], 2)
        e["complete"] = e["lines_with_act"] > 0 and e["lines_pending"] == 0
        # Best cost today: what is loaded, plus the quoted cost of lines still
        # waiting for their supplier invoice (a provision, as Finance books it).
        e["cost_best"] = round(e["act"] + e["est_pending"], 2) if e["lines_with_act"] else None
    return out


# ── currency ─────────────────────────────────────────────────────────────────
def file_currency(job: str, mw_currency: str = "", branch: str = "") -> str:
    """'USD', 'MXN', 'CAD' or '' — the known exceptions first, then MoveWare's
    own currency, then the branch letter."""
    try:
        from commissions.quotes import BRANCH_CURRENCY
    except Exception:
        BRANCH_CURRENCY = {}
    b = base(job)
    if b in BRANCH_CURRENCY:
        return BRANCH_CURRENCY[b]
    c = (mw_currency or "").upper().strip()
    if c in ("USD", "MXN", "CAD", "EUR"):
        return c
    br = (branch or "").upper().strip()
    return "USD" if br.endswith("U") else "MXN" if br.endswith("M") else ""


def to_mxn(amount: float, ccy: str, ledger_entry: dict | None = None, plan_rate: float = 17.4479):
    """Amount in pesos: pesos as they are; dollars at the rate Finance booked on
    the file's invoices when it has only dollar invoices, else the plan rate.
    None when the currency is unknown."""
    if amount is None:
        return None
    if ccy == "MXN":
        return round(amount, 2)
    e = ledger_entry or {}
    orig = e.get("inv_orig") or {}
    if ccy in ("USD", "CAD") and set(orig) == {ccy} and orig.get(ccy) and e.get("inv_mxn"):
        return round(amount * e["inv_mxn"] / orig[ccy], 2)
    if ccy == "USD":
        return round(amount * plan_rate, 2)
    return None


# ── storage ──────────────────────────────────────────────────────────────────
def save(parsed: dict, by: str = "") -> None:
    """Keep the parsed sheet in the app's database (commission_docs table, which
    survives a redeploy); fall back to /var/data."""
    doc = {"period": parsed.get("period") or {}, "files": parsed.get("files") or {},
           "loaded_at": dt.datetime.utcnow().isoformat() + "Z", "by": by}
    try:
        from commissions import store
        store.put(STORE_KEY, doc, by=by)
    except Exception:
        path = os.environ.get("COSTING_SHEET_PATH", "/var/data/costing_sheet.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(doc, fh)
    with _LOCK:
        _CACHE.clear()


def load() -> dict:
    """The latest sheet: database, then /var/data, then the seed shipped with the
    app (the sheet Lupita sent on 9 Oct 2026). Cached for 10 minutes."""
    import time
    with _LOCK:
        if _CACHE.get("at") and time.time() - _CACHE["at"] < 600:
            return _CACHE["doc"]
    doc = None
    try:
        from commissions import store
        got = store.get(STORE_KEY)
        doc = got[0] if isinstance(got, tuple) else got
    except Exception:
        doc = None
    if not doc:
        for p in (os.environ.get("COSTING_SHEET_PATH", "/var/data/costing_sheet.json"), SEED):
            try:
                with open(p) as fh:
                    doc = json.load(fh)
                    doc.setdefault("source", os.path.basename(p))
                    break
            except Exception:
                continue
    doc = doc or {"period": {}, "files": {}}
    with _LOCK:
        _CACHE.update(at=time.time(), doc=doc)
    return doc


def costs() -> dict:
    """{base_job: summary} from the latest sheet."""
    return summarize(load())


# ── supplier cost checks ─────────────────────────────────────────────────────
OVER_QUOTE = 0.10          # actual more than 10% above the quoted line cost
OVER_QUOTE_MIN = 1000.0    # …and by at least this much (file currency)
INVOICE_LATE_DAYS = 60     # quoted cost still without supplier invoice this long after delivery


def supplier_checks(parsed: dict, delivered: dict | None = None, today: dt.date | None = None) -> list:
    """One row per finding. `delivered` = {base_job: 'YYYY-MM-DD'} (MoveWare or
    Plan de Viajes) to tell which pending supplier invoices are overdue."""
    today = today or dt.date.today()
    delivered = delivered or {}
    rows, po_seen = [], {}
    for job, f in (parsed.get("files") or {}).items():
        b = base(job)
        for o in f.get("options") or []:
            if o.get("status") != "W":
                continue
            for ln in o.get("lines") or []:
                est, act, po = ln.get("est"), ln.get("act"), (ln.get("po") or "").strip()
                common = {"job": job, "line": ln.get("desc"), "creditor": ln.get("creditor") or "",
                          "quoted": est, "actual": act, "po": po, "date": ln.get("date") or ""}
                if act is not None and est and est > 0 and act > est * (1 + OVER_QUOTE) \
                        and act - est >= OVER_QUOTE_MIN:
                    rows.append({**common, "check": "over_quote", "difference": round(act - est, 2),
                                 "pct": round((act - est) / est * 100, 1)})
                if act is not None and act != 0 and not po:
                    rows.append({**common, "check": "no_approval"})
                if po and act is not None:
                    po_seen.setdefault(po, set()).add(b)
                if act is None and (est or 0) > 0:
                    d = delivered.get(b)
                    try:
                        late = d and (today - dt.date.fromisoformat(d)).days > INVOICE_LATE_DAYS
                    except ValueError:
                        late = False
                    if late:
                        rows.append({**common, "check": "invoice_missing", "delivered": d,
                                     "days": (today - dt.date.fromisoformat(d)).days})
    dup = {po: sorted(js) for po, js in po_seen.items() if len(js) > 1}
    for job, f in (parsed.get("files") or {}).items():
        for o in f.get("options") or []:
            if o.get("status") != "W":
                continue
            for ln in o.get("lines") or []:
                po = (ln.get("po") or "").strip()
                if po in dup and ln.get("act") is not None:
                    rows.append({"job": job, "line": ln.get("desc"), "creditor": ln.get("creditor") or "",
                                 "quoted": ln.get("est"), "actual": ln.get("act"), "po": po,
                                 "date": ln.get("date") or "", "check": "po_on_two_files",
                                 "other_files": [x for x in dup[po] if x != base(job)]})
    return rows


CHECK_LABELS = {
    "over_quote": ("Actual cost more than 10% over the quote", "Costo real más de 10% arriba de lo cotizado"),
    "no_approval": ("Actual cost loaded with no approval number", "Costo real cargado sin número de aprobación"),
    "po_on_two_files": ("Same approval number on two files", "Mismo número de aprobación en dos expedientes"),
    "invoice_missing": ("Quoted cost with no supplier invoice 60+ days after delivery",
                        "Costo cotizado sin factura de proveedor 60+ días después de la entrega"),
}


# ── costing sheet vs Finance's margin report ─────────────────────────────────
MATCH_TOLERANCE = 0.05


def compare_finance(summary: dict, finance: dict, currencies: dict | None = None,
                    plan_rate: float = 17.4479) -> list:
    """File by file: actual cost loaded in MoveWare (costing sheet) against the
    total cost in Finance's margin report (supplier + intercompany, MXN).
    `currencies` = {base_job: (mw_currency, branch)}. One row per file with a
    finding: match / lower: lines not loaded / higher / lower / none in Finance /
    no actual cost yet / not in Finance."""
    currencies = currencies or {}
    fin_files = (finance or {}).get("files") or {}
    ledger = (finance or {}).get("ledger") or {}
    rows = []
    for job, e in sorted(summary.items()):
        cur, br = currencies.get(job, ("", ""))
        ccy = file_currency(job, cur, br)
        L = ledger.get(job) or {}
        if not ccy and len(L.get("inv_orig") or {}) == 1:
            ccy = next(iter(L["inv_orig"]))           # the one currency Finance invoiced it in
        act_mxn = to_mxn(e["act"], ccy, L, plan_rate) if ccy else None
        F = fin_files.get(job)
        fin = (F or {}).get("total_cost")
        row = {"job": job, "currency": ccy, "lines": f'{e["lines_with_act"]} of {e["lines"]}',
               "act": e["act"], "act_mxn": act_mxn, "finance_cost_mxn": fin, "difference_mxn": None}
        if not ccy or (e["lines_with_act"] and act_mxn is None):
            row["finding"] = "currency_unknown"
        elif F is None:
            row["finding"] = "not_in_finance" if e["lines_with_act"] else "no_cost_yet"
        elif not e["lines_with_act"]:
            row["finding"] = "no_actual_in_moveware" if fin else "no_cost_yet"
        elif not fin:
            row["finding"] = "none_in_finance"
        else:
            d = (act_mxn - fin) / fin
            row["difference_mxn"] = round(act_mxn - fin, 2)
            if abs(d) <= MATCH_TOLERANCE:
                row["finding"] = "match"
            elif d < 0 and e["lines_pending"]:
                row["finding"] = "lines_not_loaded"
            else:
                row["finding"] = "higher" if d > 0 else "lower"
        rows.append(row)
    return rows


FINDING_LABELS = {
    "match": ("Matches Finance (within 5%)", "Coincide con Finanzas (±5%)"),
    "lines_not_loaded": ("Lower in MoveWare: cost lines not loaded", "Menor en MoveWare: faltan líneas de costo"),
    "no_actual_in_moveware": ("No actual cost in MoveWare yet", "Sin costo real en MoveWare"),
    "higher": ("Higher in MoveWare than Finance", "Mayor en MoveWare que en Finanzas"),
    "lower": ("Lower in MoveWare than Finance", "Menor en MoveWare que en Finanzas"),
    "none_in_finance": ("Cost in MoveWare, none in Finance", "Costo en MoveWare, sin costo en Finanzas"),
    "not_in_finance": ("Not in Finance's margin report", "No está en el reporte de margen de Finanzas"),
    "no_cost_yet": ("No cost on either side yet", "Sin costo en ningún lado"),
    "currency_unknown": ("Currency unknown", "Moneda desconocida"),
}
