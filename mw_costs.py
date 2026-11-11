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

# Bump when the reading rules change, so a build saved by older rules is redone.
RULES_VERSION = 6      # 6: every page of options and charges (11 Nov 2026)

WON_WORDS = ("w", "won", "accepted", "selected", "confirmed", "booked")
LOST_WORDS = ("c", "l", "cancelled", "canceled", "lost", "declined")

_STATE = {"running": False, "built_at": None, "error": None, "done": 0, "total": 0,
          "files": {}, "status_field": None, "reported": False}
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
        # Options carry a status (W won, Q quoted alternative, C cancelled — seen
        # live 3 Nov 2026) and none is won: no booked cost yet.
        return []
    return opts


def lines_from_charges(charges: list, scrub=None) -> list:
    out = []
    for c in charges or []:
        if not isinstance(c, dict):
            continue
        sell, est, act = (_num(c.get("valueExclusive")), _num(c.get("valueEstimateExclusive")),
                          _num(c.get("valueActualExclusive")))
        est_inc = _num(c.get("valueEstimateInclusive")) or est
        if not (sell or est or act):
            continue
        desc = str(c.get("description") or "")
        if is_total_line(desc):
            continue            # Moveware's "Option Total" line repeats the others
        out.append({"id": c.get("id"), "desc": scrub(desc) if scrub else desc,
                    "sell": round(sell, 2), "est": round(est, 2), "act": round(act, 2),
                    "est_inc": round(est_inc, 2),
                    # amounts are in the option's currency (111108: USD and MXN lines
                    # add up to the option total); this is the line's cost currency
                    "currency": c.get("currency") or "",
                    "supplier": c.get("supplierCode") or "", "po": c.get("purchase") or "",
                    "modified": str(c.get("dateModified") or "")[:10]})
    return out


def is_total_line(description: str) -> bool:
    """Moveware puts a line called "Option Total" in each quote option's charges
    (seen live 3 Nov 2026 on every file: 110578, 110586, 110601 ...). It repeats the
    lines above it (extras printed below it are not in it), so it is never counted:
    on 110578 its actual cost, 400,347.23, is exactly the Costing Sheet's."""
    return " ".join(str(description or "").split()).lower() in ("option total", "total option",
                                                                 "total de la opcion", "total opcion")


def drop_total_line(lines: list, option_value=None) -> list:
    """Kept for callers; the "Option Total" line is now left out by its name in
    lines_from_charges (the sum test missed it when extras follow it)."""
    return lines


def summarize_file(job: str, parts: list, lines: list) -> dict:
    """Same keys as costing_sheet.summarize() for one base file."""
    e = {"job": job, "parts": parts, "est": 0.0, "act": 0.0, "sell": 0.0, "lines": 0,
         "lines_with_act": 0, "lines_pending": 0, "est_pending": 0.0, "last_actual": "",
         "client_type": "", "move_type": "", "source": "moveware_api",
         "currency": "", "line_items": lines}
    ccy = {ln.get("option_currency") or ln["currency"] for ln in lines
           if ln.get("option_currency") or ln.get("currency")}
    e["currency"] = ccy.pop() if len(ccy) == 1 else ""      # mixed: the file's own currency applies
    e["est_inc"] = 0.0
    for ln in lines:
        e["lines"] += 1
        e["sell"] += ln["sell"]
        e["est"] += ln["est"]
        e["est_inc"] += ln.get("est_inc", ln["est"])
        if ln["act"]:
            e["lines_with_act"] += 1
            e["act"] += ln["act"]
            e["last_actual"] = max(e["last_actual"], ln.get("modified") or "")
        elif ln["est"] > 0:
            e["lines_pending"] += 1
            e["est_pending"] += ln["est"]
    for k in ("est", "act", "sell", "est_pending", "est_inc"):
        e[k] = round(e[k], 2)
    e["complete"] = e["lines_with_act"] > 0 and e["lines_pending"] == 0
    e["cost_best"] = round(e["act"] + e["est_pending"], 2) if e["lines_with_act"] else None
    e["profit_est"] = round(e["sell"] - e["est"], 2)
    e["profit_best"] = round(e["sell"] - e["cost_best"], 2) if e["cost_best"] is not None else None
    return e


MAX_PAGES = 50


class ReadFailed(RuntimeError):
    """A page of a per-job list could not be read: the whole read counts as failed."""


def _key(item) -> str:
    if isinstance(item, dict) and item.get("id") is not None:
        return f"id:{item['id']}"
    return json.dumps(item, sort_keys=True, default=str)


