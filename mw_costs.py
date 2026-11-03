"""
mw_costs.py — line-item revenue and cost per file, straight from Moveware's V2 API
(Bill, 3 Nov 2026: "we need to capture all the line item revenue and cost").

Moveware added cost amounts to the option charge lines (MOS-49173, Dave Pile,
20 Oct 2026; live by 3 Nov). Each line of GET /options/{opt}/charges carries, in
the line's currency and WITHOUT IVA when read from the *Exclusive fields:

    valueExclusive          sale price of the line
    valueEstimateExclusive  quoted (estimated) cost
    valueActualExclusive    actual cost loaded so far (supplier invoices)
    profit / profitActual   sale minus estimated / actual cost

Rules, the same as the Move Costing Sheet (Lupita, 9-12 Oct 2026): only cost
options that are won count; a file can have several won options (air, extras) and
they add up; lettered sequels (110771A) are added to their base file. A line with
an estimate and no actual yet is "pending" (supplier invoice not loaded).

The result has the same shape as costing_sheet.summarize(), so the audit can use
the API where it has a file and fall back to the uploaded sheet where it doesn't.
Read-only. Charge descriptions are kept (service names) with the customer's name
removed; no names are stored otherwise.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

WON_WORDS = ("w", "won", "accepted", "selected", "confirmed", "booked")
LOST_WORDS = ("c", "l", "cancelled", "canceled", "lost", "declined")

_STATE = {"running": False, "built_at": None, "error": None, "done": 0, "total": 0,
          "files": {}, "status_field": None}
_LOCK = threading.Lock()


def _path() -> str:
    for d in ("/var/data", "/data"):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return os.path.join(d, "mw_costs.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "mw_costs.json")


def base(job: str) -> str:
    s = str(job or "").strip().upper()
    while s and not s[-1].isdigit():
        s = s[:-1]
    return s


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def option_status(opt: dict) -> tuple:
    """(field, value) of the option's won/lost status, whichever field Moveware
    uses for it. ('', '') when the option says nothing."""
    for k in ("costStatus", "statusCost", "status", "statusQuote", "quoteStatus", "optionStatus"):
        v = opt.get(k)
        if isinstance(v, dict):
            v = v.get("code") or v.get("text")
        if v not in (None, ""):
            return k, str(v).strip()
    return "", ""


def won_options(options: list) -> list:
    """The options that count. Won ones when the options say; if no option carries
    a status, every option that isn't marked lost/cancelled (the costing sheet
    shows the same: a file's W options)."""
    opts = [o for o in options or [] if isinstance(o, dict) and o.get("id")]
    marked = [(o, option_status(o)[1].lower()) for o in opts]
    won = [o for o, s in marked if s in WON_WORDS]
    if won:
        return won
    if any(s for _, s in marked):
        return [o for o, s in marked if s and s not in LOST_WORDS]
    return opts


def lines_from_charges(charges: list, scrub=None) -> list:
    out = []
    for c in charges or []:
        if not isinstance(c, dict):
            continue
        sell, est, act = (_num(c.get("valueExclusive")), _num(c.get("valueEstimateExclusive")),
                          _num(c.get("valueActualExclusive")))
        if not (sell or est or act):
            continue
        desc = str(c.get("description") or "")
        out.append({"id": c.get("id"), "desc": scrub(desc) if scrub else desc,
                    "sell": round(sell, 2), "est": round(est, 2), "act": round(act, 2),
                    "currency": c.get("currency") or "",
                    "supplier": c.get("supplierCode") or "", "po": c.get("purchase") or "",
                    "modified": str(c.get("dateModified") or "")[:10]})
    return out


def summarize_file(job: str, parts: list, lines: list) -> dict:
    """Same keys as costing_sheet.summarize() for one base file."""
    e = {"job": job, "parts": parts, "est": 0.0, "act": 0.0, "sell": 0.0, "lines": 0,
         "lines_with_act": 0, "lines_pending": 0, "est_pending": 0.0, "last_actual": "",
         "client_type": "", "move_type": "", "source": "moveware_api",
         "currency": "", "line_items": lines}
    ccy = {ln["currency"] for ln in lines if ln.get("currency")}
    e["currency"] = ccy.pop() if len(ccy) == 1 else ("mixed" if ccy else "")
    for ln in lines:
        e["lines"] += 1
        e["sell"] += ln["sell"]
        e["est"] += ln["est"]
        if ln["act"]:
            e["lines_with_act"] += 1
            e["act"] += ln["act"]
            e["last_actual"] = max(e["last_actual"], ln.get("modified") or "")
        elif ln["est"] > 0:
            e["lines_pending"] += 1
            e["est_pending"] += ln["est"]
    for k in ("est", "act", "sell", "est_pending"):
        e[k] = round(e[k], 2)
    e["complete"] = e["lines_with_act"] > 0 and e["lines_pending"] == 0
    e["cost_best"] = round(e["act"] + e["est_pending"], 2) if e["lines_with_act"] else None
    e["profit_est"] = round(e["sell"] - e["est"], 2)
    e["profit_best"] = round(e["sell"] - e["cost_best"], 2) if e["cost_best"] is not None else None
    return e