def get_all(path: str, key: str, fetch=None, timeout: int = 15) -> list:
    """Every item of a per-job Moveware list, page after page (lessons learned,
    11 Nov 2026: per-job lists are paged too; reading page 1 only cut cost on
    labour-heavy jobs by 50-75 %). Asks for page 2, 3 ... until a page brings
    nothing new, so a server that ignores paging costs one extra read. A page
    that fails makes the whole read fail (ReadFailed): a short cost total is
    worse than none."""
    import mw_live
    fetch = fetch or (lambda p: mw_live._get_timed(p, timeout))
    items, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        sep = "&" if "?" in path else "?"
        try:
            data = fetch(path if page == 1 else f"{path}{sep}page={page}&offset={page}")
        except Exception as e:                    # noqa: BLE001
            if page > 1 and "404" in str(e):
                break                             # past the last page
            raise ReadFailed(f"{path} page {page}: {type(e).__name__}") from None
        if data is None:
            raise ReadFailed(f"{path} page {page}")
        got = data.get(key) if isinstance(data, dict) else (data if isinstance(data, list) else None)
        if got is None:
            if page == 1 and isinstance(data, dict):
                return []                         # an empty answer: nothing on this job
            raise ReadFailed(f"{path} page {page}: unexpected answer")
        new = [x for x in got if _key(x) not in seen]
        if not new:
            break
        seen.update(_key(x) for x in new)
        items += new
    return items


def read_file(job_id, client: str = "") -> list:
    """All counted charge lines of one Moveware job (one lot)."""
    import mw_live
    try:
        import costing_sheet as cs
        parts = cs._name_parts(client)
        scrub = (lambda t: cs._scrub(t, parts))
    except Exception:
        scrub = None
    options = get_all(f"/jobs/{job_id}/options", "options")
    lines = []
    for o in won_options(options):
        got = lines_from_charges(get_all(f"/options/{o['id']}/charges", "charges"), scrub)
        for ln in got:     # amounts are in the option's currency (live 3 Nov 2026)
            ln["option_currency"] = o.get("currency") or ""
        lines += drop_total_line(got, o.get("valueExclusive"))
    return lines


def build(files: list, pause: float = 0.05) -> dict:
    """{base_file: summary} for the audited files (every lot read, lots added up)."""
    by_base, parts = {}, {}
    with _LOCK:
        _STATE.update(running=True, error=None, done=0, total=len(files))
    try:
        seen_jobs, seen_lines = set(), set()
        for f in files:
            jid = f.get("job_id")
            if not jid or str(jid) in seen_jobs:
                continue                       # one read per Moveware job
            seen_jobs.add(str(jid))
            b = base(f.get("job"))
            try:
                ln = read_file(jid, f.get("client") or "")
            except Exception:
                ln = None
            if ln is not None:
                fresh = [x for x in ln if x.get("id") is None or x["id"] not in seen_lines]
                seen_lines.update(x["id"] for x in fresh if x.get("id") is not None)
                by_base.setdefault(b, []).extend(fresh)
                parts.setdefault(b, []).append(str(f.get("job")))
            with _LOCK:
                _STATE["done"] += 1
            time.sleep(pause)
        out = {b: summarize_file(b, parts[b], ln) for b, ln in by_base.items() if ln}
        with _LOCK:
            _STATE.update(files=out, built_at=time.time())
        if not _reported():
            report(out)
        try:
            with open(_path() + ".tmp", "w") as fh:
                json.dump({"built_at": time.time(), "rules": RULES_VERSION, "files": out}, fh)
            os.replace(_path() + ".tmp", _path())
        except Exception:
            pass
        return out
    except Exception as e:                       # noqa: BLE001
        with _LOCK:
            _STATE["error"] = f"{type(e).__name__}: {e}"[:300]
        _email_problem(f"The line-item cost read from Moveware stopped: {_STATE['error']}")
        return {}
    finally:
        with _LOCK:
            _STATE["running"] = False
        try:
            os.remove(_path() + ".building")
        except Exception:
            pass


def load_saved() -> None:
    """After a restart, use the last build until a new one finishes."""
    with _LOCK:
        if _STATE["files"]:
            return
    try:
        with open(_path()) as fh:
            d = json.load(fh)
        if d.get("rules") != RULES_VERSION:
            return                                  # built by older rules: rebuild
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
        with _LOCK:
            files_now = dict(_STATE["files"])
        if files_now and not _reported():
            threading.Thread(target=report, args=(files_now,), daemon=True, name="mw-costs-report").start()
        return False
    if _building_elsewhere():
        return False                        # another worker is reading Moveware now
    files = [f for f in (mw_live.audited_in_window() or []) if f.get("job_id")]
    if not files:
        return False
    _mark_building()
    import jobrunner
    if jobrunner.isolate():           # own process, own memory cap (4 Nov 2026)
        return jobrunner.start("mw_costs", files)
    threading.Thread(target=build, args=(files,), daemon=True, name="mw-costs").start()
    return True


def _reported() -> bool:
    """The comparison email goes out once per reading-rules version, whatever the
    number of restarts or server workers (3 Nov 2026: it went out 8 times)."""
    try:
        with open(_path() + ".reported") as fh:
            return fh.read().strip() == str(RULES_VERSION)
    except Exception:
        return False


def _mark_reported() -> None:
    try:
        with open(_path() + ".reported", "w") as fh:
            fh.write(str(RULES_VERSION))
    except Exception:
        pass


def _building_elsewhere(max_age_s: int = 2 * 3600) -> bool:
    """Another worker of THIS deploy is reading Moveware now: its marker is recent
    and the process that wrote it is still alive (a marker left by a process a
    restart killed doesn't block — 3 Nov 2026)."""
    try:
        p = _path() + ".building"
        if time.time() - os.path.getmtime(p) >= max_age_s:
            return False
        with open(p) as fh:
            pid = int((fh.read().split() or ["0"])[0])
        if pid == os.getpid() or pid <= 0:
            return False
        os.kill(pid, 0)                        # raises if that process is gone
        return True
    except Exception:
        return False


def _mark_building() -> None:
    try:
        with open(_path() + ".building", "w") as fh:
            fh.write(f"{os.getpid()} {time.time()}")
    except Exception:
        pass


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
        # the sheet's quoted cost includes IVA on taxed lines; the API gives both
        em = close(a["est"], s["est"]) or close(a.get("est_inc", a["est"]), s["est"])
        am = close(a["act"], s["act"])
        counts["est_match"] += em
        counts["act_match"] += am
        if not (em and am):
            rows.append({"job": b, "api_est": a["est"], "sheet_est": s["est"],
                         "api_act": a["act"], "sheet_act": s["act"]})
    rows.sort(key=lambda r: -abs((r["api_act"] - r["sheet_act"]) or (r["api_est"] - r["sheet_est"])))
    return {"counts": counts, "differences": rows}


def report_text(api: dict, sheet: dict) -> str:
    cmp = compare(api, sheet)
    c = cmp["counts"]
    with_act = sum(1 for e in api.values() if e["lines_with_act"])
    complete = sum(1 for e in api.values() if e["complete"])
    lines = [
        "Line-item revenue and cost from Moveware's API — first full read",
        "",
        f"Files read from the API: {len(api)} (won quote options only, lots added to their file).",
        f"  with at least one actual cost loaded: {with_act}",
        f"  every quoted cost line has its actual cost: {complete}",
        f"  cost lines still waiting for a supplier invoice: {sum(e['lines_pending'] for e in api.values())}",
        "",
        "Compared with the last Move Costing Sheet uploaded to the app:",
        f"  files in both: {c['both']}",
        f"  quoted (estimated) cost agrees: {c['est_match']} of {c['both']}",
        f"  actual cost agrees: {c['act_match']} of {c['both']}",
        f"  only in the API (newer than the sheet, or not in it): {c['api_only']}",
        f"  only in the sheet: {c['sheet_only']}",
        "",
        "Largest differences (file: API vs sheet, estimated | actual):",
    ]
    for r in cmp["differences"][:40]:
        lines.append(f"  {r['job']}: est {r['api_est']:,.2f} vs {r['sheet_est']:,.2f} | "
                     f"act {r['api_act']:,.2f} vs {r['sheet_act']:,.2f}")
    lines += ["", "Line detail of the 8 largest differences (API: sell / est / actual per line):"]
    for r in cmp["differences"][:8]:
        e = api.get(r["job"]) or {}
        lines.append(f"  {r['job']} (parts {', '.join(e.get('parts') or [])}):")
        for ln in (e.get("line_items") or [])[:25]:
            lines.append(f"     #{ln.get('id')} {ln.get('desc', '')[:30]:30} {ln['sell']:>12,.2f} "
                         f"{ln['est']:>12,.2f} {ln['act']:>12,.2f} {ln.get('currency', '')}")
    lines += ["", "The audit now uses the API's cost wherever it has the file, the sheet otherwise.",
              "Per-supplier detail (creditor, PO per invoice) is not in the API; the sheet still feeds the supplier checks."]
    return "\n".join(lines)


def report(api: dict) -> bool:
    """Email the comparison to Bill (MW_COSTS_REPORT_EMAIL), once per rules version."""
    to = os.environ.get("MW_COSTS_REPORT_EMAIL", "bill.brill@inflectionpointnow.com")
    if not to or _reported():
        return False
    _mark_reported()                         # claim it first: never twice
    try:
        import html as _h
        import costing_sheet
        import coordinator_alerts as ca
        from engine.mailer import GraphMailer
        body = ("<pre style='font:13px/1.45 monospace;white-space:pre-wrap'>"
                + _h.escape(report_text(api, costing_sheet.costs())) + "</pre>")
        GraphMailer(mailbox=ca._sender()).send_mail(
            to, "[FAIM Audit Assistant] Moveware API costs vs the Move Costing Sheet", body, html=True)
        _mark_reported()
        return True
    except Exception as e:                       # noqa: BLE001
        with _LOCK:
            _STATE["error"] = f"report: {type(e).__name__}: {e}"[:300]
        try:
            os.remove(_path() + ".reported")     # not sent: try again next time
        except Exception:
            pass
        return False


def _email_problem(text: str) -> None:
    to = os.environ.get("MW_COSTS_REPORT_EMAIL", "bill.brill@inflectionpointnow.com")
    try:
        import coordinator_alerts as ca
        from engine.mailer import GraphMailer
        GraphMailer(mailbox=ca._sender()).send_mail(
            to, "[FAIM Audit Assistant] Moveware API costs: problem", text)
    except Exception:
        pass