def read_file(job_id, client: str = "") -> list:
    """All counted charge lines of one Moveware job (one lot)."""
    import mw_live
    try:
        import costing_sheet as cs
        parts = cs._name_parts(client)
        scrub = (lambda t: cs._scrub(t, parts))
    except Exception:
        scrub = None
    od = mw_live._get_timed(f"/jobs/{job_id}/options", 15) or {}
    options = od.get("options") if isinstance(od, dict) else None
    lines = []
    for o in won_options(options or []):
        cd = mw_live._get_timed(f"/options/{o['id']}/charges", 15) or {}
        lines += lines_from_charges(cd.get("charges") if isinstance(cd, dict) else [], scrub)
    return lines


def build(files: list, pause: float = 0.05) -> dict:
    """{base_file: summary} for the audited files (every lot read, lots added up)."""
    by_base, parts = {}, {}
    with _LOCK:
        _STATE.update(running=True, error=None, done=0, total=len(files))
    try:
        for f in files:
            jid = f.get("job_id")
            if not jid:
                continue
            b = base(f.get("job"))
            try:
                ln = read_file(jid, f.get("client") or "")
            except Exception:
                ln = None
            if ln is not None:
                by_base.setdefault(b, []).extend(ln)
                parts.setdefault(b, []).append(str(f.get("job")))
            with _LOCK:
                _STATE["done"] += 1
            time.sleep(pause)
        out = {b: summarize_file(b, parts[b], ln) for b, ln in by_base.items() if ln}
        with _LOCK:
            _STATE.update(files=out, built_at=time.time())
        try:
            with open(_path() + ".tmp", "w") as fh:
                json.dump({"built_at": time.time(), "files": out}, fh)
            os.replace(_path() + ".tmp", _path())
        except Exception:
            pass
        return out
    except Exception as e:                       # noqa: BLE001
        with _LOCK:
            _STATE["error"] = f"{type(e).__name__}: {e}"[:300]
        return {}
    finally:
        with _LOCK:
            _STATE["running"] = False


def load_saved() -> None:
    """After a restart, use the last build until a new one finishes."""
    with _LOCK:
        if _STATE["files"]:
            return
    try:
        with open(_path()) as fh:
            d = json.load(fh)
        with _LOCK:
            _STATE.update(files=d.get("files") or {}, built_at=d.get("built_at"))
    except Exception:
        pass


def ensure_built(max_age_h: float = 20.0) -> bool:
    """Start a background build when there is none or it is older than max_age_h."""
    import mw_live
    load_saved()
    with _LOCK:
        if _STATE["running"]:
            return False
        fresh = _STATE["built_at"] and time.time() - _STATE["built_at"] < max_age_h * 3600
    if fresh:
        return False
    files = [f for f in (mw_live.audited_in_window() or []) if f.get("job_id")]
    if not files:
        return False
    threading.Thread(target=build, args=(files,), daemon=True, name="mw-costs").start()
    return True


def costs() -> dict:
    load_saved()
    with _LOCK:
        return dict(_STATE["files"])


def state() -> dict:
    with _LOCK:
        return {k: v for k, v in _STATE.items() if k != "files"} | {"n_files": len(_STATE["files"])}


def merged(sheet: dict) -> dict:
    """The cost per file for the audit: Moveware's API where it has the file's
    charge lines (live, every file), the uploaded Move Costing Sheet otherwise."""
    out = dict(sheet or {})
    for b, e in costs().items():
        if e.get("lines"):
            out[b] = e
    return out


def compare(api: dict, sheet: dict, tol: float = 0.02) -> dict:
    """Files in both the API and the uploaded Move Costing Sheet: do the quoted and
    actual costs agree (within 2 % or 1 unit)? The sheet's sale price includes IVA
    on taxed files, so only costs are compared."""
    rows, counts = [], {"both": 0, "est_match": 0, "act_match": 0, "api_only": 0, "sheet_only": 0}
    close = lambda a, b: abs(a - b) <= max(1.0, tol * max(abs(a), abs(b)))
    for b in sorted(set(api) | set(sheet)):
        a, s = api.get(b), sheet.get(b)
        if a and not s:
            counts["api_only"] += 1
            continue
        if s and not a:
            counts["sheet_only"] += 1
            continue
        counts["both"] += 1
        em, am = close(a["est"], s["est"]), close(a["act"], s["act"])
        counts["est_match"] += em
        counts["act_match"] += am
        if not (em and am):
            rows.append({"job": b, "api_est": a["est"], "sheet_est": s["est"],
                         "api_act": a["act"], "sheet_act": s["act"]})
    rows.sort(key=lambda r: -abs((r["api_act"] - r["sheet_act"]) or (r["api_est"] - r["sheet_est"])))
    return {"counts": counts, "differences": rows}
